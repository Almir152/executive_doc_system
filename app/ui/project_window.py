"""Рабочее окно проекта. ТЗ п.16, 17, 21, 86.

Структура повторяет раздел ТЗ п.16: Акты испытаний, АОСР, АООК, АОУСИТО,
Связанные документы, Комплекты, История. Разделы создаются пустыми, если
документов ещё нет (ТЗ п.16).
"""

import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QFileDialog, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from app import settings
from app.core import domain, validators
from app.core.services import document_service, form_service, issue_service
from app.core.services import link_service, package_service, printing
from app.core.services import project_service as service
from app.db.models import (
    ArchiveDocument, Document, HistoryEvent, Organization, Package,
)
from app.ui.document_form import DocumentFormPanel, DocumentFormWindow
from app.ui.package_dialog import PackageDialog
from app.ui.reference_picker import ReferenceMultiPicker, ReferencePicker
from app.ui.section_dialog import SectionDialog, kind_options as db_kinds

log = logging.getLogger(__name__)

_DATA_ROLE = Qt.ItemDataRole.UserRole
_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable
# Идентификатор документа в дереве: None у строки вида (ТЗ п.16).
_DOCUMENT_ID_ROLE = Qt.ItemDataRole.UserRole + 1
# Пометка строки дерева: None у вида документа, «type» у вида, иначе часть проекта.
_TREE_GROUP_ROLE = Qt.ItemDataRole.UserRole + 2

# Части дерева проекта по ТЗ п.16.
_RELATED_LABEL = "Связанные документы"
_PACKAGES_LABEL = "Комплекты"
_HISTORY_LABEL = "История"


