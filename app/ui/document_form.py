"""Редактор формы документа в рабочей области. ТЗ п.63, 64, 65, 66.

Форма открывается внутри правой рабочей области приложения (ТЗ п.65) и
предлагает сохранить незавершённый ввод при закрытии (ТЗ п.66).
"""

import logging

from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from app.core import domain
from app.core.services import form_service, project_service
from app.ui.reference_picker import ReferenceMultiPicker

log = logging.getLogger(__name__)

# Признак поля, заполняемого выбором разделов проекта (ТЗ п.21).
SECTION_SOURCE = "section"


class SignatureBlocksWidget(QGroupBox):
    """Два независимых блока подписантов (ТЗ п.63)."""

    def __init__(self, db, document_id, parent=None):
        super().__init__("Подписанты (ТЗ п.63)", parent)
        self.db = db
        self.document_id = document_id
        layout = QVBoxLayout(self)
        self.edits: dict[str, dict[str, QLineEdit]] = {}

        for block in form_service.SIGNATURE_BLOCKS:
            box = QGroupBox(block)
            form = QFormLayout(box)
            stored = form_service.get_signature_block(db, document_id, block)
            self.edits[block] = {}
            for field, label in (
                ("position", "Должность"),
                ("full_name", "Фамилия, имя"),
                ("sign_place", "Место подписи"),
            ):
                edit = QLineEdit(getattr(stored, field) or "" if stored else "")
                form.addRow(label, edit)
                self.edits[block][field] = edit
            layout.addWidget(box)

    def save(self) -> list[str]:
        """Сохранить оба блока; возвращает тексты отказов."""
        problems = []
        for block, fields in self.edits.items():
            try:
                form_service.save_signature_block(
                    self.db, self.document_id, block,
                    position=fields["position"].text(),
                    full_name=fields["full_name"].text(),
                    sign_place=fields["sign_place"].text(),
                )
            except form_service.FormError as exc:
                problems.append(str(exc))
        return problems


class ExploitationChoiceWidget(QGroupBox):
    """Решение по незаполненному представителю эксплуатации (ТЗ п.64)."""

    def __init__(self, db, document_id, parent=None):
        super().__init__("Представитель эксплуатации (ТЗ п.64)", parent)
        self.db = db
        self.document_id = document_id
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Если представитель эксплуатирующей организации не заполнен, "
            "выберите, как выводить поле. Решение относится к этому документу."
        ))

        self.keep_radio = QRadioButton(
            "Оставить пустую строку для ручного заполнения при печати"
        )
        self.omit_radio = QRadioButton("Убрать незаполненный блок из печатной формы")
        layout.addWidget(self.keep_radio)
        layout.addWidget(self.omit_radio)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.reload()

    def reload(self) -> None:
        from app.db.models import Document

        document = self.db.get(Document, self.document_id)
        choice = getattr(document, "exploitation_missing_choice", None)
        self.omit_radio.setChecked(choice == form_service.MISSING_OMIT_BLOCK)
        self.keep_radio.setChecked(choice != form_service.MISSING_OMIT_BLOCK)

    def choice(self) -> str:
        return (
            form_service.MISSING_OMIT_BLOCK if self.omit_radio.isChecked()
            else form_service.MISSING_KEEP_PLACE
        )


