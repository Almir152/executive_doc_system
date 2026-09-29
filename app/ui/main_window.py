import os
import hashlib
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QMessageBox, QInputDialog, QStackedWidget, QFileDialog,
    QLineEdit, QFormLayout, QComboBox, QGroupBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent

from app.db.database import SessionLocal, init_db
from app.db.models import Project, Document, ArchiveFile
from app.ai.connector import AIConnector
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

        # Инициализация файловой системы (защита от крашей на Windows)
        self.base_dir = os.path.abspath("storage")
        self.archive_dir = os.path.join(self.base_dir, "internal_archive")
        self.export_dir = os.path.join(self.base_dir, "packages")
        os.makedirs(self.archive_dir, exist_ok=True)
        os.makedirs(self.export_dir, exist_ok=True)

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
        self.seed_demo_data_if_empty()
        self.load_projects()
        self.load_archive_files()
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
        self.nav_list.addItems(["Проекты", "Архив файлов (SHA-256)", "Нормативы", "ИИ-Агент", "Настройки"])
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

        btn_layout = QHBoxLayout()
        btn_upload = QPushButton("📥 Добавить файл (проверка дубликатов)")
        btn_upload.setStyleSheet("background-color: #e6f7ff;")
        btn_upload.clicked.connect(self.upload_to_archive)
        btn_layout.addWidget(btn_upload)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.archive_table = QTableWidget(0, 4)
        self.archive_table.setHorizontalHeaderLabels(["ID", "Тип", "SHA-256 Hash", "Путь к файлу"])
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

    def create_ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        status_lbl = QLabel("Статус ИИ: Активен (Готов к проверке связей)")
        status_lbl.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(status_lbl)

        btn_run_ai = QPushButton("Запустить ИИ-анализ проекта")
        btn_run_ai.clicked.connect(self.run_ai_check)
        layout.addWidget(btn_run_ai)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        layout.addWidget(self.ai_output)

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

        btn_clear_cache = QPushButton("Очистить кэш выгрузок")
        btn_clear_cache.clicked.connect(self.clear_cache)
        form_storage.addRow("Обслуживание:", btn_clear_cache)
        layout.addWidget(group_storage)

        group_ai = QGroupBox("Параметры ИИ")
        form_ai = QFormLayout(group_ai)
        self.ai_mode_combo = QComboBox()
        self.ai_mode_combo.addItems(["LOCAL (Автономно)", "API (Внешний сервер)"])
        form_ai.addRow("Режим работы:", self.ai_mode_combo)
        layout.addWidget(group_ai)
        layout.addStretch()

        return page

    # -----------------------------------------------------------------
    # ЛОГИКА
    # -----------------------------------------------------------------
    def on_nav_changed(self, row: int):
        self.stack.setCurrentIndex(row)
        self.title_label.setText(f"Рабочая область: {self.nav_list.item(row).text()}")

    def seed_demo_data_if_empty(self):
        """Создает точные демо-данные (важно для pytest)"""
        if self.db.query(Project).count() == 0:
            demo = Project(
                direction="Общестроительные работы",
                title="Строительство корпуса МФТИ",
                address="г. Долгопрудный, ул. Первомайская"
            )
            self.db.add(demo)
            self.db.commit()

    def load_projects(self):
        self.projects_table.setRowCount(0)
        for p in self.db.query(Project).all():
            row = self.projects_table.rowCount()
            self.projects_table.insertRow(row)
            self.projects_table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.projects_table.setItem(row, 1, QTableWidgetItem(p.direction or ""))
            self.projects_table.setItem(row, 2, QTableWidgetItem(p.title or ""))
            self.projects_table.setItem(row, 3, QTableWidgetItem(p.address or ""))

    def load_archive_files(self):
        self.archive_table.setRowCount(0)
        for f in self.db.query(ArchiveFile).all():
            row = self.archive_table.rowCount()
            self.archive_table.insertRow(row)
            self.archive_table.setItem(row, 0, QTableWidgetItem(str(f.id)))
            self.archive_table.setItem(row, 1, QTableWidgetItem(f.doc_type or ""))
            self.archive_table.setItem(row, 2, QTableWidgetItem(f.file_hash[:20] + "..."))
            
            display_path = os.path.normpath(f.file_path or "")
            self.archive_table.setItem(row, 3, QTableWidgetItem(display_path))

    def upload_to_archive(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Все файлы (*)")
        if not file_path:
            return

        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            file_hash = sha256.hexdigest()

            existing_file = self.db.query(ArchiveFile).filter_by(file_hash=file_hash).first()
            if existing_file:
                QMessageBox.warning(self, "Дедупликация сработала!", 
                    f"Точная копия этого файла уже есть в системе!\nID в архиве: {existing_file.id}")
                return

            ext = os.path.splitext(file_path)[1]
            safe_filename = f"{file_hash[:10]}{ext}"
            dest_path = os.path.join(self.archive_dir, safe_filename)
            shutil.copy2(file_path, dest_path)

            new_doc = ArchiveFile(file_hash=file_hash, file_path=dest_path, doc_type="Пользовательский файл")
            self.db.add(new_doc)
            self.db.commit()
            
            self.load_archive_files()
            QMessageBox.information(self, "Успех", "Файл успешно загружен и проиндексирован!")

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

    def clear_cache(self):
        if os.path.exists(self.export_dir):
            count = 0
            for f in os.listdir(self.export_dir):
                fp = os.path.join(self.export_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                    count += 1
            QMessageBox.information(self, "Кэш", f"Удалено файлов: {count}")

    def add_project(self):
        title, ok1 = QInputDialog.getText(self, "Новый проект", "Введите наименование проекта:")
        if not ok1 or not title.strip():
            return

        direction, ok2 = QInputDialog.getItem(
            self, "Направление", "Выберите направление работ:",
            ["Общестроительные работы", "Внутренние инженерные сети", "Наружные инженерные сети"], 0, False
        )
        if not ok2:
            return

        proj = Project(title=title.strip(), direction=direction, address="Не указан")
        self.db.add(proj)
        self.db.commit()
        self.load_projects()

    def delete_project(self):
        selected = self.projects_table.selectedItems()
        if not selected: return
        row = selected[0].row()
        project_id = int(self.projects_table.item(row, 0).text())
        proj = self.db.query(Project).get(project_id)
        if proj:
            self.db.delete(proj)
            self.db.commit()
            self.load_projects()

    def export_project_pdf(self):
        selected = self.projects_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите проект!")
            return
        row = selected[0].row()
        project_id = int(self.projects_table.item(row, 0).text())
        target_dir = QFileDialog.getExistingDirectory(self, "Папка для выгрузки")
        if not target_dir: return
        try:
            out_path = export_package(self.db, project_id, target_dir)
            QMessageBox.information(self, "Успех", f"Пакет выгружен:\n{os.path.normpath(out_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def run_ai_check(self):
        res = self.ai.analyze_package([1])
        out = f"Результат анализа ИИ:\nСтатус: {res.get('status')}\n\nПредложения:\n"
        for p in res.get("proposals", []):
            if isinstance(p, dict): out += f"• [{p.get('code')}] {p.get('text')}\n"
            else: out += f"• {p}\n"
        self.ai_output.setText(out)

    def closeEvent(self, event: QCloseEvent):
        """Безопасно закрываем подключение к SQLite при выходе (важно для Windows)"""
        try:
            self.db.close()
        except Exception:
            pass
        super().closeEvent(event)
