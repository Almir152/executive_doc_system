"""Диалог формирования комплекта. ТЗ п.69, 70, 73, 75, 81."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services import package_service


class PackageDialog(QDialog):
    """Оператор выбирает документы, тип выгрузки и место хранения (ТЗ п.69).

    Формирование выполняет вызывающий код: диалог только собирает решение
    оператора, поэтому проверка комплекта (ТЗ п.82) остаётся перед
    созданием папки выгрузки.
    """

    def __init__(self, db: Session, project_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.base_dir: Path | None = None
        self.setWindowTitle("Формирование комплекта (ТЗ п.69)")
        self.build()
        self._load_documents()

    def build(self) -> None:
        layout = QVBoxLayout(self)

        self.documents_box = QGroupBox("Документы комплекта (ТЗ п.69)")
        documents_layout = QVBoxLayout(self.documents_box)
        self.documents_list = QListWidget()
        documents_layout.addWidget(self.documents_list)
        buttons = QHBoxLayout()
        self.btn_all = QPushButton("Отметить все")
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        buttons.addWidget(self.btn_all)
        self.btn_none = QPushButton("Снять все")
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        buttons.addWidget(self.btn_none)
        buttons.addStretch()
        documents_layout.addLayout(buttons)
        layout.addWidget(self.documents_box)

        options = QGroupBox("Параметры выгрузки (ТЗ п.70, 75, 81)")
        options_layout = QVBoxLayout(options)

        variant_row = QHBoxLayout()
        variant_row.addWidget(QLabel("Тип выгрузки:"))
        self.variant_combo = QComboBox()
        for variant in domain.EXPORT_VARIANTS:
            self.variant_combo.addItem(domain.EXPORT_VARIANT_LABELS[variant], variant)
        variant_row.addWidget(self.variant_combo, stretch=1)
        options_layout.addLayout(variant_row)

        self.page_numbering_check = QCheckBox(
            "Единая сквозная нумерация страниц в PDF (ТЗ п.81)"
        )
        options_layout.addWidget(self.page_numbering_check)

        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("Название корневой папки:"))
        self.root_name_edit = QLineEdit(package_service.PACKAGE_ROOT_NAME)
        self.root_name_edit.setToolTip(
            "В выбранной папке создаётся корневая папка комплектов (ТЗ п.70)."
        )
        root_row.addWidget(self.root_name_edit)
        options_layout.addLayout(root_row)

        place_row = QHBoxLayout()
        self.place_label = QLabel("Папка комплектов не выбрана")
        self.place_label.setWordWrap(True)
        place_row.addWidget(self.place_label, stretch=1)
        self.btn_place = QPushButton("Выбрать папку")
        self.btn_place.clicked.connect(self.choose_place)
        place_row.addWidget(self.btn_place)
        options_layout.addLayout(place_row)
        layout.addWidget(options)

        self.hint_label = QLabel(
            "Каждая выгрузка создаётся отдельной папкой и не изменяет прежние "
            "(ТЗ п.71, 72)."
        )
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Сформировать комплект")
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    # -----------------------------------------------------------------
    # ДАННЫЕ
    # -----------------------------------------------------------------

    def _load_documents(self) -> None:
        self.documents_list.clear()
        for document in package_service.project_documents(self.db, self.project_id):
            label = domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)
            number = document.number or "без номера"
            state = (
                "выпущен" if document.status == domain.DOC_STATUS_ISSUED
                else "рабочая редакция"
            )
            item = QListWidgetItem(f"{label} № {number} — {state}")
            item.setData(Qt.ItemDataRole.UserRole, document.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.documents_list.addItem(item)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.documents_list.count()):
            self.documents_list.item(index).setCheckState(state)

    def choose_place(self) -> None:
        """Оператор выбирает папку для комплектов (ТЗ п.70)."""
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка для комплектов"
        )
        if not chosen:
            return
        self.base_dir = Path(chosen)
        self.place_label.setText(f"Комплекты будут созданы в: {self.base_dir}")

    # -----------------------------------------------------------------
    # РЕШЕНИЕ ОПЕРАТОРА
    # -----------------------------------------------------------------

    def selected_document_ids(self) -> list[int]:
        ids = []
        for index in range(self.documents_list.count()):
            item = self.documents_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def variant(self) -> str:
        return self.variant_combo.currentData()

    def root_name(self) -> str:
        return self.root_name_edit.text()

    def page_numbering(self) -> bool:
        return self.page_numbering_check.isChecked()

    def _on_accept(self) -> None:
        if not self.selected_document_ids():
            self.hint_label.setText(
                "Отметьте хотя бы один документ: комплект без документов "
                "выгружать нечего (ТЗ п.69)."
            )
            return
        if self.base_dir is None:
            self.hint_label.setText(
                "Выберите папку для комплектов (ТЗ п.70)."
            )
            return
        self.accept()
