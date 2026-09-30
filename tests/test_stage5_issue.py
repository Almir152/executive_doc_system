"""Выпуск документа, новые редакции, неизменность истории. ТЗ п.85, 96, 53, 91, 93.

Проверяется главное свойство выпуска: зафиксированная версия не меняется
вследствие последующего редактирования, а правки идут в новую версию.
"""

from datetime import date, timedelta

import pytest

from app.core import domain
from app.core.services import document_service, form_service, issue_service
from app.db.models import HistoryEvent


@pytest.fixture
def aosr(db, project):
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _complete_payload(**extra) -> dict:
    payload = {
        "object_name": "Корпус 2",
        "address": "г. Москва",
        "work_description": "Армирование стен, 120 м²",
        "section_refs": "КЖ",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "work_volume": "120 м² бетона Б25",
        "has_defects": "Нет",
        "conclusion": "Работы выполнены в полном объёме",
        "work_performer": "ООО «Строй»",
    }
    payload.update(extra)
    return payload


@pytest.fixture
def filled(db, aosr):
    form_service.save_draft(db, aosr.id, _complete_payload())
    return aosr


# =====================================================================
# ВЫПУСК (ТЗ п.85)
# =====================================================================

def test_issue_fixes_version_and_status(db, filled):
    version = issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))

    assert version.issued_at is not None
    assert filled.status == domain.DOC_STATUS_ISSUED
    assert filled.doc_date == date(2024, 5, 1)
    assert len(issue_service.issued_versions(db, filled.id)) == 1


def test_issue_records_history(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))

    events = db.query(HistoryEvent).filter(
        HistoryEvent.event_type == domain.HISTORY_DOCUMENT_ISSUED
    ).all()
    assert len(events) == 1
    assert str(filled.number) in events[0].message


def test_issue_refuses_incomplete_form(db, aosr):
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2"})

    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(db, aosr.id, doc_date=date(2024, 5, 1))

    assert exc.value.problems
    assert any("обязательное поле" in p for p in exc.value.problems)
    assert aosr.status == domain.DOC_STATUS_DRAFT
    assert issue_service.issued_versions(db, aosr.id) == []


def test_issue_refuses_empty_form(db, aosr):
    with pytest.raises(issue_service.IssueError):
        issue_service.issue_document(db, aosr.id, doc_date=date(2024, 5, 1))

    assert aosr.status == domain.DOC_STATUS_DRAFT


def test_issue_requires_date(db, filled):
    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(db, filled.id)

    assert any("Дата документа" in p for p in exc.value.problems)
    assert filled.status == domain.DOC_STATUS_DRAFT


def test_issue_keeps_date_entered_by_operator(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 3, 15))

    assert filled.doc_date == date(2024, 3, 15)


def test_issue_rejects_future_date(db, filled):
    tomorrow = date.today() + timedelta(days=1)

    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(db, filled.id, doc_date=tomorrow)

    assert any("позже сегодняшней" in p for p in exc.value.problems)
    assert filled.status == domain.DOC_STATUS_DRAFT


def test_issue_honours_extra_problems_from_package_checks(db, filled):
    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(
            db, filled.id,
            doc_date=date(2024, 5, 1),
            extra_problems=["Нет исполнительной схемы (ТЗ п.82)."],
        )

    assert any("схемы" in p for p in exc.value.problems)
    assert filled.status == domain.DOC_STATUS_DRAFT


def test_second_issue_is_refused(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))

    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 2))

    assert "уже выпущен" in str(exc.value)


def test_issue_of_unknown_document(db):
    with pytest.raises(issue_service.IssueError):
        issue_service.issue_document(db, 9999, doc_date=date(2024, 5, 1))


# =====================================================================
# ИСТОРИЧЕСКАЯ НЕИЗМЕННОСТЬ (ТЗ п.85, 91, 93)
# =====================================================================

def test_issued_version_survives_new_revision(db, filled):
    first = issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))
    frozen = dict(first.payload)

    second = issue_service.start_revision(db, filled.id)
    form_service.save_draft(db, filled.id, _complete_payload(conclusion="Исправлено"))

    db.refresh(first)
    assert dict(first.payload) == frozen
    assert first.issued_at is not None
    assert second.version_no == first.version_no + 1
    assert filled.status == domain.DOC_STATUS_DRAFT


def test_new_revision_keeps_issued_version_readable(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))
    issue_service.start_revision(db, filled.id)
    form_service.save_draft(db, filled.id, _complete_payload(address="г. Казань"))

    versions = issue_service.list_versions(db, filled.id)
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[0].payload["address"] == "г. Москва"
    assert versions[1].payload["address"] == "г. Казань"


def test_revision_continues_version_numbering_not_restart(db, filled):
    first = issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))
    issue_service.start_revision(db, filled.id)
    form_service.save_draft(db, filled.id, _complete_payload())
    second = issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 2))

    assert first.version_no == 1
    assert second.version_no == 2
    assert len(issue_service.issued_versions(db, filled.id)) == 2


def test_save_draft_after_issue_uses_next_version_number(db, filled):
    """Черновик новой редакции не должен попадать на занятый номер версии."""
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))
    version = issue_service.start_revision(db, filled.id)
    form_service.save_draft(db, filled.id, _complete_payload(notes="правка"))

    db.refresh(version)
    assert version.version_no == 2
    assert version.payload["notes"] == "правка"


def test_revision_of_draft_document_is_refused(db, filled):
    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.start_revision(db, filled.id)

    assert "не выпущен" in str(exc.value)


def test_saving_draft_of_issued_document_is_refused(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))

    with pytest.raises(form_service.FormError) as exc:
        form_service.save_draft(db, filled.id, _complete_payload(notes="правка"))

    assert "не изменяется" in str(exc.value)


def test_revision_requires_frozen_version_to_exist(db, filled):
    issue_service.issue_document(db, filled.id, doc_date=date(2024, 5, 1))
    db.query(type(filled.versions[0])).filter(
        type(filled.versions[0]).document_id == filled.id
    ).delete(synchronize_session=False)
    db.commit()

    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.start_revision(db, filled.id)

    assert "разбора" in str(exc.value)


def test_is_frozen_marks_issued_versions(db, filled):
    version = issue_service.issue_document(
        db, filled.id, doc_date=date(2024, 5, 1)
    )

    assert issue_service.is_frozen(version) is True
    assert issue_service.is_frozen(None) is False


def test_next_version_no_counts_existing_versions(db, aosr):
    """Номер версии продолжает последовательность, а не начинается с единицы.

    Зафиксированная версия занимает номер 1, поэтому следующая — 2: если бы
    счётчик начинался с единицы, новая версия столкнулась бы с ней.
    """
    assert issue_service.next_version_no(db, aosr.id) == 1

    form_service.save_draft(db, aosr.id, _complete_payload())
    issue_service.issue_document(db, aosr.id, doc_date=date(2024, 5, 1))

    assert issue_service.next_version_no(db, aosr.id) == 2

    issue_service.start_revision(db, aosr.id)
    assert issue_service.next_version_no(db, aosr.id) == 3
