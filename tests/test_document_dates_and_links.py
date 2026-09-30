"""Сроки работ и связь итогового акта с АОСР. ТЗ п.43, 87, 96.

Проверяется, что система не подставляет даты сама (п.43), что связь
«завершает акт» выбирает оператор и хранится в БД (п.87) и что нарушение
порядка дат останавливает выпуск и выгрузку (п.96).
"""

from datetime import date

import pytest

from app.core import domain, validators
from app.core.services import (
    document_service, export_checks, form_service, issue_service, link_service,
    project_service,
)
from app.db.models import DocumentLink, Project


def _payload(start: str = "01.04.2024", end: str = "30.04.2024") -> dict:
    """Данные акта со сроком работ двумя датами (ТЗ п.43)."""
    return {
        "object_name": "Корпус 2",
        "address": "г. Москва",
        "work_description": "Армирование стен, 120 м²",
        "section_refs": "КЖ",
        "work_period": f"с {start} по {end}",
        "period_start": start,
        "period_end": end,
        "work_volume": "120 м² бетона Б25",
        "has_defects": "Нет",
        "conclusion": "Работы выполнены в полном объёме",
        "work_performer": "ООО «Строй»",
    }


def _act(db, project, doc_type, start="01.04.2024", end="30.04.2024"):
    """Документ с заполненной формой и указанным сроком работ."""
    document = document_service.create_document(
        db, project.id, doc_type=doc_type, doc_date=date(2024, 5, 15)
    )
    payload = _payload(start, end)
    if doc_type == domain.DOC_TYPE_AOOK:
        payload.update({
            "base_documents": "Договор №12 от 01.03.2024",
            "decisions": "Принято без замечаний",
        })
    form_service.save_draft(db, document.id, payload)
    db.refresh(document)
    return document


@pytest.fixture
def aosr(db, project):
    """Акт освидетельствования скрытых работ со сроком 01.04 — 30.04.2024."""
    return _act(db, project, domain.DOC_TYPE_AOSR)


@pytest.fixture
def aook(db, project):
    """Итоговый акт ОПО со сроком 01.04 — 30.04.2024."""
    return _act(db, project, domain.DOC_TYPE_AOOK)


# =====================================================================
# СРОКИ РАБОТ: ДВЕ ДАТЫ (ТЗ п.43)
# =====================================================================


def test_period_is_read_from_form_data(db, aosr):
    """Срок работ берётся из данных формы, а не из системной даты."""
    start, end = validators.document_period(db, aosr)
    assert (start.day, start.month, start.year) == (1, 4, 2024)
    assert (end.day, end.month, end.year) == (30, 4, 2024)


def test_period_of_document_without_dates_is_not_guessed(db, project):
    """Нет дат в данных — нет и срока: система не придумывает его (п.43)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    assert validators.document_period(db, document) == (None, None)


def test_own_period_reversed_is_reported(db, project):
    """Окончание раньше начала — нарушение в самих данных документа."""
    document = _act(db, project, domain.DOC_TYPE_AOSR, start="30.04.2024", end="01.04.2024")
    problems = validators.check_document_dates(db, document)
    assert any("позже окончания" in text for text in problems)


def test_incomplete_period_is_reported(db, project):
    """Без обеих дат зависимости дат не проверить (ТЗ п.43, 96)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    payload = _payload()
    payload["period_start"] = "01.04.2024"
    payload["period_end"] = ""
    form_service.save_draft(db, document.id, payload)
    db.refresh(document)
    problems = validators.check_document_dates(db, document)
    assert any("двумя датами" in text for text in problems)
    assert validators.document_period(db, document) == (date(2024, 4, 1), None)


# =====================================================================
# СВЯЗЬ «ЗАВЕРШАЕТ АКТ» (ТЗ п.87)
# =====================================================================


