"""Рабочее окно проекта. ТЗ п.16, 17, 21, 86.

Структура повторяет раздел ТЗ п.16: Акты испытаний, АОСР, АООК, АОУСИТО,
Связанные документы, Комплекты, История. Разделы создаются пустыми, если
документов ещё нет (ТЗ п.16).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core import domain
from app.core.services import project_service as service
from app.db.models import (
    ArchiveDocument, Document, Organization, Package, SectionKind,
)

_DATA_ROLE = Qt.ItemDataRole.UserRole
_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable


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

        self.customer_combo = QComboBox()
        self.contractor_combo = QComboBox()
        self.organizations_list = QListWidgetMulti()
        for combo, current in (
            (self.customer_combo, project.customer_org_id),
            (self.contractor_combo, project.general_contractor_org_id),
        ):
            combo.addItem("— не указан —", None)
            for org in db.query(Organization).order_by(Organization.short_name).all():
                combo.addItem(org.short_name, org.id)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        form.addRow("Заказчик", self.customer_combo)
        form.addRow("Генеральный подрядчик", self.contractor_combo)

        form.addRow("Необходимые организации", self.organizations_list)
        layout.addLayout(form)

        for org in project.organizations:
            self.organizations_list.addItem(org.short_name, org.id)

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
            "customer_org_id": self.customer_combo.currentData(),
            "general_contractor_org_id": self.contractor_combo.currentData(),
            "organization_ids": self.organizations_list.checked_ids(),
        }


class QListWidgetMulti(QWidget):
    """Отметка нескольких организаций (ТЗ п.17)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        layout.addWidget(self.list)

    def addItem(self, text: str, data=None):
        item = QListWidgetItem(text)
        item.setData(_DATA_ROLE, data)
        item.setFlags(item.flags() | _CHECKABLE)
        self.list.addItem(item)

    def checked_ids(self) -> list[int]:
        result = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                value = item.data(_DATA_ROLE)
                if value is not None:
                    result.append(value)
        return result


class ProjectWindow(QWidget):
    """Содержимое рабочего окна проекта."""

    def __init__(self, db, project_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
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
        sections_layout.addWidget(self.sections_table)
        layout.addWidget(self.sections_box)

        # ТЗ п.16: разделы документов создаются пустыми, если их ещё нет.
        documents_box = QGroupBox("Документы (ТЗ п.16)")
        documents_layout = QVBoxLayout(documents_box)
        self.documents_table = QTableWidget(0, 4)
        self.documents_table.setHorizontalHeaderLabels(
            ["Тип", "Номер", "Статус", "Выпуски"]
        )
        self.documents_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        documents_layout.addWidget(self.documents_table)
        self.documents_hint = QLabel(
            "Документов пока нет. Формы появятся после создания комплектов."
        )
        self.documents_hint.setWordWrap(True)
        documents_layout.addWidget(self.documents_hint)
        layout.addWidget(documents_box)

        summary_box = QGroupBox("Связанные документы, комплекты и история")
        summary_layout = QVBoxLayout(summary_box)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)

        self.history_table = QTableWidget(0, 3)
        self.history_table.setHorizontalHeaderLabels(["Дата", "Тип", "Событие"])
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        summary_layout.addWidget(self.history_table)
        layout.addWidget(summary_box)

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
        self._load_history(project)

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

    def _load_documents(self, project) -> None:
        documents = (
            self.db.query(Document)
            .filter(Document.project_id == project.id)
            .order_by(Document.doc_type, Document.number)
            .all()
        )
        self.documents_table.setRowCount(len(documents))
        self.documents_hint.setVisible(not documents)
        for row, document in enumerate(documents):
            issued = sum(1 for v in document.versions if v.issued_at is not None)
            values = [
                domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type),
                document.number or "",
                _status_label(document.status),
                str(issued),
            ]
            for column, value in enumerate(values):
                self.documents_table.setItem(row, column, QTableWidgetItem(value))

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
                self.history_table.setItem(row, column, QTableWidgetItem(value))

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

    def add_section(self) -> None:
        kinds = self.db.query(SectionKind).order_by(SectionKind.code).all()
        if not kinds:
            QMessageBox.warning(
                self, "Справочник разделов пуст",
                "Сначала заполните справочник разделов (ТЗ п.22).",
            )
            return
        kind_names = [k.name for k in kinds]
        kind_name, ok = QInputDialog.getItem(
            self, "Новый раздел", "Вид раздела:", kind_names, 0, False
        )
        if not ok:
            return
        code, ok = QInputDialog.getText(
            self, "Новый раздел", "Код раздела (ТЗ п.21):"
        )
        if not ok:
            return
        name, ok = QInputDialog.getText(
            self, "Новый раздел", "Наименование раздела:"
        )
        if not ok:
            return
        kind = next(k for k in kinds if k.name == kind_name)
        try:
            service.add_section(
                self.db, self.project_id,
                kind_id=kind.id, code=code, name=name,
            )
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Раздел не добавлен", str(exc))
            return
        self._load_sections(service.get_project(self.db, self.project_id))

    def delete_section(self) -> None:
        row = self.sections_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление раздела", "Выберите раздел.")
            return
        item = self.sections_table.item(row, 0)
        section = next(
            (
                s for s in service.list_sections(self.db, self.project_id)
                if s.code == item.text()
            ),
            None,
        )
        if section is None:
            return
        try:
            service.delete_section(self.db, section.id)
        except service.ProjectError as exc:
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        self._load_sections(service.get_project(self.db, self.project_id))


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
