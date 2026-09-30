"""Выпуск документа в окне проекта. ТЗ п.85, 96, 93.

Диалоги подтверждения подменяются, иначе тест зависал бы вместо падения.
"""

from datetime import date

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.core import domain
from app.core.services import document_service, form_service, issue_service
from app.ui.project_window import IssueDialog, ProjectWindow

REQUIRED = (
    "object_name", "address", "work_description", "work_period",
    "work_volume", "has_defects", "conclusion", "work_performer",
)


@pytest.fixture
def document(db, project):
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _complete(**extra) -> dict:
    payload = {key: "значение" for key in REQUIRED}
    payload["section_refs"] = "КЖ"
    payload.update(extra)
    return payload


class _StubDialog:
    """Заглушка диалога выпуска: дата вводится без показа окна."""

    date_value = date(2024, 5, 1)

    def __init__(self, document, parent=None):
        self.document = document

    def doc_date(self) -> date:
        if self.date_value is None:
            raise ValueError(
                "Укажите дату документа: она вводится оператором (ТЗ п.43)."
            )
        return self.date_value


@pytest.fixture
def issue_dialog(monkeypatch):
    stub = _StubDialog
    monkeypatch.setattr("app.ui.project_window.IssueDialog", stub)
    return stub


@pytest.fixture
def shown(monkeypatch):
    """Все сообщения собираются в список вместо показа окон."""
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        classmethod(lambda cls, parent, title, text, *a, **k: messages.append((title, text))),
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        classmethod(lambda cls, parent, title, text, *a, **k: messages.append((title, text))),
    )
    return messages


def test_issue_button_disabled_until_document_selected(qapp, db, project, shown):
    window = ProjectWindow(db, project.id)

    assert window.btn_issue_document.isEnabled() is False
    assert window.btn_new_revision.isEnabled() is False
    window.close()


def test_issue_button_enabled_for_draft(qapp, db, project, document, shown):
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    assert window.btn_issue_document.isEnabled() is True
    assert window.btn_new_revision.isEnabled() is False
    window.close()


def test_issue_shows_problems_and_keeps_document_draft(
    qapp, db, project, document, shown, issue_dialog
):
    form_service.save_draft(db, document.id, {"object_name": "Корпус 2"})
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.issue_document()

    assert document.status == domain.DOC_STATUS_DRAFT
    assert issue_service.issued_versions(db, document.id) == []
    assert any("обязательное поле" in text for _, text in shown)
    window.close()


def test_issue_success_marks_document_issued(
    qapp, db, project, document, shown, issue_dialog
):
    form_service.save_draft(db, document.id, _complete())
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.issue_document()

    assert document.status == domain.DOC_STATUS_ISSUED
    assert document.doc_date == date(2024, 5, 1)
    assert len(issue_service.issued_versions(db, document.id)) == 1
    assert window.btn_issue_document.isEnabled() is False
    assert window.btn_new_revision.isEnabled() is True
    window.close()


def test_form_of_issued_document_is_not_opened(qapp, db, project, document, shown):
    form_service.save_draft(db, document.id, _complete())
    issue_service.issue_document(db, document.id, doc_date=date(2024, 5, 1))
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.open_form()

    assert window.form_panel is None
    assert any("выпущен" in text.lower() for _, text in shown)
    window.close()


def test_new_revision_button_opens_working_copy(qapp, db, project, document, shown):
    form_service.save_draft(db, document.id, _complete())
    issue_service.issue_document(db, document.id, doc_date=date(2024, 5, 1))
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.start_revision()

    assert document.status == domain.DOC_STATUS_DRAFT
    versions = issue_service.list_versions(db, document.id)
    assert [v.version_no for v in versions] == [1, 2]
    assert window.btn_issue_document.isEnabled() is True
    window.close()


def test_new_revision_on_draft_reports_nothing_to_do(
    qapp, db, project, document, shown
):
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.start_revision()

    assert len(issue_service.list_versions(db, document.id)) == 0
    assert any("не требуется" in title for title, _ in shown)
    window.close()


