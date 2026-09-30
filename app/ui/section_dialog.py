"""Диалог раздела проектной документации. ТЗ п.20, 21, 22.

Один диалог служит для создания и для правки: набор реквизитов один и тот
же, поэтому два диалога разошлись бы по составу полей.
"""

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout,
)

from app.db.models import Organization, Representative, SectionKind
from app.ui.reference_picker import ReferencePicker


def kind_options(db) -> list[tuple[str, int]]:
    """Виды разделов из справочника (ТЗ п.22)."""
    return [
        (kind.name, kind.id)
        for kind in db.query(SectionKind).order_by(SectionKind.code).all()
    ]


def organization_options(db) -> list[tuple[str, int]]:
    """Организации из справочника (ТЗ п.18)."""
    return [
        (f"{org.short_name} (ИНН {org.inn})", org.id)
        for org in db.query(Organization).order_by(Organization.short_name).all()
    ]


def designer_options(db) -> list[tuple[str, int]]:
    """Проектировщики из справочника представителей (ТЗ п.19)."""
    return [
        (_rep_label(rep), rep.id)
        for rep in db.query(Representative).order_by(Representative.full_name).all()
    ]


def _rep_label(rep: Representative) -> str:
    name = (rep.full_name or "").strip() or (rep.short_name or "").strip()
    return name or f"Представитель №{rep.id}"


class SectionDialog(QDialog):
    """Создание или правка раздела (ТЗ п.21).

    Код раздела при правке не меняется: он участвует в именах папок
    комплекта (ТЗ п.70), и переименование потребовало бы пересборки уже
    выпущенного.
    """

    def __init__(self, db, section=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.section = section
        self.setWindowTitle("Раздел проектной документации" if section is None
                            else f"Раздел {section.code}")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.code = QLineEdit()
        if section is not None:
            self.code.setText(section.code)
            self.code.setReadOnly(True)
            self.code.setToolTip(
                "Код раздела не меняется: он участвует в именах папок комплекта "
                "(ТЗ п.70)."
            )
        form.addRow("Код раздела *", self.code)

        self.name = QLineEdit(section.name if section is not None else "")
        form.addRow("Наименование *", self.name)

        self.kind = ReferencePicker("— вид раздела не выбран —")
        self.kind.set_reference_items(kind_options(db))
        if section is not None:
            self.kind.set_current_data(section.kind_id)
        form.addRow("Вид раздела *", self.kind)

        self.organization = ReferencePicker("— организация не выбрана —")
        self.organization.set_reference_items(organization_options(db))
        if section is not None:
            self.organization.set_current_data(section.organization_id)
        form.addRow("Организация", self.organization)

        self.designer = ReferencePicker("— проектировщик не выбран —")
        self.designer.set_reference_items(designer_options(db))
        if section is not None:
            self.designer.set_current_data(section.designer_rep_id)
        form.addRow("Проектировщик", self.designer)

        self.sheets = QLineEdit(section.sheets if section is not None else "")
        form.addRow("Листы", self.sheets)

        self.required_details = QLineEdit(
            section.required_details if section is not None else ""
        )
        self.required_details.setToolTip(
            "Необходимые реквизиты раздела (ТЗ п.21)."
        )
        form.addRow("Необходимые реквизиты", self.required_details)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        """Реквизиты раздела из полей диалога."""
        return {
            "code": self.code.text().strip(),
            "name": self.name.text().strip(),
            "kind_id": self.kind.current_data(),
            "organization_id": self.organization.current_data(),
            "designer_rep_id": self.designer.current_data(),
            "sheets": self.sheets.text().strip(),
            "required_details": self.required_details.text().strip(),
        }
