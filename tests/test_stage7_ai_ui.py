"""Интерфейс ИИ-агента: запрос, черновики, подтверждение (ТЗ п.102, 104, 105)."""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from app.ai.connector import MODE_INTERNET, MODE_LOCAL, MODE_OFF
from app.config import ARCHIVE_DIR
from app.core import domain
from app.core.services import document_service
from app.db.models import DocumentLink


@pytest.fixture
def ai_documents(db, project):
    """Два связанных документа: ИИ должен что-то предложить."""
    aosr = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    return aosr, aook


@pytest.fixture
def ai_window(db, project, monkeypatch, gui_support):
    """Главное окно с включённым локальным ИИ и выбранным проектом."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.ai.mode = MODE_LOCAL
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)
    return window


@pytest.mark.gui
def test_ai_page_asks_with_operator_request(ai_window, project):
    """Запрос оператора формулируется словами (ТЗ п.102)."""
    assert ai_window.ai_request_input.text() == ""
    ai_window.ai_request_input.setPlaceholderText("Проверь комплект АОСР №15")
    ai_window.ai_request_input.setText("Проверь комплект проекта")
    ai_window.run_ai_check()
    output = ai_window.ai_output.toPlainText()
    assert "черновик" in output.lower()
    assert "Предложений" in output or "предложений" in output


@pytest.mark.gui
def test_ai_shows_draft_proposals_not_documents(ai_window, db, project, ai_documents):
    """Предложения показаны как черновики, а не как выпущенные документы
    (ТЗ п.105)."""
    ai_window.run_ai_check()
    assert ai_window.ai_table.rowCount() > 0
    assert db.query(DocumentLink).count() == 0, "анализ не меняет данные"
    statuses = [
        ai_window.ai_table.item(row, 3).text()
        for row in range(ai_window.ai_table.rowCount())
    ]
    assert set(statuses) == {domain.AI_PROPOSAL_DRAFT}


@pytest.mark.gui
def test_applying_proposal_requires_confirmation(
    ai_window, db, project, ai_documents, gui_support
):
    """Без подтверждения оператора данные не меняются (ТЗ п.104)."""
    ai_window.run_ai_check()
    ai_window.ai_table.selectRow(0)

    # Ответ «Нет»: заглушка диалога возвращает None, это не подтверждение.
    ai_window.apply_ai_proposal()
    assert gui_support["question"], "оператор должен получить вопрос о применении"
    assert db.query(DocumentLink).count() == 0
    assert ai_window.ai_table.item(0, 3).text() == domain.AI_PROPOSAL_DRAFT


@pytest.mark.gui
def test_accepted_proposal_is_applied_from_interface(
    ai_window, db, project, monkeypatch, gui_support
):
    """Подтверждённое предложение применяется кнопкой (ТЗ п.104)."""
    aosr = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()

    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    ai_window.run_ai_check()

    row = next(
        index for index in range(ai_window.ai_table.rowCount())
        if "не связан" in ai_window.ai_table.item(index, 0).text()
    )
    ai_window.ai_table.selectRow(row)
    ai_window.apply_ai_proposal()

    assert db.query(DocumentLink).filter(
        DocumentLink.document_id == aook.id,
        DocumentLink.related_document_id == aosr.id,
    ).count() == 1
    assert ai_window.ai_table.item(row, 3).text() == domain.AI_PROPOSAL_ACCEPTED
    assert any(gui_support["information"]), "оператор должен увидеть результат"


@pytest.mark.gui
def test_rejected_proposal_stays_draft(ai_window, db, project, ai_documents):
    """Отклонённое предложение не применяется (ТЗ п.104)."""
    ai_window.run_ai_check()
    ai_window.ai_table.selectRow(0)
    proposal_id = ai_window.ai_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    ai_window.reject_ai_proposal()
    assert db.query(DocumentLink).count() == 0
    row = next(
        index for index in range(ai_window.ai_table.rowCount())
        if ai_window.ai_table.item(index, 0).data(Qt.ItemDataRole.UserRole)
        == proposal_id
    )
    assert ai_window.ai_table.item(row, 3).text() == domain.AI_PROPOSAL_REJECTED


@pytest.mark.gui
def test_ai_is_hidden_when_disabled(db, project, monkeypatch, gui_support):
    """При «Нет ИИ» страница агента недоступна (ТЗ п.9)."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.ai.mode = MODE_OFF
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)
    assert window.ai_output.isVisible() is False
    window.run_ai_check()
    assert "выключен" in window.ai_output.toPlainText().lower()
    window.close()