def test_issue_without_document_selection_asks_to_choose(
    qapp, db, project, shown, issue_dialog
):
    window = ProjectWindow(db, project.id)

    window.issue_document()

    assert any("выберите" in text.lower() for _, text in shown)
    window.close()


def test_issue_without_date_is_refused(qapp, db, project, document, shown, issue_dialog):
    """Пустая дата — не «сегодня»: система не назначает дату (ТЗ п.43)."""
    form_service.save_draft(db, document.id, _complete())
    issue_dialog.date_value = None
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    window.issue_document()

    assert document.status == domain.DOC_STATUS_DRAFT
    assert any("Укажите дату" in text for _, text in shown)
    window.close()


# =====================================================================
# ДИАЛОГ ВЫПУСКА (ТЗ п.43)
# =====================================================================


def test_issue_dialog_does_not_prefill_date(qapp, db, document):
    """Дата документа не подставляется системой (ТЗ п.43)."""
    dialog = IssueDialog(document)
    try:
        assert dialog.date_edit.text() == ""
        with pytest.raises(ValueError):
            dialog.doc_date()
    finally:
        dialog.deleteLater()


def test_issue_dialog_keeps_entered_date(qapp, db, document):
    document.doc_date = date(2024, 5, 1)
    dialog = IssueDialog(document)
    try:
        assert dialog.doc_date() == date(2024, 5, 1)
        dialog.date_edit.setText("02.06.2024")
        assert dialog.doc_date() == date(2024, 6, 2)
        dialog.date_edit.setText("31.02.2024")
        with pytest.raises(ValueError):
            dialog.doc_date()
    finally:
        dialog.deleteLater()


# =====================================================================
# ПЕЧАТЬ ФОРМЫ (ТЗ п.55–62)
# =====================================================================


def test_print_button_requires_document_selection(qapp, db, project, shown):
    window = ProjectWindow(db, project.id)

    assert window.btn_print_form.isEnabled() is False
    window.close()


def test_print_button_enabled_for_selected_document(qapp, db, project, document, shown):
    window = ProjectWindow(db, project.id)
    window._select_document(document.id)

    assert window.btn_print_form.isEnabled() is True
    window.close()


def test_print_creates_pdf(qapp, db, project, document, shown, monkeypatch, tmp_path):
    form_service.save_draft(db, document.id, _complete())
    target = tmp_path / "form.pdf"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        classmethod(lambda cls, parent, title, name, filt: (str(target), filt)),
    )
    opened = []
    monkeypatch.setattr("app.ui.project_window._open_pdf", lambda path, parent: opened.append(path))

    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.print_form()

    assert target.exists()
    assert opened == [target]
    assert any("сохранён" in text.lower() for _, text in shown)
    window.close()


def test_print_cancelled_keeps_no_file(qapp, db, project, document, shown, monkeypatch, tmp_path):
    form_service.save_draft(db, document.id, _complete())
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        classmethod(lambda cls, parent, title, name, filt: ("", "")),
    )

    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.print_form()

    assert list(tmp_path.glob("*.pdf")) == []
    window.close()


def test_print_error_is_reported(qapp, db, project, document, shown, monkeypatch, tmp_path):
    """Ошибка печати показывается оператору, а не роняет приложение."""
    from app.core.services import printing

    form_service.save_draft(db, document.id, _complete())
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        classmethod(lambda cls, parent, title, name, filt: (str(tmp_path / "x.pdf"), "")),
    )
    def boom(*args, **kwargs):
        raise printing.PrintError("Шрифт не найден (ТЗ п.59).")
    monkeypatch.setattr(printing, "render_document_pdf", boom)

    window = ProjectWindow(db, project.id)
    window._select_document(document.id)
    window.print_form()

    assert any("Шрифт не найден" in text for _, text in shown)
    window.close()
