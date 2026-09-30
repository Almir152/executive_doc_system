"""Диалог создания проекта. ТЗ п.14, 17, 20.

Направление выбирается из справочника, адрес необязателен (ТЗ п.17), но поле
всё равно показано: молчаливая подстановка «Не указан» выдавала отсутствие
данных за введённое значение, и оператор искал потерянный адрес, которого
никто не вводил.
"""

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from app.ui.reference_picker import ReferencePicker


def direction_options(db) -> list[tuple[str, int]]:
    """Направления из справочника (ТЗ п.14, 20)."""
    from app.db.models import Direction

    return [
        (direction.name, direction.id)
        for direction in db.query(Direction).order_by(
            Direction.sort_order, Direction.name
        ).all()
    ]


class ProjectCreateDialog(QDialog):
    """Создание проекта: наименование, направление, адрес (ТЗ п.17)."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый проект")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Например: Жилой дом по ул. Ленина, 12")
        form.addRow("Наименование *", self.title_edit)

        directions = direction_options(db)
        self.direction_picker = ReferencePicker("— выберите направление —")
        self.direction_picker.set_reference_items(directions)
        form.addRow("Направление *", self.direction_picker)

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("Можно оставить пустым и заполнить позже")
        form.addRow("Адрес", self.address_edit)

        layout.addLayout(form)

        hint = QLabel(
            "Звёздочкой отмечены обязательные поля. Остальные реквизиты "
            "карточки — заказчик, подрядчик, организации — заполняются в "
            "рабочем окне проекта кнопкой «Редактировать карточку» (ТЗ п.17)."
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
        """Данные для сервиса создания проекта."""
        return {
            "title": self.title_edit.text(),
            "direction_id": self.direction_picker.currentData(),
            "address": self.address_edit.text(),
        }