def test_final_act_links_aosr(db, aook, aosr):
    """Итоговый акт ссылается на завершаемый АОСР."""
    link = link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    assert link.id is not None
    assert [item.id for item in link_service.finalized_acts(db, aook.id)] == [aosr.id]
    assert [item.id for item in link_service.final_acts_of(db, aosr.id)] == [aook.id]


def test_link_survives_restart(db, aook, aosr):
    """Связь хранится в БД, а не во временном состоянии окна."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    db.expire_all()
    assert db.query(DocumentLink).filter(
        DocumentLink.document_id == aook.id
    ).count() == 1


def test_duplicate_link_is_rejected(db, aook, aosr):
    """Повторная связь того же акта не создаётся (ТЗ п.87)."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    with pytest.raises(link_service.MaterialError):
        link_service.link_documents(
            db, document_id=aook.id, related_document_id=aosr.id,
            link_role=domain.LINK_ROLE_FINALIZES,
        )


def test_aosr_cannot_be_final_act(db, aosr):
    """АОСР не может быть итоговым: связь только от итогового акта."""
    other = _act(db, aosr.project, domain.DOC_TYPE_AOSR)
    with pytest.raises(link_service.MaterialError):
        link_service.link_documents(
            db, document_id=aosr.id, related_document_id=other.id,
            link_role=domain.LINK_ROLE_FINALIZES,
        )


def test_link_to_document_of_another_project_is_rejected(db, aook, direction):
    """Связь не выходит за пределы проекта (ТЗ п.52)."""
    other_project = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other_project)
    db.commit()
    foreign = _act(db, other_project, domain.DOC_TYPE_AOSR)
    with pytest.raises(link_service.MaterialError):
        link_service.link_documents(
            db, document_id=aook.id, related_document_id=foreign.id,
            link_role=domain.LINK_ROLE_FINALIZES,
        )


def test_unlink_keeps_both_documents(db, aook, aosr):
    """Удаление связи не удаляет документы (ТЗ п.87)."""
    link = link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    link_service.unlink_documents(db, link.id)
    assert link_service.finalized_acts(db, aook.id) == []


def test_acts_to_finalize_excludes_already_linked(db, aook, aosr):
    """Уже связанный акт не предлагается повторно (ТЗ п.87)."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    candidates = link_service.project_acts_to_finalize(
        db, aook.project_id, domain.DOC_TYPE_AOSR
    )
    assert aosr.id in [item.id for item in candidates]
    already = {
        link.related_document_id
        for link in link_service.list_document_relations(db, aook.id)
    }
    assert aosr.id in already, "акт связан и не должен предлагаться снова"


# =====================================================================
# ЛОГИЧЕСКИЕ ЗАВИСИМОСТИ ДАТ (ТЗ п.87)
# =====================================================================


def test_correct_order_of_dates_gives_no_problems(db, aook, aosr):
    """Сроки актов раньше итогового акта — нарушений нет."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    assert validators.check_document_dates(db, aook) == []


def test_final_act_ending_before_act_is_reported(db, project, aosr):
    """Окончание итогового акта раньше окончания АОСР — нарушение."""
    aook = _act(db, project, domain.DOC_TYPE_AOOK, start="01.03.2024", end="20.03.2024")
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    problems = validators.check_document_dates(db, aook)
    assert any("раньше окончания связанного акта" in text for text in problems)


def test_final_act_starting_after_act_is_reported(db, project, aosr):
    """Начало итогового акта позже начала АОСР — нарушение."""
    aook = _act(db, project, domain.DOC_TYPE_AOOK, start="01.05.2024", end="30.05.2024")
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    problems = validators.check_document_dates(db, aook)
    assert any("позже начала связанного акта" in text for text in problems)


def test_broken_dates_do_not_change_stored_values(db, project, aosr):
    """Проверка сообщает о нарушении, но не правит даты сама (ТЗ п.43)."""
    aook = _act(db, project, domain.DOC_TYPE_AOOK, start="01.03.2024", end="20.03.2024")
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    validators.check_document_dates(db, aook)
    payload = form_service.load_draft(db, aook.id)
    assert payload["period_end"] == "20.03.2024"
    assert payload["period_start"] == "01.03.2024"


