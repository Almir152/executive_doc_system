"""Регрессионные тесты Этапа 0: приложение запускается и не теряет данные.

Каждый тест ниже закрывает конкретную ошибку, найденную при проверке
приложения. Названия тестов описывают исходный дефект.
"""

from datetime import datetime, timezone

import pytest

from app.config import ARCHIVE_DIR, BACKUP_DIR, DATA_DIR, PACKAGES_DIR, ensure_dirs, utcnow
from app.core import domain
from app.core.services.storage_service import (
    ARCHIVE_CATEGORIES, add_file_to_archive, calculate_hash, can_delete_archive_document,
    find_by_hash,
)
from app.core.validators import ValidationError, validate_aook_dates


# -------------------------------------------------------------------
# КОНФИГУРАЦИЯ ПУТЕЙ (ТЗ п.72)
# -------------------------------------------------------------------
def test_data_dir_is_not_cwd_relative():
    """Пути хранилища не должны зависеть от текущего рабочего каталога."""
    ensure_dirs()
    for path in (DATA_DIR, ARCHIVE_DIR, PACKAGES_DIR, BACKUP_DIR):
        assert path.is_absolute()
        assert path.is_dir()


def test_three_stores_are_separate():
    """ТЗ п.72: рабочее хранилище, комплекты и BACKUP — разные сущности."""
    assert ARCHIVE_DIR != PACKAGES_DIR
    assert PACKAGES_DIR != BACKUP_DIR
    assert ARCHIVE_DIR != BACKUP_DIR
    assert DATA_DIR not in (PACKAGES_DIR, BACKUP_DIR)


# -------------------------------------------------------------------
# ВРЕМЯ (ТЗ: даты документов не должны «поезжать» по зонам)
# -------------------------------------------------------------------
def test_utcnow_is_naive_and_comparable(db, direction):
    """SQLite теряет tzinfo, поэтому во всём приложении naive-UTC."""
    from app.db.models import Project

    now = utcnow()
    assert now.tzinfo is None, "utcnow() обязан возвращать naive-UTC"

    p = Project(direction_id=direction.id, title="Проверка времени", address="a")
    db.add(p)
    db.commit()
    db.expire_all()

    stored = db.query(Project).filter(Project.id == p.id).one()
    assert stored.created_at.tzinfo is None
    # Раньше это падало с "can't compare offset-naive and offset-aware"
    assert stored.created_at <= utcnow()
    assert stored.created_at <= datetime.now(timezone.utc).replace(tzinfo=None)


# -------------------------------------------------------------------
# ЦЕЛОСТНОСТЬ СВЯЗЕЙ (ТЗ п.49, 50, 52)
# -------------------------------------------------------------------
def test_foreign_keys_are_enforced(db):
    """Без PRAGMA foreign_keys ondelete в моделях не действует."""
    from sqlalchemy import text

    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_every_new_connection_enforces_foreign_keys(db):
    """Прагма выставляется на КАЖДОМ новом подключении, а не один раз.

    Проверяется на свежем соединении: иначе соединение, оставшееся в пуле
    после миграции, скрыло бы ненастроенный обработчик подключения.
    """
    from sqlalchemy import text

    from app.db.database import engine

    engine.dispose()  # сброс пула: следующее подключение создаётся заново
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1, (
            "обработчик подключения обязан включать внешние ключи"
        )