class FormVersionWidget(QGroupBox):
    """Выбор версии формы документа (ТЗ п.24, 62, 96).

    Версия выбирается явно, а не подбирается под объём печати: структура
    формы не изменяется программой, поэтому полный и краткий варианты
    существуют как отдельные версии.
    """

    def __init__(self, db, document_id, on_change=None, parent=None):
        super().__init__("Версия формы (ТЗ п.96)", parent)
        self.db = db
        self.document_id = document_id
        self.on_change = on_change
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Структура формы не изменяется под печать (ТЗ п.62): полный "
            "образец и краткий вариант — разные версии. Выбранная версия "
            "закрепляется за документом и сохраняется при выпуске."
        ))
        self.combo = QComboBox()
        layout.addWidget(self.combo)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        self.combo.activated.connect(self._activate)
        self.reload()

    def reload(self) -> None:
        from app.db.models import Document

        document = self.db.get(Document, self.document_id)
        self.combo.blockSignals(True)
        self.combo.clear()
        if document is None:
            self.combo.blockSignals(False)
            return
        forms = form_service.available_versions(self.db, document.doc_type)
        # Закреплённой версии нет — действует актуальная (ТЗ п.96).
        selected = document.form_version_id
        if selected is None:
            selected = next((f.id for f in forms if f.is_current), None)
        for form in forms:
            mark = " — актуальная" if form.is_current else ""
            self.combo.addItem(f"v{form.version}: {form.title}{mark}", form.id)
            if form.id == selected:
                self.combo.setCurrentIndex(self.combo.count() - 1)
        if self.combo.currentIndex() < 0 and self.combo.count():
            self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        self.setEnabled(document.status != domain.DOC_STATUS_ISSUED)

    def _activate(self, index: int) -> None:
        version_id = self.combo.itemData(index)
        error = form_service.pin_form_version(self.db, self.document_id, version_id)
        self.error_label.setText(error)
        if error:
            # Версия не сохранилась: список обязан показать ту, что в силе,
            # иначе экран противоречит состоянию документа (ТЗ п.96).
            self.reload()
            return
        if self.on_change is not None:
            self.on_change()


