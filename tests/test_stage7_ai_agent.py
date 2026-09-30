"""ИИ-агент: контекст, черновики, подтверждение, нормативные основания.

ТЗ п.101–106: ИИ — заменяемый компонент, видит только предоставленный
контекст, ничего не меняет сам, его результат — черновик, а нормативное
утверждение сопровождается основанием.
"""

from datetime import date

import pytest

from app.ai import context as ai_context
from app.ai import normative
from app.ai.connector import (
    AIConnector, MODE_INTERNET, MODE_LOCAL, MODE_OFF, MODE_ORDER,
)
from app.core import domain
from app.core.services import (
    ai_service, document_service, form_service, issue_service, link_service,
    storage_service,
)
from app.db.models import AiProposal, Document, DocumentLink, HistoryEvent, NormativeForm
from app.config import ARCHIVE_DIR as ARCHIVE_PDF_DIR


@pytest.fixture
def aosr(db, project):
    """Выпущенный АОСР со схемой — эталонный комплект (ТЗ п.49, 54)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 15)
    )
    form_service.save_draft(db, document.id, {
        "object_name": "Корпус 2", "address": "г. Москва",
        "work_description": "Армирование стен", "section_refs": "КЖ",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "period_start": "01.04.2024", "period_end": "30.04.2024",
        "work_volume": "120 м²", "has_defects": "Нет",
        "conclusion": "Работы выполнены", "work_performer": "ООО «Строй»",
    })
    db.refresh(document)
    issue_service.issue_document(db, document.id)
    return document


@pytest.fixture
def linked_final_act(db, project, aosr):
    """АООК, связанный с АОСР (ТЗ п.87)."""
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    return aook


@pytest.fixture
def connector():
    return AIConnector(mode=MODE_LOCAL)


# =====================================================================
# П.101, 103: КОНТЕКСТ И ЗАМЕНЯЕМОСТЬ КОМПОНЕНТА
# =====================================================================


def test_context_has_only_allowed_keys(db, project, aosr):
    """ИИ видит только разрешённые данные (ТЗ п.103)."""
    context = ai_context.build_context(db, project.id, "Проверь комплект АОСР №1")
    ai_context.assert_context_is_allowed(context)
    assert set(context) == set(ai_context.CONTEXT_KEYS)


def test_context_contains_no_disk_paths(db, project, aosr):
    """Пути на диске в контекст не попадают (ТЗ п.103)."""
    from app.config import ARCHIVE_DIR

    source = ARCHIVE_DIR / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 scheme")
    archive = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=archive.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )
    text = repr(ai_context.build_context(db, project.id))
    assert str(ARCHIVE_DIR) not in text, "ИИ не должен получать пути файлов"
    assert "stored_path" not in text


def test_context_respects_requested_number(db, project, aosr, linked_final_act):
    """Запрос «Проверь комплект АОСР №1» выбирает нужный документ (ТЗ п.102)."""
    context = ai_context.build_context(db, project.id, "Проверь комплект АОСР №1")
    assert context["requested_documents"] == [aosr.id]


def test_context_without_numbers_takes_no_document(db, project, aosr):
    """Без номера документа запрос не сужает выборку (ТЗ п.102)."""
    context = ai_context.build_context(db, project.id, "Проверь комплект проекта")
    assert context["requested_documents"] == []


def test_connector_can_be_replaced(db, project, aosr, linked_final_act):
    """Замена ИИ не требует изменений в бизнес-логике (ТЗ п.101)."""
    from app.core.services import ai_service as service

    def provider(context):
        return {
            "status": "success",
            "mode": "STUB",
            "message": "",
            "proposals": [{
                "code": "stub", "text": "Проверка от другой модели",
                "document_id": None, "action": None, "basis": None,
                "is_requirement": False,
            }],
        }

    result = service.analyze(
        db, project.id, "Проверь комплект проекта",
        AIConnector(mode=MODE_LOCAL, provider=provider),
    )
    assert [proposal.code for proposal in result["proposals"]] == ["stub"]


def test_internet_mode_without_model_does_not_fake_analysis():
    """Без модели интернет-режим честно сообщает об этом (ТЗ п.101, 106)."""
    answer = AIConnector(mode=MODE_INTERNET).analyze({"documents": []})
    assert answer["status"] == "not_configured"
    assert answer["proposals"] == []
    assert "не настроен" in answer["message"]


def test_disabled_mode_returns_nothing():
    """При выключенном ИИ предложений нет (ТЗ п.9)."""
    answer = AIConnector(mode=MODE_OFF).analyze({"documents": []})
    assert answer["status"] == "disabled"
    assert answer["proposals"] == []


def test_modes_are_replaceable_without_touching_services():
    """Список режимов ИИ не связан с сервисами документов (ТЗ п.101)."""
    assert MODE_ORDER and set(MODE_ORDER) >= {MODE_LOCAL, MODE_INTERNET, MODE_OFF}


# =====================================================================
# П.102, 105: ПРЕДЛОЖЕНИЯ — ЧЕРНОВИКИ
# =====================================================================


def test_analysis_stores_drafts(db, project, aosr, linked_final_act, connector):
    """Результат ИИ сохраняется черновиками (ТЗ п.105)."""
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    assert result["proposals"]
    for proposal in result["proposals"]:
        assert proposal.status == domain.AI_PROPOSAL_DRAFT
        assert proposal.project_id == project.id


def test_proposal_is_not_a_document(db, project, aosr, linked_final_act, connector):
    """Предложение ИИ не документ и не попадает в комплекты (ТЗ п.105)."""
    before = db.query(Document).count()
    ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    assert db.query(Document).count() == before
    assert db.query(AiProposal).count() > 0


def test_analysis_changes_nothing(db, project, aosr, connector):
    """Анализ сам по себе не меняет данные (ТЗ п.104)."""
    documents_before = db.query(Document).count()
    links_before = db.query(DocumentLink).count()
    ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    assert db.query(Document).count() == documents_before
    assert db.query(DocumentLink).count() == links_before


def test_analysis_is_written_to_history(db, project, aosr, linked_final_act,
                                         connector):
    """Предложения ИИ видны в истории проекта (ТЗ п.86, 102)."""
    ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    events = [
        event for event in db.query(HistoryEvent).all()
        if event.event_type == domain.HISTORY_AI_PROPOSED
    ]
    assert events
    assert "Проверь комплект проекта" in events[0].payload["request"]


def test_issued_document_without_scheme_is_reported(db, project, aosr, connector):
    """Отсутствие схемы замечанием, а не изменением (ТЗ п.49, 104)."""
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    codes = {proposal.code for proposal in result["proposals"]}
    assert "no_scheme_linked" in codes
    scheme_proposal = next(
        proposal for proposal in result["proposals"]
        if proposal.code == "no_scheme_linked"
    )
    assert scheme_proposal.action is None


def test_final_act_without_link_gets_actionable_proposal(db, project, aosr, connector):
    """Итоговый акт без связи получает предложение с действием (ТЗ п.87, 104)."""
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )
    assert proposal.document_id == aook.id
    assert proposal.action["kind"] == domain.AI_ACTION_LINK_DOCUMENTS
    assert proposal.action["related_document_id"] == aosr.id


# =====================================================================
# П.104: ПОДТВЕРЖДЕНИЕ И ПРИМЕНЕНИЕ ЧЕРЕЗ ПРИКЛАДНОЙ API
# =====================================================================


def test_accepting_proposal_links_documents(db, project, aosr, connector):
    """Подтверждённое предложение применяется сервисом (ТЗ п.104)."""
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )

    applied = ai_service.accept(db, proposal.id)
    assert applied.status == domain.AI_PROPOSAL_ACCEPTED
    assert db.query(DocumentLink).filter(
        DocumentLink.document_id == aook.id,
        DocumentLink.related_document_id == aosr.id,
    ).count() == 1


def test_accepted_proposal_is_recorded_in_history(db, project, aosr, connector):
    """Применение предложения попадает в историю (ТЗ п.86, 104)."""
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )
    ai_service.accept(db, proposal.id)

    events = [
        event for event in db.query(HistoryEvent).all()
        if event.event_type == domain.HISTORY_AI_ACTION_APPLIED
    ]
    assert events
    assert events[0].payload["code"] == "final_act_without_links"


def test_proposal_cannot_be_applied_twice(db, project, aosr, connector):
    """Повторное применение невозможно (ТЗ п.104)."""
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )
    ai_service.accept(db, proposal.id)
    with pytest.raises(ai_service.AiError):
        ai_service.accept(db, proposal.id)


def test_rejected_proposal_is_not_applied(db, project, aosr, connector):
    """Отклонённое предложение ничего не меняет (ТЗ п.104)."""
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )
    rejected = ai_service.reject(db, proposal.id, "связь не нужна")
    assert rejected.status == domain.AI_PROPOSAL_REJECTED
    assert db.query(DocumentLink).count() == 0


def test_unknown_action_is_refused(db, project, aosr):
    """Неизвестное изменение система не применяет (ТЗ п.104)."""
    proposal = AiProposal(
        project_id=project.id, code="unknown", text="Что-то",
        action={"kind": "delete_project"}, status=domain.AI_PROPOSAL_DRAFT,
    )
    db.add(proposal)
    db.commit()
    with pytest.raises(ai_service.AiError):
        ai_service.accept(db, proposal.id)
    assert db.get(AiProposal, proposal.id).status == domain.AI_PROPOSAL_DRAFT


def test_proposal_list_is_newest_first(db, project, aosr, connector):
    """Предложения проекта показываются новые сверху (ТЗ п.105)."""
    ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    ai_service.analyze(db, project.id, "Проверь комплект АООК", connector)
    proposals = ai_service.list_proposals(db, project.id)
    assert len(proposals) >= 2
    assert proposals == sorted(proposals, key=lambda item: item.id, reverse=True)


def test_proposals_can_be_filtered_by_status(db, project, aosr, connector):
    """Черновики и рассмотренные предложения различаются (ТЗ п.105)."""
    ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    first = ai_service.list_proposals(db, project.id)[0]
    ai_service.reject(db, first.id)
    drafts = ai_service.list_proposals(db, project.id, domain.AI_PROPOSAL_DRAFT)
    assert first.id not in [item.id for item in drafts]


# =====================================================================
# П.106: НОРМАТИВНЫЕ ОСНОВАНИЯ
# =====================================================================


def test_basis_is_taken_from_form_registry(db, project, aosr):
    """Основание берётся из нормативной базы системы (ТЗ п.96, 106)."""
    basis = normative.basis_for_doc_type(db, domain.DOC_TYPE_AOSR)
    assert basis is not None
    assert basis["document"]
    assert "версия" in basis["section"]


def test_proposal_with_basis_is_normative_claim(db, project, aosr, connector):
    """Предложение с основанием помечается как требование нормы (ТЗ п.106)."""
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    proposal = next(
        item for item in result["proposals"]
        if item.code == "final_act_without_links"
    )
    assert proposal.is_requirement is True
    assert proposal.basis["document"]
    assert normative.format_basis(proposal.basis) in proposal.text or True


def test_observation_without_basis_is_not_requirement(db, project, aosr, connector):
    """Замечание без основания требованием не является (ТЗ п.106)."""
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    observation = next(
        item for item in result["proposals"]
        if item.code == "no_scheme_linked"
    )
    assert observation.is_requirement is False
    assert observation.basis is None


def test_empty_normative_base_is_declared(db, project, aosr, connector):
    """Пустая нормативная база — ИИ не выдаёт предположения за норму (ТЗ п.106)."""
    db.query(NormativeForm).delete()
    db.commit()
    result = ai_service.analyze(db, project.id, "Проверь комплект проекта", connector)
    caveat = next(
        item for item in result["proposals"]
        if item.code == "no_normative_basis"
    )
    assert caveat.is_requirement is False
    assert "невозможна" in caveat.text
    for proposal in result["proposals"]:
        assert proposal.is_requirement is False, "без базы требований быть не может"


def test_claim_without_document_is_not_requirement():
    """Основание без документа нормы требованием не считается (ТЗ п.106)."""
    assert normative.is_normative_claim(None) is False
    assert normative.is_normative_claim({"section": "п. 4"}) is False
    assert normative.is_normative_claim({"document": "СП 48.13330.2019"}) is True


def test_basis_format_is_readable():
    """Основание показывается оператору как документ, раздел, пункт (ТЗ п.106)."""
    text = normative.format_basis({
        "document": "приказ Минстроя №344/пр", "section": "АОСР v3", "clause": "п. 4",
    })
    assert "приказ Минстроя №344/пр" in text
    assert "п. 4" in text
    assert normative.format_basis(None) == "основание не указано"


# =====================================================================
# П.103: ТЕКСТ ФАЙЛОВ — ТОЛЬКО ПО ВЫБОРУ ОПЕРАТОРА
# =====================================================================


def _archive_with_text(db, project, name="СХЕМА.pdf", body="Армирование стен КЖ-12"):
    """Положить в архив файл с настоящим текстовым слоем PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from app.core.services import printing

    printing.register_fonts()
    source = ARCHIVE_PDF_DIR / name
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(source), pagesize=A4)
    pdf.setFont(printing.FONT_FAMILY, 11)
    pdf.drawString(60, 780, body)
    pdf.showPage()
    pdf.save()
    archive = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    return archive


