"""Загрузка файла в архив с реквизитами документа качества. ТЗ п.44–46."""

from datetime import date

from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QVBoxLayout,
)

from app.core import domain
from app.core.services import storage_service

# ТЗ п.46: срок действия спрашивается только у документов, где он обязателен.
VALIDITY_TYPES = domain.QUALITY_DOC_TYPES_WITH_VALIDITY


class ArchiveUploadDialog(QDialog):
    """Реквизиты загружаемого в архив файла.

    Вид документа качества и срок его действия вводятся оператором, а не
    выводятся из имени файла (ТЗ п.45, 46). Акты, к которым относится
    документ качества, выбираются отдельно при связывании: автоматического
    прикрепления ко всем актам материала нет (ТЗ п.45).
    """

    def __init__(self, db, file_name: str, category: str, parent=None):
        super().__init__(parent)
        self.db = db
        self._valid = False

        self.setWindowTitle("Загрузка в архив")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Файл: {file_name}"))

        layout.addWidget(QLabel("Категория архива (ТЗ п.50):"))
        self.category_combo = QComboBox()
        self.category_combo.addItems(list(domain.ARCHIVE_CATEGORIES))
        self.category_combo.setCurrentText(category)
        self.category_combo.currentTextChanged.connect(self._update_enabled)
        layout.addWidget(self.category_combo)

        layout.addWidget(QLabel("Вид документа качества (ТЗ п.45):"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("— не документ качества —", "")
        for quality_type in domain.QUALITY_DOC_TYPES:
            self.quality_combo.addItem(quality_type, quality_type)
        self.quality_combo.currentIndexChanged.connect(self._update_enabled)
        layout.addWidget(self.quality_combo)

        layout.addWidget(QLabel("Дата начала действия (ТЗ п.46):"))
        self.validity_from = QDateEdit()
        self.validity_from.setCalendarPopup(True)
        self.validity_from.setDisplayFormat("dd.MM.yyyy")
        self.validity_from.setDate(date(2024, 1, 1))
        layout.addWidget(self.validity_from)

        layout.addWidget(QLabel("Дата окончания действия (ТЗ п.46):"))
        self.validity_to = QDateEdit()
        self.validity_to.setCalendarPopup(True)
        self.validity_to.setDisplayFormat("dd.MM.yyyy")
        self.validity_to.setDate(date(2030, 1, 1))
        layout.addWidget(self.validity_to)

        layout.addWidget(QLabel("Номер документа:"))
        self.number_edit = QLineEdit()
        self.number_edit.setPlaceholderText("не обязательно")
        layout.addWidget(self.number_edit)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_enabled()

    # ------------------------------------------------------------------
    # Состояние полей
    # ------------------------------------------------------------------
    def _update_enabled(self):
        """Поля качества доступны только в части «Материалы и документы качества»."""
        is_materials = (
            self.category_combo.currentText() == domain.ARCHIVE_CATEGORY_MATERIALS
        )
        self.quality_combo.setEnabled(is_materials)
        needs_dates = (
            is_materials and self.quality_type() in VALIDITY_TYPES
        )
        self.validity_from.setEnabled(needs_dates)
        self.validity_to.setEnabled(needs_dates)
        if needs_dates:
            self.hint.setText(
                "Для сертификата и декларации срок действия обязателен "
                "(ТЗ п.46)."
            )
        elif is_materials:
            self.hint.setText(
                "Документ качества прикрепляется к конкретным актам при "
                "связывании, а не ко всем сразу (ТЗ п.45)."
            )
        else:
            self.hint.setText("")

    # ------------------------------------------------------------------
    # Значения для сервиса
    # ------------------------------------------------------------------
    def category(self) -> str:
        return self.category_combo.currentText()

    def quality_type(self) -> str:
        return self.quality_combo.currentData() or ""

    def validity_dates(self) -> tuple[date | None, date | None]:
        if not (self.validity_from.isEnabled() and self.validity_to.isEnabled()):
            return None, None
        from PyQt6.QtCore import QDate

        start = self.validity_from.date()
        end = self.validity_to.date()
        return (
            date(start.year(), start.month(), start.day())
            if start != QDate() else None,
            date(end.year(), end.month(), end.day())
            if end != QDate() else None,
        )

    def number(self) -> str | None:
        return self.number_edit.text().strip() or None

    def options(self) -> dict:
        """Параметры для storage_service.add_file_to_archive (ТЗ п.45, 46)."""
        start, end = self.validity_dates()
        return {
            "quality_type": self.quality_type() or None,
            "validity_from": start,
            "validity_to": end,
            "number": self.number(),
        }

    # ------------------------------------------------------------------
    def _on_accept(self):
        start, end = self.validity_dates()
        try:
            storage_service.check_quality_details(
                self.quality_type() or None, start, end, self.category()
            )
        except storage_service.StorageError as exc:
            self.hint.setText(str(exc))
            return
        self.accept()


def show_upload_dialog(db, file_name: str, category: str, parent=None) -> dict | None:
    """Показать диалог. Возвращает параметры загрузки или None при отмене."""
    dialog = ArchiveUploadDialog(db, file_name, category, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return {
        "category": dialog.category(),
        **dialog.options(),
    }