class DocumentDialog(QDialog):
    """Создание или правка реквизитов документа. ТЗ п.42, 43."""

    def __init__(self, db, project_id, doc_type=None, document=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.document = document
        if document is not None:
            self.setWindowTitle("Реквизиты документа")
            self.doc_type = document.doc_type
        else:
            self.setWindowTitle("Новый документ")
            self.doc_type = doc_type
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        for value, label in domain.DOC_TYPE_LABELS.items():
            self.type_combo.addItem(label, value)
        index = self.type_combo.findData(self.doc_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        self.type_combo.setToolTip(
            "Вид документа не меняется после создания"
            if document is not None else "Вид документа по ТЗ п.42"
        )
        self.type_combo.setEnabled(document is None)
        form.addRow("Вид документа", self.type_combo)

        if document is None:
            self.number_edit = QLineEdit(
                document_service.next_document_number(
                    db, project_id, self.doc_type
                )
            )
        else:
            self.number_edit = QLineEdit(document.number or "")
        self.number_edit.setToolTip(
            "Система предлагает номер, оператор может его изменить (ТЗ п.42)."
        )
        form.addRow("Номер *", self.number_edit)

        self.date_edit = QLineEdit(
            document.doc_date.strftime("%d.%m.%Y") if document and document.doc_date else ""
        )
        self.date_edit.setPlaceholderText("дд.мм.гггг")
        self.date_edit.setToolTip(
            "Дата вводится оператором и не изменяется системой (ТЗ п.43)."
        )
        form.addRow("Дата документа", self.date_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "doc_type": self.type_combo.currentData(),
            "number": self.number_edit.text(),
            "doc_date": parse_ru_date(self.date_edit.text()),
        }


class IssueDialog(QDialog):
    """Подтверждение выпуска с вводом даты. ТЗ п.43, 85.

    Дата не подставляется: система не имеет права назначать дату документа
    (ТЗ п.43), поэтому поле пустое, если оператор ещё не вводил её в
    реквизитах.
    """

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle("Выпуск документа")
        self.setModal(True)

        layout = QVBoxLayout(self)
        heading = QLabel(
            f"Документ: {document.type_label} № {document.number}"
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        note = QLabel(
            "После выпуска версия фиксируется и не изменяется (ТЗ п.85). "
            "Правки возможны только в новой редакции."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.date_edit = QLineEdit(
            document.doc_date.strftime("%d.%m.%Y") if document.doc_date else ""
        )
        self.date_edit.setPlaceholderText("дд.мм.гггг")
        self.date_edit.setToolTip(
            "Дата вводится оператором и не изменяется системой (ТЗ п.43)."
        )
        form.addRow("Дата документа *", self.date_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def doc_date(self) -> date:
        """Дата выпуска; пустое поле — ошибка, а не «сегодня» (ТЗ п.43)."""
        parsed = parse_ru_date(self.date_edit.text())
        if parsed is None:
            raise ValueError(
                "Укажите дату документа: она вводится оператором (ТЗ п.43)."
            )
        return parsed


def parse_ru_date(text: str) -> date | None:
    """Дата из поля ввода: дд.мм.гггг или пусто."""
    text = (text or "").strip()
    if not text:
        return None
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(
        f"Дата «{text}» не распознана. Ожидается формат дд.мм.гггг (ТЗ п.43)."
    )


class ProjectCardDialog(QDialog):
    """Правка статических данных карточки (ТЗ п.17)."""

    def __init__(self, db, project, parent=None):
        super().__init__(parent)
        self.db = db
        self.project = project
        self.setWindowTitle("Карточка проекта")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(project.title or "")
        self.address_edit = QLineEdit(project.address or "")
        form.addRow("Наименование *", self.title_edit)
        form.addRow("Адрес", self.address_edit)

        organizations = db.query(Organization).order_by(Organization.short_name).all()
        org_items = [(org.short_name, org.id) for org in organizations]
        self.customer_combo = ReferencePicker("— не указан —")
        self.contractor_combo = ReferencePicker("— не указан —")
        for picker, current in (
            (self.customer_combo, project.customer_org_id),
            (self.contractor_combo, project.general_contractor_org_id),
        ):
            picker.set_reference_items(org_items)
            picker.set_current_data(current)
        form.addRow("Заказчик", self.customer_combo)
        form.addRow("Генеральный подрядчик", self.contractor_combo)

        self.organizations_list = ReferenceMultiPicker()
        self.organizations_list.set_reference_items(org_items)
        form.addRow("Необходимые организации", self.organizations_list)
        layout.addLayout(form)

        self.organizations_list.set_selected_values(
            [org.id for org in project.organizations]
        )

        hint = QLabel(
            "Звёздочкой отмечены обязательные поля. Правка сохраняется "
            "отдельной кнопкой."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "title": self.title_edit.text(),
            "address": self.address_edit.text(),
            "customer_org_id": self.customer_combo.current_data(),
            "general_contractor_org_id": self.contractor_combo.current_data(),
            "organization_ids": self.organizations_list.selected_values(),
        }


class ProjectWindow(QWidget):
    """Содержимое рабочего окна проекта."""

    def __init__(self, db, project_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.form_panel = None
        # Родитель нужен, чтобы окно принадлежало главному и закрывалось
        # вместе с ним. Но дочерний виджет с родителем — не отдельное окно:
        # вызов show() показывал бы его внутри главного за центральным виджетом,
        # и оператор не видел ничего. Флаг Window делает его самостоятельным
        # окном, сохраняя родителя (ТЗ п.16).
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowTitle("Рабочее окно проекта")
        self.resize(1000, 700)
        self.build()
        self.reload()

    # -----------------------------------------------------------------
    # ПОСТРОЕНИЕ
    # -----------------------------------------------------------------
    def build(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        header.addWidget(self.title_label, stretch=1)
        self.btn_edit_card = QPushButton("Редактировать карточку")
        self.btn_edit_card.clicked.connect(self.edit_card)
        header.addWidget(self.btn_edit_card)
        layout.addLayout(header)

        self.card_label = QLabel()
        self.card_label.setWordWrap(True)
        self.card_label.setStyleSheet("color: #444;")
        layout.addWidget(self.card_label)

        self.sections_box = QGroupBox("Проектная документация (ТЗ п.21)")
        sections_layout = QVBoxLayout(self.sections_box)
        add_row = QHBoxLayout()
        self.btn_add_section = QPushButton("+ Раздел")
        self.btn_add_section.clicked.connect(self.add_section)
        add_row.addWidget(self.btn_add_section)
        self.btn_edit_section = QPushButton("Изменить раздел")
        self.btn_edit_section.clicked.connect(self.edit_section)
        add_row.addWidget(self.btn_edit_section)
        self.btn_delete_section = QPushButton("Удалить раздел")
        self.btn_delete_section.setStyleSheet(
            "background-color: #ffdddd; color: #990000;"
        )
        self.btn_delete_section.clicked.connect(self.delete_section)
        add_row.addWidget(self.btn_delete_section)
        add_row.addStretch()
        sections_layout.addLayout(add_row)

        self.sections_table = QTableWidget(0, 6)
        self.sections_table.setHorizontalHeaderLabels(
            ["Код", "Наименование", "Организация", "Проектировщик", "Листы", "Реквизиты"]
        )
        self.sections_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.sections_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        sections_layout.addWidget(self.sections_table)
        layout.addWidget(self.sections_box)

        # ТЗ п.16: виды документов показаны дерево; пустой вид создаётся сразу,
        # чтобы оператор мог начать работу до появления первого документа.
        self.documents_box = QGroupBox("Документы проекта (ТЗ п.16, 42, 43)")
        documents_layout = QVBoxLayout(self.documents_box)
        doc_buttons = QHBoxLayout()
        self.btn_add_document = QPushButton("+ Документ")
        self.btn_add_document.clicked.connect(self.add_document)
        doc_buttons.addWidget(self.btn_add_document)
        self.btn_edit_document = QPushButton("Изменить реквизиты")
        self.btn_edit_document.clicked.connect(self.edit_document)
        doc_buttons.addWidget(self.btn_edit_document)
        self.btn_open_form = QPushButton("Открыть форму (п.65)")
        self.btn_open_form.clicked.connect(self.open_form)
        doc_buttons.addWidget(self.btn_open_form)
        self.btn_close_form = QPushButton("Закрыть форму")
        self.btn_close_form.clicked.connect(self.close_form)
        self.btn_close_form.setEnabled(False)
        doc_buttons.addWidget(self.btn_close_form)
        # ТЗ п.85: выпуск фиксирует версию, новая редакция открывает
        # следующую. Обе операции доступны только для выбранного документа.
        self.btn_issue_document = QPushButton("Выпустить документ")
        self.btn_issue_document.clicked.connect(self.issue_document)
        self.btn_issue_document.setEnabled(False)
        doc_buttons.addWidget(self.btn_issue_document)
        self.btn_new_revision = QPushButton("Новая редакция")
        self.btn_new_revision.clicked.connect(self.start_revision)
        self.btn_new_revision.setEnabled(False)
        doc_buttons.addWidget(self.btn_new_revision)
        # ТЗ п.55–62: печатная форма собирается по нормативному описанию.
        self.btn_print_form = QPushButton("Печать формы")
        self.btn_print_form.clicked.connect(self.print_form)
        self.btn_print_form.setEnabled(False)
        doc_buttons.addWidget(self.btn_print_form)
        doc_buttons.addStretch()
        documents_layout.addLayout(doc_buttons)

        self.document_tree = QTreeWidget()
        self.document_tree.setHeaderLabels(
            ["Раздел / документ", "Номер", "Дата", "Статус", "Выпуски"]
        )
        self.document_tree.setSelectionBehavior(
            QTreeWidget.SelectionBehavior.SelectRows
        )
        self.document_tree.itemSelectionChanged.connect(self._on_tree_selection)

        # ТЗ п.16: Связанные документы, Комплекты и История — части дерева
        # проекта, а не отдельные экраны. Показываются как узлы с числом
        # записей; сами перечни остаются ниже. Узлы создаются при каждой
        # перерисовке: очистка дерева уничтожает прежние элементы.
        documents_layout.addWidget(self.document_tree)
        self.documents_hint = QLabel(
            "Пустые разделы созданы по ТЗ п.16. Создайте первый документ "
            "через «+ Документ»."
        )
        self.documents_hint.setWordWrap(True)
        documents_layout.addWidget(self.documents_hint)
        layout.addWidget(self.documents_box)

        # Рабочая область формы документа (ТЗ п.65).
        self.form_container = QWidget()
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_container.setVisible(False)

        # ТЗ п.65: форму можно развернуть и вернуть к прежнему размеру, а
        # режим открытия выбирается один раз для приложения.
        form_tools = QHBoxLayout()
        self.btn_expand_form = QPushButton("Развернуть форму")
        self.btn_expand_form.clicked.connect(self.toggle_form_expanded)
        form_tools.addWidget(self.btn_expand_form)
        self.form_mode_combo = QComboBox()
        for mode in settings.FORM_MODES:
            self.form_mode_combo.addItem(settings.FORM_MODE_LABELS[mode], mode)
        self.form_mode_combo.setCurrentIndex(
            self.form_mode_combo.findData(settings.form_open_mode())
        )
        self.form_mode_combo.currentIndexChanged.connect(self._save_form_mode)
        self.form_mode_combo.setToolTip(
            "Режим открытия формы (ТЗ п.65): внутри рабочей области или отдельно."
        )
        form_tools.addWidget(QLabel("Открывать форму:"))
        form_tools.addWidget(self.form_mode_combo)
        form_tools.addStretch()
        self.form_layout.addLayout(form_tools)
        layout.addWidget(self.form_container)
        self._form_expanded = False
        self._hidden_while_expanded: list[QWidget] = []
        self.separate_form_window: DocumentFormWindow | None = None

        self.materials_box = QGroupBox(
            "Материалы (ТЗ п.44) и связи документов (ТЗ п.45, 47, 48)"
        )
        materials_layout = QVBoxLayout(self.materials_box)
        material_buttons = QHBoxLayout()
        self.btn_add_material = QPushButton("+ Материал")
        self.btn_add_material.clicked.connect(self.add_material)
        material_buttons.addWidget(self.btn_add_material)
        self.btn_delete_material = QPushButton("Удалить материал")
        self.btn_delete_material.setStyleSheet(
            "background-color: #ffdddd; color: #990000;"
        )
        self.btn_delete_material.clicked.connect(self.delete_material)
        material_buttons.addWidget(self.btn_delete_material)
        self.material_search = QLineEdit()
        self.material_search.setPlaceholderText("Поиск материала (ТЗ п.44)")
        self.material_search.textChanged.connect(self._reload_materials)
        material_buttons.addWidget(self.material_search, stretch=1)
        materials_layout.addLayout(material_buttons)

        # ТЗ п.44, 45: материал относится к конкретным актам испытаний.
        self.btn_material_acts = QPushButton("+ Акты испытаний материала")
        self.btn_material_acts.clicked.connect(self.add_material_test_acts)
        material_buttons.addWidget(self.btn_material_acts)
        self.btn_material_acts_remove = QPushButton("Убрать связь с актом")
        self.btn_material_acts_remove.setStyleSheet(
            "background-color: #ffdddd; color: #990000;"
        )
        self.btn_material_acts_remove.clicked.connect(self.delete_material_test_act)
        material_buttons.addWidget(self.btn_material_acts_remove)

        self.materials_table = QTableWidget(0, 6)
        self.materials_table.setHorizontalHeaderLabels(
            ["Тип", "Наименование", "Ед.", "Кол-во", "Примечание", "Актов"]
        )
        self.materials_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.materials_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.materials_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        # Выбор материала должен сразу обновлять таблицу его актов: иначе
        # «Убрать связь с актом» работает по актам предыдущего материала.
        self.materials_table.itemSelectionChanged.connect(self._reload_material_acts)
        materials_layout.addWidget(self.materials_table)

        self.links_box = QGroupBox("Связанные документы выбранного документа")
        self.material_acts_hint = QLabel(
            "Сначала выберите строку с материалом (ТЗ п.44)."
        )
        self.material_acts_hint.setWordWrap(True)
        materials_layout.addWidget(self.material_acts_hint)
        self.material_acts_table = QTableWidget(0, 2)
        self.material_acts_table.setHorizontalHeaderLabels(["Акт испытаний", "Дата"])
        self.material_acts_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        materials_layout.addWidget(self.material_acts_table)

        links_layout = QVBoxLayout(self.links_box)
        link_buttons = QHBoxLayout()
        self.btn_link = QPushButton("+ Связь с файлом архива")
        self.btn_link.clicked.connect(self.add_link)
        link_buttons.addWidget(self.btn_link)
        self.btn_unlink = QPushButton("Удалить связь")
        self.btn_unlink.setStyleSheet("background-color: #ffdddd; color: #990000;")
        self.btn_unlink.clicked.connect(self.delete_link)
        link_buttons.addWidget(self.btn_unlink)
        link_buttons.addStretch()
        links_layout.addLayout(link_buttons)

        # ТЗ п.87: итоговый акт ссылается на акты, которые он завершает.
        # По этой связи проверяются логические зависимости дат.
        self.btn_finalize = QPushButton("+ Акты, которые завершает документ")
        self.btn_finalize.clicked.connect(self.add_finalized_acts)
        link_buttons.addWidget(self.btn_finalize)
        self.btn_unfinalize = QPushButton("Убрать связь актов")
        self.btn_unfinalize.setStyleSheet("background-color: #ffdddd; color: #990000;")
        self.btn_unfinalize.clicked.connect(self.delete_finalized_act)
        link_buttons.addWidget(self.btn_unfinalize)

        self.finalized_table = QTableWidget(0, 3)
        self.finalized_table.setHorizontalHeaderLabels(
            ["АОСР", "Период работ", "Проверка дат"]
        )
        self.finalized_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        links_layout.addWidget(self.finalized_table)

        self.links_table = QTableWidget(0, 4)
        self.links_table.setHorizontalHeaderLabels(
            ["Файл", "Категория", "Роль", "Версия"]
        )
        self.links_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        links_layout.addWidget(self.links_table)
        self.links_hint = QLabel(
            "Связи показываются для документа, выбранного выше."
        )
        self.links_hint.setWordWrap(True)
        links_layout.addWidget(self.links_hint)
        layout.addWidget(self.materials_box)
        layout.addWidget(self.links_box)

        self.summary_box = QGroupBox("Связанные документы, комплекты и история")
        summary_layout = QVBoxLayout(self.summary_box)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)

        self.history_table = QTableWidget(0, 3)
        self.history_table.setHorizontalHeaderLabels(["Дата", "Тип", "Событие"])
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.history_table.itemSelectionChanged.connect(self.show_history_details)
        summary_layout.addWidget(self.history_table)

        # ТЗ п.86: по событию комплекта видно, что именно было выгружено.
        self.history_details = QTextEdit()
        self.history_details.setReadOnly(True)
        self.history_details.setMaximumHeight(120)
        self.history_details.setVisible(False)
        summary_layout.addWidget(self.history_details)

        # ТЗ п.16: комплекты проекта видны рядом с документами, отдельный
        # раздел «Реестры» в системе отсутствует (ТЗ п.68).
        self.packages_box = QGroupBox("Комплекты проекта (ТЗ п.16, 69, 70)")
        packages_layout = QVBoxLayout(self.packages_box)
        package_buttons = QHBoxLayout()
        self.btn_make_package = QPushButton("Сформировать комплект")
        self.btn_make_package.clicked.connect(self.make_package)
        package_buttons.addWidget(self.btn_make_package)
        self.btn_open_package = QPushButton("Открыть папку комплекта")
        self.btn_open_package.clicked.connect(self.open_package_folder)
        package_buttons.addWidget(self.btn_open_package)
        # Ведомость состава проекта — рабочий отчёт оператора по дереву
        # проекта (ТЗ п.16); печатные формы документов печатаются отдельно.
        self.btn_print_project = QPushButton("Печать состава проекта")
        self.btn_print_project.clicked.connect(self.print_project_report)
        package_buttons.addWidget(self.btn_print_project)
        package_buttons.addStretch()
        packages_layout.addLayout(package_buttons)

        self.packages_table = QTableWidget(0, 5)
        self.packages_table.setHorizontalHeaderLabels(
            ["Комплект", "Дата", "Вариант", "Нумерация", "Состояние"]
        )
        self.packages_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.packages_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        packages_layout.addWidget(self.packages_table)
        self.packages_hint = QLabel()
        self.packages_hint.setWordWrap(True)
        packages_layout.addWidget(self.packages_hint)
        layout.addWidget(self.packages_box)
        layout.addWidget(self.summary_box)

    # -----------------------------------------------------------------
    # ДАННЫЕ
    # -----------------------------------------------------------------
    def reload(self) -> None:
        project = service.get_project(self.db, self.project_id)
        if project is None:
            self.title_label.setText("Проект не найден")
            return
        self.title_label.setText(f"ПРОЕКТ: {project.title}")
        self.card_label.setText(self._card_text(project))
        self._load_sections(project)
        self._load_documents(project)
        self._load_summary(project)
        self._load_packages(project)
        self._load_history(project)
        self._reload_materials()
        self._reload_links()

    def _refresh_history(self) -> None:
        """Обновить таблицу истории после операции, писавшей событие (ТЗ п.86)."""
        project = service.get_project(self.db, self.project_id)
        if project is not None:
            self._load_history(project)

    def _reload_keeping_document(self) -> None:
        """Полная перерисовка окна с сохранением выбранного документа."""
        document = self._selected_document_quiet()
        self.reload()
        if document is not None:
            self._select_document(document.id)

    def _card_text(self, project) -> str:
        parts = [
            f"Направление: {project.direction.name if project.direction else '—'}",
            f"Адрес: {project.address or '—'}",
            f"Заказчик: {project.customer.short_name if project.customer else '—'}",
            "Генподрядчик: "
            + (project.general_contractor.short_name
               if project.general_contractor else "—"),
        ]
        if project.organizations:
            names = ", ".join(o.short_name for o in project.organizations)
            parts.append(f"Необходимые организации: {names}")
        return "   •   ".join(parts)

    def _load_sections(self, project) -> None:
        sections = service.list_sections(self.db, project.id)
        self.sections_table.setRowCount(0)
        for section in sections:
            row = self.sections_table.rowCount()
            self.sections_table.insertRow(row)
            values = [
                section.code,
                section.name,
                section.organization.short_name if section.organization else "—",
                _representative_name(section.designer) if section.designer else "—",
                section.sheets or "—",
                (section.required_details or "").strip() or "—",
            ]
            for column, value in enumerate(values):
                self.sections_table.setItem(row, column, QTableWidgetItem(value))

    def _add_tree_group(self, label: str, marker: str, count: int | None = None):
        """Узел дерева для части проекта, не являющейся видом документа."""
        item = QTreeWidgetItem([label, "" if count is None else str(count), "", "", ""])
        item.setData(0, _DOCUMENT_ID_ROLE, None)
        item.setData(0, _TREE_GROUP_ROLE, marker)
        self.document_tree.addTopLevelItem(item)
        item.setExpanded(True)
        return item

    def _load_documents(self, project) -> None:
        """Дерево документов по видам (ТЗ п.16).

        Пустой вид создаётся сразу: оператор должен иметь рабочий раздел
        ещё до того, как появился первый документ.
        """
        documents = document_service.list_documents(self.db, project.id)
        self.document_tree.clear()
        by_type: dict[str, list] = {t: [] for t in domain.NUMBERED_DOC_TYPES}
        for document in documents:
            by_type.setdefault(document.doc_type, []).append(document)

        for doc_type in domain.NUMBERED_DOC_TYPES:
            label = domain.DOC_TYPE_LABELS.get(doc_type, doc_type)
            group = self._add_tree_group(label, "type")
            for document in by_type.get(doc_type, []):
                issued = sum(1 for v in document.versions if v.issued_at is not None)
                item = QTreeWidgetItem([
                    f"{label} № {document.number}",
                    document.number or "",
                    document.doc_date.strftime("%d.%m.%Y")
                    if document.doc_date else "—",
                    _status_label(document.status),
                    str(issued),
                ])
                item.setData(0, _DOCUMENT_ID_ROLE, document.id)
                group.addChild(item)
            group.setExpanded(True)

        # Части проекта из ТЗ п.16: связаны, комплекты, история.
        self._add_tree_group(
            _RELATED_LABEL, "related",
            self._archive_documents_count(project.id),
        )
        self._add_tree_group(
            _PACKAGES_LABEL, "packages", self._packages_count(project.id)
        )
        self._add_tree_group(
            _HISTORY_LABEL, "history",
            service.history_count(self.db, project.id),
        )

        self.documents_hint.setVisible(not documents)
        self._update_document_buttons()

    def _archive_documents_count(self, project_id: int) -> int:
        return (
            self.db.query(ArchiveDocument)
            .filter(ArchiveDocument.project_id == project_id)
            .count()
        )

    def _packages_count(self, project_id: int) -> int:
        return (
            self.db.query(Package).filter(Package.project_id == project_id).count()
        )

    def _load_summary(self, project) -> None:
        archive_count = (
            self.db.query(ArchiveDocument)
            .filter(ArchiveDocument.project_id == project.id)
            .count()
        )
        package_count = (
            self.db.query(Package).filter(Package.project_id == project.id).count()
        )
        self.summary_label.setText(
            f"Связанные документы: {archive_count}   •   "
            f"Комплекты: {package_count}   •   "
            f"Представители: {len(project.representatives)}"
        )

    def _load_packages(self, project) -> None:
        """Комплекты проекта: новые сверху, состояние папки (ТЗ п.16, 73)."""
        packages = package_service.list_packages(self.db, project.id)
        self.packages_table.setRowCount(0)
        for package in packages:
            row = self.packages_table.rowCount()
            self.packages_table.insertRow(row)
            entries = package_service.package_entries(self.db, package.id)
            state = "папка на месте"
            if not package_service.package_exists_on_disk(package):
                # ТЗ п.73: папку могли удалить или переместить.
                state = "папка недоступна — выберите место заново"
            values = [
                package.folder_name,
                package.created_at.strftime("%d.%m.%Y %H:%M"),
                domain.EXPORT_VARIANT_LABELS.get(
                    package.export_variant, package.export_variant
                ),
                "сквозная" if package.page_numbering else "без нумерации",
                f"{state}, строк реестра: {len(entries)}",
            ]
            for column, value in enumerate(values):
                self.packages_table.setItem(row, column, QTableWidgetItem(value))
        self.packages_hint.setText(
            "Отдельного раздела «Реестры» нет: реестр создаётся как часть "
            "конкретной выгрузки (ТЗ п.68)."
        )

    def _selected_package(self):
        row = self.packages_table.currentRow()
        packages = package_service.list_packages(self.db, self.project_id)
        if row < 0 or row >= len(packages):
            return None
        return packages[row]

    def make_package(self) -> None:
        """Сформировать комплект из выбранных документов (ТЗ п.69)."""
        from app.core.services import export_checks

        dialog = PackageDialog(self.db, self.project_id, self)
        if dialog.exec() != PackageDialog.DialogCode.Accepted:
            return
        document_ids = dialog.selected_document_ids()

        # ТЗ п.82: проверка до создания папки выгрузки.
        result = export_checks.check_package(self.db, self.project_id, document_ids)
        if result.has_errors:
            answer = QMessageBox.question(
                self, "Проверка комплекта (ТЗ п.82)",
                "Обнаружены ошибки комплекта:\n\n"
                + "\n".join(f"• {problem}" for problem in result.problems[:20])
                + "\n\nСформировать комплект с файлом «Ошибки выгрузки.txt»?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            package = package_service.create_package(
                self.db, self.project_id,
                base_dir=dialog.base_dir,
                root_name=dialog.root_name(),
                document_ids=document_ids,
                variant=dialog.variant(),
                page_numbering=dialog.page_numbering(),
                allow_errors=result.has_errors,
            )
        except package_service.PackagePartialError as exc:
            # Комплект и запись в истории уже созданы: это не отказ, а
            # недогруженные файлы.
            self.reload()
            QMessageBox.warning(self, "Комплект создан частично", str(exc))
            return
        except (package_service.PackageError, printing.PrintError,
                domain.UnknownLinkRole) as exc:
            # ТЗ п.73: место хранения предлагается выбрать заново.
            QMessageBox.warning(self, "Комплект не сформирован", str(exc))
            return
        except OSError as exc:
            # Диск, права, длинные пути: сбой файловой операции не должен
            # закрывать окно без объяснения.
            QMessageBox.warning(
                self, "Комплект не сформирован",
                f"Ошибка файловой операции: {exc}\n\n"
                "Проверьте свободное место и права на папку комплектов.",
            )
            return
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Не удалось сформировать комплект")
            QMessageBox.critical(
                self, "Комплект не сформирован",
                f"{type(exc).__name__}: {exc}\n\n"
                "Подробности записаны в файл app.log рядом с базой.",
            )
            return
        self.reload()
        QMessageBox.information(
            self, "Комплект сформирован",
            f"{package.folder_name}\n{package.absolute_path}",
        )

    def open_package_folder(self) -> None:
        """Открыть папку комплекта; недоступная папка требует выбора заново."""
        package = self._selected_package()
        if package is None:
            QMessageBox.information(
                self, "Комплект", "Выберите комплект в списке (ТЗ п.16)."
            )
            return
        if not package_service.package_exists_on_disk(package):
            QMessageBox.warning(
                self, "Папка комплекта недоступна (ТЗ п.73)",
                f"Папка «{package.absolute_path}» удалена или перемещена.\n"
                "Выберите папку комплектов заново при следующей выгрузке.",
            )
            return
        _open_folder(package.absolute_path, self)

    def _load_history(self, project) -> None:
        events = service.list_events(self.db, project.id)
        self.history_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [
                _format_datetime(event.created_at),
                event.event_type,
                event.message or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, event.id)
                self.history_table.setItem(row, column, item)

    def show_history_details(self) -> None:
        """Показать состав комплекта по выбранному событию (ТЗ п.86)."""
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            self.history_details.setVisible(False)
            return
        item = self.history_table.item(rows[0].row(), 0)
        event = self.db.get(HistoryEvent, item.data(Qt.ItemDataRole.UserRole)) if item else None
        if event is None or event.event_type != domain.HISTORY_PACKAGE_EXPORTED:
            self.history_details.setVisible(False)
            return
        composition = (event.payload or {}).get("composition")
        if not composition:
            self.history_details.setVisible(False)
            return
        self.history_details.setPlainText(
            package_service.composition_text(composition)
        )
        self.history_details.setVisible(True)

    # -----------------------------------------------------------------
    # ДЕЙСТВИЯ
    # -----------------------------------------------------------------
    def edit_card(self) -> None:
        project = service.get_project(self.db, self.project_id)
        if project is None:
            return
        dialog = ProjectCardDialog(self.db, project, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            service.update_project_card(self.db, project.id, **dialog.values())
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Карточка не сохранена", str(exc))
            return
        self.reload()

    # -----------------------------------------------------------------
    # МАТЕРИАЛЫ И СВЯЗИ (ТЗ п.44, 45, 47, 48)
    # -----------------------------------------------------------------
    def _reload_materials(self) -> None:
        search = self.material_search.text() if hasattr(self, "material_search") else ""
        materials = link_service.list_materials(self.db, self.project_id, search)
        self.materials_table.setRowCount(len(materials))
        for row, material in enumerate(materials):
            acts = link_service.list_test_acts_of_material(self.db, material.id)
            values = [
                material.material_type.name if material.material_type else "—",
                material.name,
                material.unit or "—",
                _quantity_label(material.quantity),
                material.note or "—",
                str(len(acts)),
            ]
            for column, value in enumerate(values):
                self.materials_table.setItem(row, column, QTableWidgetItem(value))
        self._reload_material_acts()

    def _selected_material(self):
        """Материал, выбранный в таблице (ТЗ п.44)."""
        row = self.materials_table.currentRow()
        materials = link_service.list_materials(
            self.db, self.project_id, self.material_search.text()
        )
        if row < 0 or row >= len(materials):
            return None
        return materials[row]

    def add_material_test_acts(self) -> None:
        """Указать акты испытаний, в которых проверялся материал (ТЗ п.44, 45)."""
        from app.ui.material_dialog import MaterialActsDialog

        material = self._selected_material()
        if material is None:
            QMessageBox.information(
                self, "Выберите материал",
                "Сначала выберите строку с материалом (ТЗ п.44).",
            )
            return
        dialog = MaterialActsDialog(
            self.db, self.project_id, material, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        created = 0
        problem = None
        for document_id in dialog.selected_acts():
            try:
                link_service.link_material_to_test_act(
                    self.db, material.id, document_id
                )
            except link_service.MaterialError as exc:
                # break, а не return: уже созданные связи должны быть видны,
                # иначе счётчик актов и история останутся неверными.
                problem = str(exc)
                break
            created += 1
        self._reload_materials()
        self._refresh_history()
        if problem is not None:
            QMessageBox.warning(
                self, "Связь не создана",
                f"{problem}\n\nСоздано связей до отказа: {created}.",
            )
        elif created:
            QMessageBox.information(
                self, "Связи созданы",
                f"Актов испытаний указано: {created}. Документ качества "
                "прикрепляется к конкретному акту (ТЗ п.45).",
            )

    def delete_material_test_act(self) -> None:
        """Убрать связь материала с актом испытаний (ТЗ п.52)."""
        material = self._selected_material()
        if material is None:
            QMessageBox.information(
                self, "Выберите материал",
                "Сначала выберите строку с материалом (ТЗ п.44).",
            )
            return
        link = self.material_acts_table.currentRow()
        acts = link_service.list_test_acts_of_material(self.db, material.id)
        if link < 0 or link >= len(acts):
            QMessageBox.information(
                self, "Выберите связь",
                f"Выберите связь с актом испытаний в поле «Акты испытаний: "
                f"{material.name}» (ТЗ п.44).",
            )
            return
        answer = QMessageBox.question(
            self, "Удаление связи",
            f"Убрать материал «{material.name}» из акта испытаний "
            f"№ {acts[link].number}? Документы не удаляются (ТЗ п.52).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for item in material.test_act_links:
            if item.document_id == acts[link].id:
                try:
                    link_service.unlink_material_from_test_act(self.db, item.id)
                except link_service.MaterialError as exc:
                    QMessageBox.warning(self, "Связь не удалена", str(exc))
                    break
                break
        self._reload_materials()
        self._reload_material_acts()
        self._refresh_history()

    def _reload_material_acts(self) -> None:
        """Показать акты испытаний выбранного материала (ТЗ п.44)."""
        material = self._selected_material()
        if material is None:
            self.material_acts_table.setRowCount(0)
            self.material_acts_hint.setText(
                "Сначала выберите строку с материалом (ТЗ п.44)."
            )
            return
        acts = link_service.list_test_acts_of_material(self.db, material.id)
        self.material_acts_table.setRowCount(len(acts))
        for row, act in enumerate(acts):
            self.material_acts_table.setItem(row, 0, QTableWidgetItem(
                f"Акт испытаний № {act.number}"
            ))
            self.material_acts_table.setItem(row, 1, QTableWidgetItem(
                act.doc_date.strftime("%d.%m.%Y") if act.doc_date else "—"
            ))
        self.material_acts_hint.setText(
            f"Актов испытаний у материала «{material.name}»: {len(acts)}. "
            "Документ качества выбирается для конкретного акта (ТЗ п.45)."
        )

    def add_material(self) -> None:
        from app.ui.material_dialog import MaterialDialog

        dialog = MaterialDialog(self.db, self.project_id, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            link_service.save_material(self.db, self.project_id, **dialog.values())
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Материал не сохранён", str(exc))
            return
        self._reload_materials()

    def delete_material(self) -> None:
        row = self.materials_table.currentRow()
        materials = link_service.list_materials(
            self.db, self.project_id, self.material_search.text()
        )
        if row < 0 or row >= len(materials):
            QMessageBox.information(
                self, "Выберите материал", "Сначала выберите строку с материалом."
            )
            return
        material = materials[row]
        answer = QMessageBox.question(
            self, "Удаление материала",
            f"Удалить материал «{material.name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            link_service.delete_material(self.db, material.id)
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        self._reload_materials()

    def _reload_links(self) -> None:
        document = self._selected_document_quiet()
        if document is None:
            self.links_table.setRowCount(0)
            self.finalized_table.setRowCount(0)
            self.links_hint.setText(
                "Связи показываются для документа, выбранного в перечне выше."
            )
            return
        self._reload_finalized_acts(document)
        links = link_service.list_document_links(self.db, document.id)
        self.links_hint.setText(
            f"Связи документа "
            f"{domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)} "
            f"№ {document.number}: {len(links)}. "
            "Файл хранится в архиве один раз (ТЗ п.49)."
        )
        self.links_table.setRowCount(len(links))
        for row, link in enumerate(links):
            archive_document = link.archive_document
            values = [
                archive_document.original_name if archive_document else "—",
                archive_document.category if archive_document else "—",
                link.link_role,
                str(link.archive_version.version_no)
                if link.archive_version else "—",
            ]
            for column, value in enumerate(values):
                self.links_table.setItem(row, column, QTableWidgetItem(value))

    def _selected_document_quiet(self):
        """Документ, выбранный в дереве, или None без показания сообщений."""
        items = self.document_tree.selectedItems()
        if not items:
            return None
        document_id = items[0].data(0, _DOCUMENT_ID_ROLE)
        if document_id is None:
            return None
        return self.db.get(Document, document_id)

    def add_link(self) -> None:
        """Связать выбранный документ с файлом архива (ТЗ п.45, 47, 48)."""
        from app.ui.material_dialog import LinkDialog

        document = self._selected_document()
        if document is None:
            return
        dialog = LinkDialog(self.db, self.project_id, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            link_service.link_document_to_archive(
                self.db, document_id=document.id, **dialog.values()
            )
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Связь не создана", str(exc))
            return
        # Событие связи пишет сервис в своей транзакции (ТЗ п.86).
        self._reload_keeping_document()

    def delete_link(self) -> None:
        """Удалить связь, сохранив архивный документ (ТЗ п.52)."""
        row = self.links_table.currentRow()
        document = self._selected_document_quiet()
        if document is None or row < 0:
            QMessageBox.information(
                self, "Выберите связь", "Сначала выберите документ и связь в перечне."
            )
            return
        links = link_service.list_document_links(self.db, document.id)
        if row >= len(links):
            return
        link = links[row]
        name = link.archive_document.original_name if link.archive_document else "?"
        answer = QMessageBox.question(
            self, "Удаление связи",
            f"Удалить связь с файлом «{name}»? Файл останется в архиве (ТЗ п.52).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            link_service.unlink_document_from_archive(self.db, link.id)
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Связь не удалена", str(exc))
            self._reload_links()
            return
        self._reload_keeping_document()

    def _reload_finalized_acts(self, document) -> None:
        """Показать завершаемые акты и результат проверки дат (ТЗ п.87)."""
        relations = link_service.list_document_relations(self.db, document.id)
        problems = validators.check_document_dates(self.db, document)
        self.finalized_table.setRowCount(len(relations))
        for row, link in enumerate(relations):
            related = link.related_document
            start, end = validators.document_period(self.db, related)
            period = (
                f"{validators.format_date(start)} — {validators.format_date(end)}"
                if start or end else "срок не задан (ТЗ п.43)"
            )
            state = "не задан"
            if start and end:
                state = (
                    "есть нарушение"
                    if any(
                        f"АОСР № {related.number}" in text or "окончания" in text
                        for text in problems
                    ) else "порядок дат соблюдён"
                )
            for column, value in enumerate(
                (f"АОСР № {related.number}", period, state)
            ):
                self.finalized_table.setItem(row, column, QTableWidgetItem(value))
        if problems:
            self.finalized_table.setToolTip("; ".join(problems))
        else:
            self.finalized_table.setToolTip(
                "Логические зависимости дат не нарушены (ТЗ п.87)."
            )

    def add_finalized_acts(self) -> None:
        """Указать, какие АОСР завершает выбранный документ (ТЗ п.87)."""
        from app.ui.document_link_dialog import DocumentLinkDialog

        document = self._selected_document()
        if document is None:
            return
        dialog = DocumentLinkDialog(self.db, self.project_id, document, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        created = 0
        problem = None
        for related_id in dialog.selected_acts():
            try:
                link_service.link_documents(
                    self.db, related_document_id=related_id, **values
                )
            except link_service.MaterialError as exc:
                # partial: уже созданные связи не откатываются
                problem = str(exc)
                break
            created += 1
        self._reload_keeping_document()
        if problem is not None:
            QMessageBox.warning(
                self, "Связь не создана",
                f"{problem}\n\nСоздано связей до отказа: {created}.",
            )
        elif created:
            QMessageBox.information(
                self, "Связи созданы",
                f"Указано завершаемых актов: {created}. Проверка дат "
                "выполнена (ТЗ п.87).",
            )

    def delete_finalized_act(self) -> None:
        """Убрать связь «итоговый акт завершает АОСР» (ТЗ п.87)."""
        document = self._selected_document_quiet()
        row = self.finalized_table.currentRow()
        if document is None or row < 0:
            QMessageBox.information(
                self, "Выберите связь",
                "Сначала выберите документ и акт в списке завершаемых.",
            )
            return
        relations = link_service.list_document_relations(self.db, document.id)
        if row >= len(relations):
            return
        link = relations[row]
        answer = QMessageBox.question(
            self, "Удаление связи",
            f"Убрать связь с АОСР № {link.related_document.number}? "
            "Проверка дат будет выполнена без него (ТЗ п.87).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            link_service.unlink_documents(self.db, link.id)
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Связь не удалена", str(exc))
            self._reload_links()
            return
        self._reload_keeping_document()

    def add_section(self) -> None:
        """Создать раздел проектной документации (ТЗ п.21)."""
        if not db_kinds(self.db):
            QMessageBox.warning(
                self, "Справочник разделов пуст",
                "Сначала заполните справочник разделов (ТЗ п.22).",
            )
            return
        dialog = SectionDialog(self.db)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            service.add_section(
                self.db, self.project_id,
                kind_id=values["kind_id"], code=values["code"], name=values["name"],
                organization_id=values["organization_id"],
                designer_rep_id=values["designer_rep_id"],
                sheets=values["sheets"], required_details=values["required_details"],
            )
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Раздел не добавлен", str(exc))
            return
        self._load_sections(service.get_project(self.db, self.project_id))

    def edit_section(self) -> None:
        """Изменить реквизиты раздела (ТЗ п.21).

        `update_section()` существовал, но был доступен только из кода:
        оператор не мог исправить раздел, не редактируя базу.
        """
        section = self._selected_section()
        if section is None:
            return
        dialog = SectionDialog(self.db, section)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            service.update_section(
                self.db, section.id,
                name=values["name"], kind_id=values["kind_id"],
                organization_id=values["organization_id"],
                designer_rep_id=values["designer_rep_id"],
                sheets=values["sheets"], required_details=values["required_details"],
            )
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Раздел не изменён", str(exc))
            return
        self._load_sections(service.get_project(self.db, self.project_id))

    def _selected_section(self):
        """Раздел, выбранный в таблице разделов, с пояснением отказа."""
        row = self.sections_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Выберите раздел", "Сначала выберите раздел в таблице."
            )
            return None
        item = self.sections_table.item(row, 0)
        return next(
            (
                s for s in service.list_sections(self.db, self.project_id)
                if s.code == item.text()
            ),
            None,
        )

    def add_document(self) -> None:
        """Создать документ с предложенным номером (ТЗ п.42).

        Если в дереве выбран вид, документ создаётся сразу в нём; иначе вид
        уточняется. Пустой раздел вида в дереве — рабочий (ТЗ п.16).
        """
        doc_type = self._selected_doc_type()
        if doc_type is None:
            labels = list(domain.DOC_TYPE_LABELS.values())
            choice, ok = QInputDialog.getItem(
                self, "Новый документ", "Вид документа:", labels, 0, False
            )
            if not ok:
                return
            doc_type = next(
                value for value, label in domain.DOC_TYPE_LABELS.items()
                if label == choice
            )
        dialog = DocumentDialog(self.db, self.project_id, doc_type=doc_type, parent=self)
        try:
            values = self._document_values(dialog)
        except (ValueError, document_service.DocumentNumberError) as exc:
            QMessageBox.warning(self, "Документ не создан", str(exc))
            return
        if values is None:
            return
        if values["doc_type"] != doc_type:
            # Вид уточнён в диалоге: создаём в выбранном там виде.
            doc_type = values["doc_type"]
        try:
            document = document_service.create_document(
                self.db, self.project_id,
                doc_type=doc_type, number=values["number"],
                doc_date=values["doc_date"],
            )
        except document_service.DocumentNumberError as exc:
            QMessageBox.warning(self, "Документ не создан", str(exc))
            return
        # Событие «документ создан» пишет сам сервис в своей транзакции
        # (ТЗ п.86): иначе создание из другого окна осталось бы в истории
        # незаметным.
        self.reload()
        self._select_document(document.id)

    def _selected_doc_type(self) -> str | None:
        """Вид документа, выбранный в дереве (строка вида или документ)."""
        items = self.document_tree.selectedItems()
        if not items:
            return None
        item = items[0]
        if item.data(0, _DOCUMENT_ID_ROLE) is not None:
            document = self.db.get(Document, item.data(0, _DOCUMENT_ID_ROLE))
            return document.doc_type if document else None
        for doc_type, label in domain.DOC_TYPE_LABELS.items():
            if item.text(0) == label:
                return doc_type
        return None

    def _select_document(self, document_id: int) -> None:
        """Выделить документ в дереве после создания или правки."""
        root = self.document_tree.invisibleRootItem()
        for top_index in range(root.childCount()):
            top = root.child(top_index)
            for index in range(top.childCount()):
                child = top.child(index)
                if child.data(0, _DOCUMENT_ID_ROLE) == document_id:
                    self.document_tree.setCurrentItem(child)
                    return

    def edit_document(self) -> None:
        """Изменить номер и дату документа (ТЗ п.42, 43)."""
        document = self._selected_document()
        if document is None:
            return
        dialog = DocumentDialog(
            self.db, self.project_id, document=document, parent=self
        )
        try:
            values = self._document_values(dialog)
        except (ValueError, document_service.DocumentNumberError) as exc:
            QMessageBox.warning(self, "Не сохранено", str(exc))
            return
        if values is None:
            return

        number_changed = values["number"] != document.number
        date_changed = values["doc_date"] != document.doc_date
        if not number_changed and not date_changed:
            return

        try:
            # Правку и запись в историю выполняет сервис (ТЗ п.86).
            document_service.update_document_card(
                self.db, document,
                number=values["number"] if number_changed else None,
                doc_date=values["doc_date"],
                project_id=self.project_id,
            )
        except document_service.DocumentNumberError as exc:
            QMessageBox.warning(
                self, "Не сохранено",
                str(exc) if "выпущенного" in str(exc)
                else f"Не удалось сохранить: {exc}",
            )
            return
        self.reload()

    def _document_values(self, dialog: DocumentDialog) -> dict | None:
        """Реквизиты из диалога; None означает отказ оператора."""
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()

    def _selected_document(self):
        document = self._selected_document_quiet()
        if document is None:
            QMessageBox.information(
                self, "Выберите документ",
                "Сначала выберите документ в дереве видов (ТЗ п.16).",
            )
        return document

    def _on_tree_selection(self) -> None:
        """Выбор документа в дереве перерисовывает его связи."""
        self._reload_links()
        self._update_document_buttons()

    # -----------------------------------------------------------------
    # ВЫПУСК ДОКУМЕНТА (ТЗ п.85, 93)
    # -----------------------------------------------------------------
    def _update_document_buttons(self) -> None:
        """Разрешить выпуск рабочему документу, новую редакцию — выпущенному."""
        document = self._selected_document_quiet()
        issued = document is not None and document.status == domain.DOC_STATUS_ISSUED
        self.btn_issue_document.setEnabled(document is not None and not issued)
        self.btn_new_revision.setEnabled(issued)
        self.btn_print_form.setEnabled(document is not None)

    def print_form(self) -> None:
        """Напечатать форму документа в PDF по нормативному описанию (ТЗ п.55–62).

        Открывается системный просмотрщик PDF: оператор печатает на принтере
        теми настройками, которые приняты в его системе.
        """
        document = self._selected_document()
        if document is None:
            return
        safe_number = re.sub(r"[^\w\-]+", "_", document.number or "б_номера")
        suggested = Path.home() / f"{document.type_label}_{safe_number}.pdf"
        target, _ = QFileDialog.getSaveFileName(
            self, "Сохранить печатную форму", str(suggested), "PDF (*.pdf)"
        )
        if not target:
            return
        try:
            path = printing.render_document_pdf(
                self.db, document, target, record_history=True
            )
        except (printing.PrintError, domain.UnknownLinkRole) as exc:
            QMessageBox.warning(self, "Форма не напечатана", str(exc))
            return
        except OSError as exc:
            # Диск, права, длинные пути: PDF не создан, но окно должно жить.
            QMessageBox.warning(
                self, "Форма не напечатана",
                f"Ошибка файловой операции: {exc}\n\n"
                "Проверьте свободное место и права на выбранную папку.",
            )
            return
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Не удалось напечатать форму документа")
            QMessageBox.critical(
                self, "Форма не напечатана",
                f"{type(exc).__name__}: {exc}\n\n"
                "Подробности записаны в файл app.log рядом с базой.",
            )
            return
        # Печать пишет событие в историю (ТЗ п.86): обновляем таблицу.
        self._refresh_history()
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 — путь выбран оператором
            else:
                _open_pdf(path, self)
        except OSError as exc:
            QMessageBox.information(
                self, "Форма напечатана",
                f"Файл сохранён: {path}\nОткрыть автоматически не удалось: {exc}",
            )
            return
        QMessageBox.information(
            self, "Форма напечатана",
            f"Файл сохранён: {path} (ТЗ п.55–62).",
        )

    def print_project_report(self) -> None:
        """Напечатать ведомость состава проекта (ТЗ п.16)."""
        project = service.get_project(self.db, self.project_id)
        if project is None:
            return
        safe_title = re.sub(r"[^\w\-]+", "_", project.title or "проект")
        suggested = Path.home() / f"Состав_{safe_title}.pdf"
        target, _ = QFileDialog.getSaveFileName(
            self, "Сохранить ведомость состава проекта", str(suggested),
            "PDF (*.pdf)",
        )
        if not target:
            return
        try:
            path = printing.render_project_report(self.db, project, target)
        except printing.PrintError as exc:
            QMessageBox.warning(self, "Ведомость не напечатана", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(
                self, "Ведомость не напечатана",
                f"Ошибка файловой операции: {exc}\n\n"
                "Проверьте свободное место и права на выбранную папку.",
            )
            return
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Не удалось напечатать ведомость состава проекта")
            QMessageBox.critical(
                self, "Ведомость не напечатана",
                f"{type(exc).__name__}: {exc}\n\n"
                "Подробности записаны в файл app.log рядом с базой.",
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 — путь выбран оператором
            else:
                _open_pdf(path, self)
        except OSError as exc:
            QMessageBox.information(
                self, "Ведомость напечатана",
                f"Файл сохранён: {path}\nОткрыть автоматически не удалось: {exc}",
            )
            return
        QMessageBox.information(
            self, "Ведомость напечатана", f"Файл сохранён: {path} (ТЗ п.16).",
        )

    def issue_document(self) -> None:
        """Выпустить документ: зафиксировать версию (ТЗ п.85).

        Незаполненные обязательные поля (ТЗ п.96) показываются списком, и
        выпуск не происходит: зафиксировать неполный документ нельзя.
        """
        document = self._selected_document()
        if document is None:
            return
        dialog = IssueDialog(document, parent=self)
        # Диалог раньше не показывался: дата читалась из невидимого поля, и
        # документ без даты вообще нельзя было выпустить. Теперь оператор
        # подтверждает выпуск и при необходимости вводит дату (ТЗ п.43, 85).
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            doc_date = dialog.doc_date()
        except ValueError as exc:
            QMessageBox.warning(self, "Не выпущено", str(exc))
            return
        try:
            version = issue_service.issue_document(
                self.db, document.id, doc_date=doc_date
            )
        except issue_service.IssueError as exc:
            QMessageBox.warning(self, "Документ не выпущен", exc.report())
            return
        QMessageBox.information(
            self,
            "Документ выпущен",
            f"Зафиксирована версия {version.version_no} документа "
            f"«{document.type_label} № {document.number}» (ТЗ п.85). "
            "Изменить её нельзя; для правок начните новую редакцию.",
        )
        self.reload()
        self._select_document(document.id)

    def start_revision(self) -> None:
        """Начать новую редакцию выпущенного документа (ТЗ п.93)."""
        document = self._selected_document()
        if document is None:
            return
        if document.status != domain.DOC_STATUS_ISSUED:
            QMessageBox.information(
                self, "Редакция не требуется",
                f"«{document.type_label} № {document.number}» ещё не выпущен, "
                "документ и так рабочий (ТЗ п.85).",
            )
            return
        try:
            version = issue_service.start_revision(self.db, document.id)
        except issue_service.IssueError as exc:
            QMessageBox.warning(self, "Новая редакция не создана", exc.report())
            return
        self.reload()
        self._select_document(document.id)
        QMessageBox.information(
            self, "Новая редакция",
            f"Создана версия {version.version_no} для правки. "
            f"Выпущенная версия {version.version_no - 1} остаётся в истории "
            "без изменений (ТЗ п.93).",
        )

    def open_form(self) -> None:
        """Открыть форму выбранного документа (ТЗ п.65).

        Режим открытия — внутри рабочей области или отдельно — выбирается
        оператором один раз для приложения (ТЗ п.65).
        """
        document = self._selected_document()
        if document is None:
            return
        if document.status == domain.DOC_STATUS_ISSUED:
            QMessageBox.information(
                self, "Документ выпущен",
                f"«{document.type_label} № {document.number}» выпущен: его "
                "версия зафиксирована и не изменяется (ТЗ п.85). "
                "Чтобы продолжить работу, начните новую редакцию.",
            )
            return
        if self.form_panel is not None and self.form_panel.document_id != document.id:
            if not self._close_form_confirmed():
                return

        if settings.form_open_mode() == settings.FORM_MODE_SEPARATE:
            # Встроенная панель не должна остаться жить рядом с отдельным
            # окном: иначе по одному документу открыты две формы.
            self._destroy_form()
            self.form_container.setVisible(False)
            self.btn_close_form.setEnabled(False)
            self._open_form_separate(document)
            return

        self._destroy_form()
        self.form_panel = DocumentFormPanel(
            self.db, document.id, project_id=document.project_id
        )
        self.form_layout.addWidget(self.form_panel)
        self.form_container.setVisible(True)
        self.btn_close_form.setEnabled(True)

    def _open_form_separate(self, document) -> None:
        """Показать форму в отдельном окне (ТЗ п.65)."""
        self._close_separate_form()
        window = DocumentFormWindow(
            self.db, document.id, project_id=document.project_id
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Закрытие окна оператором уничтожает объект C++; ссылку нужно
        # обнулить, иначе следующее обращение к ней даст RuntimeError.
        window.destroyed.connect(self._on_separate_form_destroyed)
        window.show()
        self.separate_form_window = window

    def _on_separate_form_destroyed(self, obj=None) -> None:
        if obj is self.separate_form_window:
            self.separate_form_window = None

    def _close_separate_form(self) -> None:
        """Закрыть отдельное окно формы, если оно ещё существует."""
        window = self.separate_form_window
        if window is None:
            return
        if sip.isdeleted(window):
            self.separate_form_window = None
            return
        window.close()
        self.separate_form_window = None

    def _save_form_mode(self) -> None:
        """Сохранить выбранный режим открытия формы (ТЗ п.65)."""
        mode = self.form_mode_combo.currentData()
        if mode:
            settings.set_form_open_mode(mode)

    def toggle_form_expanded(self) -> None:
        """Развернуть форму или вернуть прежний размер (ТЗ п.65)."""
        if self.form_panel is None and self.separate_form_window is None:
            return
        if self._form_expanded:
            for widget in self._hidden_while_expanded:
                widget.setVisible(True)
            self._hidden_while_expanded = []
            self._form_expanded = False
            self.btn_expand_form.setText("Развернуть форму")
        else:
            self._hidden_while_expanded = [
                widget
                for widget in (
                    self.sections_box, self.documents_box, self.materials_box,
                    self.links_box, self.summary_box, self.packages_box,
                )
                if widget.isVisible()
            ]
            for widget in self._hidden_while_expanded:
                widget.setVisible(False)
            self._form_expanded = True
            self.btn_expand_form.setText("Восстановить размер")

    def _destroy_form(self) -> None:
        """Убрать панель формы, вернув скрытые при развороте блоки."""
        if self.form_panel is not None:
            self.form_panel.deleteLater()
            self.form_panel = None
        if self._form_expanded:
            for widget in self._hidden_while_expanded:
                widget.setVisible(True)
            self._hidden_while_expanded = []
            self._form_expanded = False
            self.btn_expand_form.setText("Развернуть форму")

    def close_form(self) -> None:
        """Закрыть форму; незавершённый ввод предлагается сохранить (ТЗ п.66)."""
        if not self._close_form_confirmed():
            return
        self._close_separate_form()
        self._destroy_form()
        self.form_container.setVisible(False)
        self.btn_close_form.setEnabled(False)

    def _close_form_confirmed(self) -> bool:
        """Спросить о несохранённом вводе; False означает отказ закрывать."""
        if self.form_panel is None:
            return True
        panel = self.form_panel
        if not form_service.has_unsaved_work(self.db, panel.document_id, panel.payload()):
            return True

        answer = QMessageBox.question(
            self,
            "Незавершённая форма (ТЗ п.66)",
            "Форма заполнена не полностью. Сохранить черновик перед закрытием?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            if not panel.save():
                return False
        return True

    def closeEvent(self, event) -> None:
        """Закрытие рабочего окна не должно молча терять ввод формы (ТЗ п.66)."""
        if not self._close_form_confirmed():
            event.ignore()
            return
        super().closeEvent(event)

    def delete_section(self) -> None:
        section = self._selected_section()
        if section is None:
            return
        try:
            service.delete_section(self.db, section.id)
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        self._load_sections(service.get_project(self.db, self.project_id))


def _open_folder(path: str, parent) -> None:
    """Открыть папку комплекта в файловом менеджере (ТЗ п.16, 70)."""
    folder = Path(path)
    try:
        if sys.platform == "win32":
            os.startfile(str(folder))  # noqa: S606 — папка выбрана оператором
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except (FileNotFoundError, OSError) as exc:
        QMessageBox.information(
            parent, "Комплект",
            f"Папка комплекта: {folder}\nОткрыть её автоматически не удалось: {exc}",
        )


def _open_pdf(path: Path, parent) -> None:
    """Открыть PDF системным просмотрщиком (ТЗ п.55).

    Ошибка открытия не должна выглядеть как ошибка печати: файл уже
    сформирован, оператору нужно лишь знать, что просмотрщик не запустился.
    """
    for command in (["xdg-open"], ["open"], ["sensible-browser"]):
        try:
            subprocess.Popen(command + [str(path)])
            return
        except (FileNotFoundError, OSError):
            continue
    QMessageBox.information(
        parent, "Файл сформирован",
        f"PDF сохранён: {path}. Открыть его автоматически не удалось.",
    )


def _quantity_label(quantity: float | None) -> str:
    """Количество материала без лишних нулей после запятой."""
    if quantity is None:
        return "—"
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:.3f}".rstrip("0").rstrip(".")


def _status_label(status: str | None) -> str:
    return {
        domain.DOC_STATUS_DRAFT: "Рабочий",
        domain.DOC_STATUS_ISSUED: "Выпущен",
    }.get(status, status or "—")


def _representative_name(representative) -> str:
    full_name = (representative.full_name or "").strip()
    return full_name or representative.short_name or "—"


def _format_datetime(value) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%d.%m.%Y %H:%M")
    except AttributeError:
        return str(value)
