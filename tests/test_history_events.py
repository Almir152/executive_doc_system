"""История проекта сохраняет результаты работы (ТЗ п.86).

Проверяются все пять пунктов требования: версии документов, изменения
архивных документов, сформированные комплекты, реестры и исторические PDF.
"""

from datetime import date

import pytest

from app.core import domain
from app.core.services import (
    document_service, form_service, issue_service, link_service, package_service,
    printing, project_service, storage_service,
)


@pytest.fixture
def aosr(db, project):
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
    return document


def _event_types(db, project_id) -> list[str]:
    return [event.event_type for event in project_service.list_events(db, project_id)]


# =====================================================================
# ВЕРСИИ ДОКУМЕНТОВ (ТЗ п.85, 86)
# =====================================================================


def test_version_and_revision_are_recorded(db, aosr):
    """Выпуск версии и новая редакция остаются в истории (ТЗ п.85, 86)."""
    issue_service.issue_document(db, aosr.id)
    issue_service.start_revision(db, aosr.id)
    types = _event_types(db, aosr.project_id)
    assert domain.HISTORY_DOCUMENT_ISSUED in types
    assert domain.HISTORY_DOCUMENT_REVISION in types


def test_document_card_change_is_recorded_by_service(db, aosr):
    """Правка реквизитов пишет историю в своей транзакции (ТЗ п.42, 86)."""
    document_service.update_document_card(db, aosr, doc_date=date(2024, 6, 1))
    event = project_service.list_events(db, aosr.project_id)[0]
    assert event.event_type == domain.HISTORY_DOCUMENT_UPDATED
    assert event.payload["doc_date"] == "2024-06-01"


def test_card_change_of_issued_number_is_rejected(db, aosr):
    """Номер выпущенного документа не меняется (ТЗ п.85)."""
    issue_service.issue_document(db, aosr.id)
    with pytest.raises(document_service.DocumentNumberError):
        document_service.update_document_card(db, aosr, number="9")
    db.refresh(aosr)
    assert aosr.number != "9"


# =====================================================================
# КОМПЛЕКТЫ И РЕЕСТРЫ (ТЗ п.75, 86)
# =====================================================================


@pytest.fixture
def package(db, project, aosr, tmp_path):
    issue_service.issue_document(db, aosr.id)
    return package_service.create_package(
        db, project.id, base_dir=tmp_path, allow_errors=True
    )


def test_package_and_register_are_separate_events(db, project, package):
    """Комплект и его реестр — две отдельные записи (ТЗ п.86)."""
    types = _event_types(db, project.id)
    assert domain.HISTORY_PACKAGE_EXPORTED in types
    assert domain.HISTORY_REGISTER_WRITTEN in types

    register = next(
        event for event in project_service.list_events(db, project.id)
        if event.event_type == domain.HISTORY_REGISTER_WRITTEN
    )
    assert register.payload["file_name"] == package_service.REGISTRY_FILE_NAME
    assert register.payload["rows"] >= 1


def test_register_counts_attachments(db, project, aosr, tmp_path):
    """В реестре видно число приложений (ТЗ п.75, 79)."""
    from app.core.services.storage_service import add_file_to_archive

    source = tmp_path / "СХЕМА №1.pdf"
    source.write_bytes(b"%PDF-1.4 scheme")
    scheme = add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_ATTACHMENT,
    )
    issue_service.issue_document(db, aosr.id)
    package_service.create_package(db, project.id, base_dir=tmp_path, allow_errors=True)

    register = next(
        event for event in project_service.list_events(db, project.id)
        if event.event_type == domain.HISTORY_REGISTER_WRITTEN
    )
    assert register.payload["attachments"] == 1


# =====================================================================
# ИСТОРИЧЕСКИЕ PDF (ТЗ п.86)
# =====================================================================


def test_saved_historical_pdf_is_recorded(db, aosr, tmp_path):
    """Сохранённый PDF выпущенного документа виден в истории (ТЗ п.86)."""
    issue_service.issue_document(db, aosr.id)
    target = tmp_path / "АОСР_1.pdf"
    printing.render_document_pdf(db, aosr, target, record_history=True)

    event = project_service.list_events(db, aosr.project_id)[0]
    assert event.event_type == domain.HISTORY_PDF_SAVED
    assert event.payload["issued"] is True
    assert event.payload["file_name"] == "АОСР_1.pdf"


def test_package_rendering_does_not_flood_history(db, project, package):
    """Сборка комплекта не пишет событие на каждый документ (ТЗ п.86)."""
    types = _event_types(db, project.id)
    assert types.count(domain.HISTORY_PDF_SAVED) == 0


def test_draft_pdf_is_marked_as_draft(db, aosr, tmp_path):
    """PDF черновика отличается от исторического (ТЗ п.86)."""
    printing.render_document_pdf(
        db, aosr, tmp_path / "АОСР_черновик.pdf", record_history=True
    )
    event = project_service.list_events(db, aosr.project_id)[0]
    assert event.payload["issued"] is False
    assert "черновика" in event.message


# =====================================================================
# ИЗМЕНЕНИЯ АРХИВНЫХ ДОКУМЕНТОВ (ТЗ п.86, 92)
# =====================================================================


def test_archive_changes_are_recorded(db, project, tmp_path):
    """Загрузка файла и новая редакция архива — события истории (ТЗ п.86)."""
    source = tmp_path / "ЖУРНАЛ.pdf"
    source.write_bytes(b"%PDF-1.4 journal")
    archive_document = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_PROTOCOLS,
    )
    replacement = tmp_path / "ЖУРНАЛ (2).pdf"
    replacement.write_bytes(b"%PDF-1.4 journal v2")
    storage_service.add_version(
        db, archive_document_id=archive_document.id, src_path=replacement
    )
    types = _event_types(db, project.id)
    assert domain.HISTORY_ARCHIVE_FILE_ADDED in types
    assert domain.HISTORY_ARCHIVE_VERSION_ADDED in types


@pytest.mark.gui
def test_window_print_records_pdf_in_history(db, aosr, qapp, monkeypatch, tmp_path):
    """Печать формы из окна оставляет след в истории (ТЗ п.86)."""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from app.ui.project_window import ProjectWindow

    issue_service.issue_document(db, aosr.id)
    target = tmp_path / "печать.pdf"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    # Просмотрщик в тесте не нужен: PDF уже сохранён на диск (ТЗ п.55).
    monkeypatch.setattr("app.ui.project_window._open_pdf", lambda p, w: None)
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )

    window = ProjectWindow(db, aosr.project_id)
    window.show()
    window._select_document(aosr.id)
    window.print_form()

    event = project_service.list_events(db, aosr.project_id)[0]
    assert event.event_type == domain.HISTORY_PDF_SAVED
    assert target.exists()
    window.close()
