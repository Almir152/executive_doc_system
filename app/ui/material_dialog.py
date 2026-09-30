"""Диалоги материала и связи документа с архивом. ТЗ п.44, 45, 47, 48."""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
    QLineEdit, QTextEdit, QVBoxLayout,
)

from app.core import domain
from app.core.services import directory_service


class MaterialDialog(QDialog):
    """Материал проекта (ТЗ п.44)."""

    def __init__(self, db, project_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.setWindowTitle("Материал проекта")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        for material_type in directory_service.list_material_types(db):
            self.type_combo.addItem(material_type.name, material_type.id)
        if self.type_combo.count() == 0:
            QLabel(
                "Справочник типов материалов пуст: заполните его на странице "
                "«Справочники» (ТЗ п.44)."
            )
        form.addRow("Тип *", self.type_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Например: труба стальная 57х3х5")
        form.addRow("Наименование *", self.name_edit)

        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("м, шт, тн")
        form.addRow("Единица измерения", self.unit_edit)

        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setRange(0, 1_000_000)
        self.quantity_spin.setDecimals(3)
        self.quantity_spin.setSpecialValueText("не указано")
        form.addRow("Количество", self.quantity_spin)

        self.note_edit = QTextEdit()
        self.note_edit.setMaximumHeight(70)
        form.addRow("Примечание", self.note_edit)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        quantity = self.quantity_spin.value()
        return {
            "name": self.name_edit.text(),
            "material_type_id": self.type_combo.currentData(),
            "unit": self.unit_edit.text(),
            "quantity": quantity if quantity > 0 else None,
            "note": self.note_edit.toPlainText(),
        }


class LinkDialog(QDialog):
    """Выбор архивного файла и роли связи (ТЗ п.45, 47, 48)."""

    def __init__(self, db, project_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.setWindowTitle("Связь с файлом архива")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.archive_combo = QComboBox()
        self.archive_combo.addItem("— выберите файл —", None)
        for archive_document in _archive_choices(db, project_id):
            self.archive_combo.addItem(
                f"{archive_document.original_name} — {archive_document.category}",
                archive_document.id,
            )
        form.addRow("Файл архива *", self.archive_combo)

        self.role_combo = QComboBox()
        for role in domain.LINK_ROLES:
            self.role_combo.addItem(role, role)
        form.addRow("Роль связи *", self.role_combo)

        self.pin_check = True
        note = QLabel(
            "Файл хранится в архиве один раз и не копируется для каждой связи "
            "(ТЗ п.49). При создании связи закрепляется текущая версия файла, "
            "чтобы новая редакция архива не изменила уже выданный комплект "
            "(ТЗ п.91)."
        )
        note.setWordWrap(True)
        form.addRow(note)

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "archive_document_id": self.archive_combo.currentData(),
            "link_role": self.role_combo.currentData(),
            "pin_version": self.pin_check,
        }


def _archive_choices(db, project_id):
    """Архивные документы проекта, у которых есть загруженный файл."""
    from sqlalchemy import select

    from app.db.models import ArchiveDocument, ArchiveFileVersion

    documents = list(
        db.scalars(
            select(ArchiveDocument)
            .where(ArchiveDocument.project_id == project_id)
            .order_by(ArchiveDocument.category, ArchiveDocument.original_name)
        ).all()
    )
    choices = []
    for document in documents:
        has_file = db.scalar(
            select(ArchiveFileVersion.id).where(
                ArchiveFileVersion.archive_document_id == document.id
            ).limit(1)
        )
        if has_file:
            choices.append(document)
    return choices


class MaterialActsDialog(QDialog):
    """Акты испытаний, в которых проверялся материал. ТЗ п.44, 45.

    Связь выбирает оператор поштучно: массовое прикрепление документа
    качества ко всем актам материала недопустимо (ТЗ п.45), поэтому и
    список, и отметка здесь явные.
    """

    def __init__(self, db, project_id, material, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.material = material

        self.setWindowTitle("Акты испытаний материала")
        layout = QVBoxLayout(self)

        self.hint = QLabel(
            f"Отметьте акты испытаний, в которых проверялся материал "
            f"«{material.name}». Документ качества прикрепляется к "
            "конкретному акту, а не ко всем сразу (ТЗ п.45)."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem

        from app.core.services import link_service

        already = {
            act.id for act in link_service.list_test_acts_of_material(db, material.id)
        }
        acts = link_service.project_test_acts(db, project_id)
        self.acts_list = QListWidget()
        for act in acts:
            date_text = act.doc_date.strftime("%d.%m.%Y") if act.doc_date else "дата не задана"
            item = QListWidgetItem(f"Акт испытаний № {act.number} ({date_text})")
            item.setData(Qt.ItemDataRole.UserRole, act.id)
            item.setFlags(
                item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable
                if act.id in already else item.flags()
            )
            item.setCheckState(
                Qt.CheckState.Checked if act.id in already
                else Qt.CheckState.Unchecked
            )
            if act.id in already:
                item.setText(item.text() + " — уже указан")
            self.acts_list.addItem(item)
        layout.addWidget(self.acts_list)

        if not acts:
            self.hint.setText(
                "В проекте нет актов испытаний. Создайте акт испытаний "
                "(ТЗ п.36) и свяжите с ним материал."
            )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_acts(self) -> list[int]:
        """Акты, отмеченные оператором."""
        from PyQt6.QtCore import Qt

        return [
            self.acts_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.acts_list.count())
            if self.acts_list.item(row).checkState() == Qt.CheckState.Checked
        ]
