from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QLabel, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox
)
from PyQt6.QtCore import Qt
from app.db.database import SessionLocal
from app.db.models import Project, Document
from app.ai.connector import AIConnector

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Единая система управления исполнительной документацией (ТЗ)")
        self.resize(1100, 700)
        self.db = SessionLocal()
        self.ai = AIConnector(mode="LOCAL")

        self.init_ui()
        self.load_projects()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

        # --- Левая панель (Навигация) ---
        left_panel = QVBoxLayout()
        left_label = QLabel("НАВИГАЦИЯ")
        left_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.nav_list = QListWidget()
        self.nav_list.addItems(["Проекты", "Архив файлов", "Нормативы", "ИИ-Агент", "Настройки"])
        self.nav_list.currentRowChanged.connect(self.on_nav_changed)

        left_panel.addWidget(left_label)
        left_panel.addWidget(self.nav_list)

        left_container = QWidget()
        left_container.setLayout(left_panel)
        left_container.setFixedWidth(200)

        # --- Правая панель (Рабочая область) ---
        right_panel = QVBoxLayout()
        self.title_label = QLabel("Рабочая область: Проекты")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        # Таблица проектов
        self.projects_table = QTableWidget()
        self.projects_table.setColumnCount(4)
        self.projects_table.setHorizontalHeaderLabels(["ID", "Направление", "Наименование", "Адрес"])
        self.projects_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # Панель ИИ
        self.ai_status_label = QLabel("Статус ИИ: Зелёный (Локальный ИИ активен)")
        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setFixedHeight(100)

        btn_ai_check = QPushButton("Запустить ИИ-проверку связей")
        btn_ai_check.clicked.connect(self.run_ai_check)

        right_panel.addWidget(self.title_label)
        right_panel.addWidget(self.projects_table)
        right_panel.addWidget(self.ai_status_label)
        right_panel.addWidget(btn_ai_check)
        right_panel.addWidget(self.ai_output)

        right_container = QWidget()
        right_container.setLayout(right_panel)

        # Компоновка главного окна
        main_layout.addWidget(left_container)
        main_layout.addWidget(right_container)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def load_projects(self):
        projects = self.db.query(Project).all()
        self.projects_table.setRowCount(len(projects))
        for row, p in enumerate(projects):
            self.projects_table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.projects_table.setItem(row, 1, QTableWidgetItem(p.direction))
            self.projects_table.setItem(row, 2, QTableWidgetItem(p.title))
            self.projects_table.setItem(row, 3, QTableWidgetItem(p.address or ""))

    def on_nav_changed(self, row):
        item_text = self.nav_list.item(row).text()
        self.title_label.setText(f"Рабочая область: {item_text}")

    def run_ai_check(self):
        res = self.ai.analyze_package([])
        self.ai_output.setText(f"Результат анализа ИИ:\n- Status: {res['status']}\n- Предложение: {res['proposals'][0]}")

    def closeEvent(self, event):
        self.db.close()
        event.accept()