class DocumentFormPanel(QWidget):
    """Панель формы документа: поля, подписанты, черновик."""

    def __init__(self, db, document_id, project_id=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.document_id = document_id
        # Разделы берутся из своего проекта: документ может быть не из того
        # проекта, который открыт в окне.
        self.project_id = project_id
        if self.project_id is None:
            from app.db.models import Document

            document = db.get(Document, document_id)
            self.project_id = document.project_id if document is not None else None
        self.field_edits: dict[str, QWidget] = {}
        self.build()
        self.reload()

    # -----------------------------------------------------------------
    def build(self) -> None:
        outer = QVBoxLayout(self)

        self.header = QLabel()
        self.header.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.header.setWordWrap(True)
        outer.addWidget(self.header)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        self.form_version = FormVersionWidget(
            self.db, self.document_id, on_change=self.reload
        )
        outer.addWidget(self.form_version)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        self.fields_box = QGroupBox("Поля формы")
        self.fields_form = QFormLayout(self.fields_box)
        layout.addWidget(self.fields_box)

        self.signatures = SignatureBlocksWidget(self.db, self.document_id)
        layout.addWidget(self.signatures)

        self.exploitation = ExploitationChoiceWidget(self.db, self.document_id)
        layout.addWidget(self.exploitation)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)

        buttons = QHBoxLayout()
        self.btn_save = QPushButton("Сохранить форму")
        self.btn_save.clicked.connect(lambda: self.save(validate=False))
        buttons.addWidget(self.btn_save)
        self.btn_check = QPushButton("Проверить заполнение")
        self.btn_check.clicked.connect(lambda: self.save(validate=True))
        buttons.addWidget(self.btn_check)
        buttons.addStretch()
        self.dirty_label = QLabel()
        buttons.addWidget(self.dirty_label)
        outer.addLayout(buttons)

        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.problems.setMaximumHeight(90)
        self.problems.setPlaceholderText(
            "Незаполненные поля появятся здесь (ТЗ п.96)."
        )
        outer.addWidget(self.problems)

    # -----------------------------------------------------------------
    def reload(self) -> None:
        from app.db.models import Document

        document = self.db.get(Document, self.document_id)
        if document is None:
            self.header.setText("Документ не найден")
            return
        label = domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)
        self.header.setText(f"{label} № {document.number}")
        date_text = (
            document.doc_date.strftime("%d.%m.%Y") if document.doc_date else "дата не задана"
        )
        self.status_label.setText(
            f"{date_text}   •   статус: {_status_label(document.status)}"
        )
        self.form_version.reload()

        payload = form_service.load_draft(self.db, self.document_id)
        while self.fields_form.count():
            item = self.fields_form.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.field_edits: dict[str, QWidget] = {}
        for section, field in form_service._iter_fields(self.db, document):
            edit = self._build_editor(field, payload.get(field["key"]))
            self.field_edits[field["key"]] = edit
            marker = " *" if field.get("required") else ""
            self.fields_form.addRow(
                f"{field.get('label') or field['key']}{marker}", edit
            )

        self.exploitation.reload()
        # Решение по п.64 предлагают только там, где форма это допускает:
        # в обычном АОСР представителя эксплуатации нет вовсе (ТЗ п.40).
        self.exploitation.setVisible(
            form_service.form_allows_exploitation_omission(self.db, self.document_id)
        )
        self.dirty_label.setText("")

    # -----------------------------------------------------------------
    def _build_editor(self, field: dict, value):
        """Поле ввода по описанию формы.

        Поле со ссылкой на разделы проектной документации заполняется выбором
        из структурированных сведений проекта, а не текстом (ТЗ п.21).
        """
        if field.get("source") == SECTION_SOURCE:
            return self._build_section_picker(field, value)
        return _build_editor(field, value)

    def _build_section_picker(self, field: dict, value):
        """Выбор одного или нескольких разделов проекта (ТЗ п.21)."""
        picker = ReferenceMultiPicker()
        sections = project_service.list_sections(self.db, self.project_id)
        picker.set_reference_items(
            [(f"{s.code} — {s.name}", s.code) for s in sections]
        )
        if value:
            picker.set_selected_values(
                value if isinstance(value, list) else [value]
            )
        if not sections:
            picker.setToolTip(
                "В проекте нет разделов проектной документации (ТЗ п.21)."
            )
        return picker

    def payload(self) -> dict:
        """Данные формы из полей ввода.

        Пустые поля не попадают в результат: иначе нетронутая форма
        отличалась бы от сохранённой и при закрытии спрашивала бы о
        сохранении пустоты (ТЗ п.66).
        """
        result = {}
        for key, edit in self.field_edits.items():
            if isinstance(edit, ReferenceMultiPicker):
                # Выбор разделов хранится списком кодов (ТЗ п.21).
                chosen = edit.selected_values()
                if chosen:
                    result[key] = chosen
                continue
            if isinstance(edit, QLineEdit):
                value = edit.text().strip()
            elif isinstance(edit, QTextEdit):
                value = edit.toPlainText().strip()
            else:
                # Неизвестный редактор раньше молча терял значение. Теперь
                # это видно в журнале: пропавшее поле невозможно заметить
                # глазами, а причина должна быть найдена (ТЗ п.66).
                log.warning(
                    "Поле формы %r: неизвестный редактор %s, значение не "
                    "сохранено", key, type(edit).__name__,
                )
                continue
            if value:
                result[key] = value
        return result

    def save(self, validate: bool = False) -> bool:
        """Сохранить черновик, подписантов и решение по п.64.

        Перед сохранением незаполненного представителя эксплуатации
        предлагается выбрать, как выводить поле (ТЗ п.64).
        """
        try:
            if validate:
                found = form_service.check_payload(
                    self.db, self.document_id, self.payload()
                )
                if found:
                    self.problems.setPlainText("\n".join(f"• {p}" for p in found))
                    QMessageBox.warning(
                        self, "Форма заполнена не полностью",
                        "Незаполненные поля перечислены внизу (ТЗ п.96).",
                    )
                    return False

            decision = self._ask_exploitation_decision()
        except form_service.FormError as exc:
            QMessageBox.warning(self, "Форма не проверена", str(exc))
            return False
        if decision is False:
            return False

        choice = decision
        if choice is None and self.exploitation.isVisible():
            # Оператор может переменить ранее принятое решение.
            choice = self.exploitation.choice()

        try:
            form_service.save_draft(
                self.db, self.document_id, self.payload(),
                validate=False, exploitation_choice=choice,
            )
        except form_service.FormError as exc:
            QMessageBox.warning(self, "Форма не сохранена", str(exc))
            return False

        try:
            problems = self.signatures.save()
        except Exception as exc:  # noqa: BLE001 - окно важнее падения
            log.exception("Не удалось сохранить подписантов")
            QMessageBox.critical(
                self, "Подписанты не сохранены",
                f"{type(exc).__name__}: {exc}\n\n"
                "Черновик формы сохранён. Повторите сохранение подписантов.",
            )
            return False
        if problems:
            self.problems.setPlainText("\n".join(f"• {p}" for p in problems))
            QMessageBox.warning(
                self, "Подписанты заполнены не полностью",
                "Ошибки перечислены внизу. Черновик формы сохранён (ТЗ п.63).",
            )
            return False
        self.exploitation.reload()
        self.problems.setPlainText("")
        self.dirty_label.setText("сохранено")
        return True

    def _ask_exploitation_decision(self) -> str | bool | None:
        """Спросить о выводе незаполненного поля или отменить сохранение.

        None — спрашивать не нужно, False — оператор отказался сохранять.
        """
        if not form_service.needs_exploitation_decision(
            self.db, self.document_id, self.payload()
        ):
            return None
        answer = QMessageBox(self)
        answer.setWindowTitle("Представитель эксплуатации (ТЗ п.64)")
        answer.setText(
            "Представитель эксплуатирующей организации не заполнен.\n"
            "Как вывести поле при печати?"
        )
        keep = answer.addButton(
            "Оставить пустую строку", QMessageBox.ButtonRole.AcceptRole
        )
        omit = answer.addButton(
            "Убрать незаполненный блок", QMessageBox.ButtonRole.AcceptRole
        )
        answer.addButton(QMessageBox.StandardButton.Cancel)
        answer.exec()
        clicked = answer.clickedButton()
        if clicked is keep:
            return form_service.MISSING_KEEP_PLACE
        if clicked is omit:
            return form_service.MISSING_OMIT_BLOCK
        return False


