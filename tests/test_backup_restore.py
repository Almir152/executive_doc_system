"""Резервная копия, восстановление и перенос. ТЗ п.74, 98.

Копия должна позволять восстановить проект на другом компьютере: в неё
входят база, физические файлы, связи, версии, история и настройки, а
комплекты пользователя — нет (ТЗ п.72, 74).
"""

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app.core import domain
from app.core.services import (
    backup_service, document_service, form_service, issue_service, link_service,
    storage_service,
)


@pytest.fixture
def material_type(db):
    from app.db.models import MaterialType

    item = MaterialType(code="BETON", name="Бетон")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def live_storage(tmp_path):
    """Живое хранилище тестовой сессии (то же, что у оператора).

    Копии складываются во временную папку, а восстановление проверяется на
    отдельном «другом компьютере» — рабочая база тестов не страдает.
    """
    from app.config import ARCHIVE_DIR, DB_PATH, SETTINGS_PATH

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.is_file():
        SETTINGS_PATH.write_text(
            '{"font": "Times New Roman", "font_size": 11}', encoding="utf-8"
        )
    return {
        "db_path": DB_PATH,
        "archive_dir": ARCHIVE_DIR,
        "settings_path": SETTINGS_PATH,
        "backup_dir": tmp_path / "backups",
    }


@pytest.fixture
def populated(db, live_storage, project):
    """Проект с документами, выпуском, связями и файлом архива (ТЗ п.98)."""
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

    source = live_storage["archive_dir"] / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 scheme 12")
    scheme = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    material_type = _material_type(db)
    material = link_service.save_material(
        db, project.id, name="Бетон Б25", material_type_id=material_type.id,
    )
    test_act = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="1"
    )
    link_service.link_material_to_test_act(db, material.id, test_act.id)
    return live_storage


def _material_type(session):
    """Тип материала: справочник мог быть уже наполнен (ТЗ п.44)."""
    from app.db.models import MaterialType

    item = session.query(MaterialType).filter(MaterialType.code == "BETON").first()
    if item is None:
        item = MaterialType(code="BETON", name="Бетон")
        session.add(item)
        session.commit()
        session.refresh(item)
    return item


def _open_session(live_storage):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{live_storage['db_path']}")
    return sessionmaker(bind=engine)()


# =====================================================================
# СОСТАВ КОПИИ (ТЗ п.74, 98)
# =====================================================================


def test_backup_contains_database_files_and_settings(db, populated):
    """В копии есть база, файлы архива и настройки (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    assert (folder / "app.db").is_file()
    assert (folder / "manifest.json").is_file()
    assert (folder / "settings.json").is_file()
    assert list((folder / "files").rglob("*.pdf")), "файлы архива не скопированы"


def test_backup_manifest_counts_relations_versions_history(db, populated):
    """Манифест показывает связи, версии и историю (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    counts = backup_service.read_manifest(folder)["counts"]
    assert counts["projects"] == 1
    assert counts["documents"] == 2
    assert counts["versions"] >= 1
    assert counts["events"] >= 1
    assert counts["archive_links"] == 1
    assert counts["material_links"] == 1


def test_backup_refuses_unsaved_changes(db, populated, project):
    """Копия снимается только из сохранённого состояния (ТЗ п.86, 98)."""
    project.title = "Правка без сохранения"
    with pytest.raises(backup_service.BackupError):
        backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                     db_path=populated["db_path"],
                                     archive_dir=populated["archive_dir"],
                                     settings_path=populated["settings_path"])
    assert backup_service.list_backups(populated["backup_dir"]) == []


def test_backup_does_not_mix_packages(db, populated):
    """Пользовательские комплекты в копию резервного назначения не входят."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    manifest = backup_service.read_manifest(folder)
    assert manifest["packages_included"] is False
    assert not list(folder.rglob("packages"))


def test_backup_is_a_separate_folder_from_packages(db, populated):
    """Копия — отдельная папка, а не комплект (ТЗ п.74)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    assert folder.parent == populated["backup_dir"]
    assert folder.name.startswith("Резервная копия")


