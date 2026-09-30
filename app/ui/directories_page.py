"""Экран справочников: организации, представители, разделы, типы материалов.

ТЗ п.18–20, 22, 44. Справочники общие для всех проектов, поэтому вынесены
в отдельную страницу, а не в окно проекта.
"""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core.services import directory_service as service

# Справочник задаётся описанием: колонки, обязательность, редактор.
_ORG_FIELDS = [
    ("short_name", "Краткое наименование *"),
    ("ogrn", "ОГРН"),
    ("inn", "ИНН"),
    ("address", "Адрес"),
    ("phone", "Телефон"),
    ("fax", "Факс"),
    ("sro", "СРО"),
    ("nopriz", "НОПРИЗ"),
]

_REP_FIELDS = [
    ("position", "Должность *"),
    ("full_name", "Фамилия, имя *"),
    ("phone", "Телефон"),
    ("email", "Эл. почта"),
]


class OrganizationDialog(QDialog):
    """Реквизиты организации. ТЗ п.18."""

    TITLE = "Организация"

    def __init__(self, db, organization=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.organization = organization
        self.setWindowTitle(
            f"{self.TITLE}: правка" if organization else f"{self.TITLE}: новая"
        )
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.edits = {}
        for field, label in _ORG_FIELDS:
            edit = QLineEdit(getattr(organization, field) or "" if organization else "")
            form.addRow(label, edit)
            self.edits[field] = edit

        self.ogrn_note = QLabel(
            "Поле «полное наименование организации» по ТЗ п.18 отдельно не "
            "используется: заполняется краткое наименование."
        )
        self.ogrn_note.setWordWrap(True)
        form.addRow(self.ogrn_note)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {field: self.edits[field].text() for field, _ in _ORG_FIELDS}


class RepresentativeDialog(QDialog):
    """Представитель организации. ТЗ п.19."""

    def __init__(self, db, representative=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.representative = representative
        self.setWindowTitle(
            "Представитель: правка" if representative else "Представитель: новый"
        )
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.organization_combo = QComboBox()
        self.organization_combo.addItem("— выберите организацию —", None)
        for organization in service.list_organizations(db):
            self.organization_combo.addItem(organization.short_name, organization.id)
        if representative is not None:
            index = self.organization_combo.findData(representative.organization_id)
            if index >= 0:
                self.organization_combo.setCurrentIndex(index)
        form.addRow("Организация *", self.organization_combo)

        self.edits = {}
        for field, label in _REP_FIELDS:
            edit = QLineEdit(getattr(representative, field) or "" if representative else "")
            form.addRow(label, edit)
            self.edits[field] = edit

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        values = {field: self.edits[field].text() for field, _ in _REP_FIELDS}
        values["organization_id"] = self.organization_combo.currentData()
        return values


class SimpleCodeDialog(QDialog):
    """Справочник с кодом и наименованием: разделы, типы материалов."""

    def __init__(self, db, title, row=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title}: правка" if row else f"{title}: новая")
        self.setModal(True)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.code_edit = QLineEdit(getattr(row, "code", "") if row else "")
        self.name_edit = QLineEdit(getattr(row, "name", "") if row else "")
        form.addRow("Код *", self.code_edit)
        form.addRow("Наименование *", self.name_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {"code": self.code_edit.text(), "name": self.name_edit.text()}


class DirectoryPage(QWidget):
    """Четыре справочника на вкладках."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.build()
        self.reload()

    def build(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tab(
            "Организации (ТЗ п.18)",
            ["Наименование", "ИНН", "ОГРН", "Адрес", "Телефон", "СРО"],
            self.add_organization, self.edit_organization, self.delete_organization,
        ), "Организации")
        self.tabs.addTab(self._build_tab(
            "Представители (ТЗ п.19)",
            ["Организация", "Должность", "Фамилия, имя", "Телефон", "Эл. почта"],
            self.add_representative, self.edit_representative,
            self.delete_representative,
        ), "Представители")
        # Название вкладки уточнено намеренно: в рабочем окне проекта есть
        # «Разделы», и в приёмке «Справочники → Разделы» принимали за них.
        self.tabs.addTab(self._build_tab(
            "Виды разделов (ТЗ п.22)",
            ["Код", "Наименование"],
            self.add_section_kind, self.edit_section_kind, self.delete_section_kind,
        ), "Виды разделов")
        self.tabs.addTab(self._build_tab(
            "Типы материалов (ТЗ п.44)",
            ["Код", "Наименование"],
            self.add_material_type, self.edit_material_type, self.delete_material_type,
        ), "Типы материалов")
        layout.addWidget(self.tabs)

    def _build_tab(self, hint, columns, on_add, on_edit, on_delete):
        page = QWidget()
        layout = QVBoxLayout(page)

        label = QLabel(hint)
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QHBoxLayout()
        add = QPushButton("+ Добавить")
        add.clicked.connect(on_add)
        edit = QPushButton("Изменить")
        edit.clicked.connect(on_edit)
        delete = QPushButton("Удалить")
        delete.setStyleSheet("background-color: #ffdddd; color: #990000;")
        delete.clicked.connect(on_delete)
        for button in (add, edit, delete):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        if "Организации" in hint:
            search_row = QHBoxLayout()
            search_row.addWidget(QLabel("Поиск (ТЗ п.20):"))
            self.org_search = QLineEdit()
            self.org_search.setPlaceholderText("Наименование, ИНН или ОГРН")
            self.org_search.textChanged.connect(self.reload_organizations)
            search_row.addWidget(self.org_search, stretch=1)
            layout.addLayout(search_row)

        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.doubleClicked.connect(on_edit)
        layout.addWidget(table)

        if "Организации" in hint:
            self.org_table = table
        elif "Представители" in hint:
            self.rep_table = table
        elif "Виды разделов" in hint:
            self.kind_table = table
        else:
            self.material_type_table = table
        return page

    # -----------------------------------------------------------------
    # ДАННЫЕ
    # -----------------------------------------------------------------
    def reload(self) -> None:
        self.reload_organizations()
        self.reload_representatives()
        self.reload_section_kinds()
        self.reload_material_types()

    def reload_organizations(self) -> None:
        search = getattr(self, "org_search", None)
        rows = service.list_organizations(self.db, search.text() if search else "")
        self._fill(
            self.org_table,
            [
                [
                    o.short_name, o.inn or "—", o.ogrn or "—", o.address or "—",
                    o.phone or "—", o.sro or "—",
                ]
                for o in rows
            ],
        )

    def reload_representatives(self) -> None:
        rows = service.list_representatives(self.db)
        self._fill(self.rep_table, [
            [
                r.organization.short_name if r.organization else "—",
                r.position, r.full_name, r.phone or "—", r.email or "—",
            ]
            for r in rows
        ])

    def reload_section_kinds(self) -> None:
        self._fill(self.kind_table, [
            [k.code, k.name] for k in service.list_section_kinds(self.db)
        ])

    def reload_material_types(self) -> None:
        self._fill(self.material_type_table, [
            [t.code, t.name] for t in service.list_material_types(self.db)
        ])

    def _fill(self, table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))

    # -----------------------------------------------------------------
    # ДЕЙСТВИЯ: организации
    # -----------------------------------------------------------------
    def add_organization(self) -> None:
        self._edit_organization(OrganizationDialog(self.db, None, self))

    def edit_organization(self) -> None:
        organization = self._selected(
            self.org_table, service.list_organizations(self.db), "организацию"
        )
        if organization is not None:
            self._edit_organization(OrganizationDialog(self.db, organization, self))

    def _edit_organization(self, dialog: OrganizationDialog) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            service.save_organization(
                self.db,
                dialog.organization.id if dialog.organization else None,
                **dialog.values(),
            )
        except service.DirectoryError as exc:
            QMessageBox.warning(self, "Не сохранено", str(exc))
            return
        self.reload_organizations()

    def delete_organization(self) -> None:
        organization = self._selected(
            self.org_table, service.list_organizations(self.db), "организацию"
        )
        if organization is None:
            return
        self._delete_with_confirm(
            organization.short_name,
            lambda: service.delete_organization(self.db, organization.id),
        )

    # -----------------------------------------------------------------
    # ДЕЙСТВИЯ: представители
    # -----------------------------------------------------------------
    def add_representative(self) -> None:
        self._edit_representative(RepresentativeDialog(self.db, None, self))

    def edit_representative(self) -> None:
        representative = self._selected(
            self.rep_table, service.list_representatives(self.db), "представителя"
        )
        if representative is not None:
            self._edit_representative(
                RepresentativeDialog(self.db, representative, self)
            )

    def _edit_representative(self, dialog: RepresentativeDialog) -> None:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            service.save_representative(
                self.db,
                dialog.representative.id if dialog.representative else None,
                **dialog.values(),
            )
        except service.DirectoryError as exc:
            QMessageBox.warning(self, "Не сохранено", str(exc))
            return
        self.reload_representatives()

    def delete_representative(self) -> None:
        representative = self._selected(
            self.rep_table, service.list_representatives(self.db), "представителя"
        )
        if representative is None:
            return
        self._delete_with_confirm(
            f"{representative.organization.short_name} — {representative.full_name}",
            lambda: service.delete_representative(self.db, representative.id),
        )

    # -----------------------------------------------------------------
    # ДЕЙСТВИЯ: разделы и типы материалов
    # -----------------------------------------------------------------
    def add_section_kind(self) -> None:
        self._edit_code(
            "Вид раздела", None, service.save_section_kind,
            service.list_section_kinds(self.db), self.reload_section_kinds,
        )

    def edit_section_kind(self) -> None:
        kinds = service.list_section_kinds(self.db)
        kind = self._selected(self.kind_table, kinds, "вид раздела")
        if kind is not None:
            self._edit_code(
                "Вид раздела", kind, service.save_section_kind, kinds,
                self.reload_section_kinds,
            )

    def delete_section_kind(self) -> None:
        kinds = service.list_section_kinds(self.db)
        kind = self._selected(self.kind_table, kinds, "вид раздела")
        if kind is not None:
            self._delete_with_confirm(
                f"{kind.code} — {kind.name}",
                lambda: service.delete_section_kind(self.db, kind.id),
            )

    def add_material_type(self) -> None:
        self._edit_code(
            "Тип материала", None, service.save_material_type,
            service.list_material_types(self.db), self.reload_material_types,
        )

    def edit_material_type(self) -> None:
        types = service.list_material_types(self.db)
        material_type = self._selected(
            self.material_type_table, types, "тип материала"
        )
        if material_type is not None:
            self._edit_code(
                "Тип материала", material_type, service.save_material_type, types,
                self.reload_material_types,
            )

    def delete_material_type(self) -> None:
        types = service.list_material_types(self.db)
        material_type = self._selected(
            self.material_type_table, types, "тип материала"
        )
        if material_type is not None:
            self._delete_with_confirm(
                f"{material_type.code} — {material_type.name}",
                lambda: service.delete_material_type(self.db, material_type.id),
            )

    # -----------------------------------------------------------------
    # ОБЩЕЕ
    # -----------------------------------------------------------------
    def _edit_code(self, title, row, save, rows, reload_table) -> None:
        dialog = SimpleCodeDialog(self.db, title, row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save(
                self.db, getattr(row, "id", None), **dialog.values()
            )
        except service.DirectoryError as exc:
            QMessageBox.warning(self, "Не сохранено", str(exc))
            return
        reload_table()

    def _selected(self, table: QTableWidget, rows, subject: str):
        index = table.currentRow()
        if index < 0 or index >= len(rows):
            QMessageBox.information(
                self, f"Выберите {subject}", f"Сначала выберите строку: {subject}."
            )
            return None
        return rows[index]

    def _delete_with_confirm(self, name: str, action) -> None:
        answer = QMessageBox.question(
            self, "Удаление",
            f"Удалить «{name}»? Если запись используется в данных, отказ "
            "придёт с объяснением причины.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            action()
        except service.DirectoryError as exc:
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        self.reload()