def test_deleting_referenced_row_is_rejected_by_database(db, project):
    """Внешние ключи проверяются самой БД, а не только ORM."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.db.models import ArchiveDocument

    archive = ArchiveDocument(
        project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_PROJECT,
        file_type="pdf",
        original_name="x.pdf",
    )
    db.add(archive)
    db.commit()

    # Ссылка на несуществующий проект обязана быть отвергнута.
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE archive_documents SET project_id = 999999 WHERE id = :i"),
            {"i": archive.id},
        )
        db.commit()
    db.rollback()

    assert db.get(ArchiveDocument, archive.id) is not None


def test_orphan_link_is_rejected_by_database(db, project):
    """Нельзя создать связь с несуществующим архивным документом."""
    from sqlalchemy.exc import IntegrityError

    from app.db.models import Document, DocumentArchiveLink

    doc = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1")
    db.add(doc)
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(
            DocumentArchiveLink.__table__.insert().values(
                document_id=doc.id, archive_document_id=999999, link_role="Приложение"
            )
        )
        db.commit()
    db.rollback()


def test_used_archive_document_cannot_be_deleted(db, project, tmp_path):
    """ТЗ п.50, 109: используемый архивный документ удалять нельзя."""
    from app.core.services.storage_service import StorageError, delete_archive_document
    from app.db.models import Document, DocumentArchiveLink

    src = tmp_path / "used.pdf"
    src.write_bytes(b"used-file-content")
    archive_doc = add_file_to_archive(
        db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES
    )
    doc = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1")
    db.add(doc)
    db.commit()
    db.add(DocumentArchiveLink(
        document_id=doc.id,
        archive_document_id=archive_doc.id,
        archive_version_id=archive_doc.current_version.id,
        link_role=domain.LINK_ROLE_SCHEME,
    ))
    db.commit()

    db.expire_all()
    allowed, links = can_delete_archive_document(db, archive_doc.id)
    assert allowed is False
    assert links == 1
    with pytest.raises(StorageError):
        delete_archive_document(db, archive_doc.id)


# -------------------------------------------------------------------
# АРХИВ: ДЕДУПЛИКАЦИЯ И СЧЁТЧИК СВЯЗЕЙ (ТЗ п.47, 51, 84, 92)
# -------------------------------------------------------------------
def test_same_file_stored_once_and_linked_many_times(db, project, tmp_path):
    """ТЗ п.47, 84, 92: один физический файл — одна копия, связей много."""
    from app.db.models import ArchiveDocument, Document, DocumentArchiveLink

    scheme = tmp_path / "Схема №12.pdf"
    scheme.write_bytes(b"%PDF-1.4 executive scheme")

    files_before = set(ARCHIVE_DIR.iterdir())
    af1 = add_file_to_archive(db, scheme, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    af2 = add_file_to_archive(db, scheme, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    files_after = set(ARCHIVE_DIR.iterdir())

    assert af1.id == af2.id, "Один и тот же файл должен вернуть тот же документ"
    assert len(files_after - files_before) == 1, "Физическая копия не должна дублироваться"
    assert db.query(ArchiveDocument).count() == 1

    aosr_15 = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="15")
    aosr_18 = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="18")
    aook_4 = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOOK, number="4")
    db.add_all([aosr_15, aosr_18, aook_4])
    db.commit()
    version_id = af1.current_version.id
    for d in (aosr_15, aosr_18, aook_4):
        db.add(DocumentArchiveLink(
            document_id=d.id,
            archive_document_id=af1.id,
            archive_version_id=version_id,
            link_role=domain.LINK_ROLE_SCHEME,
        ))
    db.commit()

    db.expire_all()
    af = db.query(ArchiveDocument).one()
    assert af.links_count == 3, "ТЗ п.51: счётчик связей архивного документа"
    assert {link.document.number for link in af.links} == {"15", "18", "4"}


def test_different_content_with_same_name_is_not_merged(db, project, tmp_path):
    """Дедупликация по содержимому, а не по имени файла."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    a = dir_a / "Схема.pdf"
    b = dir_b / "Схема.pdf"
    a.write_bytes(b"content-A")
    b.write_bytes(b"content-B")

    assert a.name == b.name, "Имена совпадают — различие только в содержимом"
    af1 = add_file_to_archive(db, a, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    af2 = add_file_to_archive(db, b, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    assert af1.id != af2.id
    assert af1.versions[0].stored_path != af2.versions[0].stored_path


def test_find_by_hash_and_calculate_hash(db, project, tmp_path):
    f = tmp_path / "cert.pdf"
    f.write_bytes(b"certificate")
    digest = calculate_hash(f)
    assert len(digest) == 64

    archive_doc = add_file_to_archive(
        db, f, project.id, domain.ARCHIVE_CATEGORY_MATERIALS
    )
    found = find_by_hash(db, digest)
    assert found is not None
    assert found.archive_document_id == archive_doc.id
    assert find_by_hash(db, "0" * 64) is None


def test_archive_categories_match_tz_50():
    """ТЗ п.50: четыре логические части архива."""
    assert len(ARCHIVE_CATEGORIES) == 4
    assert "Исполнительные схемы" in ARCHIVE_CATEGORIES
    assert "Материалы и документы качества" in ARCHIVE_CATEGORIES
    assert "Протоколы и обследования" in ARCHIVE_CATEGORIES
    assert "Архив проекта" in ARCHIVE_CATEGORIES


# -------------------------------------------------------------------
# ПРОВЕРКА ДАТ (ТЗ п.33, 87)
# -------------------------------------------------------------------
def test_aook_end_must_not_precede_linked_aosr_end():
    """ТЗ п.33, 87: АООК не может закончиться раньше связанного АОСР."""
    aosr_start = datetime(2026, 1, 10)
    aosr_end = datetime(2026, 1, 20)
    with pytest.raises(ValidationError):
        validate_aook_dates(
            aook_start=datetime(2026, 1, 5),
            aook_end=datetime(2026, 1, 15),
            aosr_dates=[(aosr_start, aosr_end)],
        )


def test_aook_start_must_not_follow_linked_aosr_start():
    """ТЗ п.33, 87: АООК не может начаться позже связанного АОСР."""
    aosr_start = datetime(2026, 1, 10)
    aosr_end = datetime(2026, 1, 20)
    with pytest.raises(ValidationError):
        validate_aook_dates(
            aook_start=datetime(2026, 1, 15),
            aook_end=datetime(2026, 1, 25),
            aosr_dates=[(aosr_start, aosr_end)],
        )


def test_aook_dates_valid_case_passes():
    aosr_start = datetime(2026, 1, 10)
    aosr_end = datetime(2026, 1, 20)
    validate_aook_dates(
        aook_start=datetime(2026, 1, 5),
        aook_end=datetime(2026, 1, 25),
        aosr_dates=[(aosr_start, aosr_end)],
    )


def test_aook_dates_self_consistency():
    with pytest.raises(ValidationError):
        validate_aook_dates(
            aook_start=datetime(2026, 2, 1),
            aook_end=datetime(2026, 1, 1),
            aosr_dates=[],
        )


# =====================================================================
# Режим журнала SQLite
#
# Журнал хранится в самом файле базы и переживает перезапуск программы,
# поэтому база, однажды открытая в режиме WAL (например, прежней версией),
# остаётся в нём навсегда. Пока приложение работает, последние коммиты
# лежат в app.db-wal, которого нет среди файлов копии app.db, — значит
# копия перестаёт быть полноценной резервной (ТЗ п.74, 98).
# =====================================================================


def test_foreign_keys_survive_contended_database_file(tmp_path):
    """Занятый файл базы не отключает проверку внешних ключей молча.

    Второй экземпляр программы или внешний просмотрщик держат файл; приложение
    обязано либо работать с включёнными внешними ключами, либо отказаться
    запускаться с понятным сообщением, но не продолжать без них: тогда
    ondelete="CASCADE"/"RESTRICT" из моделей перестают действовать
    (ТЗ п.49, 50, 52).
    """
    import sqlite3

    from sqlalchemy import create_engine, event

    from app.db.database import _set_sqlite_pragmas

    db_path = tmp_path / "contended.db"
    seed = sqlite3.connect(db_path)
    seed.execute("PRAGMA journal_mode=WAL").fetchall()
    seed.close()

    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("PRAGMA locking_mode=EXCLUSIVE")
    blocker.execute("BEGIN EXCLUSIVE")
    blocker.execute("CREATE TABLE t (a INT)")

    own_engine = create_engine(f"sqlite:///{db_path}")
    event.listen(own_engine, "connect", _set_sqlite_pragmas)
    try:
        try:
            with own_engine.connect() as c:
                enabled = c.exec_driver_sql("PRAGMA foreign_keys").fetchone()[0]
        except RuntimeError as exc:
            # Отказ запускаться допустим, но сообщение должно быть actionable.
            assert "внешних ключей" in str(exc) and "Закройте" in str(exc), str(exc)
        else:
            assert enabled == 1, (
                "приложение работает с отключёнными внешними ключами — "
                "связи не защищены")
    finally:
        blocker.rollback()
        blocker.close()
        own_engine.dispose()


def test_connection_is_refused_when_foreign_keys_cannot_be_enabled():
    """Если прагма не сработала, подключение не выдаётся молча."""
    from app.db.database import _set_sqlite_pragmas

    class FakeCursor:
        def __init__(self, values):
            self.values = values

        def execute(self, sql):
            key = sql.split("=")[0].strip()
            if key == "PRAGMA foreign_keys" and "=" not in sql:
                return self
            if sql in self.values:
                return self
            return self

        def fetchone(self):
            return (0,)

        def close(self):
            pass

    class FakeConnection:
        isolation_level = ""
        cursor_obj = FakeCursor({})

        def cursor(self):
            return self.cursor_obj

    with pytest.raises(RuntimeError, match="не удалось включить проверку внешних ключей"):
        _set_sqlite_pragmas(FakeConnection(), None)


def test_database_does_not_use_wal_journal(db):
    """Обычный режим журнала: копия одного app.db остаётся полной."""
    journal = db.connection().exec_driver_sql("PRAGMA journal_mode").fetchone()[0]
    assert journal != "wal", (
        f"база работает в режиме WAL: незакрытые коммиты остаются в app.db-wal, "
        f"копия одного app.db окажется неполной ({journal})")
    assert journal == "delete"


def test_database_leaves_wal_mode_if_it_was_left_in_it(tmp_path):
    """База, оставшаяся в WAL от прежней версии, возвращается в обычный режим."""
    import sqlite3

    from sqlalchemy import create_engine

    from app.db.database import Base, _set_sqlite_pragmas

    db_path = tmp_path / "wal.db"
    # Режим задаётся на уровне файла базы, до подключения приложения.
    seed = sqlite3.connect(db_path)
    seed.execute("PRAGMA journal_mode=WAL").fetchall()
    assert seed.execute("PRAGMA journal_mode").fetchone()[0] == "wal", \
        "не удалось создать базу в WAL"
    seed.close()

    # Тот же обработчик подключения, что и у боевого движка.
    raw = sqlite3.connect(db_path)
    try:
        _set_sqlite_pragmas(raw, None)
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        raw.close()

    own_engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(bind=own_engine)
        with own_engine.connect() as c:
            assert c.exec_driver_sql("PRAGMA journal_mode").fetchone()[0] != "wal"
    finally:
        own_engine.dispose()


# -------------------------------------------------------------------
# ПОВЕДЕНИЕ ПРИ ЗАНЯТОМ ФАЙЛЕ БД (проверка Этапа 0, 2-й раунд)
# -------------------------------------------------------------------

def test_contended_wal_is_reported_to_operator_not_swallowed(db):
    """База в WAL при занятом файле -> init_db обязан сообщить об этом.

    Дефект: смена режима журнала не удаётся, пока файл держит другой процесс.
    Прежний код писал предупреждение в logger, но собранное приложение
    оконное (console=False), и оператор не видел ничего: он считал копию
    app.db полной, а она была неполной (ТЗ п.74, 98).
    """
    import sqlite3 as sq
    from app.config import DB_PATH
    from app.db.database import engine, init_db

    blocked = sq.connect(str(DB_PATH), isolation_level=None)
    try:
        sq_conn = sq.connect(str(DB_PATH), isolation_level=None)
        try:
            sq_conn.execute("PRAGMA journal_mode=WAL")
        finally:
            sq_conn.close()
        blocked.execute("BEGIN EXCLUSIVE")
        blocked.execute("SELECT count(*) FROM directions").fetchone()

        report = init_db()

        assert report["journal_mode"].lower() == "wal", \
            "условие теста не создано: база должна остаться в WAL"
        assert report["journal_ok"] is False, (
            "init_db обязан вернуть journal_ok=False, иначе main.py не покажет "
            "оператору предупреждение о неполной копии базы"
        )
    finally:
        blocked.rollback()
        blocked.close()
        # Освобождаем файл: собственный пул соединений тоже держит его, и без
        # dispose следующие тесты получили бы базу в WAL.
        engine.dispose()
        fix = sq.connect(str(DB_PATH), isolation_level=None)
        try:
            fix.execute("PRAGMA journal_mode=DELETE")
        finally:
            fix.close()


def test_pragmas_are_applied_even_when_file_is_locked(db):
    """Проверено, что занятость файла НЕ мешает включить внешние ключи.

    Это опровергает прежнюю формулировку в тексте ошибки: PRAGMA foreign_keys
    — настройка соединения, а не файла, поэтому под BEGIN EXCLUSIVE чужого
    соединения она применяется и даёт 1.
    """
    import sqlite3 as sq
    from app.config import DB_PATH
    from app.db.database import _set_sqlite_pragmas

    blocked = sq.connect(str(DB_PATH), isolation_level=None)
    try:
        blocked.execute("BEGIN EXCLUSIVE")
        blocked.execute("SELECT count(*) FROM directions").fetchone()
        raw = sq.connect(str(DB_PATH))
        try:
            _set_sqlite_pragmas(raw, None)
            enabled = raw.execute("PRAGMA foreign_keys").fetchone()[0]
            assert enabled == 1, (
                "внешние ключи включаются даже при занятом файле; если это "
                "перестанет быть так, текст ошибки в обработчике надо переписать"
            )
        finally:
            raw.close()
    finally:
        blocked.rollback()
        blocked.close()


def test_init_db_reports_journal_state_on_normal_start(db):
    """В отчёте init_db всегда есть сведения о режиме журнала."""
    from app.db.database import init_db

    report = init_db()
    assert "journal_ok" in report and "journal_mode" in report
    assert report["journal_mode"].lower() != "wal", \
        "боевая база обязана быть в обычном режиме"


def test_setup_logging_writes_to_file_without_duplicates():
    """Журнал пишется в файл app.log и не дублируется (оконная сборка)."""
    import logging
    from app.config import LOG_PATH, setup_logging

    setup_logging()
    setup_logging()
    logger = logging.getLogger("test_stage0_logging")
    logger.error("проверочная запись")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert LOG_PATH.exists(), "файл журнала не создан: оператор не увидит ошибки"
    lines = [l for l in LOG_PATH.read_text(encoding="utf-8").splitlines()
             if "проверочная запись" in l]
    assert len(lines) == 1, f"запись продублирована {len(lines)} раз(а)"
    LOG_PATH.write_text("", encoding="utf-8")


def test_aook_validator_tolerates_missing_link_list():
    """АООК без связей по АОСР — норма, а не TypeError (п.33)."""
    start = datetime(2026, 1, 1)
    validate_aook_dates(start, start, None)
    validate_aook_dates(start, start, [])


# -------------------------------------------------------------------
# НЕОБРАБОТАННЫЕ ОШИБКИ (ТЗ п.106)
# -------------------------------------------------------------------
def test_crash_handler_replaces_silent_exit(qapp, monkeypatch):
    """Необработанная ошибка показывается окном, а не закрывает программу.

    Собранное приложение оконное (console=False): без обработчика
    необработанное исключение в слоте Qt завершает процесс, и оператор
    видит только исчезновение окна без причины.
    """
    import sys

    import main

    original = sys.excepthook
    shown = {}
    monkeypatch.setattr(
        "main.QMessageBox",
        type("Box", (), {
            "Icon": type("Icon", (), {"Critical": None}),
            "__init__": lambda self, *args, **kwargs: None,
            "setText": lambda self, text: shown.update(text=text),
            "setInformativeText": lambda self, text: None,
            "exec": lambda self: 0,
        }),
    )
    try:
        main.install_crash_handler()
        try:
            raise PermissionError(13, "Permission denied")
        except PermissionError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = original

    assert "PermissionError" in shown["text"]
    assert "app.log" in shown["text"]


def test_crash_handler_keeps_keyboard_interrupt(qapp, monkeypatch):
    """Ctrl+C не перехватывается: программа должна завершаться сама."""
    import sys

    import main

    original = sys.excepthook
    called = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *args: called.append(args))
    try:
        main.install_crash_handler()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        sys.excepthook = original

    assert len(called) == 1
