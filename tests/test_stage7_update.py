"""Обновление программы не уничтожает данные (ТЗ п.97).

Изменение структуры базы выполняется только через миграцию, а перед
обновлением снимается резервная копия (ТЗ п.98).
"""

from datetime import date
from pathlib import Path

import pytest
import sqlalchemy

from app.core import domain
from app.config import BACKUP_DIR, PACKAGES_DIR
from app.core import validators
from app.core.services import (
    backup_service, document_service, form_service, issue_service, link_service,
    package_service, storage_service,
)
from app.db import migrations
from app.db.models import (
    ArchiveDocument, ArchiveFileVersion, Document, DocumentArchiveLink, DocumentLink,
    DocumentVersion, HistoryEvent, MaterialTestActLink, Package, PackageEntry, Project,
)

PREVIOUS_SCHEMA_VERSION = 5


@pytest.fixture
def previous_release(db, project):
    """База предыдущей версии программы: проект, документы, архив, комплект."""
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

    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1"
    )
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )

    from app.config import ARCHIVE_DIR

    source = ARCHIVE_DIR / "Журнал №2.pdf"
    source.write_bytes(b"%PDF-1.4 journal 2")
    archive_doc = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=archive_doc.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    package_service.create_package(
        db, project.id, base_dir=PACKAGES_DIR, allow_errors=True
    )

    # Возвращаем базу к прежней версии: таблица следующей миграции ещё нет.
    MaterialTestActLink.__table__.drop(db.get_bind())
    raw = db.get_bind().raw_connection()
    try:
        migrations.set_user_version(raw, PREVIOUS_SCHEMA_VERSION)
        raw.commit()
    finally:
        raw.close()
    db.commit()
    return project


def _counts(db) -> dict:
    return {
        "projects": db.query(Project).count(),
        "documents": db.query(Document).count(),
        "archive_documents": db.query(ArchiveDocument).count(),
        "archive_file_versions": db.query(ArchiveFileVersion).count(),
        "archive_links": db.query(DocumentArchiveLink).count(),
        "document_links": db.query(DocumentLink).count(),
        "versions": db.query(DocumentVersion).count(),
        "packages": db.query(Package).count(),
        "package_entries": db.query(PackageEntry).count(),
        "events": db.query(HistoryEvent).count(),
    }


def test_update_applies_pending_migration(db, previous_release):
    """Структура базы меняется только через миграцию (ТЗ п.97)."""
    engine = db.get_bind()
    assert migrations.pending_for_engine(engine) == [
        "006 material_test_act_links"
    ]
    applied = migrations.apply_migrations(engine)
    assert applied == ["material_test_act_links"]
    assert migrations.get_user_version(
        engine.raw_connection().driver_connection
    ) == migrations.SCHEMA_VERSION


def test_update_keeps_projects_documents_and_versions(db, previous_release):
    """Проекты, документы и версии переживают обновление (ТЗ п.97)."""
    before = _counts(db)
    migrations.apply_migrations(db.get_bind())

    db.expire_all()
    assert _counts(db) == before
    document = db.query(Document).filter(
        Document.doc_type == domain.DOC_TYPE_AOSR
    ).one()
    assert document.status == domain.DOC_STATUS_ISSUED
    assert validators.document_period(db, document) == (
        date(2024, 4, 1), date(2024, 4, 30)
    )
    versions = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document.id
    ).all()
    assert versions and versions[0].issued_at is not None


def test_update_keeps_archive_and_links(db, previous_release):
    """Архив, связи и файлы переживают обновление (ТЗ п.97)."""
    before = _counts(db)
    migrations.apply_migrations(db.get_bind())

    db.expire_all()
    assert _counts(db) == before
    link = db.query(DocumentArchiveLink).one()
    version = db.query(ArchiveFileVersion).filter(
        ArchiveFileVersion.id == link.archive_version_id
    ).one()
    assert Path(version.stored_path).is_file()
    assert db.query(ArchiveDocument).one().original_name == "Журнал №2.pdf"


def test_update_keeps_history_and_packages(db, previous_release):
    """История и комплекты с реестром переживают обновление (ТЗ п.97)."""
    before = _counts(db)
    migrations.apply_migrations(db.get_bind())
    db.expire_all()
    assert _counts(db) == before
    package = db.query(Package).one()
    assert package.project_id == previous_release.id
    assert db.query(PackageEntry).filter(PackageEntry.package_id == package.id).count() >= 1
    assert db.query(HistoryEvent).count() >= 1


def test_update_keeps_database_consistent(db, previous_release):
    """После обновления внешние ключи целы (ТЗ п.54, 97)."""
    migrations.apply_migrations(db.get_bind())
    assert migrations.foreign_key_check(
        db.get_bind().raw_connection().driver_connection
    ) == []


def test_update_backup_is_taken_before_migration(db, previous_release):
    """Перед миграцией снимается копия прежнего состояния (ТЗ п.97, 98)."""
    safety = backup_service.create_update_backup(base_dir=BACKUP_DIR)
    problems = backup_service.verify_backup(safety)
    assert problems == [], problems
    assert (safety / "app.db").is_file()
    assert list((safety / "files").rglob("*.pdf"))
    manifest = backup_service.read_manifest(safety)
    assert manifest["counts"]["projects"] == 1
    assert manifest["counts"]["documents"] == 2
    assert manifest["counts"]["versions"] >= 1


def test_update_backup_can_restore_previous_release(db, previous_release, tmp_path):
    """Копия до обновления возвращает прежнее состояние целиком (ТЗ п.97, 98)."""
    safety = backup_service.create_update_backup(base_dir=BACKUP_DIR)
    migrations.apply_migrations(db.get_bind())

    target = tmp_path / "откат"
    (target / "internal_archive").mkdir(parents=True)
    backup_service.restore_backup(
        safety, db_path=target / "app.db", archive_dir=target / "internal_archive",
        settings_path=target / "settings.json", safety_dir=tmp_path / "safety",
    )
    raw = sqlalchemy.create_engine(f"sqlite:///{target / 'app.db'}")
    try:
        assert migrations.get_user_version(
            raw.raw_connection().driver_connection
        ) == PREVIOUS_SCHEMA_VERSION, "вернулась прежняя схема"
        assert migrations.foreign_key_check(
            raw.raw_connection().driver_connection
        ) == []
    finally:
        raw.dispose()


def test_window_reports_update_to_operator(qapp, db, project, monkeypatch):
    """Оператор видит, что база обновлена и где копия до обновления (ТЗ п.97)."""
    from app.ui.main_window import MainWindow

    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.pending_update_migrations",
        lambda: ["006 material_test_act_links"],
    )
    monkeypatch.setattr(
        "app.ui.main_window.init_db",
        lambda: {"migrations": ["material_test_act_links"], "pending_before": [
            "006 material_test_act_links"]},
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda parent, title, text: shown.update(title=title, text=text),
    )
    window = MainWindow()
    try:
        assert shown["title"] == "Обновление программы"
        assert "material_test_act_links" in shown["text"]
        assert "Копия до обновления" in shown["text"]
        assert window.update_report["update_backup"] is not None
    finally:
        window.close()


def test_window_without_updates_stays_quiet(qapp, db, project, monkeypatch):
    """Без миграций оператора не беспокоят (ТЗ п.97)."""
    from app.ui.main_window import MainWindow

    called = []
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: called.append(args),
    )
    window = MainWindow()
    try:
        assert called == []
        assert window.update_report["update_backup"] is None
    finally:
        window.close()
