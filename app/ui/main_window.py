import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QMessageBox, QInputDialog, QStackedWidget, QFileDialog,
    QLineEdit, QFormLayout, QComboBox, QGroupBox
)
from PyQt6.QtGui import QCloseEvent

from app.config import ARCHIVE_DIR, DATA_DIR, PACKAGES_DIR, ensure_dirs
from app.core import domain
from app.db.database import SessionLocal, init_db
from app.db.models import ArchiveDocument, Direction, Project, Document
from app.ai.connector import AIConnector, MODE_LABELS, MODE_ORDER
from app.core.services.storage_service import (
    ARCHIVE_CATEGORIES, ARCHIVE_CATEGORY_DEFAULT, StorageError, add_file_to_archive,
    calculate_hash, find_by_hash,
)
from app.core.services.exporter import export_package


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Единая система управления ИД")
        self.resize(1150, 750)

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

        # База данных и ИИ
        init_db()
        self.db = SessionLocal()
        self.ai = AIConnector(mode="LOCAL")

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
            "Формы документов", "ИИ-Агент", "Настройки",
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
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.projects_table = QTableWidget(0, 4)
        self.projects_table.setHorizontalHeaderLabels(["ID", "Направление", "Наименование", "Адрес"])
        self.projects_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.projects_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.projects_table)

        return page

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
        self.archive_table = QTableWidget(0, 5)
        self.archive_table.setHorizontalHeaderLabels(
            ["ID", "Категория", "Имя файла", "Связей", "SHA-256"]
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

        self.btn_run_ai = QPushButton("Запустить ИИ-анализ проекта")
        self.btn_run_ai.clicked.connect(self.run_ai_check)
        layout.addWidget(self.btn_run_ai)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        layout.addWidget(self.ai_output)

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
        layout.addWidget(group_storage)

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

    def load_archive_files(self):
        self.archive_table.setRowCount(0)
        for f in self.db.query(ArchiveDocument).all():
            row = self.archive_table.rowCount()
            self.archive_table.insertRow(row)
            self.archive_table.setItem(row, 0, QTableWidgetItem(str(f.id)))
            self.archive_table.setItem(row, 1, QTableWidgetItem(f.category or ""))
            self.archive_table.setItem(row, 2, QTableWidgetItem(f.original_name or ""))
            # ТЗ п.51: счётчик связей архивного документа.
            self.archive_table.setItem(row, 3, QTableWidgetItem(str(f.links_count)))
            current = f.current_version
            hash_text = (current.file_hash[:16] + "...") if current else ""
            self.archive_table.setItem(row, 4, QTableWidgetItem(hash_text))

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

        try:
            file_hash = calculate_hash(Path(file_path))
            existing = find_by_hash(self.db, file_hash)
            if existing:
                # ТЗ п.92: физическая копия не создаётся второй раз.
                QMessageBox.information(
                    self,
                    "Дубликат",
                    f"Такой файл уже есть в архиве (ID {existing.archive_document_id}, "
                    f"связей: {existing.archive_document.links_count}).\n"
                    f"Новая копия не создана — используйте существующий архивный документ.",
                )
                return

            category = self.archive_category_combo.currentText() or ARCHIVE_CATEGORY_DEFAULT
            archive_doc = add_file_to_archive(self.db, Path(file_path), project_id, category)
            self.load_archive_files()
            QMessageBox.information(
                self, "Успех",
                f"Файл загружен в архив (ID {archive_doc.id}).\n"
                f"Категория: {archive_doc.category}\n"
                f"Версий: {len(archive_doc.versions)}\n"
                f"Связей: {archive_doc.links_count}",
            )
        except StorageError as e:
            QMessageBox.critical(self, "Ошибка загрузки", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка загрузки", str(e))

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
        """ТЗ п.9: переключение режима ИИ и индикация состояния."""
        self.ai.mode = MODE_ORDER[index]
        self.refresh_ai_state()

    def refresh_ai_state(self):
        """Обновить индикацию и видимость чата ИИ (ТЗ п.9)."""
        color = self.ai.mode_color()
        text = f"ИИ: {self.ai.mode_label}"

        for label in (getattr(self, "ai_page_status", None), getattr(self, "ai_status_label", None)):
            if label is not None:
                label.setText(text)
                label.setStyleSheet(f"color: {color}; font-weight: bold;")

        if getattr(self, "ai_output", None) is not None:
            self.ai_output.setVisible(self.ai.enabled)
        if getattr(self, "btn_run_ai", None) is not None:
            self.btn_run_ai.setVisible(self.ai.enabled)
        if not self.ai.enabled and getattr(self, "ai_output", None) is not None:
            self.ai_output.clear()

    def add_project(self):
        title, ok1 = QInputDialog.getText(self, "Новый проект", "Введите наименование проекта:")
        if not ok1 or not title.strip():
            return

        # Направление выбирается из справочника (ТЗ п.14, 20), а не из
        # захардкоженного списка: справочник редактируется оператором.
        directions = self.db.query(Direction).order_by(Direction.sort_order).all()
        if not directions:
            QMessageBox.critical(
                self, "Справочник пуст",
                "Справочник направлений не заполнен. Перезапустите приложение.",
            )
            return
        names = [d.name for d in directions]
        direction, ok2 = QInputDialog.getItem(
            self, "Направление", "Выберите направление работ:", names, 0, False
        )
        if not ok2:
            return

        direction_row = self.db.query(Direction).filter(Direction.name == direction).one()
        proj = Project(
            title=title.strip(),
            direction_id=direction_row.id,
            address="Не указан",
        )
        self.db.add(proj)
        self.db.commit()
        self.load_projects()

    def delete_project(self):
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Удаление проекта", "Выберите проект.")
            return
        proj = self.db.get(Project, project_id)
        if proj is None:
            return

        # ТЗ п.86, 109: проект — корень истории. Удаление необратимо,
        # поэтому требуем явного подтверждения оператора.
        doc_count = self.db.query(Document).filter(Document.project_id == project_id).count()
        archive_count = (
            self.db.query(ArchiveDocument)
            .filter(ArchiveDocument.project_id == project_id)
            .count()
        )
        if archive_count:
            # Архивные файлы — доказательства; каскадного удаления нет.
            QMessageBox.warning(
                self, "Удаление невозможно",
                f"В проекте {archive_count} архивных документов.\n"
                "Сначала очистите архив проекта — файлы не удаляются "
                "автоматически вместе с проектом.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Удаление проекта",
            f"Удалить проект «{proj.title}»?\n"
            f"Вместе с ним будет удалено документов: {doc_count}.\n"
            "Действие необратимо.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.db.delete(proj)
        self.db.commit()
        self.load_projects()

    def export_project_pdf(self):
        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "Ошибка", "Выберите проект!")
            return
        target_dir = QFileDialog.getExistingDirectory(self, "Папка для выгрузки")
        if not target_dir: return
        try:
            out_path = export_package(self.db, project_id, target_dir)
            QMessageBox.information(self, "Успех", f"Пакет выгружен:\n{os.path.normpath(out_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def run_ai_check(self):
        if not self.ai.enabled:
            self.ai_output.clear()
            return

        project_id = self.selected_project_id()
        if project_id is None:
            QMessageBox.warning(self, "ИИ-анализ", "Выберите проект в разделе «Проекты».")
            return
        doc_ids = [
            d.id for d in
            self.db.query(Document).filter(Document.project_id == project_id).all()
        ]

        res = self.ai.analyze_package(doc_ids)
        out = (
            f"Результат анализа ИИ ({self.ai.mode_label})\n"
            f"Проект: ID {project_id}, документов: {len(doc_ids)}\n"
            f"Статус: {res.get('status')}\n\nПредложения:\n"
        )
        for p in res.get("proposals", []):
            if isinstance(p, dict):
                out += f"• [{p.get('code')}] {p.get('text')}\n"
            else:
                out += f"• {p}\n"
        self.ai_output.setText(out)

    def closeEvent(self, event: QCloseEvent):
        """Безопасно закрываем подключение к SQLite при выходе (важно для Windows)"""
        try:
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