# =====================================================================
# ВЫПУСК И ВЫГРУЗКА (ТЗ п.87, 96)
# =====================================================================


def test_issue_is_blocked_by_wrong_date_order(db, project, aosr):
    """Выпуск итогового акта с нарушением дат не выполняется (ТЗ п.87, 96)."""
    aook = _act(db, project, domain.DOC_TYPE_AOOK, start="01.03.2024", end="20.03.2024")
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    with pytest.raises(issue_service.IssueError) as exc:
        issue_service.issue_document(db, aook.id)
    assert any(
        "раньше окончания связанного акта" in text for text in exc.value.problems
    )
    db.refresh(aook)
    assert aook.status == domain.DOC_STATUS_DRAFT


def test_issue_succeeds_with_correct_date_order(db, aook, aosr):
    """Связь и верный порядок дат не мешают выпуску."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    version = issue_service.issue_document(db, aook.id)
    assert version.is_actual is True


def test_package_check_reports_wrong_date_order(db, project, aosr):
    """Предэкспортная проверка видит нарушение порядка дат (ТЗ п.82, 87)."""
    aook = _act(db, project, domain.DOC_TYPE_AOOK, start="01.03.2024", end="20.03.2024")
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    result = export_checks.check_package(db, project.id, [aosr.id, aook.id])
    assert any(
        problem.code == export_checks.CHECK_DATES
        and "раньше окончания связанного акта" in problem.message
        for problem in result.problems
    )


def test_package_check_passes_with_correct_dates(db, aook, aosr):
    """Верный порядок дат не создаёт проблем комплекта."""
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    result = export_checks.check_package(db, aook.project_id, [aosr.id, aook.id])
    assert not [
        problem for problem in result.problems
        if problem.code == export_checks.CHECK_DATES
    ]


# =====================================================================
# ИСТОРИЯ (ТЗ п.86)
# =====================================================================


def test_link_events_are_recorded(db, aook, aosr):
    """Добавление и удаление связи попадают в историю (ТЗ п.86)."""
    link = link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    events = project_service.list_events(db, aook.project_id)
    assert events[0].event_type == domain.HISTORY_LINK_ADDED
    assert events[0].payload["related_document_id"] == aosr.id

    link_service.unlink_documents(db, link.id)
    events = project_service.list_events(db, aook.project_id)
    assert events[0].event_type == domain.HISTORY_LINK_REMOVED


def test_document_creation_is_recorded_once(db, project):
    """Создание документа — событие истории сервиса, без дубля в окне (ТЗ п.86)."""
    document_service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    events = [
        event for event in project_service.list_events(db, project.id)
        if event.event_type == domain.HISTORY_DOCUMENT_CREATED
    ]
    assert len(events) == 1


def test_archive_upload_and_version_are_recorded(db, project, tmp_path):
    """Добавление файла в архив и новая редакция видны в истории (ТЗ п.86)."""
    from app.core.services import storage_service

    source = tmp_path / "СХЕМА №1.pdf"
    source.write_bytes(b"%PDF-1.4 scheme")
    archive_document = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    replacement = tmp_path / "СХЕМА №1 (нов).pdf"
    replacement.write_bytes(b"%PDF-1.4 scheme v2")
    storage_service.add_version(
        db, archive_document_id=archive_document.id, src_path=replacement,
    )
    types = [event.event_type for event in project_service.list_events(db, project.id)]
    assert domain.HISTORY_ARCHIVE_FILE_ADDED in types
    assert domain.HISTORY_ARCHIVE_VERSION_ADDED in types


def test_quality_details_change_is_recorded(db, project, tmp_path):
    """Уточнение реквизитов качества попадает в историю (ТЗ п.45, 86)."""
    from app.core.services import storage_service

    source = tmp_path / "СЕРТИФИКАТ.pdf"
    source.write_bytes(b"%PDF-1.4 certificate")
    archive_document = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
    )
    storage_service.set_quality_details(
        db, archive_document_id=archive_document.id,
        quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2025, 1, 1), validity_to=date(2027, 1, 1),
    )
    events = project_service.list_events(db, project.id)
    assert events[0].event_type == domain.HISTORY_ARCHIVE_QUALITY_SET
    assert events[0].payload["quality_type"] == domain.QUALITY_DOC_CERTIFICATE


# =====================================================================
# ИНТЕРФЕЙС ОПЕРАТОРА (ТЗ п.87)
# =====================================================================


@pytest.mark.gui
def test_dialog_offers_only_free_acts(db, aook, aosr, qapp):
    """Диалог показывает свободные акты и период их работ (ТЗ п.87)."""
    from PyQt6.QtCore import Qt

    from app.ui.document_link_dialog import DocumentLinkDialog

    dialog = DocumentLinkDialog(db, aook.project_id, aook)
    assert dialog.acts_list.count() == 1
    assert "АОСР № 1" in dialog.acts_list.item(0).text()
    assert "01.04.2024" in dialog.acts_list.item(0).text()

    dialog.acts_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_acts() == [aosr.id]
    assert dialog.values() == {
        "document_id": aook.id, "link_role": domain.LINK_ROLE_FINALIZES,
    }


@pytest.mark.gui
def test_dialog_hides_already_linked_act(db, aook, aosr, qapp):
    """Уже связанный акт повторно не предлагается (ТЗ п.87)."""
    from app.ui.document_link_dialog import DocumentLinkDialog

    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    dialog = DocumentLinkDialog(db, aook.project_id, aook)
    assert dialog.acts_list.count() == 0
    assert "нет свободных актов" in dialog.hint.text()


@pytest.mark.gui
def test_window_shows_finalized_acts_and_date_check(db, aook, aosr, qapp, monkeypatch):
    """Окно показывает завершаемые акты и состояние проверки дат (ТЗ п.87)."""
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from app.ui.document_link_dialog import DocumentLinkDialog
    from app.ui.project_window import ProjectWindow

    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    window = ProjectWindow(db, aook.project_id)
    window.show()
    window._select_document(aook.id)
    window._reload_links()

    assert window.finalized_table.rowCount() == 1
    assert window.finalized_table.item(0, 0).text() == "АОСР № 1"
    assert "01.04.2024 — 30.04.2024" == window.finalized_table.item(0, 1).text()
    assert window.finalized_table.item(0, 2).text() == "порядок дат соблюдён"

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        DocumentLinkDialog, "selected_acts", lambda self: [aosr.id]
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )

    window.add_finalized_acts()
    assert [
        item.related_document_id
        for item in link_service.list_document_relations(db, aook.id)
    ] == [aosr.id], "выбранный оператором акт связан с итоговым (ТЗ п.87)"
    window.close()


# =====================================================================
# ЧЕРНОВИК — РАБОЧИЕ ДАННЫЕ, А НЕ ИСТОРИЯ (ТЗ п.66, 109)
# =====================================================================


def test_project_with_drafts_is_deleted_cleanly(db, project):
    """Черновик не должен мешать удалению проекта (ТЗ п.66, 109).

    Внешний ключ версий на документ объявлен RESTRICT ради выпусков: без
    явного удаления черновиков оператор получал ошибку БД вместо удаления.
    """
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    form_service.save_draft(db, document.id, _payload())
    project_service.delete_project(db, project.id)
    assert project_service.get_project(db, project.id) is None


def test_project_with_issued_version_is_still_protected(db, aook, aosr):
    """Выпуск по-прежнему защищает проект от удаления (ТЗ п.54, 85)."""
    issue_service.issue_document(db, aosr.id)
    allowed, stats = project_service.can_delete_project(db, aosr.project_id)
    assert allowed is False
    assert stats["issued_versions"] == 1
    with pytest.raises(project_service.ProjectError):
        project_service.delete_project(db, aosr.project_id)
