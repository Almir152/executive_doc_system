"""Тесты GUI формы документа в рабочей области. ТЗ п.65, 66.

Панель и окно проекта проверяются без диалогов подтверждения: они
модифицируются напрямую, чтобы тест не зависел от интерактивных окон.
"""

import pytest
from PyQt6.QtWidgets import QMessageBox, QPushButton

from app.core import domain
from app.core.services import document_service, form_service, project_service
from app.db.models import SectionKind
from app.ui.document_form import (
    DocumentFormPanel, ExploitationChoiceWidget, SignatureBlocksWidget,
)
from app.ui.project_window import ProjectWindow
from app.ui.reference_picker import ReferenceMultiPicker


def _add_section(db, project, code="КЖ"):
    """Раздел проектной документации для выбора в форме (ТЗ п.21)."""
    kind = db.query(SectionKind).first()
    return project_service.add_section(
        db, project.id, kind_id=kind.id, code=code,
        name=f"Конструкции {code}", sheets="12",
    )


@pytest.fixture
def document(db, project):
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _find_button(widget, text):
    return next(
        b for b in widget.findChildren(QPushButton) if b.text() == text
    )


def _set_field(panel, key, value):
    """Заполнить поле формы: разделы выбираются из списка (ТЗ п.21)."""
    edit = panel.field_edits[key]
    if isinstance(edit, ReferenceMultiPicker):
        edit.set_selected_values([value])
    else:
        edit.setText(value)


def _fill_required(panel, sections=("КЖ",)):
    for key in (
        "object_name", "address", "work_description",
        "work_period", "period_start", "period_end",
        "work_volume", "has_defects", "conclusion", "work_performer",
    ):
        _set_field(panel, key, "значение")
    for code in sections:
        _set_field(panel, "section_refs", code)


# =====================================================================
# ПАНЕЛЬ ФОРМЫ
# =====================================================================


def test_panel_shows_required_fields_of_form(qapp, db, document):
    """Поля строятся по описанию нормативной формы, а не по коду (ТЗ п.96)."""
    panel = DocumentFormPanel(db, document.id)
    assert "object_name" in panel.field_edits
    assert "work_performer" in panel.field_edits


def test_panel_loads_saved_draft(qapp, db, document):
    """Открытие формы показывает сохранённый черновик (ТЗ п.66)."""
    form_service.save_draft(db, document.id, {"object_name": "Корпус 2"})
    panel = DocumentFormPanel(db, document.id)
    assert panel.field_edits["object_name"].text() == "Корпус 2"


def test_section_field_offers_project_sections(qapp, db, project):
    """Разделы проекта выбираются из списка, а не вписываются текстом (ТЗ п.21)."""
    _add_section(db, project, "КЖ")
    _add_section(db, project, "АР")
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )

    panel = DocumentFormPanel(db, document.id)
    picker = panel.field_edits["section_refs"]

    assert isinstance(picker, ReferenceMultiPicker)
    assert picker.item_count() == 2
    picker.set_selected_values(["КЖ"])
    assert panel.payload()["section_refs"] == ["КЖ"]


def test_section_field_keeps_saved_selection(qapp, db, project):
    """Выбранные разделы восстанавливаются при открытии черновика (ТЗ п.21, 66)."""
    _add_section(db, project, "КЖ")
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    form_service.save_draft(db, document.id, {"section_refs": ["КЖ"]})

    panel = DocumentFormPanel(db, document.id)

    assert panel.field_edits["section_refs"].selected_values() == ["КЖ"]


def test_section_field_rejects_code_outside_project(qapp, db, project):
    """Раздел, не относящийся к проекту, выбрать нельзя (ТЗ п.21)."""
    _add_section(db, project, "КЖ")
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    panel = DocumentFormPanel(db, document.id)

    panel.field_edits["section_refs"].set_selected_values(["ЧУЖОЙ"])

    assert "section_refs" not in panel.payload()


