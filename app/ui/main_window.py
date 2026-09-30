import logging
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QMessageBox, QStackedWidget, QFileDialog,
    QLineEdit, QFormLayout, QComboBox, QGroupBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent

from app.config import ARCHIVE_DIR, BACKUP_DIR, DATA_DIR, PACKAGES_DIR, ensure_dirs
from app.core import domain
from app.db.database import (
    SessionLocal, init_db, pending_update_migrations, release_database,
)
from app.db.models import Direction, Project
from app import settings
from app.ai import normative, secrets as secret_store
from app.ai.connector import (
    AIConnector, MODE_INTERNET, MODE_LABELS, MODE_LOCAL, MODE_ORDER,
    build_internet_provider,
)
from app.core.services import ai_service, backup_service, storage_service
from app.core.services.storage_service import (
    ARCHIVE_CATEGORIES, ARCHIVE_CATEGORY_DEFAULT, StorageError, add_file_to_archive,
    calculate_hash, find_by_hash,
)
from app.ui.archive_dialog import show_upload_dialog
from app.core.services import export_checks
from app.core.services.export_checks import check_package
from app.core.services.package_service import PackageError, create_package
from app.core.services.printing import PrintError
from app.core.domain import UnknownLinkRole
from app.ui.package_dialog import PackageDialog
from app.ui.directories_page import DirectoryPage
from app.ui.project_dialog import ProjectCreateDialog
from app.ui.project_window import ProjectWindow
from app.core.services.project_service import (
    ProjectError, can_delete_project, create_project, delete_project,
)

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Единая система управления ИД")
        self.resize(1150, 750)
        self.project_window = None

        # Легкая адаптация стиля (полезно для Windows)
        self.setStyleSheet("""
            QTableWidget { font-size: 13px; }
            QLabel { font-size: 14px; }
            QPushButton { padding: 6px; font-size: 13px; }
        """)

        # Пути хранилища берём из app.config, а не от текущего рабочего
        # каталога: иначе запуск не из корня проекта (ярлык, двойной клик,
        # собранное приложение) указывал бы на другую папку (ТЗ п.72).
        ensure_dirs()
        self.base_dir = str(DATA_DIR)
        self.archive_dir = str(ARCHIVE_DIR)
        self.export_dir = str(PACKAGES_DIR)
        self.backup_dir = str(BACKUP_DIR)

        # База данных и ИИ. Перед применением миграций снимается копия:
        # обновление не должно уничтожить проекты и документы (ТЗ п.97, 98).
        self.update_report = self.prepare_database()
        self.db = SessionLocal()
        # Режим ИИ переживает перезапуск (ТЗ п.9, 101).
        self.secrets = secret_store.STORE
        self.secrets.migrate_plain_to_dpapi(secret_store.GIGACHAT_KEY)
        self.ai = AIConnector(
            # Первый запуск начинает с локального ИИ: наружу ничего не
            # отправляется, пока оператор не выбрал интернет-режим (ТЗ п.9).
            mode=settings.ai_mode(MODE_LOCAL),
            provider=build_internet_provider(),
        )

        self.norms_data = [
            {"code": "СП 48.13330.2019", "title": "Организация строительства", "category": "Свод правил"},
            {"code": "ГОСТ Р 21.101-2020", "title": "Основные требования к ПД и РД", "category": "ГОСТ"},
            {"code": "РД-11-02-2006", "title": "Состав и порядок ведения ИД", "category": "РД"},
        ]

        self.init_ui()
        self.load_projects()
        self.load_archive_files()
        self.load_forms()
        self.load_norms()
        self.load_backups()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # ЛЕВАЯ ПАНЕЛЬ
        nav_container = QWidget()
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(5, 5, 5, 5)

        nav_title = QLabel("НАВИГАЦИЯ")
        nav_title.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        nav_layout.addWidget(nav_title)

        self.nav_list = QListWidget()
        self.nav_list.addItems([
            "Проекты", "Архив файлов (SHA-256)", "Нормативы",
            "Формы документов", "Справочники", "ИИ-Агент", "Настройки",
        ])
        self.nav_list.setFixedWidth(220)
        self.nav_list.setCurrentRow(0)
        nav_layout.addWidget(self.nav_list)
        main_layout.addWidget(nav_container)

        # ПРАВАЯ ПАНЕЛЬ
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)

        self.title_label = QLabel("Рабочая область: Проекты")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        right_layout.addWidget(self.title_label)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.create_projects_page())
        self.stack.addWidget(self.create_archive_page())
        self.stack.addWidget(self.create_norms_page())
        self.stack.addWidget(self.create_forms_page())
        self.directories_page = DirectoryPage(self.db)
        self.stack.addWidget(self.directories_page)
        self.stack.addWidget(self.create_ai_page())
        self.stack.addWidget(self.create_settings_page())

        right_layout.addWidget(self.stack)
        main_layout.addWidget(right_container, stretch=1)

        self.nav_list.currentRowChanged.connect(self.on_nav_changed)

    # -----------------------------------------------------------------
    # СТРАНИЦЫ
    # -----------------------------------------------------------------
    def create_projects_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        btn_layout = QHBoxLayout()
        self.btn_add_project = QPushButton("+ Создать проект")
        self.btn_add_project.clicked.connect(self.add_project)
        btn_layout.addWidget(self.btn_add_project)

        self.btn_delete_project = QPushButton("Удалить проект")
        self.btn_delete_project.setStyleSheet("background-color: #ffdddd; color: #990000;")
        self.btn_delete_project.clicked.connect(self.delete_project)
        btn_layout.addWidget(self.btn_delete_project)

        self.btn_export = QPushButton("Экспорт комплекта (PDF)")
        self.btn_export.clicked.connect(self.export_project_pdf)
        btn_layout.addWidget(self.btn_export)

        # Работа с документами идёт в отдельном окне проекта, которое до этого
        # открывалось только двойным щелчком. Явная кнопка убирает целый класс
        # ошибок приёмки, когда оператор искал «+ Документ» в главном окне
        # (ТЗ п.16).
        self.btn_open_project = QPushButton("Открыть рабочее окно проекта")
        self.btn_open_project.setStyleSheet("background-color: #e6f7ff;")
        self.btn_open_project.clicked.connect(self.open_project_window)
        btn_layout.addWidget(self.btn_open_project)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        hint = QLabel(
            "Выберите проект в таблице и нажмите «Открыть рабочее окно проекта» "
            "(или дважды щёлкните по строке). Разделы, документы, материалы, "
            "связи с архивом, комплекты и печать форм находятся в этом окне."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.projects_table = QTableWidget(0, 4)
        self.projects_table.setHorizontalHeaderLabels(["ID", "Направление", "Наименование", "Адрес"])
        self.projects_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.projects_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # ТЗ п.16: рабочее окно проекта открывается из списка проектов.
        self.projects_table.itemDoubleClicked.connect(self.open_project_window)
        # Выбор проекта в таблице — это и есть выбор проекта для ИИ.
        self.projects_table.itemSelectionChanged.connect(self.refresh_ai_state)
        layout.addWidget(self.projects_table)

        return page

    def open_project_window(self):
        """Открыть рабочее окно проекта (ТЗ п.16)."""
        project_id = self.selected_project_id()
        if project_id is None:
            # Молчаливый выход выглядел как «кнопка не работает».
            QMessageBox.warning(
                self, "Рабочее окно проекта",
                "Сначала выберите проект в таблице.",
            )
            return
        if self.project_window is not None:
            self.project_window.close()
        self.project_window = ProjectWindow(self.db, project_id, self)
        self.project_window.show()
        self.project_window.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose, True
        )

    def create_archive_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        # ТЗ п.50: логические части архива выбираются при загрузке файла.
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("Категория:"))
        self.archive_category_combo = QComboBox()
        self.archive_category_combo.addItems(list(ARCHIVE_CATEGORIES))
        cat_layout.addWidget(self.archive_category_combo, stretch=1)

        btn_upload = QPushButton("Добавить файл в архив")
        btn_upload.setStyleSheet("background-color: #e6f7ff;")
        btn_upload.clicked.connect(self.upload_to_archive)
        cat_layout.addWidget(btn_upload)
        cat_layout.addStretch()
        layout.addLayout(cat_layout)

        # ТЗ п.51: для архивного документа отображается количество его связей.
        # ТЗ п.44: поиск по архиву проекта по имени, номеру и виду.
        self.archive_search_input = QLineEdit()
        self.archive_search_input.setPlaceholderText(
            "Поиск по архиву: имя, номер, вид документа качества"
        )
        self.archive_search_input.textChanged.connect(self.filter_archive_files)
        layout.addWidget(self.archive_search_input)

        self.archive_table = QTableWidget(0, 6)
        self.archive_table.setHorizontalHeaderLabels(
            ["ID", "Категория", "Имя файла", "Вид качества", "Срок действия",
             "Связей"]
        )
        self.archive_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.archive_table)

        return page

    def create_norms_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Поиск:"))
        self.search_norms_input = QLineEdit()
        self.search_norms_input.setPlaceholderText("Введите шифр...")
        self.search_norms_input.textChanged.connect(self.filter_norms)
        filter_layout.addWidget(self.search_norms_input)
        layout.addLayout(filter_layout)

        self.norms_table = QTableWidget(0, 3)
        self.norms_table.setHorizontalHeaderLabels(["Шифр", "Наименование", "Категория"])
        self.norms_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.norms_table)

        return page

    def create_forms_page(self):
        """Нормативные формы документов из справочника (ТЗ п.24, 96)."""
        page = QWidget()
        layout = QVBoxLayout(page)

        info = QLabel(
            "Формы хранятся в справочнике и версионируются: изменение формы "
            "создаёт новую версию, а выпущенные документы сохраняют ту, по "
            "которой были сформированы (ТЗ п.96)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.forms_table = QTableWidget(0, 5)
        self.forms_table.setHorizontalHeaderLabels(
            ["Тип документа", "Версия", "Наименование формы", "Основание", "Разделов"]
        )
        self.forms_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.forms_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.forms_table)

        self.forms_detail = QLabel("")
        self.forms_detail.setWordWrap(True)
        layout.addWidget(self.forms_detail)

        self.forms_table.itemSelectionChanged.connect(self.show_form_details)
        return page

    def create_ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        # ТЗ п.9: при выборе «Нет ИИ» чат ИИ скрывается.
        self.ai_page_status = QLabel()
        self.ai_page_status.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.ai_page_status)

        # Текущий проект для ИИ выбирается в разделе «Проекты». Показываем его
        # здесь явно: иначе оператор нажимал «Спросить ИИ» и получал только
        # предупреждение, не понимая, где выбирается проект (ТЗ п.102).
        self.ai_project_hint = QLabel()
        self.ai_project_hint.setWordWrap(True)
        layout.addWidget(self.ai_project_hint)

        # ТЗ п.102: оператор формулирует запрос словами («Проверь комплект
        # АОСР №15»), а агент сам выбирает нужные документы.
        request_row = QHBoxLayout()
        self.ai_request_input = QLineEdit()
        self.ai_request_input.setPlaceholderText(
            "Например: Проверь комплект АОСР №15"
        )
        self.ai_request_input.returnPressed.connect(self.run_ai_check)
        request_row.addWidget(self.ai_request_input)
        self.btn_run_ai = QPushButton("Спросить ИИ")
        self.btn_run_ai.clicked.connect(self.run_ai_check)
        request_row.addWidget(self.btn_run_ai)
        layout.addLayout(request_row)

        # ТЗ п.105: результат ИИ — черновик, а не исполнительный документ.
        draft_hint = QLabel(
            "Предложения ИИ сохраняются как черновики. Они не являются "
            "выпущенными документами и ничего не меняют, пока оператор не "
            "подтвердит применение (ТЗ п.104, 105)."
        )
        draft_hint.setWordWrap(True)
        layout.addWidget(draft_hint)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setMaximumHeight(110)
        layout.addWidget(self.ai_output)

        # ТЗ п.103: ИИ получает только то, что оператор отметил. Передача
        # текста файла включается галочками, по умолчанию всё выключено.
        self.ai_files_box = QGroupBox(
            "Текст файлов архива для анализа (ТЗ п.103)"
        )
        files_layout = QVBoxLayout(self.ai_files_box)
        self.ai_files_hint = QLabel(
            "Ничего не отмечено — ИИ видит только реквизиты. Отмеченные "
            "файлы читает система; пути и имена папок наружу не передаются. "
            "В интернет-режиме передача текста невозможна."
        )
        self.ai_files_hint.setWordWrap(True)
        files_layout.addWidget(self.ai_files_hint)
        self.ai_files_list = QListWidget()
        self.ai_files_list.setSelectionMode(
            QListWidget.SelectionMode.NoSelection
        )
        self.ai_files_list.setMaximumHeight(110)
        files_layout.addWidget(self.ai_files_list)
        layout.addWidget(self.ai_files_box)

        self.ai_table = QTableWidget(0, 4)
        self.ai_table.setHorizontalHeaderLabels(
            ["Предложение", "Основание", "Черновик", "Статус"]
        )
        self.ai_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.ai_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ai_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.ai_table)

        actions = QHBoxLayout()
        self.btn_apply_proposal = QPushButton("Применить предложение")
        self.btn_apply_proposal.clicked.connect(self.apply_ai_proposal)
        actions.addWidget(self.btn_apply_proposal)
        self.btn_reject_proposal = QPushButton("Отклонить")
        self.btn_reject_proposal.clicked.connect(self.reject_ai_proposal)
        actions.addWidget(self.btn_reject_proposal)
        actions.addStretch()
        layout.addLayout(actions)

        self.refresh_ai_state()
        return page

    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        group_storage = QGroupBox("Локальное хранилище (Windows/Linux совместимо)")
        form_storage = QFormLayout(group_storage)

        display_path = os.path.normpath(self.base_dir)
        self.path_input = QLineEdit(display_path)
        self.path_input.setReadOnly(True)
        form_storage.addRow("Директория БД и файлов:", self.path_input)

        # ТЗ п.72: рабочее хранилище, комплекты и BACKUP — разные сущности.
        # Удалять их одним «обслуживанием» нельзя, поэтому здесь только
        #Backup-функция (раздел Этап 6) и просмотр путей.
        form_storage.addRow("Внутренний архив:", QLabel(os.path.normpath(self.archive_dir)))
        form_storage.addRow("Папка комплектов:", QLabel(os.path.normpath(self.export_dir)))
        form_storage.addRow("Резервные копии:", QLabel(os.path.normpath(self.backup_dir)))
        layout.addWidget(group_storage)

        # ТЗ п.74, 98: копия проекта — отдельная сущность, а не комплект.
        group_backup = QGroupBox("Резервное копирование проекта (ТЗ п.74, 98)")
        backup_layout = QVBoxLayout(group_backup)
        backup_hint = QLabel(
            "Копия содержит базу, файлы архива, связи, версии, историю и "
            "настройки. Восстановление на другом компьютере возвращает проект "
            "в рабочее состояние."
        )
        backup_hint.setWordWrap(True)
        backup_layout.addWidget(backup_hint)

        buttons = QHBoxLayout()
        self.btn_create_backup = QPushButton("Создать резервную копию")
        self.btn_create_backup.clicked.connect(self.create_backup_action)
        buttons.addWidget(self.btn_create_backup)

        self.btn_restore_backup = QPushButton("Восстановить из копии…")
        self.btn_restore_backup.clicked.connect(self.restore_backup_action)
        buttons.addWidget(self.btn_restore_backup)
        backup_layout.addLayout(buttons)

        self.backup_list = QTableWidget(0, 3)
        self.backup_list.setHorizontalHeaderLabels(
            ["Копия", "Создана", "Проектов / документов"]
        )
        self.backup_list.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.backup_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.backup_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        backup_layout.addWidget(self.backup_list)

        refresh = QPushButton("Обновить список копий")
        refresh.clicked.connect(self.load_backups)
        backup_layout.addWidget(refresh)
        layout.addWidget(group_backup)

        group_ai = QGroupBox("Параметры ИИ")
        form_ai = QFormLayout(group_ai)
        # ТЗ п.9: нет ИИ / локальный ИИ / интернет-ИИ.
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems([MODE_LABELS[m] for m in MODE_ORDER])
        self.ai_mode_combo.setCurrentIndex(
            MODE_ORDER.index(self.ai.mode) if self.ai.mode in MODE_ORDER else 0
        )
        self.ai_mode_combo.currentIndexChanged.connect(self.on_ai_mode_changed)
        form_ai.addRow("Режим работы:", self.ai_mode_combo)
        self.ai_status_label = QLabel()
        self.ai_status_label.setStyleSheet("font-weight: bold;")
        form_ai.addRow("Состояние:", self.ai_status_label)

        # ТЗ п.101: интернет-ИИ — внешний сервис, у него есть адрес,
        # модель и ключ доступа. Без них режим честно остаётся
        # ненастроенным, а не изображает работу.
        from app.ai.gigachat import (
            DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_SCOPE,
        )

        self.ai_base_url_edit = QLineEdit(
            settings.get_setting("gigachat_base_url", DEFAULT_BASE_URL)
        )
        form_ai.addRow("Адрес сервиса:", self.ai_base_url_edit)
        self.ai_model_edit = QLineEdit(
            settings.get_setting("gigachat_model", DEFAULT_MODEL)
        )
        form_ai.addRow("Модель:", self.ai_model_edit)
        self.ai_scope_edit = QLineEdit(
            settings.get_setting("gigachat_scope", DEFAULT_SCOPE)
        )
        form_ai.addRow("Область доступа (scope):", self.ai_scope_edit)
        self.ai_key_edit = QLineEdit()
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText(
            "Ключ авторизации GigaChat (сохраняется в защищённом хранилище)"
        )
        self.ai_key_edit.setToolTip(
            "Ключ копируется из личного кабинета целиком и вводится без "
            "изменений — кодировать его не нужно. Если вводите пару вручную, "
            "укажите «Client ID:Client Secret» через двоеточие."
        )
        form_ai.addRow("Ключ GigaChat:", self.ai_key_edit)
        self.ai_key_state = QLabel()
        self.ai_key_state.setWordWrap(True)
        form_ai.addRow("Хранение ключа:", self.ai_key_state)

        ai_buttons = QHBoxLayout()
        save_ai = QPushButton("Сохранить параметры ИИ")
        save_ai.clicked.connect(self.save_ai_settings)
        ai_buttons.addWidget(save_ai)
        check_ai = QPushButton("Проверить подключение")
        check_ai.clicked.connect(self.check_ai_connection)
        ai_buttons.addWidget(check_ai)
        forget_ai = QPushButton("Забыть ключ")
        forget_ai.clicked.connect(self.forget_ai_key)
        ai_buttons.addWidget(forget_ai)
        form_ai.addRow(ai_buttons)

        self.refresh_ai_key_state()
        self.on_ai_mode_changed(self.ai_mode_combo.currentIndex())
        layout.addWidget(group_ai)
        layout.addStretch()

        return page

    # -----------------------------------------------------------------
    # ЛОГИКА
    # -----------------------------------------------------------------
    def on_nav_changed(self, row: int):
        self.stack.setCurrentIndex(row)
        self.title_label.setText(f"Рабочая область: {self.nav_list.item(row).text()}")
        # Подсказка о текущем проекте на странице ИИ должна отражать выбор,
        # сделанный в «Проектах».
        self.refresh_ai_state()

    def selected_project_id(self) -> int | None:
        """ID выбранного проекта или None, если проект не выбран."""
        selected = self.projects_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.projects_table.item(row, 0)
        if item is None:
            return None
        try:
            return int(item.text())
        except ValueError:
            return None

    def load_forms(self):
        """Показать формы из справочника: версия, основание, объём."""
        from app.db.models import NormativeForm

        self.forms_table.setRowCount(0)
        for form in self.db.query(NormativeForm).order_by(
            NormativeForm.doc_type, NormativeForm.version.desc()
        ).all():
            definition = form.definition or {}
            row = self.forms_table.rowCount()
            self.forms_table.insertRow(row)
            self.forms_table.setItem(
                row, 0, QTableWidgetItem(domain.DOC_TYPE_LABELS.get(
                    form.doc_type, form.doc_type)))
            self.forms_table.setItem(row, 1, QTableWidgetItem(
                f"v{form.version}" + ("" if form.is_current else " (архивная)")))
            self.forms_table.setItem(row, 2, QTableWidgetItem(form.title))
            self.forms_table.setItem(row, 3, QTableWidgetItem(form.basis or "—"))
            self.forms_table.setItem(row, 4, QTableWidgetItem(
                str(len(definition.get("sections", [])))))
        self.forms_detail.setText(
            "" if self.forms_table.rowCount() else
            "Справочник форм пуст. Формы загружаются при первом запуске."
        )

    def show_form_details(self):
        """Разделы выбранной формы: состав печатного документа."""
        from app.db.models import NormativeForm

        rows = self.forms_table.selectionModel().selectedRows() \
            if self.forms_table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        doc_type = self.forms_table.item(row, 0).text()
        version = self.forms_table.item(row, 1).text().split()[0].lstrip("v")
        form = self.db.query(NormativeForm).filter_by(
            doc_type=next(
                (k for k, v in domain.DOC_TYPE_LABELS.items() if v == doc_type),
                doc_type),
            version=int(version),
        ).one_or_none()
        if form is None:
            self.forms_detail.setText("")
            return
        lines = []
        for section in (form.definition or {}).get("sections", []):
            blocks = section.get("blocks", [])
            lines.append(
                f"{section.get('number')}. {section.get('title')} — блоков: {len(blocks)}"
            )
        self.forms_detail.setText("\n".join(lines))

    def load_projects(self):
        self.projects_table.setRowCount(0)
        for p in self.db.query(Project).all():
            row = self.projects_table.rowCount()
            self.projects_table.insertRow(row)
            self.projects_table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.projects_table.setItem(
                row, 1, QTableWidgetItem(p.direction.name if p.direction else "")
            )
            self.projects_table.setItem(row, 2, QTableWidgetItem(p.title or ""))
            self.projects_table.setItem(row, 3, QTableWidgetItem(p.address or ""))

    def load_archive_files(self, search: str = ""):
        """Показать архив проекта (ТЗ п.44, 51, 46)."""
        project_id = self.selected_project_id()
        self.archive_table.setRowCount(0)
        if project_id is None:
            return
        for f in storage_service.search_archive_documents(
            self.db, project_id, search=search
        ):
            row = self.archive_table.rowCount()
            self.archive_table.insertRow(row)
            self.archive_table.setItem(row, 0, QTableWidgetItem(str(f.id)))
            self.archive_table.setItem(row, 1, QTableWidgetItem(f.category or ""))
            self.archive_table.setItem(row, 2, QTableWidgetItem(f.original_name or ""))
            # Вид документа качества и срок действия вводятся оператором
            # (ТЗ п.45, 46) и показываются рядом с именем файла.
            quality_type = f.note.split("Вид документа качества: ")[-1] \
                if f.note and "Вид документа качества: " in f.note else ""
            self.archive_table.setItem(row, 3, QTableWidgetItem(quality_type))
            self.archive_table.setItem(
                row, 4, QTableWidgetItem(storage_service.quality_validity_text(f))
            )
            # ТЗ п.51: счётчик связей архивного документа.
            self.archive_table.setItem(row, 5, QTableWidgetItem(str(f.links_count)))

    def filter_archive_files(self, text: str = ""):
        """Поиск по архиву проекта (ТЗ п.44)."""
        self.load_archive_files(text)

    def upload_to_archive(self):
        # Архивный документ принадлежит проекту (ТЗ п.89, 90), поэтому без
        # выбранного проекта загрузка не имеет смысла.
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(
                self, "Нужен проект",
                "Выберите проект в разделе «Проекты»: архивный документ "
                "принадлежит конкретному проекту.",
            )
            return

        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Все файлы (*)")
        if not file_path:
            return

        source = Path(file_path)
        try:
            file_hash = calculate_hash(source)
            existing = find_by_hash(self.db, file_hash)
        except StorageError as e:
            QMessageBox.critical(self, "Ошибка загрузки", str(e))
            return

        if existing:
            # ТЗ п.92: физическая копия не создаётся второй раз.
            QMessageBox.information(
                self, "Дубликат",
                f"Такой файл уже есть в архиве (ID {existing.archive_document_id}, "
                f"связей: {existing.archive_document.links_count}).\n"
                f"Новая копия не создана — используйте существующий архивный документ.",
            )
            return

        # ТЗ п.45, 46: вид документа качества и срок его действия вводит
        # оператор; к актам документ качества прикрепляется отдельно.
        category = self.archive_category_combo.currentText() or ARCHIVE_CATEGORY_DEFAULT
        try:
            details = show_upload_dialog(
                self.db, source.name, category, parent=self
            )
        except StorageError as e:
            QMessageBox.critical(self, "Ошибка загрузки", str(e))
            return
        if details is None:
            return

        try:
            archive_doc = add_file_to_archive(
                self.db, source, project_id,
                category=details["category"],
                quality_type=details["quality_type"],
                validity_from=details["validity_from"],
                validity_to=details["validity_to"],
                number=details["number"],
            )
        except StorageError as e:
            QMessageBox.critical(self, "Ошибка загрузки", str(e))
            return

        self.load_archive_files(self.archive_search_input.text())
        text = f"Файл загружен в архив (ID {archive_doc.id}).\nКатегория: {archive_doc.category}"
        validity = storage_service.quality_validity_text(archive_doc)
        if validity:
            text += f"\nСрок действия: {validity}"
        QMessageBox.information(
            self, "Успех",
            f"{text}\nВерсий: {len(archive_doc.versions)}\n"
            f"Связей: {archive_doc.links_count}",
        )

    def load_norms(self):
        self.norms_table.setRowCount(0)
        for n in self.norms_data:
            row = self.norms_table.rowCount()
            self.norms_table.insertRow(row)
            self.norms_table.setItem(row, 0, QTableWidgetItem(n["code"]))
            self.norms_table.setItem(row, 1, QTableWidgetItem(n["title"]))
            self.norms_table.setItem(row, 2, QTableWidgetItem(n["category"]))

    def filter_norms(self, text: str):
        text = text.lower()
        self.norms_table.setRowCount(0)
        for n in self.norms_data:
            if text in n["code"].lower() or text in n["title"].lower():
                row = self.norms_table.rowCount()
                self.norms_table.insertRow(row)
                self.norms_table.setItem(row, 0, QTableWidgetItem(n["code"]))
                self.norms_table.setItem(row, 1, QTableWidgetItem(n["title"]))
                self.norms_table.setItem(row, 2, QTableWidgetItem(n["category"]))

    def on_ai_mode_changed(self, index: int):
        """ТЗ п.9: переключение режима ИИ и индикация состояния.

        Выбранный режим сохраняется: обработка данных не должна
        молча возвращаться к другой при следующем запуске.
        """
        self.ai.mode = MODE_ORDER[index]
        settings.set_ai_mode(self.ai.mode)
        self.refresh_ai_state()

    def save_ai_settings(self):
        """Сохранить адрес, модель, область доступа и ключ (ТЗ п.101)."""
        from app.ai import secrets as secret_store

        settings.set_setting("gigachat_base_url",
                             self.ai_base_url_edit.text().strip())
        settings.set_setting("gigachat_model", self.ai_model_edit.text().strip())
        settings.set_setting("gigachat_scope", self.ai_scope_edit.text().strip())
        entered = self.ai_key_edit.text().strip()
        if entered:
            backend = self.secrets.set(secret_store.GIGACHAT_KEY, entered)
            self.ai_key_edit.clear()
            self.ai_key_state.setText(
                "Ключ сохранён средствами Windows."
                if backend == secret_store.BACKEND_DPAPI
                else "Ключ сохранён открытым в файле настроек — это небезопасно, "
                     "замените способ хранения или удалите ключ."
            )
        self.refresh_ai_key_state()
        self.ai.provider = build_internet_provider()
        self.refresh_ai_state()

    def refresh_ai_key_state(self):
        """Показать, где хранится ключ и чем это грозит (ТЗ п.101)."""
        from app.ai import secrets as secret_store

        status = self.secrets.status(secret_store.GIGACHAT_KEY)
        if not status["present"]:
            text = "Ключ не задан."
        else:
            names = {
                secret_store.BACKEND_DPAPI: "защищённое хранилище Windows (DPAPI)",
                secret_store.BACKEND_ENV: "переменная окружения",
                secret_store.BACKEND_PLAIN: "открытый файл настроек",
            }
            text = f"Ключ сохранён: {names.get(status['backend'], 'неизвестно')}."
        if status["warnings"]:
            text += " " + " ".join(status["warnings"])
        self.ai_key_state.setText(text)

    def forget_ai_key(self):
        """Удалить ключ: без него интернет-ИИ работать не сможет."""
        from app.ai import secrets as secret_store

        self.secrets.forget(secret_store.GIGACHAT_KEY)
        self.ai_key_edit.clear()
        self.ai.provider = build_internet_provider()
        self.refresh_ai_key_state()
        self.refresh_ai_state()

    def check_ai_connection(self):
        """Проверить ключ и доступность модели до рабочего запроса."""
        from app.ai.gigachat import GigaChatConfig, GigaChatError, GigaChatProvider
        from app.ai import secrets as secret_store

        self.save_ai_settings()
        config = GigaChatConfig(
            base_url=self.ai_base_url_edit.text().strip(),
            model=self.ai_model_edit.text().strip(),
            scope=self.ai_scope_edit.text().strip(),
        )
        provider = GigaChatProvider(
            config, self.secrets.get(secret_store.GIGACHAT_KEY)
        )
        if not provider.configured():
            QMessageBox.warning(
                self, "ИИ-агент",
                "Задайте модель и ключ авторизации GigaChat.",
            )
            return
        try:
            answer = provider.ask("Ответь одним словом: готовность проверена.")
        except GigaChatError as exc:
            QMessageBox.critical(self, "ИИ-агент", str(exc))
            return
        QMessageBox.information(self, "ИИ-агент", f"Соединение установлено.\n{answer}")

    def refresh_ai_state(self):
        """Обновить индикацию и видимость чата ИИ (ТЗ п.9)."""
        color = self.ai.mode_color()
        text = f"ИИ: {self.ai.mode_label}"

        for label in (getattr(self, "ai_page_status", None), getattr(self, "ai_status_label", None)):
            if label is not None:
                label.setText(text)
                label.setStyleSheet(f"color: {color}; font-weight: bold;")

        project_id = self.selected_project_id()
        if getattr(self, "ai_project_hint", None) is not None:
            if project_id is None:
                self.ai_project_hint.setText(
                    "Проект не выбран. Откройте «Проекты», выберите строку и "
                    "вернитесь сюда — ИИ работает с выбранным проектом "
                    "(ТЗ п.102)."
                )
                self.ai_project_hint.setStyleSheet("color: #990000;")
            else:
                project = self.db.get(Project, project_id)
                title = project.title if project is not None else f"№{project_id}"
                self.ai_project_hint.setText(
                    f"Текущий проект: {title}. Сменить — в разделе «Проекты»."
                )
                self.ai_project_hint.setStyleSheet("")

        if getattr(self, "ai_output", None) is not None:
            self.ai_output.setVisible(self.ai.enabled)
        if getattr(self, "btn_run_ai", None) is not None:
            self.btn_run_ai.setVisible(self.ai.enabled)
        if not self.ai.enabled and getattr(self, "ai_output", None) is not None:
            self.ai_output.clear()

    def add_project(self):
        # Направление выбирается из справочника (ТЗ п.14, 20), а не из
        # захардкоженного списка: справочник редактируется оператором.
        if not self.db.query(Direction).first():
            QMessageBox.critical(
                self, "Справочник пуст",
                "Справочник направлений не заполнен. Перезапустите приложение.",
            )
            return

        dialog = ProjectCreateDialog(self.db, self)
        if not dialog.exec():
            return
        values = dialog.values()
        if not (values["title"] or "").strip():
            QMessageBox.warning(
                self, "Новый проект", "Укажите наименование проекта."
            )
            return
        if values["direction_id"] is None:
            QMessageBox.warning(
                self, "Новый проект", "Выберите направление работ."
            )
            return
        try:
            create_project(self.db, **values)
        except ProjectError as exc:
            QMessageBox.critical(self, "Новый проект", str(exc))
            return
        self.load_projects()

    def delete_project(self):
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Удаление проекта", "Выберите проект.")
            return
        proj = self.db.get(Project, project_id)
        if proj is None:
            return

        # Правила удаления живут в сервисе, а не здесь. Дублирование логики
        # в окне привело к тому, что защита от потери выпусков и архива
        # работала на сервисном пути, а оператор удалял проект из интерфейса
        # мимо неё и получал необработанную ошибку (ТЗ п.97).
        allowed, stats = can_delete_project(self.db, project_id)
        if not allowed:
            QMessageBox.warning(
                self,
                "Удаление невозможно",
                self._deletion_blocked_reason(stats),
            )
            return

        # ТЗ п.86, 109: проект — корень истории. Удаление необратимо,
        # поэтому требуем явного подтверждения оператора.
        answer = QMessageBox.question(
            self,
            "Удаление проекта",
            f"Удалить проект «{proj.title}»?\n"
            f"Вместе с ним будет удалено документов: {stats['documents']}.\n"
            "Действие необратимо.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            delete_project(self.db, project_id)
        except ProjectError as exc:
            # Правило могло сработать между проверкой и удалением.
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        self.load_projects()

    @staticmethod
    def _deletion_blocked_reason(stats: dict) -> str:
        """Причина отказа словами оператора, а не именами полей."""
        if stats.get("archive_documents"):
            return (
                f"В проекте {stats['archive_documents']} архивных документов.\n"
                "Сначала очистите архив проекта — файлы не удаляются "
                "автоматически вместе с проектом."
            )
        return (
            f"В проекте {stats['issued_versions']} выпущенных версий документов.\n"
            "Выпуск — исторический результат (ТЗ п.54, 85) и не удаляется "
            "вместе с проектом."
        )

    def export_project_pdf(self):
        """Сформировать комплект: выбор документов, вариант и папка (ТЗ п.69)."""
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Ошибка", "Выберите проект!")
            return

        dialog = PackageDialog(self.db, project_id, self)
        if dialog.exec() != PackageDialog.DialogCode.Accepted:
            return
        document_ids = dialog.selected_document_ids()

        # ТЗ п.82: проверяются только документы этой выгрузки, до создания
        # папки комплектов.
        result = check_package(self.db, project_id, document_ids)
        if result.has_errors and not self._confirm_export_with_errors(result):
            return

        try:
            package = create_package(
                self.db, project_id,
                base_dir=dialog.base_dir,
                root_name=dialog.root_name(),
                document_ids=document_ids,
                variant=dialog.variant(),
                page_numbering=dialog.page_numbering(),
                allow_errors=result.has_errors,
            )
        except (PackageError, PrintError, UnknownLinkRole) as exc:
            # ТЗ п.73: место хранения предлагается выбрать заново.
            QMessageBox.critical(self, "Комплект не сформирован", str(exc))
            return
        except StorageError as exc:
            QMessageBox.critical(self, "Ошибка файла", str(exc))
            return

        folder = os.path.normpath(package.absolute_path)
        text = f"Комплект сформирован:\n{folder}"
        if package.has_errors_file:
            text += (
                f"\n\nПроблемы записаны в файл:\n"
                f"{os.path.normpath(str(Path(folder) / export_checks.ERRORS_FILE_NAME))}"
            )
        if result.warnings:
            # Длинный список замечаний сверху: оператор видит главное сразу
            # (ТЗ п.83), остальное — в отчёте и файле ошибок.
            shown = result.warnings[:10]
            lines = [f"• {problem.subject}: {problem.message}" for problem in shown]
            rest = len(result.warnings) - len(shown)
            if rest:
                lines.append(f"• и ещё {rest} замечаний — см. отчёт проверки.")
            text += "\n\nЗамечания:\n" + "\n".join(lines)
        QMessageBox.information(
            self,
            "Выгружено с ошибками" if package.has_errors_file else "Успех",
            text,
        )

    def _confirm_export_with_errors(self, result) -> bool:
        """Показать проблемы комплекта и спросить, выгружать ли всё равно.

        ТЗ п.83: при выборе «всё равно завершить» в папке выгрузки создаётся
        `Ошибки выгрузки.txt`; отказ отменяет выгрузку целиком.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Проверка комплекта (ТЗ п.82)")
        box.setText(
            "Обнаружены проблемы комплекта:\n\n"
            + "\n".join(f"• {problem}" for problem in result.problems[:20])
            + ("\n…" if len(result.problems) > 20 else "")
        )
        finish = box.addButton(
            "Всё равно завершить", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Отменить выгрузку", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is finish

    def run_ai_check(self):
        """Спросить ИИ по проекту (ТЗ п.102).

        Агент получает только контекст, собранный системой (ТЗ п.103), а его
        предложения сохраняются черновиками (ТЗ п.105).
        """
        if not self.ai.enabled:
            self.ai_output.setText("ИИ выключен. Включите его в настройках.")
            return
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(
                self, "ИИ-агент",
                "Выберите проект в разделе «Проекты»: ИИ работает с проектом "
                "и его документами (ТЗ п.102).",
            )
            return
        request = self.ai_request_input.text().strip()
        if not request:
            request = "Проверь комплект проекта"
        file_text_ids = self.selected_ai_file_ids()
        if file_text_ids and not self._confirm_ai_file_text(file_text_ids):
            return
        try:
            result = ai_service.analyze(
                self.db, project_id, request, self.ai, file_text_ids=file_text_ids
            )
        except (ai_service.AiError, ValueError) as exc:
            QMessageBox.critical(self, "ИИ-агент", str(exc))
            return
        answer = result["answer"]
        from app.ai.context import describe_context

        text = (
            f"Режим: {self.ai.mode_label}. Статус: {answer.get('status')}\n"
            f"Контекст: {describe_context(result['context'])}\n"
            f"Найдено предложений: {len(result['proposals'])}\n"
            "Все предложения — черновики; применение — только по подтверждению "
            "оператора (ТЗ п.104, 105)."
        )
        if answer.get("message"):
            text += f"\n{answer['message']}"
        self.ai_output.setText(text)
        self.load_ai_proposals(project_id)

    def load_ai_proposals(self, project_id: int):
        """Показать предложения ИИ по проекту (ТЗ п.105)."""
        self.load_ai_file_list(project_id)
        proposals = ai_service.list_proposals(self.db, project_id)
        self.ai_table.setRowCount(0)
        for proposal in proposals:
            row = self.ai_table.rowCount()
            self.ai_table.insertRow(row)
            item = QTableWidgetItem(f"{proposal.code}: {proposal.text}")
            item.setData(Qt.ItemDataRole.UserRole, proposal.id)
            self.ai_table.setItem(row, 0, item)
            self.ai_table.setItem(
                row, 1, QTableWidgetItem(normative.format_basis(proposal.basis))
            )
            self.ai_table.setItem(
                row, 2,
                QTableWidgetItem(
                    "требование нормы" if proposal.is_requirement else "наблюдение"
                ),
            )
            self.ai_table.setItem(row, 3, QTableWidgetItem(proposal.status))

    def _confirm_ai_file_text(self, file_text_ids: list[int]) -> bool:
        """Подтверждение передачи текста файлов ИИ (ТЗ п.103, 104).

        Оператор видит, что именно будет прочитано, и может отказаться.
        Режим проверяется здесь же: интернет-режим с текстом файлов
        запрещён, поэтому предупреждение не должно молча удивлять.
        """
        names = []
        for index in range(self.ai_files_list.count()):
            item = self.ai_files_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) in file_text_ids:
                names.append(item.text())
        if self.ai.mode == MODE_INTERNET:
            QMessageBox.warning(
                self, "Передача текста файлов (ТЗ п.103)",
                "В интернет-режиме текст файлов передавать нельзя.\n"
                "Снимите отметки или переключитесь на локальный ИИ.",
            )
            return False
        answer = QMessageBox.question(
            self,
            "Передача текста файлов ИИ (ТЗ п.103)",
            "Будут прочитаны и переданы ИИ файлы:\n• "
            + "\n• ".join(names)
            + "\n\nПередача выполняется локально, пути и имена папок не "
            "передаются. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def load_ai_file_list(self, project_id: int | None) -> None:
        """Показать файлы архива, текст которых можно передать ИИ (ТЗ п.103).

        Список заполняется без галочек: передать содержимое файла можно только
        сознательным выбором оператора (ТЗ п.103, 104).
        """
        self.ai_files_list.clear()
        if project_id is None:
            self.ai_files_hint.setText(
                "Выберите проект: файлы его архива появятся здесь (ТЗ п.103)."
            )
            return
        from app.db.models import ArchiveDocument
        from app.core.services import storage_service

        documents = (
            self.db.query(ArchiveDocument)
            .filter(ArchiveDocument.project_id == project_id)
            .order_by(ArchiveDocument.category, ArchiveDocument.original_name)
            .all()
        )
        for archive in documents:
            version = storage_service.actual_file_version(self.db, archive.id)
            label = f"{archive.category}: {archive.original_name}"
            if version is not None:
                label += f" (версия {version.version_no})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, archive.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.ai_files_list.addItem(item)
        self.ai_files_hint.setText(
            "Ничего не отмечено — ИИ видит только реквизиты. Отмеченные "
            "файлы читает система; пути и имена папок наружу не передаются. "
            "В интернет-режиме передача текста невозможна."
        )

    def selected_ai_file_ids(self) -> list[int]:
        """Идентификаторы файлов, текст которых оператор передал ИИ (ТЗ п.103)."""
        ids = []
        for index in range(self.ai_files_list.count()):
            item = self.ai_files_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                archive_id = item.data(Qt.ItemDataRole.UserRole)
                if archive_id is not None:
                    ids.append(archive_id)
        return ids

    def _selected_ai_proposal_id(self) -> int | None:
        """ID выбранного предложения ИИ или None (ТЗ п.104)."""
        rows = self.ai_table.selectionModel().selectedRows() if self.ai_table.selectionModel() else []
        if not rows:
            return None
        item = self.ai_table.item(rows[0].row(), 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def apply_ai_proposal(self):
        """Подтвердить предложение ИИ и применить через API (ТЗ п.104)."""
        proposal_id = self._selected_ai_proposal_id()
        if proposal_id is None:
            QMessageBox.information(
                self, "Предложение ИИ",
                "Выберите предложение в таблице (ТЗ п.104).",
            )
            return
        answer = QMessageBox.question(
            self,
            "Применение предложения ИИ (ТЗ п.104)",
            "Изменение выполнит система через свои проверки.\n"
            "Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            proposal = ai_service.accept(self.db, proposal_id)
        except ai_service.AiError as exc:
            QMessageBox.warning(self, "Предложение не применено", str(exc))
            return
        self.load_ai_proposals(proposal.project_id)
        QMessageBox.information(
            self, "Предложение применено",
            f"{proposal.decision_note or 'Изменение выполнено'} (ТЗ п.104).",
        )

    def reject_ai_proposal(self):
        """Отклонить предложение ИИ (ТЗ п.104)."""
        proposal_id = self._selected_ai_proposal_id()
        if proposal_id is None:
            QMessageBox.information(
                self, "Предложение ИИ",
                "Выберите предложение в таблице (ТЗ п.104).",
            )
            return
        try:
            proposal = ai_service.reject(self.db, proposal_id)
        except ai_service.AiError as exc:
            QMessageBox.warning(self, "Предложение", str(exc))
            return
        self.load_ai_proposals(proposal.project_id)

    # -----------------------------------------------------------------
    # РЕЗЕРВНОЕ КОПИРОВАНИЕ (ТЗ п.74, 98)
    # -----------------------------------------------------------------

    def prepare_database(self) -> dict:
        """Копия перед обновлением, затем миграции и отчёт оператору (ТЗ п.97).

        Пока база не доведена до актуальной схемы, изменять её структуру
        нельзя, а миграция пересобирает таблицы. Поэтому сначала снимается
        копия (ТЗ п.98), и только потом применяются миграции.
        """
        pending = pending_update_migrations()
        safety = None
        if pending:
            try:
                safety = backup_service.create_update_backup()
            except backup_service.BackupError as exc:
                QMessageBox.critical(
                    self,
                    "Обновление программы",
                    f"Не удалось сделать резервную копию перед обновлением:\n{exc}\n\n"
                    "Обновление не выполнено: без копии миграция может привести "
                    "к потере данных (ТЗ п.97, 98).",
                )
                raise
        report = init_db()
        report["update_backup"] = safety
        if pending:
            applied = ", ".join(report.get("migrations") or []) or "нет"
            text = (
                "Программа обновлена, схема базы приведена к актуальной версии.\n"
                f"Применено миграций: {applied}\n"
                "Проекты, документы, архив, связи, версии, комплекты и реестры "
                "сохранены."
            )
            if safety is not None:
                text += f"\n\nКопия до обновления: {safety}"
            QMessageBox.information(self, "Обновление программы", text)
        return report

    def load_backups(self):
        """Показать имеющиеся копии: свежие сверху (ТЗ п.74)."""
        self.backup_list.setRowCount(0)
        for item in backup_service.list_backups(BACKUP_DIR):
            counts = item.get("counts", {})
            row = self.backup_list.rowCount()
            self.backup_list.insertRow(row)
            self.backup_list.setItem(row, 0, QTableWidgetItem(item["name"]))
            self.backup_list.setItem(
                row, 1, QTableWidgetItem(item["created_at"].strftime("%d.%m.%Y %H:%M"))
            )
            self.backup_list.setItem(row, 2, QTableWidgetItem(
                f"{counts.get('projects', 0)} / {counts.get('documents', 0)}"
            ))

    def create_backup_action(self):
        """Создать копию проекта: база, файлы, настройки (ТЗ п.74, 98).

        Несохранённые правки не коммитятся молча: сервис их отклонит, чтобы
        копия не разошлась с тем, что оператор видит (ТЗ п.86, 98).
        """
        try:
            folder = backup_service.create_backup(self.db)
        except backup_service.BackupError as exc:
            QMessageBox.critical(self, "Резервная копия", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            # Сюда попадает всё, что сервис не описал. Молча закрывать
            # программу нельзя: оператор должен увидеть причину.
            log.exception("Не удалось создать резервную копию")
            QMessageBox.critical(
                self,
                "Резервная копия",
                f"Не удалось создать резервную копию.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                "Подробности записаны в файл app.log рядом с базой.",
            )
            return
        # Чтение и проверка только что созданной копии тоже могут упасть
        # (диск отключили, файл занят): это не должно закрывать программу.
        try:
            self.load_backups()
            problems = backup_service.verify_backup(folder)
            text = backup_service.describe_backup(folder)
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Не удалось проверить созданную копию")
            QMessageBox.warning(
                self,
                "Резервная копия",
                f"Копия создана, но проверить её не удалось.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"Папка: {folder}",
            )
            return
        if problems:
            QMessageBox.warning(
                self,
                "Резервная копия",
                text + "\n\nКопия создана, но проверка нашла замечания:\n"
                + "\n".join(f"• {problem}" for problem in problems),
            )
        else:
            QMessageBox.information(
                self,
                "Резервная копия",
                text + "\n\nКопия создана и проверена.\n"
                f"Папка: {folder}",
            )

    def restore_backup_action(self):
        """Восстановить проект из копии (ТЗ п.98).

        Восстановление подменяет файл базы, поэтому соединения с ней
        освобождаются, а оператору предлагается перезапустить программу:
        открытые списки и окно проекта показывали бы прежние данные.
        """
        if self.project_window is not None:
            self.project_window.close()
            self.project_window = None
        folder = QFileDialog.getExistingDirectory(
            self, "Папка резервной копии", str(BACKUP_DIR)
        )
        if not folder:
            return
        try:
            problems = backup_service.verify_backup(folder)
        except backup_service.BackupError as exc:
            problems = [str(exc)]
        if problems:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Копия не пригодна для восстановления")
            box.setText(
                "Восстановление невозможно, рабочие данные не изменены:\n\n"
                + "\n".join(f"• {problem}" for problem in problems)
            )
            box.exec()
            return
        answer = QMessageBox.question(
            self,
            "Восстановление из копии (ТЗ п.98)",
            f"Восстановить проект из копии\n{folder}?\n\n"
            "Текущие данные будут отложены в папку копий, чтобы восстановление\n"
            "можно было отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            release_database()
            safety = backup_service.restore_backup(folder)
        except backup_service.BackupError as exc:
            self.db = SessionLocal()
            QMessageBox.critical(
                self,
                "Восстановление не выполнено",
                f"{exc}\n\nРабочие данные не изменены.",
            )
            return
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Восстановление из копии прервано ошибкой")
            self.db = SessionLocal()
            QMessageBox.critical(
                self,
                "Восстановление не выполнено",
                f"Восстановление прервано ошибкой.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                "Рабочие данные не изменены, прежние данные отложены в папку "
                "копий. Подробности записаны в файл app.log рядом с базой.",
            )
            return
        self.db = SessionLocal()
        self.load_backups()
        QMessageBox.information(
            self,
            "Восстановление выполнено",
            f"Проект восстановлен из копии {Path(folder).name}.\n"
            f"Прежние данные отложены: {safety}\n\n"
            "Перезапустите программу, чтобы увидеть восстановленные данные.",
        )

    def closeEvent(self, event: QCloseEvent):
        """Безопасно закрываем подключение к SQLite при выходе (важно для Windows)"""
        try:
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