def test_each_backup_gets_its_own_folder(db, populated):
    """Прежние копии не перезаписываются (ТЗ п.74)."""
    first = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                         db_path=populated["db_path"],
                                         archive_dir=populated["archive_dir"],
                                         settings_path=populated["settings_path"])
    second = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    assert first != second
    assert sorted(item["name"] for item in backup_service.list_backups(
        populated["backup_dir"]
    )) == ["Резервная копия 01", "Резервная копия 02"]


def test_backup_refuses_when_archive_file_is_missing(db, populated):
    """Пропавший файл архива делает копию неполной (ТЗ п.98)."""
    for path in populated["archive_dir"].rglob("*.pdf"):
        path.unlink()
    with pytest.raises(backup_service.BackupError):
        backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                     db_path=populated["db_path"],
                                     archive_dir=populated["archive_dir"],
                                     settings_path=populated["settings_path"])


def test_backup_does_not_touch_live_data(db, populated):
    """Создание копии не меняет рабочую систему (ТЗ п.74)."""
    before = populated["db_path"].read_bytes()
    backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                 db_path=populated["db_path"],
                                 archive_dir=populated["archive_dir"],
                                 settings_path=populated["settings_path"])
    assert populated["db_path"].read_bytes() == before
    assert list(populated["archive_dir"].rglob("*.pdf"))


# =====================================================================
# ПРОВЕРКА КОПИИ (ТЗ п.98)
# =====================================================================


