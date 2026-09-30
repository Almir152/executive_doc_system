"""Рабочее окно проекта. ТЗ п.16, 17, 21, 86.

Структура повторяет раздел ТЗ п.16: Акты испытаний, АОСР, АООК, АОУСИТО,
Связанные документы, Комплекты, История. Разделы создаются пустыми, если
документов ещё нет (ТЗ п.16).
"""

from datetime import date, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core import domain
from app.core.services import document_service, link_service
from app.core.services import project_service as service
from app.db.models import (
    ArchiveDocument, Organization, Package, SectionKind,
)

_DATA_ROLE = Qt.ItemDataRole.UserRole
_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable


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
        documents_box = QGroupBox("Документы (ТЗ п.16, 42, 43)")
        documents_layout = QVBoxLayout(documents_box)
        doc_buttons = QHBoxLayout()
        self.btn_add_document = QPushButton("+ Документ")
        self.btn_add_document.clicked.connect(self.add_document)
        doc_buttons.addWidget(self.btn_add_document)
        self.btn_edit_document = QPushButton("Изменить реквизиты")
        self.btn_edit_document.clicked.connect(self.edit_document)
        doc_buttons.addWidget(self.btn_edit_document)
        doc_buttons.addStretch()
        documents_layout.addLayout(doc_buttons)
        self.documents_table = QTableWidget(0, 5)
        self.documents_table.setHorizontalHeaderLabels(
            ["Тип", "Номер", "Дата", "Статус", "Выпуски"]
        )
        self.documents_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        documents_layout.addWidget(self.documents_table)
        self.documents_hint = QLabel(
            "Документов пока нет. Создайте первый документ проекта."
        )
        self.documents_hint.setWordWrap(True)
        documents_layout.addWidget(self.documents_hint)
        layout.addWidget(documents_box)

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

        self.materials_table = QTableWidget(0, 5)
        self.materials_table.setHorizontalHeaderLabels(
            ["Тип", "Наименование", "Ед.", "Кол-во", "Примечание"]
        )
        self.materials_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.materials_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        materials_layout.addWidget(self.materials_table)

        self.links_box = QGroupBox("Связанные документы выбранного документа")
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
        self._reload_materials()
        self._reload_links()

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
        documents = document_service.list_documents(self.db, project.id)
        self.documents_table.setRowCount(len(documents))
        self.documents_hint.setVisible(not documents)
        for row, document in enumerate(documents):
            issued = sum(1 for v in document.versions if v.issued_at is not None)
            values = [
                domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type),
                document.number or "",
                document.doc_date.strftime("%d.%m.%Y") if document.doc_date else "—",
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

    # -----------------------------------------------------------------
    # МАТЕРИАЛЫ И СВЯЗИ (ТЗ п.44, 45, 47, 48)
    # -----------------------------------------------------------------
    def _reload_materials(self) -> None:
        search = self.material_search.text() if hasattr(self, "material_search") else ""
        materials = link_service.list_materials(self.db, self.project_id, search)
        self.materials_table.setRowCount(len(materials))
        for row, material in enumerate(materials):
            values = [
                material.material_type.name if material.material_type else "—",
                material.name,
                material.unit or "—",
                _quantity_label(material.quantity),
                material.note or "—",
            ]
            for column, value in enumerate(values):
                self.materials_table.setItem(row, column, QTableWidgetItem(value))

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
            self.links_hint.setText(
                "Связи показываются для документа, выбранного в перечне выше."
            )
            return
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
        """Выбранный документ или None без показания сообщений."""
        row = self.documents_table.currentRow()
        documents = document_service.list_documents(self.db, self.project_id)
        if row < 0 or row >= len(documents):
            return None
        return documents[row]

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
            link = link_service.link_document_to_archive(
                self.db, document_id=document.id, **dialog.values()
            )
        except link_service.MaterialError as exc:
            QMessageBox.warning(self, "Связь не создана", str(exc))
            return
        service.record_event(
            self.db, self.project_id, "document_linked",
            f"К документу № {document.number} привязан файл "
            f"{link.archive_document.original_name if link.archive_document else '—'}",
            entity_type="document", entity_id=document.id,
        )
        self.db.commit()
        self._reload_links()
        self._load_summary(service.get_project(self.db, self.project_id))

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
        link_service.unlink_document_from_archive(self.db, link.id)
        self._reload_links()
        self._load_summary(service.get_project(self.db, self.project_id))

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

    def add_document(self) -> None:
        """Создать документ с предложенным номером (ТЗ п.42)."""
        labels = list(domain.DOC_TYPE_LABELS.values())
        choice, ok = QInputDialog.getItem(
            self, "Новый документ", "Вид документа:", labels, 0, False
        )
        if not ok:
            return
        doc_type = next(
            value for value, label in domain.DOC_TYPE_LABELS.items() if label == choice
        )
        dialog = DocumentDialog(self.db, self.project_id, doc_type=doc_type, parent=self)
        try:
            values = self._document_values(dialog)
        except (ValueError, document_service.DocumentNumberError) as exc:
            QMessageBox.warning(self, "Документ не создан", str(exc))
            return
        if values is None:
            return
        try:
            document = document_service.create_document(self.db, self.project_id, **values)
        except document_service.DocumentNumberError as exc:
            QMessageBox.warning(self, "Документ не создан", str(exc))
            return
        service.record_event(
            self.db, self.project_id, "document_created",
            f"Создан документ {domain.DOC_TYPE_LABELS[document.doc_type]} "
            f"№ {document.number}",
            entity_type="document", entity_id=document.id,
        )
        self.db.commit()
        self.reload()

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

        if number_changed:
            issued = any(v.issued_at is not None for v in document.versions)
            if issued:
                QMessageBox.warning(
                    self, "Номер не меняется",
                    "Номер выпущенного документа менять нельзя: он уже использован "
                    "в комплекте (ТЗ п.85).",
                )
                return
            document_service.validate_document_number(
                self.db, self.project_id, document.doc_type, values["number"]
            )
            document.number = values["number"]
        document.doc_date = values["doc_date"]

        service.record_event(
            self.db, self.project_id, "document_updated",
            f"Изменены реквизиты документа "
            f"{domain.DOC_TYPE_LABELS[document.doc_type]} № {document.number}",
            entity_type="document", entity_id=document.id,
        )
        try:
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 — причина показывается оператору
            self.db.rollback()
            QMessageBox.warning(self, "Не сохранено", f"Не удалось сохранить: {exc}")
            return
        self.reload()

    def _document_values(self, dialog: DocumentDialog) -> dict | None:
        """Реквизиты из диалога; None означает отказ оператора."""
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()

    def _selected_document(self):
        row = self.documents_table.currentRow()
        documents = document_service.list_documents(self.db, self.project_id)
        if row < 0 or row >= len(documents):
            QMessageBox.information(
                self, "Выберите документ", "Сначала выберите документ в перечне."
            )
            return None
        return documents[row]

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
