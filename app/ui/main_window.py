import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QMessageBox, QInputDialog, QStackedWidget, QFileDialog
)
from PyQt6.QtCore import Qt

from app.db.database import SessionLocal, init_db
from app.db.models import Project, Document, ArchiveFile
from app.ai.connector import AIConnector
from app.core.services.exporter import export_package


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Единая система управления исполнительной документацией")
        self.resize(1100, 700)

        # База данных и ИИ
        init_db()
        self.db = SessionLocal()
        self.ai = AIConnector(mode="LOCAL")

        self.init_ui()
        self.seed_demo_data_if_empty()
        self.load_projects()
        self.load_archive_files()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # -------------------------------------------------------------
        # ЛЕВАЯ ПАНЕЛЬ (НАВИГАЦИЯ)
        # -------------------------------------------------------------
        nav_container = QWidget()
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(5, 5, 5, 5)

        nav_title = QLabel("НАВИГАЦИЯ")
        nav_title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 5px;")
        nav_layout.addWidget(nav_title)

        self.nav_list = QListWidget()
        self.nav_list.addItems([
            "Проекты",
            "Архив файлов",
            "Нормативы",
            "ИИ-Агент",
            "Настройки"
        ])
        self.nav_list.setFixedWidth(180)
        self.nav_list.setCurrentRow(0)
        nav_layout.addWidget(self.nav_list)

        main_layout.addWidget(nav_container)

        # -------------------------------------------------------------
        # ПРАВАЯ ПАНЕЛЬ (STACKED WIDGET ДЛЯ СМЕНЫ ВКЛАДОК)
        # -------------------------------------------------------------
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)

        self.title_label = QLabel("Рабочая область: Проекты")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        right_layout.addWidget(self.title_label)

        self.stack = QStackedWidget()

        # Страница 0: Проекты
        self.page_projects = self.create_projects_page()
        self.stack.addWidget(self.page_projects)

        # Страница 1: Архив файлов
        self.page_archive = self.create_archive_page()
        self.stack.addWidget(self.page_archive)

        # Страница 2: Нормативы
        self.page_norms = QLabel("Раздел 'Нормативы' (СП / ГОСТ / РД)\n\nЗдесь доступен справочник нормативных документов.")
        self.page_norms.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.page_norms)

        # Страница 3: ИИ-Агент
        self.page_ai = self.create_ai_page()
        self.stack.addWidget(self.page_ai)

        # Страница 4: Настройки
        self.page_settings = QLabel("Раздел 'Настройки'\n\nКонфигурация SQLite, хранилища и параметров экспорта.")
        self.page_settings.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.page_settings)

        right_layout.addWidget(self.stack)
        main_layout.addWidget(right_container, stretch=1)

        # Сигналы
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

        self.btn_delete_project = QPushButton("Удалить выбранный проект")
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

        label = QLabel("Реестр дедуплицированных файлов (SHA-256):")
        layout.addWidget(label)

        self.archive_table = QTableWidget(0, 4)
        self.archive_table.setHorizontalHeaderLabels(["ID", "Тип", "SHA-256 Hash", "Файл / Имя"])
        self.archive_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.archive_table)

        return page

    def create_ai_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        status_lbl = QLabel("Статус ИИ: Зелёный (Локальный ИИ активен)")
        status_lbl.setStyleSheet("color: green; font-weight: bold; font-size: 13px;")
        layout.addWidget(status_lbl)

        btn_run_ai = QPushButton("Запустить ИИ-проверку связей")
        btn_run_ai.clicked.connect(self.run_ai_check)
        layout.addWidget(btn_run_ai)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        layout.addWidget(self.ai_output)

        return page

    # -----------------------------------------------------------------
    # ЛОГИКА И СОБЫТИЯ
    # -----------------------------------------------------------------
    def on_nav_changed(self, row: int):
        self.stack.setCurrentIndex(row)
        item_text = self.nav_list.item(row).text()
        self.title_label.setText(f"Рабочая область: {item_text}")

    def seed_demo_data_if_empty(self):
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
        projects = self.db.query(Project).all()
        for p in projects:
            row = self.projects_table.rowCount()
            self.projects_table.insertRow(row)
            self.projects_table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.projects_table.setItem(row, 1, QTableWidgetItem(p.direction or ""))
            self.projects_table.setItem(row, 2, QTableWidgetItem(p.title or ""))
            self.projects_table.setItem(row, 3, QTableWidgetItem(p.address or ""))

    def load_archive_files(self):
        self.archive_table.setRowCount(0)
        files = self.db.query(ArchiveFile).all()
        for f in files:
            row = self.archive_table.rowCount()
            self.archive_table.insertRow(row)
            self.archive_table.setItem(row, 0, QTableWidgetItem(str(f.id)))
            self.archive_table.setItem(row, 1, QTableWidgetItem(f.doc_type or ""))
            self.archive_table.setItem(row, 2, QTableWidgetItem(f.file_hash[:16] + "..."))
            self.archive_table.setItem(row, 3, QTableWidgetItem(f.file_path or ""))

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
        if not selected:
            QMessageBox.warning(self, "Предупреждение", "Выберите проект в таблице для удаления!")
            return

        row = selected[0].row()
        project_id = int(self.projects_table.item(row, 0).text())
        project_name = self.projects_table.item(row, 2).text()

        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Вы уверены, что хотите удалить проект '{project_name}' (ID: {project_id})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if confirm == QMessageBox.StandardButton.Yes:
            proj = self.db.query(Project).get(project_id)
            if proj:
                self.db.delete(proj)
                self.db.commit()
                self.load_projects()
                QMessageBox.information(self, "Успех", "Проект успешно удален!")

    def export_project_pdf(self):
        selected = self.projects_table.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите проект для экспорта!")
            return

        row = selected[0].row()
        project_id = int(self.projects_table.item(row, 0).text())

        target_dir = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения выгрузки")
        if not target_dir:
            return

        try:
            out_path = export_package(self.db, project_id, target_dir)
            QMessageBox.information(self, "Готово", f"Пакет сформирован:\n{out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось экспортировать: {str(e)}")

    def run_ai_check(self):
        res = self.ai.analyze_package([1, 2, 3])
        output = f"Результат анализа ИИ:\nСтатус: {res.get('status', 'OK')}\n\nПредложения:\n"
        proposals = res.get("proposals", [])
        for prop in proposals:
            if isinstance(prop, dict):
                output += f"• [{prop.get('code', 'INFO')}] {prop.get('text', '')}\n"
            else:
                output += f"• {str(prop)}\n"
        self.ai_output.setText(output)