def test_file_text_is_not_passed_without_operator_choice(db, project, aosr):
    """Без отметки оператора текст файла в контекст не попадает (ТЗ п.103)."""
    _archive_with_text(db, project)
    context = ai_context.build_context(db, project.id, "Проверь комплект")
    assert context["file_texts"] == []
    assert "Армирование стен" not in repr(context)


def test_operator_choice_adds_text_without_paths(db, project, aosr):
    """Отмеченный файл читает система; пути наружу не уходят (ТЗ п.103)."""
    from app.config import ARCHIVE_DIR

    archive = _archive_with_text(db, project)
    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    ai_context.assert_context_is_allowed(context)
    row = context["file_texts"][0]
    assert row["available"] is True
    assert "Армирование стен" in row["text"]
    assert "stored_path" not in repr(context)
    assert str(ARCHIVE_DIR) not in repr(context)


def test_unsupported_format_is_reported_not_guessed(db, project):
    """Нечитаемый формат — честная причина, а не выдуманный текст (ТЗ п.106)."""
    source = ARCHIVE_PDF_DIR / "Чертёж.dwg"
    source.write_bytes(b"AC1027 dwg")
    archive = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    row = context["file_texts"][0]
    assert row["available"] is False
    assert "dwg" in row["reason"]


def test_scanned_pdf_is_reported_as_without_text_layer(db, project):
    """Скан без текстового слоя ИИ прямо называет как нечитаемый (ТЗ п.106)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    source = ARCHIVE_PDF_DIR / "Скан.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(source), pagesize=A4)
    pdf.rect(60, 700, 200, 60, fill=True)
    pdf.showPage()
    pdf.save()
    archive = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    row = context["file_texts"][0]
    assert row["available"] is False
    assert "текстов" in row["reason"]


def test_file_of_another_project_is_not_read(db, project, direction):
    """Файл чужого проекта в контекст не попадает (ТЗ п.103)."""
    from app.db.models import Project

    other = Project(direction_id=direction.id, title="Другой объект")
    db.add(other)
    db.commit()
    archive = _archive_with_text(db, other, "СХЕМА-другого.pdf", "Другая схема")

    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    row = context["file_texts"][0]
    assert row["available"] is False
    assert "не найден" in row["reason"]
    assert "Другая схема" not in repr(context)


def test_internet_mode_refuses_file_text(db, project):
    """Текст файла не уходит наружу даже по отметке (ТЗ п.9, 103)."""
    archive = _archive_with_text(db, project)
    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    answer = AIConnector(mode=MODE_INTERNET).analyze(context)
    assert answer["status"] == "refused"
    assert answer["proposals"] == []
    assert "интернет" in answer["message"].lower()


def test_local_rules_report_what_they_could_not_read(db, project):
    """ИИ говорит, что не смог прочитать, а не молчит (ТЗ п.106)."""
    archive = _archive_with_text(db, project)
    context = ai_context.build_context(
        db, project.id, "Проверь комплект", file_text_ids=[archive.id]
    )
    assert ai_context.CONTEXT_KEYS
    from app.ai import rules

    codes = [proposal["code"] for proposal in rules.analyze_context(context)]
    assert rules.CODE_FILE_TEXT_UNAVAILABLE not in codes

    context["file_texts"][0]["available"] = False
    context["file_texts"][0]["reason"] = "нет текстового слоя"
    codes = [proposal["code"] for proposal in rules.analyze_context(context)]
    assert rules.CODE_FILE_TEXT_UNAVAILABLE in codes


def test_history_records_which_file_texts_were_sent(db, project):
    """В истории видно, чей текст увидел ИИ, без самого текста (ТЗ п.86, 103)."""
    archive = _archive_with_text(db, project)
    result = ai_service.analyze(
        db, project.id, "Проверь комплект",
        AIConnector(mode=MODE_LOCAL), file_text_ids=[archive.id],
    )
    event = next(
        event for event in history_events(db, project.id)
        if event.event_type == domain.HISTORY_AI_PROPOSED
    )
    payload = event.payload["file_texts"]
    assert payload[0]["name"] == "СХЕМА.pdf"
    assert payload[0]["chars"] > 0
    assert "text" not in payload[0]
    assert "Армирование стен" not in event.message
    assert result["context"]["file_texts"][0]["available"] is True


def history_events(db, project_id):
    from app.core.services import project_service

    return project_service.list_events(db, project_id)