def test_save_button_persists_draft(qapp, db, document):
    """Кнопка «Сохранить форму» записывает черновик без проверки полноты."""
    panel = DocumentFormPanel(db, document.id)
    _set_field(panel, "object_name", "Корпус 2")
    _find_button(panel, "Сохранить форму").click()

    assert form_service.load_draft(db, document.id)["object_name"] == "Корпус 2"


def test_check_button_reports_missing_fields(db, document, monkeypatch):
    """Кнопка проверки перечисляет незаполненные поля, а не молчит (ТЗ п.96)."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args[1:]),
    )
    panel = DocumentFormPanel(db, document.id)
    _find_button(panel, "Проверить заполнение").click()

    assert warnings, "проверка обязана сообщить о неполноте"
    assert "Наименование объекта" in panel.problems.toPlainText()


def test_check_button_warns_about_issued_document(db, document, monkeypatch):
    """Выпущенный документ не редактируется через интерфейс (ТЗ п.54, 85)."""
    document.status = domain.DOC_STATUS_ISSUED
    db.commit()
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args[1:]),
    )
    panel = DocumentFormPanel(db, document.id)
    _find_button(panel, "Сохранить форму").click()

    assert warnings
    assert form_service.load_draft(db, document.id) == {}


# =====================================================================
# ПОДПИСАНТЫ И П.64
# =====================================================================


def test_signature_blocks_widget_saves_both_blocks(qapp, db, document):
    """Оба блока подписантов сохраняются из одного виджета (ТЗ п.63)."""
    widget = SignatureBlocksWidget(db, document.id)
    widget.edits[form_service.BLOCK_HANDED_OVER]["full_name"].setText("Иванов И. И.")
    widget.edits[form_service.BLOCK_ACCEPTED]["full_name"].setText("Петров П. П.")

    assert widget.save() == []
    assert form_service.get_signature_block(
        db, document.id, form_service.BLOCK_HANDED_OVER
    ).full_name == "Иванов И. И."
    assert form_service.get_signature_block(
        db, document.id, form_service.BLOCK_ACCEPTED
    ).full_name == "Петров П. П."


def test_signature_blocks_widget_shows_saved_values(qapp, db, document):
    form_service.save_signature_block(
        db, document.id, form_service.BLOCK_HANDED_OVER,
        position="ГИП", full_name="Иванов И. И.", sign_place="Москва",
    )
    widget = SignatureBlocksWidget(db, document.id)
    assert widget.edits[form_service.BLOCK_HANDED_OVER]["position"].text() == "ГИП"


def test_exploitation_widget_reflects_saved_choice(qapp, db, project):
    """Виджет показывает сохранённое решение по документу (ТЗ п.64)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO
    )
    form_service.set_exploitation_missing_choice(
        db, document.id, form_service.MISSING_OMIT_BLOCK
    )
    widget = ExploitationChoiceWidget(db, document.id)
    assert widget.omit_radio.isChecked()
    assert not widget.keep_radio.isChecked()


def test_exploitation_widget_is_hidden_in_plain_aosr(qapp, db, project):
    """В обычном АОСР представителя эксплуатации нет (ТЗ п.40)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    panel = DocumentFormPanel(db, document.id)
    # Место в форме есть (Этап 2), но выбирать способ вывода нельзя (ТЗ п.40).
    assert "exploitation_rep" in panel.field_edits
    assert not panel.exploitation.isVisibleTo(panel)


def _patch_dialog_choice(monkeypatch, choose):
    """Подменить ответ диалога п.64, сохранив его собственную логику.

    Подменяется только нажатие кнопки, а решение «спрашивать ли вообще»
    остаётся настоящим — иначе проверка п.64 ничего не проверяла бы.
    """
    holder = {"calls": 0, "button": None}

    def fake_exec(dialog):
        holder["calls"] += 1
        holder["button"] = _dialog_button(dialog, choose)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(
        QMessageBox, "clickedButton", lambda dialog: holder["button"]
    )
    return holder


def _dialog_button(dialog, choose):
    if isinstance(choose, QMessageBox.StandardButton):
        return dialog.button(choose)
    for button in dialog.buttons():
        if choose in button.text():
            return button
    return None


def test_form_asks_before_saving_omitted_block(qapp, db, project, monkeypatch):
    """Перед сохранением предлагается выбор по п.64, и он сохраняется."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO
    )
    panel = DocumentFormPanel(db, document.id)
    asked = _patch_dialog_choice(monkeypatch, "Убрать незаполненный блок")

    assert panel.save() is True
    assert asked["calls"] == 1
    db.expire_all()
    assert document.exploitation_missing_choice == form_service.MISSING_OMIT_BLOCK


