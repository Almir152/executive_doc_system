"""Выбор актов, которые завершает итоговый документ. ТЗ п.87."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout,
)

from app.core import domain, validators
from app.core.services import link_service


class DocumentLinkDialog(QDialog):
    """Акты скрытых работ, завершаемые итоговым документом (ТЗ п.87).

    Связь выбирает оператор: без неё нельзя проверить, что срок итогового
    акта не противоречит срокам актов. Файл при этом не копируется —
    связь логическая (ТЗ п.47, 48).
    """

    def __init__(self, db, project_id: int, document, parent=None):
        super().__init__(parent)
        self.db = db
        self.project_id = project_id
        self.document = document

        self.setWindowTitle("Акты, завершаемые документом")
        layout = QVBoxLayout(self)

        already = {
            link.related_document_id
            for link in link_service.list_document_relations(db, document.id)
        }
        candidates = [
            item for item in link_service.project_acts_to_finalize(
                db, project_id, domain.DOC_TYPE_AOSR
            )
            if item.id not in already and item.id != document.id
        ]

        self.hint = QLabel(
            "Отметьте акты освидетельствования скрытых работ, которые "
            f"завершает {document.type_label} № {document.number}. По ним "
            "проверяются даты: окончание итогового акта не может быть раньше "
            "окончания связанного акта (ТЗ п.87)."
        )
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.acts_list = QListWidget()
        for item in candidates:
            start, end = validators.document_period(db, item)
            period = ""
            if start or end:
                period = f"  [{validators.format_date(start)} — " \
                         f"{validators.format_date(end)}]"
            entry = QListWidgetItem(f"АОСР № {item.number}{period}")
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            entry.setCheckState(Qt.CheckState.Unchecked)
            self.acts_list.addItem(entry)
        layout.addWidget(self.acts_list)

        if not candidates:
            self.hint.setText(
                "В проекте нет свободных актов освидетельствования скрытых "
                "работ. Создайте АОСР и укажите срок его работ двумя датами "
                "(ТЗ п.43, 87)."
            )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_acts(self) -> list[int]:
        """Идентификаторы отмеченных актов в порядке перечисления."""
        return [
            self.acts_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.acts_list.count())
            if self.acts_list.item(row).checkState() == Qt.CheckState.Checked
        ]

    def values(self) -> dict:
        """Параметры для link_service.link_documents (ТЗ п.87)."""
        return {
            "document_id": self.document.id,
            "link_role": domain.LINK_ROLE_FINALIZES,
        }