@pytest.mark.gui
def test_normative_basis_is_shown_to_operator(ai_window, db, project):
    """Основание требования видно оператору (ТЗ п.106)."""
    from app.core.services import form_service, issue_service
    from datetime import date

    aosr = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 15)
    )
    form_service.save_draft(db, aosr.id, {
        "object_name": "Корпус 2", "address": "г. Москва",
        "work_description": "Армирование стен", "section_refs": "КЖ",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "period_start": "01.04.2024", "period_end": "30.04.2024",
        "work_volume": "120 м²", "has_defects": "Нет",
        "conclusion": "Работы выполнены", "work_performer": "ООО «Строй»",
    })
    db.refresh(aosr)
    issue_service.issue_document(db, aosr.id)
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()

    ai_window.run_ai_check()
    row = next(
        index for index in range(ai_window.ai_table.rowCount())
        if "не связан" in ai_window.ai_table.item(index, 0).text()
    )
    basis = ai_window.ai_table.item(row, 1).text()
    kind = ai_window.ai_table.item(row, 2).text()
    assert basis and basis != "основание не указано", "у требования должно быть основание"
    assert kind == "требование нормы"


# =====================================================================
# П.103: ФАЙЛЫ ПЕРЕДАЮТСЯ ТОЛЬКО ПО ОТМЕТКЕ ОПЕРАТОРА
# =====================================================================


def _archive_pdf(db, project, name="СХЕМА UI.pdf", body="Текст схемы для анализа"):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.core.services import printing, storage_service

    printing.register_fonts()
    source = ARCHIVE_DIR / name
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(source), pagesize=A4)
    pdf.setFont(printing.FONT_FAMILY, 11)
    pdf.drawString(60, 780, body)
    pdf.showPage()
    pdf.save()
    return storage_service.add_file_to_archive(
        db=db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )


@pytest.mark.gui
def test_ai_file_list_is_unchecked_by_default(ai_window, db, project, ai_documents):
    """Без отметки оператора текст файла ИИ не передаётся (ТЗ п.103)."""
    _archive_pdf(db, project)
    ai_window.run_ai_check()

    assert ai_window.ai_files_list.count() == 1
    assert ai_window.ai_files_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert "Ничего не отмечено" in ai_window.ai_files_hint.text()
    assert "Текст файлов" not in ai_window.ai_output.toPlainText()


@pytest.mark.gui
def test_ai_file_text_needs_confirmation(
    ai_window, db, project, ai_documents, monkeypatch
):
    """Текст отмеченного файла передаётся только после подтверждения."""
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    _archive_pdf(db, project)
    ai_window.run_ai_check()
    item = ai_window.ai_files_list.item(0)
    item.setCheckState(Qt.CheckState.Checked)

    ai_window.run_ai_check()

    assert asked, "оператор должен подтвердить передачу текста"
    assert "СХЕМА UI.pdf" in asked[0]
    assert "переданы" not in ai_window.ai_output.toPlainText().lower()


@pytest.mark.gui
def test_ai_file_text_is_sent_after_confirmation(
    ai_window, db, project, ai_documents, monkeypatch
):
    """После подтверждения ИИ видит текст файла (ТЗ п.103)."""
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    _archive_pdf(db, project)
    ai_window.run_ai_check()
    ai_window.ai_files_list.item(0).setCheckState(Qt.CheckState.Checked)

    ai_window.run_ai_check()

    output = ai_window.ai_output.toPlainText()
    assert "СХЕМА UI.pdf" in output
    assert "Текст файлов" in output


@pytest.mark.gui
def test_ai_internet_mode_blocks_file_text(
    ai_window, db, project, ai_documents, monkeypatch
):
    """В интернет-режиме текст файла не передаётся (ТЗ п.9, 103)."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda parent, title, text: warnings.append(text)),
    )
    _archive_pdf(db, project)
    ai_window.ai.mode = MODE_INTERNET
    ai_window.run_ai_check()
    ai_window.ai_files_list.item(0).setCheckState(Qt.CheckState.Checked)

    ai_window.run_ai_check()

    assert warnings, "интернет-режим должен отказать"
    assert "интернет" in warnings[0].lower()