def test_form_does_not_ask_in_plain_aosr(qapp, db, project, monkeypatch):
    """В обычном АОСР спрашивать не о чем (ТЗ п.40)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    panel = DocumentFormPanel(db, document.id)
    asked = _patch_dialog_choice(monkeypatch, "Убрать незаполненный блок")

    assert panel.save() is True
    assert asked["calls"] == 0
    db.expire_all()
    assert document.exploitation_missing_choice is None


def test_form_asks_once(qapp, db, project, monkeypatch):
    """После решения повторный вопрос при сохранении не задаётся (ТЗ п.64)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO
    )
    panel = DocumentFormPanel(db, document.id)
    asked = _patch_dialog_choice(monkeypatch, "Оставить пустую строку")
    panel.save()
    panel.save()

    assert asked["calls"] == 1


def test_filled_block_needs_no_question(qapp, db, project, monkeypatch):
    """Заполненный представитель не вызывает вопроса (ТЗ п.64)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO
    )
    panel = DocumentFormPanel(db, document.id)
    panel.field_edits["exploitation_rep"].setText("ООО «Эксплуатация»")
    asked = _patch_dialog_choice(monkeypatch, "Убрать незаполненный блок")

    assert panel.save() is True
    assert asked["calls"] == 0


def test_cancelling_decision_keeps_form_unsaved(
    qapp, db, project, monkeypatch
):
    """Отказ от решения отменяет сохранение (ТЗ п.64)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO
    )
    panel = DocumentFormPanel(db, document.id)
    panel.field_edits["object_name"].setText("Объект")
    _patch_dialog_choice(monkeypatch, QMessageBox.StandardButton.Cancel)

    assert panel.save() is False
    assert form_service.load_draft(db, document.id) == {}


# =====================================================================
# РАБОЧАЯ ОБЛАСТЬ ПРОЕКТА (ТЗ п.65, 66)
# =====================================================================


def test_form_opens_in_project_window(qapp, db, project, document, monkeypatch):
    """Форма открывается в рабочей области окна проекта (ТЗ п.65)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    assert window.form_panel is None
    window.open_form()

    assert window.form_panel is not None
    assert window.form_panel.document_id == document.id
    assert window.form_container.isVisible() or not window.isVisible()
    window.close()


def test_form_opens_in_separate_window_when_mode_chosen(qapp, db, project, document):
    """Согласованный режим открывает форму отдельно (ТЗ п.65)."""
    from app import settings

    settings.set_form_open_mode(settings.FORM_MODE_SEPARATE)
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.open_form()

    assert window.form_panel is None, "в режиме «отдельно» панели в области нет"
    assert window.separate_form_window is not None
    assert window.separate_form_window.panel.document_id == document.id
    window.close()


def test_form_mode_is_kept_between_windows(qapp, db, project, document):
    """Настройка режима сохраняется и применяется к следующему окну (ТЗ п.65)."""
    from app import settings

    settings.set_form_open_mode(settings.FORM_MODE_INSIDE)
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.form_mode_combo.setCurrentIndex(
        window.form_mode_combo.findData(settings.FORM_MODE_SEPARATE)
    )

    assert settings.form_open_mode() == settings.FORM_MODE_SEPARATE
    assert ProjectWindow(db, project.id).form_mode_combo.currentData() == (
        settings.FORM_MODE_SEPARATE
    )
    settings.set_form_open_mode(settings.FORM_MODE_INSIDE)
    window.close()


def test_form_can_be_expanded_and_restored(qapp, db, project, document):
    """Форма разворачивается и возвращается к прежнему размеру (ТЗ п.65)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.show()
    window.open_form()

    assert window.sections_box.isVisible()
    window.toggle_form_expanded()

    assert not window.sections_box.isVisible(), "при развороте блоки должны скрыться"
    assert window.btn_expand_form.text() == "Восстановить размер"

    window.toggle_form_expanded()

    assert window.sections_box.isVisible()
    assert window.btn_expand_form.text() == "Развернуть форму"
    window.close()