def _build_editor(field: dict, value):
    """Поле ввода по типу данных описания формы."""
    kind = (field.get("type") or "text").lower()
    if kind in ("text", "number", "date", None):
        edit = QLineEdit(str(value or ""))
        if kind == "date":
            edit.setPlaceholderText("дд.мм.гггг")
        return edit
    edit = QTextEdit()
    edit.setPlainText(str(value or ""))
    edit.setMaximumHeight(70)
    return edit


class DocumentFormWindow(QMainWindow):
    """Форма документа в отдельном окне (ТЗ п.65).

    Тот же `DocumentFormPanel`, что и в рабочей области: режим открытия
    меняет только место показа, а не состав полей и правила сохранения.
    """

    def __init__(self, db, document_id, project_id=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.document_id = document_id
        self.setWindowTitle("Форма документа (ТЗ п.65)")
        # Форма длинная: окно по умолчанию компактное и свободно
        # уменьшается, а поля и подписи прокручиваются внутри панели.
        self.resize(760, 600)
        self.setMinimumSize(560, 400)
        self.panel = DocumentFormPanel(db, document_id, project_id=project_id)
        self.setCentralWidget(self.panel)

    def closeEvent(self, event) -> None:
        """Предложить сохранить незавершённый ввод при закрытии (ТЗ п.66)."""
        panel = self.panel
        try:
            unsaved = form_service.has_unsaved_work(
                self.db, panel.document_id, panel.payload()
            )
        except Exception:  # noqa: BLE001 - форма всё равно должна закрыться
            log.exception("Не удалось проверить незавершённый ввод формы")
            unsaved = False
        if not unsaved:
            super().closeEvent(event)
            return
        answer = QMessageBox.question(
            self, "Незавершённая форма (ТЗ п.66)",
            "Форма заполнена не полностью. Сохранить черновик перед закрытием?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if answer == QMessageBox.StandardButton.Save and not panel.save():
            event.ignore()
            return
        super().closeEvent(event)


def _status_label(status: str | None) -> str:
    return {
        domain.DOC_STATUS_DRAFT: "рабочий",
        domain.DOC_STATUS_ISSUED: "выпущен",
    }.get(status, status or "—")
