from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QLabel, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog
)
from PyQt6.QtCore import Qt
from app.db.database import SessionLocal
from app.db.models import Project, Document
from app.ai.connector import AIConnector

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Единая система управления исполнительной документацией (ТЗ)")
        self.resize(1150, 720)
        self.db = SessionLocal()
        self.ai = AIConnector(mode="LOCAL")

        self.init_ui()
        self.seed_demo_data_if_empty()
        self.load_projects()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout()

        # --- Левая панель (Навигация) ---
        left_panel = QVBoxLayout()
        left_label = QLabel("НАВИГАЦИЯ")
        left_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.nav_list = QListWidget()
        self.nav_list.addItems(["Проекты", "Архив файлов", "Нормативы", "ИИ-Агент", "Настройки"])
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self.on_nav_changed)

        left_panel.addWidget(left_label)
        left_panel.addWidget(self.nav_list)

        left_container = QWidget()
        left_container.setLayout(left_panel)
        left_container.setFixedWidth(210)

        # --- Правая панель (Рабочая область) ---
        right_panel = QVBoxLayout()
        self.title_label = QLabel("Рабочая область: Проекты")
        self.title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        
        # Кнопка создания проекта
        btn_layout = QHBoxLayout()
        self.btn_add_project = QPushButton("+ Создать проект")
        self.btn_add_project.setStyleSheet("padding: 6px 12px; font-weight: bold;")
        self.btn_add_project.clicked.connect(self.create_project_dialog)
        btn_layout.addWidget(self.btn_add_project)
        btn_layout.addStretch()

        # Таблица проектов
        self.projects_table = QTableWidget()
        self.projects_table.setColumnCount(4)
        self.projects_table.setHorizontalHeaderLabels(["ID", "Направление", "Наименование", "Адрес"])
        self.projects_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # Панель ИИ
        self.ai_status_label = QLabel("Статус ИИ: Зелёный (Локальный ИИ активен)")
        self.ai_status_label.setStyleSheet("color: green; font-weight: bold;")
        
        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setFixedHeight(90)

        btn_ai_check = QPushButton("Запустить ИИ-проверку связей")
        btn_ai_check.clicked.connect(self.run_ai_check)

        right_panel.addWidget(self.title_label)
        right_panel.addLayout(btn_layout)
        right_panel.addWidget(self.projects_table)
        right_panel.addWidget(self.ai_status_label)
        right_panel.addWidget(btn_ai_check)
        right_panel.addWidget(self.ai_output)

        right_container = QWidget()
        right_container.setLayout(right_panel)

        # Компоновка
        main_layout.addWidget(left_container)
        main_layout.addWidget(right_container)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def seed_demo_data_if_empty(self):
        """Создает начальный демо-проект, если база пустая"""
        if self.db.query(Project).count() == 0:
            demo_project = Project(
                direction="Общестроительные работы",
                title="Строительство корпуса МФТИ",
                address="г. Долгопрудный, ул. Первомайская"
            )
            self.db.add(demo_project)
            self.db.commit()

            aosr1 = Document(project_id=demo_project.id, doc_type="АОСР", number="1")
            aosr2 = Document(project_id=demo_project.id, doc_type="АОСР", number="2")
            self.db.add_all([aosr1, aosr2])
            self.db.commit()

    def load_projects(self):
        projects = self.db.query(Project).all()
        self.projects_table.setRowCount(len(projects))
        for row, p in enumerate(projects):
            self.projects_table.setItem(row, 0, QTableWidgetItem(str(p.id)))
            self.projects_table.setItem(row, 1, QTableWidgetItem(p.direction))
            self.projects_table.setItem(row, 2, QTableWidgetItem(p.title))
            self.projects_table.setItem(row, 3, QTableWidgetItem(p.address or ""))

    def create_project_dialog(self):
        title, ok = QInputDialog.getText(self, "Новый проект", "Введите наименование проекта:")
        if ok and title:
            direction, ok2 = QInputDialog.getItem(
                self, "Направление", "Выберите направление:",
                ["Общестроительные работы", "Внутренние инженерные сети", "Наружные инженерные сети"], 0, False
            )
            if ok2 and direction:
                new_p = Project(direction=direction, title=title, address="г. Москва")
                self.db.add(new_p)
                self.db.commit()
                self.load_projects()
                QMessageBox.information(self, "Успех", f"Проект '{title}' успешно создан!")

    def on_nav_changed(self, row):
        item_text = self.nav_list.item(row).text()
        self.title_label.setText(f"Рабочая область: {item_text}")

    def run_ai_check(self):
        res = self.ai.analyze_package([])
        self.ai_output.setText(f"Результат анализа ИИ:\n- Status: {res['status']}\n- Предложение: {res['proposals'][0]}")

    def closeEvent(self, event):
        self.db.close()
        event.accept()