def test_closing_form_restores_hidden_blocks(qapp, db, project, document):
    """После закрытия развёрнутой формы блоки проекта возвращаются (ТЗ п.65)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.show()
    window.open_form()
    window.toggle_form_expanded()

    window.close_form()

    assert window.sections_box.isVisible()
    assert window.btn_expand_form.text() == "Развернуть форму"
    window.close()


def test_form_button_requires_document_selection(qapp, db, project, monkeypatch):
    """Без выбранного документа форма не открывается (ТЗ п.65)."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: shown.append(args[1:]),
    )
    window = ProjectWindow(db, project.id)
    window.open_form()

    assert shown, "без документа должно быть объяснение"
    assert window.form_panel is None
    window.close()


def test_closing_form_offers_to_save_draft(qapp, db, project, document, monkeypatch):
    """Незавершённая форма спрашивает о сохранении черновика (ТЗ п.66)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.open_form()
    _set_field(window.form_panel, "object_name", "Корпус 2")

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Save,
    )
    window.close_form()

    assert form_service.load_draft(db, document.id) == {"object_name": "Корпус 2"}
    assert window.form_panel is None
    window.close()


def test_closing_form_can_discard_draft(qapp, db, project, document, monkeypatch):
    """Оператор вправе закрыть форму без сохранения (ТЗ п.66)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.open_form()
    _set_field(window.form_panel, "object_name", "Корпус 2")

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Discard,
    )
    window.close_form()

    assert form_service.load_draft(db, document.id) == {}
    assert window.form_panel is None
    window.close()


def test_cancel_keeps_form_open(qapp, db, project, document, monkeypatch):
    """Отказ от сохранения не закрывает форму: ввод не теряется (ТЗ п.66)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.open_form()
    _set_field(window.form_panel, "object_name", "Корпус 2")

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    window.close_form()

    assert window.form_panel is not None
    assert form_service.load_draft(db, document.id) == {}
    window.close()


def test_closing_filled_form_does_not_ask(qapp, db, project, document, monkeypatch):
    """Сохранённая форма закрывается без лишнего вопроса (ТЗ п.66)."""
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.open_form()
    _fill_required(window.form_panel)
    _find_button(window.form_panel, "Сохранить форму").click()

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: asked.append(args[1:]),
    )
    window.close_form()

    assert not asked, "после сохранения вопрос о черновике неуместен"
    window.close()


def test_untouched_form_closes_without_question(qapp, db, project, monkeypatch):
    """Открытие и закрытие формы без правок не должно ничего спрашивать (ТЗ п.66)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.open_form()

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: asked.append(args[1:]),
    )
    window.close_form()

    assert not asked, "пустая форма не должна считаться несохранённой работой"
    window.close()


def test_switching_document_prompts_before_replacing_form(
    db, project, monkeypatch
):
    """Смена документа не должна молча затереть чужую форму (ТЗ п.66)."""
    first = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK
    )
    window = ProjectWindow(db, project.id)
    window._select_document(first.id)
    window.open_form()
    _set_field(window.form_panel, "object_name", "Корпус 2")

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    window._select_document(second.id)
    window.open_form()

    assert window.form_panel.document_id == first.id
    window.close()