def test_verified_backup_has_no_problems(db, populated):
    """Свежая копия проходит проверку (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    assert backup_service.verify_backup(folder) == []


def test_changed_database_is_detected(db, populated):
    """Изменённая база обнаруживается по контрольной сумме (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    with sqlite3.connect(str(folder / "app.db")) as raw:
        raw.execute("UPDATE projects SET title = 'Подмена'")
    problems = backup_service.verify_backup(folder)
    assert any("Контрольная сумма базы" in problem for problem in problems)


def test_changed_file_is_detected(db, populated):
    """Изменённый файл архива обнаруживается (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    for path in (folder / "files").rglob("*.pdf"):
        path.write_bytes("%PDF-1.4 подмена".encode())
    problems = backup_service.verify_backup(folder)
    assert any("Файл изменён" in problem for problem in problems)


def test_missing_manifest_is_reported(db, populated):
    """Папка без манифеста копией не считается (ТЗ п.74, 98)."""
    folder = populated["backup_dir"] / "Резервная копия 07"
    folder.mkdir(parents=True)
    problems = backup_service.verify_backup(folder)
    assert any("не резервная копия" in problem for problem in problems)


def test_file_lost_from_manifest_is_reported(db, populated):
    """Файл, вычеркнутый из манифеста, обнаруживается по базе копии (ТЗ п.49, 98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    manifest = backup_service.read_manifest(folder)
    manifest["files"] = []
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    problems = backup_service.verify_backup(folder)
    assert any("нет файла версии" in problem for problem in problems)


def test_path_outside_backup_folder_is_rejected(db, populated, tmp_path):
    """Путь манифеста не должен вести за пределы копии (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    manifest = backup_service.read_manifest(folder)
    manifest["files"][0]["relative"] = "../выход/файл.pdf"
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    problems = backup_service.verify_backup(folder)
    assert any("Недопустимый путь" in problem for problem in problems)
    target = {
        "db_path": tmp_path / "цель" / "app.db",
        "archive_dir": tmp_path / "цель" / "internal_archive",
        "settings_path": tmp_path / "цель" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    with pytest.raises(backup_service.BackupError):
        backup_service.restore_backup(
            folder,
            db_path=target["db_path"],
            archive_dir=target["archive_dir"],
            settings_path=target["settings_path"],
        )


def test_unsupported_format_is_reported(db, populated):
    """Копия неизвестного формата не восстанавливается молча (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    manifest = backup_service.read_manifest(folder)
    manifest["format"] = 99
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    problems = backup_service.verify_backup(folder)
    assert any("Формат копии" in problem for problem in problems)


# =====================================================================
# ВОССТАНОВЛЕНИЕ И ПЕРЕНОС (ТЗ п.98)
# =====================================================================


def test_restore_returns_project_to_other_storage(db, populated, tmp_path):
    """Копия восстанавливает проект на другом компьютере (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    # Целевой компьютер: своя база, свой архив, свои настройки.
    target = {
        "db_path": tmp_path / "other" / "app.db",
        "archive_dir": tmp_path / "other" / "internal_archive",
        "settings_path": tmp_path / "other" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)

    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )

    session = _open_session(target)
    try:
        from app.db.models import Document, DocumentArchiveLink, Project

        assert db.query(Project).count() == 1
        assert session.query(Project).count() == 1
        assert session.query(Document).count() == 2
        assert session.query(DocumentArchiveLink).count() == 1
        assert list(target["archive_dir"].rglob("*.pdf")), "файлы архива не вернулись"
        assert (target["settings_path"]).is_file()
    finally:
        session.close()


def test_restore_repoints_archive_paths_to_new_folder(db, populated, tmp_path):
    """Пути файлов в восстановленной базе указывают на новый каталог (ТЗ п.49, 98).

    Пути в базе абсолютные, поэтому без переноса проект на другом компьютере
    открылся бы и не нашёл своих файлов.
    """
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = {
        "db_path": tmp_path / "other" / "app.db",
        "archive_dir": tmp_path / "other" / "internal_archive",
        "settings_path": tmp_path / "other" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    session = _open_session(target)
    try:
        from app.db.models import ArchiveFileVersion

        versions = session.query(ArchiveFileVersion).all()
        assert versions
        for version in versions:
            stored = Path(version.stored_path)
            assert target["archive_dir"] in stored.parents, stored
            assert stored.is_file(), "файл по новому пути отсутствует"
    finally:
        session.close()


def test_restored_archive_integrity_holds(db, populated, tmp_path):
    """После переноса проверка целостности архива ошибок не находит (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = {
        "db_path": tmp_path / "other" / "app.db",
        "archive_dir": tmp_path / "other" / "internal_archive",
        "settings_path": tmp_path / "other" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    session = _open_session(target)
    try:
        assert storage_service.find_missing_files(session) == [], "файлы не нашлись"
    finally:
        session.close()


def test_restore_refuses_when_database_is_busy(db, populated, tmp_path):
    """Занятая база не подменяется: второй экземпляр программы (ТЗ п.54, 98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = _other_machine(tmp_path, populated)
    holder = sqlite3.connect(str(target["db_path"]), timeout=0.5)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("SELECT COUNT(*) FROM projects").fetchone()
        with pytest.raises(backup_service.BackupError) as info:
            backup_service.restore_backup(
                folder,
                db_path=target["db_path"],
                archive_dir=target["archive_dir"],
                settings_path=target["settings_path"],
                safety_dir=tmp_path / "safety",
            )
        assert "занята" in str(info.value)
    finally:
        holder.rollback()
        holder.close()


def test_restore_keeps_releases_versions_and_history(db, populated, tmp_path):
    """Выпуски, версии и история переживают перенос (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = {
        "db_path": tmp_path / "other" / "app.db",
        "archive_dir": tmp_path / "other" / "internal_archive",
        "settings_path": tmp_path / "other" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    session = _open_session(target)
    try:
        from app.db.models import DocumentVersion, HistoryEvent

        versions = session.query(DocumentVersion).all()
        assert [version.issued_at for version in versions if version.issued_at]
        assert session.query(HistoryEvent).count() >= 1
    finally:
        session.close()


def _other_machine(tmp_path, populated, name="other") -> dict:
    """Отдельное хранилище: как на другом компьютере (ТЗ п.98)."""
    target = {
        "db_path": tmp_path / name / "app.db",
        "archive_dir": tmp_path / name / "internal_archive",
        "settings_path": tmp_path / name / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    shutil.copy2(populated["db_path"], target["db_path"])
    return target


def test_restore_keeps_current_data_in_safety_copy(db, populated, tmp_path):
    """Перед восстановлением текущая база откладывается в сторону (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    # «Рабочая система» получила новые данные после копии.
    target = _other_machine(tmp_path, populated)
    session = _open_session(target)
    from app.db.models import Project

    project = session.query(Project).one()
    original_title = project.title
    project.title = "Изменён после копии"
    session.commit()
    session.close()

    safety = backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    assert (safety / "app.db").is_file()

    session = _open_session(target)
    try:
        assert session.query(Project).one().title == original_title, "вернулась копия"
    finally:
        session.close()

    raw = sqlite3.connect(str(safety / "app.db"))
    try:
        title = raw.execute("SELECT title FROM projects").fetchone()[0]
    finally:
        raw.close()
    assert title == "Изменён после копии", "прежние данные не потеряны"


def test_restore_removes_files_not_in_backup(db, populated, tmp_path):
    """Лишние файлы архива после восстановления не остаются (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = _other_machine(tmp_path, populated)
    stray = target["archive_dir"] / "Лишний.pdf"
    stray.write_bytes(b"%PDF-1.4 stray")

    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    assert not stray.exists()
    assert list(target["archive_dir"].rglob("*.pdf"))


def test_restore_refuses_broken_backup(db, populated, tmp_path):
    """Испорченная копия не трогает рабочие данные (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    (folder / "app.db").write_bytes("не база".encode())
    before = sorted(path.name for path in populated["archive_dir"].rglob("*"))

    with pytest.raises(backup_service.BackupError):
        backup_service.restore_backup(
            folder,
            db_path=populated["db_path"],
            archive_dir=populated["archive_dir"],
            settings_path=populated["settings_path"],
            safety_dir=populated["backup_dir"],
        )
    assert sorted(path.name for path in populated["archive_dir"].rglob("*")) == before


def test_restore_output_database_is_consistent(db, populated, tmp_path):
    """Восстановленная база согласована: внешние ключи целы (ТЗ п.98)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    target = {
        "db_path": tmp_path / "other" / "app.db",
        "archive_dir": tmp_path / "other" / "internal_archive",
        "settings_path": tmp_path / "other" / "settings.json",
    }
    target["archive_dir"].mkdir(parents=True)
    backup_service.restore_backup(
        folder,
        db_path=target["db_path"],
        archive_dir=target["archive_dir"],
        settings_path=target["settings_path"],
        safety_dir=tmp_path / "safety",
    )
    raw = sqlite3.connect(str(target["db_path"]))
    try:
        assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        raw.close()


def test_list_backups_is_newest_first(db, populated):
    """Список копий показывает свежие сверху (ТЗ п.74)."""
    for _ in range(2):
        backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                     db_path=populated["db_path"],
                                     archive_dir=populated["archive_dir"],
                                     settings_path=populated["settings_path"])
    names = [item["name"] for item in backup_service.list_backups(
        populated["backup_dir"]
    )]
    assert names == ["Резервная копия 02", "Резервная копия 01"]
    assert backup_service.describe_backup(
        populated["backup_dir"] / names[0]
    ).startswith("Резервная копия 02")


def test_describe_backup_readable_for_operator(db, populated):
    """Описание копии понятно оператору (ТЗ п.74)."""
    folder = backup_service.create_backup(db, base_dir=populated["backup_dir"],
                                          db_path=populated["db_path"],
                                          archive_dir=populated["archive_dir"],
                                          settings_path=populated["settings_path"])
    text = backup_service.describe_backup(folder)
    assert "Проектов: 1" in text
    assert "Файлов архива: 1" in text
