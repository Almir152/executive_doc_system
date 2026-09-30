"""Тесты Этапа 1: схема данных, версии и миграция унаследованной БД.

Миграция — самая опасная часть разработки: ошибка в ней необратимо портит
рабочее хранилище оператора. Поэтому миграция проверяется на настоящей
унаследованной схеме, а не на схеме текущей версии кода.
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core import domain
from app.core.services.storage_service import (
    add_file_to_archive, add_version, delete_archive_document,
    find_missing_files, find_orphan_files,
)
from app.db.migrations import SCHEMA_VERSION, apply_migrations
from app.db.models import (
    ArchiveFileVersion, Document, DocumentLink, DocumentVersion,
)


# =====================================================================
# СПРАВОЧНИКИ (ТЗ п.14, 22, 44)
# =====================================================================

def test_three_directions_seeded(db):
    """ТЗ п.14: ровно три направления работ."""
    from app.db.models import Direction

    names = [d.name for d in db.query(Direction).order_by(Direction.sort_order).all()]
    assert names == list(domain.DIRECTIONS)


def test_section_kinds_seeded_and_extensible(db):
    """ТЗ п.22: справочник разделов расширяемый, набор — стартовый."""
    from app.db.models import SectionKind

    codes = [s.code for s in db.query(SectionKind).all()]
    for code in ("ГП", "АР", "КЖ", "КМ", "ВК", "ВВ", "ОТ", "ТС", "НВ", "НК", "ЭС"):
        assert code in codes, f"раздел {code} должен быть в справочнике"
    assert len(codes) == len(set(codes)), "коды разделов не должны повторяться"


def test_reference_data_seeding_is_idempotent(db):
    """Повторный запуск не создаёт дублей в справочниках."""
    from app.db.models import Direction
    from app.db.seed import seed_reference_data

    before = db.query(Direction).count()
    added = seed_reference_data(db)
    assert all(v == 0 for v in added.values()), f"повторно добавлено: {added}"
    assert db.query(Direction).count() == before


def test_organization_has_no_separate_full_name_field(db):
    """ТЗ п.18: отдельного поля «полное наименование» в форме нет."""
    from app.db.models import Organization

    columns = {c.name for c in inspect(Organization).columns}
    assert "short_name" in columns
    assert "full_name" not in columns
    # Реквизиты, перечисленные в ТЗ п.18, присутствуют.
    for field in ("ogrn", "inn", "address", "phone", "fax", "sro", "nopriz"):
        assert field in columns, f"реквизит {field} должен быть в справочнике"


# =====================================================================
# АРХИВ: ВЕРСИИ (ТЗ п.53, 90, 91, 93)
# =====================================================================

def test_new_version_does_not_overwrite_previous(db, project, tmp_path):
    """ТЗ п.53, 93: новая редакция не уничтожает прежнюю."""
    from app.db.models import ArchiveDocument

    src = tmp_path / "Схема.pdf"
    src.write_bytes(b"version-1")
    archive_doc = add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    v1_path = archive_doc.current_version.stored_path

    src.write_bytes(b"version-2-revised")
    v2 = add_version(db, archive_doc.id, src)

    db.expire_all()
    archive_doc = db.query(ArchiveDocument).one()
    assert len(archive_doc.versions) == 2
    assert v2.version_no == 2
    assert v1_path != v2.stored_path, "у версий должны быть разные файлы"


def test_only_one_actual_version(db, project, tmp_path):
    """ТЗ п.91: актуальная версия ровно одна, история сохраняется."""
    from app.db.models import ArchiveDocument

    src = tmp_path / "Протокол.pdf"
    src.write_bytes(b"v1")
    archive_doc = add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_PROTOCOLS)
    for i in range(2, 5):
        src.write_bytes(f"v{i}".encode())
        add_version(db, archive_doc.id, src)

    db.expire_all()
    archive_doc = db.query(ArchiveDocument).one()
    assert len(archive_doc.versions) == 4
    actual = [v for v in archive_doc.versions if v.is_actual]
    assert len(actual) == 1, "актуальной должна быть ровно одна версия"
    assert archive_doc.current_version.version_no == 4


def test_link_pins_file_version_and_survives_new_version(db, project, tmp_path):
    """ТЗ п.91: историческая выгрузка ссылается на то состояние, что использовала.

    Это ключевое требование: появление новой редакции не должно менять уже
    сформированный комплект.
    """
    from app.db.models import Document, DocumentArchiveLink

    src = tmp_path / "Схема.pdf"
    src.write_bytes(b"original")
    archive_doc = add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    pinned_version_id = archive_doc.current_version.id

    doc = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1")
    db.add(doc)
    db.commit()
    db.add(DocumentArchiveLink(
        document_id=doc.id,
        archive_document_id=archive_doc.id,
        archive_version_id=pinned_version_id,
        link_role=domain.LINK_ROLE_SCHEME,
    ))
    db.commit()

    # Новая редакция выходит уже после того, как схема привязана к акту.
    src.write_bytes(b"revised-after-linking")
    add_version(db, archive_doc.id, src)

    db.expire_all()
    link = db.query(DocumentArchiveLink).one()
    assert link.archive_version_id == pinned_version_id, (
        "связь обязана остаться на прежней версии файла (ТЗ п.91)"
    )
    assert link.archive_version.version_no == 1


def test_orphan_and_missing_file_detection(db, project, tmp_path):
    """Контроль целостности хранилища: оба отклонения находятся."""
    src = tmp_path / "Схема.pdf"
    src.write_bytes(b"content")
    archive_doc = add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)

    assert find_orphan_files(db) == []
    assert find_missing_files(db) == []

    # Файл исчез с диска.
    from pathlib import Path

    Path(archive_doc.current_version.stored_path).unlink()
    assert len(find_missing_files(db)) == 1
    assert find_orphan_files(db) == []


def test_orphan_file_on_disk_is_detected(db, project, tmp_path):
    """Файл в каталоге архива без записи в БД — признак сбоя загрузки."""
    from app.config import ARCHIVE_DIR

    stray = ARCHIVE_DIR / ("0" * 64 + "_stray.pdf")
    stray.write_bytes(b"stray")
    try:
        orphans = find_orphan_files(db)
        assert stray.resolve() in orphans
    finally:
        stray.unlink()


# =====================================================================
# ПРОЕКТ И АРХИВ: ЗАЩИТА ОТ ПОТЕРИ ФАЙЛОВ (ТЗ п.54, 109)
# =====================================================================

def test_archive_document_survives_project_deletion_attempt(db, project, tmp_path):
    """ТЗ п.54, 109: файл-доказательство не исчезает вместе с проектом."""
    from app.core.services.project_service import ProjectError, delete_project
    from app.db.models import ArchiveDocument

    src = tmp_path / "Схема.pdf"
    src.write_bytes(b"evidence")
    add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)

    with pytest.raises(ProjectError):
        delete_project(db, project.id)

    assert db.query(ArchiveDocument).count() == 1
    assert find_missing_files(db) == [], "файл должен остаться на диске"


def test_database_itself_blocks_cascade_deletion_of_archive(db, project, tmp_path):
    """Защита должна быть в схеме, а не только в сервисе.

    Проверяется удаление проекта в обход project_service, то есть так, как
    удалил бы новый код, написанный неосторожно. ON DELETE RESTRICT обязан
    запретить каскадное удаление архивных файлов (ТЗ п.54, 109).
    """
    from sqlalchemy.exc import IntegrityError

    from app.db.models import ArchiveDocument, Document, Project

    src = tmp_path / "Схема.pdf"
    src.write_bytes(b"evidence")
    add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    stored = archive_path(db, ArchiveDocument)

    db.add(Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1"))
    db.commit()

    db.delete(project)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.query(Project).count() == 1, "проект не должен быть удалён"
    assert db.query(ArchiveDocument).count() == 1
    assert Path(stored).is_file(), "файл-доказательство обязан уцелеть"
    assert find_missing_files(db) == []


def archive_path(db, model) -> str:
    """Путь к файлу актуальной версии единственного архивного документа."""
    from app.db.models import ArchiveFileVersion

    version = db.query(ArchiveFileVersion).one()
    return version.stored_path


# =====================================================================
# МИГРАЦИЯ УНАСЛЕДОВАННОЙ БД (ТЗ п.97, 110)
# =====================================================================

LEGACY_SCHEMA = """
CREATE TABLE projects (
    id INTEGER NOT NULL,
    direction VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    address VARCHAR,
    created_at DATETIME,
    PRIMARY KEY (id)
);
CREATE TABLE documents (
    id INTEGER NOT NULL,
    project_id INTEGER,
    doc_type VARCHAR NOT NULL,
    number VARCHAR NOT NULL,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(project_id) REFERENCES projects (id)
);
CREATE TABLE archive_files (
    id INTEGER NOT NULL,
    file_type VARCHAR NOT NULL,
    original_name VARCHAR NOT NULL,
    stored_path VARCHAR NOT NULL,
    file_hash VARCHAR NOT NULL,
    version INTEGER,
    created_at DATETIME,
    PRIMARY KEY (id),
    UNIQUE (file_hash)
);
CREATE TABLE document_file_links (
    document_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    PRIMARY KEY (document_id, file_id),
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES archive_files (id) ON DELETE RESTRICT
);
"""


def _build_legacy_db(
    path, *, with_document=True, with_link=True, with_version=False
):
    """Создать унаследованную БД в точности как в рабочей до миграции."""
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, direction, title, address, created_at) "
        "VALUES (1, 'Общестроительные работы', 'Строительство корпуса МФТИ', "
        "'г. Долгопрудный', '2026-09-29 21:59:54.025753')"
    )
    if with_document:
        conn.execute(
            "INSERT INTO documents (id, project_id, doc_type, number, created_at) "
            "VALUES (1, 1, 'AOSR', '15', '2026-09-29 22:00:00.000000')"
        )
    conn.execute(
        "INSERT INTO archive_files (id, file_type, original_name, stored_path, "
        "file_hash, version, created_at) VALUES "
        "(1, 'Исполнительные схемы', 'Схема №12.pdf', '/legacy/path/scheme.pdf', "
        "'abc123', 2, '2026-09-29 22:12:23.433526')"
    )
    if with_document and with_link:
        conn.execute("INSERT INTO document_file_links VALUES (1, 1)")
    conn.commit()
    conn.close()


def _migrate(tmp_path, name, **kwargs):
    """Прогнать миграцию на отдельной БД и вернуть подключение к результату."""
    from app.db.database import Base
    from app.db.migrations import apply_migrations, set_user_version

    db_path = tmp_path / name
    _build_legacy_db(db_path, **kwargs)

    url = f"sqlite:///{db_path}"
    from sqlalchemy import create_engine

    own_engine = create_engine(url, connect_args={"check_same_thread": False})
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()

    applied = apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)
    return own_engine, applied, db_path


def _assert_all_migrations_applied(applied):
    """Миграции проверяются по свойствам, а не по списку имён.

    Жёсткое сравнение со списком ломало бы каждое добавление следующей
    миграции, хотя данные переносятся корректно.
    """
    from app.db.migrations import MIGRATIONS, SCHEMA_VERSION

    assert applied == [name for _v, name, _fn in MIGRATIONS]
    assert SCHEMA_VERSION == MIGRATIONS[-1][0], "версия схемы разошлась с реестром"


def test_migration_preserves_legacy_data(tmp_path):
    """Главное требование миграции: данные оператора не теряются."""
    engine, applied, _ = _migrate(tmp_path, "legacy.db")

    _assert_all_migrations_applied(applied)
    with engine.connect() as conn:
        projects = list(conn.execute(text("SELECT title, address FROM projects")))
        assert projects == [("Строительство корпуса МФТИ", "г. Долгопрудный")]

        docs = list(conn.execute(text("SELECT number, status FROM documents")))
        assert docs == [("15", domain.DOC_STATUS_DRAFT)]

        archive = list(conn.execute(
            text("SELECT original_name, category, file_type FROM archive_documents")
        ))
        # Категория лежала в поле file_type — миграция обязана её восстановить.
        assert archive == [("Схема №12.pdf", domain.ARCHIVE_CATEGORY_SCHEMES, "pdf")]

        versions = list(conn.execute(
            text("SELECT archive_document_id, version_no, file_hash FROM archive_file_versions")
        ))
        assert versions == [(1, 2, "abc123")]


def test_migration_translates_legacy_doc_type_codes(tmp_path):
    """Коды типов документов переводятся в значения ТЗ п.42."""
    engine, _, _ = _migrate(tmp_path, "types.db")
    with engine.connect() as conn:
        doc_type = conn.execute(text("SELECT doc_type FROM documents")).scalar()
    assert doc_type == domain.DOC_TYPE_AOSR
    assert doc_type in domain.NUMBERED_DOC_TYPES


def test_migration_preserves_links_with_pinned_version(tmp_path):
    """Связь переезжает и закрепляет версию файла (ТЗ п.91)."""
    engine, _, _ = _migrate(tmp_path, "links.db")
    with engine.connect() as conn:
        rows = list(conn.execute(text(
            "SELECT document_id, archive_document_id, archive_version_id, link_role "
            "FROM document_archive_links"
        )))
    assert len(rows) == 1
    document_id, archive_id, version_id, role = rows[0]
    assert document_id == 1
    assert archive_id == 1
    assert version_id is not None, "связь должна закреплять версию файла"
    assert role == domain.LINK_ROLE_ATTACHMENT


def test_migration_makes_database_consistent(tmp_path):
    """После миграции внешние ключи согласованы, временных таблиц нет."""
    from app.db.migrations import foreign_key_check

    engine, _, _ = _migrate(tmp_path, "fk.db")
    with engine.connect() as conn:
        assert foreign_key_check(conn) == []
        names = [r[0] for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )]
    assert not [n for n in names if "__" in n], f"остались временные таблицы: {names}"


def test_migration_adds_missing_on_delete_cascade(tmp_path):
    """ТЗ п.97: project_id получает ON DELETE CASCADE, которого не было."""
    engine, _, _ = _migrate(tmp_path, "cascade.db")
    with engine.connect() as conn:
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='documents'"
        )).scalar()
    assert "ON DELETE CASCADE" in ddl
    assert "NOT NULL" in ddl


def test_migration_preserves_unknown_direction(tmp_path):
    """Направление, которого нет в справочнике ТЗ, не теряется."""
    db_path = tmp_path / "unknown_dir.db"
    _build_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO projects (id, direction, title) VALUES (2, 'Ландшафтные работы', 'X')")
    conn.commit()
    conn.close()

    from sqlalchemy import create_engine

    from app.db.database import Base
    from app.db.migrations import apply_migrations, set_user_version

    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    with own_engine.connect() as conn:
        titles = [r[0] for r in conn.execute(text("SELECT title FROM projects ORDER BY id"))]
        directions = [r[0] for r in conn.execute(
            text("SELECT d.name FROM directions d JOIN projects p ON p.direction_id = d.id "
                "ORDER BY p.id")
        )]
    assert titles == ["Строительство корпуса МФТИ", "X"]
    assert "Ландшафтные работы" in directions, "неизвестное направление должно попасть в справочник"


def test_migration_is_not_applied_twice(tmp_path):
    """Миграция применяется один раз: повторный запуск — no-op (ТЗ п.97)."""
    from sqlalchemy import create_engine

    from app.db.migrations import apply_migrations, get_user_version, set_user_version

    db_path = tmp_path / "twice.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()

    _assert_all_migrations_applied(apply_migrations(own_engine))
    assert apply_migrations(own_engine) == [], "повторное применение недопустимо"

    raw = own_engine.raw_connection()
    try:
        assert get_user_version(raw) == SCHEMA_VERSION
    finally:
        raw.close()

    with own_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM archive_documents")).scalar() == 1


def test_migration_of_empty_legacy_database(tmp_path):
    """Пустая унаследованная БД тоже должна мигрировать без ошибок."""
    from sqlalchemy import create_engine

    from app.db.database import Base
    from app.db.migrations import apply_migrations, set_user_version

    db_path = tmp_path / "empty.db"
    _build_legacy_db(db_path, with_document=False, with_link=False)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM archive_files")
    conn.commit()
    conn.close()

    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()

    _assert_all_migrations_applied(apply_migrations(own_engine))
    Base.metadata.create_all(bind=own_engine)
    with own_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM archive_documents")).scalar() == 0


def test_migration_rolls_back_on_failure(tmp_path):
    """Ошибка внутри миграции откатывает её целиком (ТЗ п.97)."""
    from sqlalchemy import create_engine

    from app.db.migrations import MIGRATIONS, apply_migrations, get_user_version, set_user_version

    db_path = tmp_path / "rollback.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()

    def broken(conn):
        conn.execute("DROP TABLE projects")
        raise RuntimeError("сбой в середине миграции")

    original = MIGRATIONS[0]
    MIGRATIONS[0] = (1, "broken", broken)
    try:
        with pytest.raises(RuntimeError, match="сбой"):
            apply_migrations(own_engine)
    finally:
        MIGRATIONS[0] = original

    raw = own_engine.raw_connection()
    try:
        assert get_user_version(raw) == 0, "незавершённая миграция не должна отмечаться"
        names = {r[0] for r in raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        raw.close()
    assert "projects" in names, "откат обязан вернуть исходную таблицу"
    assert not [n for n in names if "__" in n], f"остались временные таблицы: {names}"


# =====================================================================
# Устойчивость миграции к неполным данным оператора
#
# Унаследованная схема не требовала ни проект у документа, ни ссылку
# на существующую запись, и внешние ключи в ней могли быть выключены.
# Миграция обязана такие данные пережить, ничего не удаляя молча
# (ТЗ п.54, 97).
# =====================================================================


def test_migration_keeps_document_without_project(tmp_path):
    """Документ без проекта переносится в отдельный проект, а не теряется."""
    from sqlalchemy import create_engine, text

    from app.db.database import Base
    from app.db.migrations import apply_migrations, set_user_version

    db_path = tmp_path / "orphan_doc.db"
    _build_legacy_db(db_path, with_document=False)
    conn = sqlite3.connect(db_path)
    # Унаследованная схема разрешала пустой project_id.
    conn.execute(
        "INSERT INTO documents (id, project_id, doc_type, number, created_at) "
        "VALUES (7, NULL, 'AOSR', '15', '2026-09-29 22:00:00.000000')"
    )
    conn.commit()
    conn.close()

    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    with own_engine.connect() as c:
        row = c.execute(text(
            "SELECT d.number, p.title FROM documents d JOIN projects p"
            " ON p.id = d.project_id WHERE d.id = 7")).fetchone()
    assert row is not None, "документ без проекта потерян при миграции"
    assert row[0] == "15", "реквизиты документа изменились"
    assert "без проекта" in row[1], (
        f"ожидался отдельный проект-приёмник, получено «{row[1]}»"
    )


def test_migration_keeps_archive_file_without_any_project(tmp_path):
    """Архивный файл переживает случай, когда проектов в базе нет вовсе."""
    from sqlalchemy import create_engine, text

    from app.db.database import Base
    from app.db.migrations import apply_migrations, set_user_version

    db_path = tmp_path / "no_projects.db"
    _build_legacy_db(db_path, with_document=False)
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM projects")
    conn.commit()
    conn.close()

    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    with own_engine.connect() as c:
        row = c.execute(text(
            "SELECT a.original_name, p.title FROM archive_documents a"
            " JOIN projects p ON p.id = a.project_id")).fetchone()
        assert c.execute(text("SELECT count(*) FROM archive_file_versions")).scalar() == 1
    assert row is not None, "архивный файл потерян"
    assert row[0] == "Схема №12.pdf"


def test_migration_drops_links_pointing_nowhere_without_corrupting_database(tmp_path):
    """Битая ссылка не переносится, но и не оставляет базу несогласованной."""
    from sqlalchemy import create_engine, text

    from app.db.database import Base
    from app.db.migrations import apply_migrations, foreign_key_check, set_user_version

    db_path = tmp_path / "broken_link.db"
    _build_legacy_db(db_path, with_document=False, with_link=False)
    conn = sqlite3.connect(db_path)
    # Ссылка на несуществующий документ возможна при выключенных FK.
    conn.execute("INSERT INTO document_file_links VALUES (999, 1)")
    # Ссылка на несуществующий файл — тоже.
    conn.execute("INSERT INTO document_file_links VALUES (1, 888)")
    conn.commit()
    conn.close()

    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    raw = own_engine.raw_connection()
    try:
        assert foreign_key_check(raw) == [], "после миграции база осталась битой"
    finally:
        raw.close()
    with own_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM document_archive_links")).scalar() == 0


def test_migration_of_already_migrated_database_adds_only_new_steps(tmp_path):
    """Обновление 1 -> 2 добавляет индексы и не трогает данные оператора.

    Сценарий повторяет настоящее состояние рабочей базы: она была переведена
    на версию 1 прежней версией программы, которая индексов ещё не знала.
    """
    from sqlalchemy import create_engine, text

    from app.db.database import Base
    from app.db.migrations import apply_migrations, get_user_version, set_user_version

    NEW_INDEXES = ("ix_documents_project", "ix_archive_documents_project",
                   "ix_doc_archive_links_archive", "ix_doc_archive_links_version",
                   "ix_materials_project", "ix_packages_project")

    db_path = tmp_path / "upgrade.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine, target=1)
    Base.metadata.create_all(bind=own_engine)

    # Снимаем индексы, как если бы базу перевела прежняя версия программы.
    plain = sqlite3.connect(db_path)
    present = {r[0] for r in plain.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    for name in NEW_INDEXES:
        if name in present:
            plain.execute(f"DROP INDEX {name}")
    plain.commit()
    plain.close()

    applied = apply_migrations(own_engine)
    assert applied == [
        "add_lookup_indexes",
        "protect_issued_document_versions",
        "restore_version_number_uniqueness",
        "document_dates_and_links",
        "material_test_act_links",
    ], f"неожиданный набор шагов: {applied}"

    raw = own_engine.raw_connection()
    try:
        assert get_user_version(raw) == SCHEMA_VERSION
    finally:
        raw.close()
    now = {r[0] for r in sqlite3.connect(db_path).execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    missing = [n for n in NEW_INDEXES if n not in now]
    assert not missing, f"индексы не созданы обновлением: {missing}"
    with own_engine.connect() as c:
        assert c.execute(text("SELECT title FROM projects")).scalar() == \
            "Строительство корпуса МФТИ", "обновление затронуло данные"
        assert c.execute(text("SELECT count(*) FROM archive_file_versions")).scalar() == 1


def test_migration_is_not_reapplied_to_current_database(tmp_path):
    """Повторный запуск на актуальной базе не меняет ничего."""
    from sqlalchemy import create_engine

    from app.db.database import Base
    from app.db.migrations import apply_migrations, get_user_version, set_user_version

    db_path = tmp_path / "current.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    assert apply_migrations(own_engine) == [], "актуальная миграция применена повторно"
    raw = own_engine.raw_connection()
    try:
        assert get_user_version(raw) == SCHEMA_VERSION
    finally:
        raw.close()


def test_database_newer_than_program_is_not_downgraded(tmp_path):
    """Более новая схема не переписывается под старую программу."""
    from sqlalchemy import create_engine

    from app.db.migrations import apply_migrations, get_user_version, set_user_version

    db_path = tmp_path / "future.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 99)
    finally:
        raw.close()
    with pytest.raises(RuntimeError, match="новее поддерживаемой"):
        apply_migrations(own_engine)
    raw = own_engine.raw_connection()
    try:
        assert get_user_version(raw) == 99, "версия чужой схемы затёрта"
    finally:
        raw.close()


def test_integrity_check_accepts_any_connection_kind(tmp_path):
    """Проверка целостности должна работать с любым видом подключения.

    Раньше она падала вместо того, чтобы сообщить о проблеме, если её
    вызывали с подключением из ``engine.raw_connection()``.
    """
    from sqlalchemy import create_engine

    from app.db.database import Base
    from app.db.migrations import apply_migrations, foreign_key_check, set_user_version

    db_path = tmp_path / "integrity.db"
    _build_legacy_db(db_path)
    own_engine = create_engine(f"sqlite:///{db_path}")
    raw = own_engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()
    apply_migrations(own_engine)
    Base.metadata.create_all(bind=own_engine)

    pooled = own_engine.raw_connection()
    try:
        assert foreign_key_check(pooled) == []
    finally:
        pooled.close()
    with own_engine.connect() as c:
        assert foreign_key_check(c) == []
    assert foreign_key_check(sqlite3.connect(db_path)) == []


def test_lookup_indexes_exist_for_project_queries(tmp_path, db):
    """Выборки интерфейса опираются на индексы, а не на просмотр таблиц."""
    conn = db.connection()
    names = {
        r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index'")
    }
    for expected in ("ix_documents_project", "ix_archive_documents_project",
                     "ix_doc_archive_links_archive", "ix_doc_archive_links_version",
                     "ix_materials_project", "ix_packages_project"):
        assert expected in names, f"нет индекса {expected}"

    for table, column in (("documents", "project_id"),
                          ("archive_documents", "project_id"),
                          ("document_archive_links", "archive_document_id")):
        plan = conn.exec_driver_sql(
            f"EXPLAIN QUERY PLAN SELECT id FROM {table} WHERE {column} = 1").fetchall()
        assert any("USING INDEX" in r[-1] or "USING COVERING INDEX" in r[-1] for r in plan), (
            f"{table}.{column} читается полным просмотром таблицы: {plan}")


def test_bootstrap_table_from_legacy_schema_does_not_break_migration(db, tmp_path):
    """Справочник, пришедший из прежней версии, не должен ронять миграцию.

    Дефект (найден при проверке этапа 1): create_table() выполняла безусловный
    CREATE TABLE. Если направления или виды разделов уже были в унаследованной
    базе, миграция падала на 'table already exists' — оператор терял доступ к
    базе целиком вместе с данными.
    """
    from app.db import migrations
    from app.db.models import Direction
    from app.db.database import engine

    raw = engine.raw_connection()
    try:
        # Повторяем вызов на уже существующем справочнике.
        migrations.create_table(raw, Direction.__table__)
        # И ensure_bootstrap_tables тоже должен быть идемпотентен.
        migrations.ensure_bootstrap_tables(raw)
        names = [
            r[0] for r in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "directions" in names
        # Данные справочника не потеряны.
        count = raw.execute("SELECT count(*) FROM directions").fetchone()[0]
        assert count > 0, "идемпотентный create_table стёр справочник"
    finally:
        raw.close()


def test_bootstrap_reference_table_in_old_shape_is_completed(db):
    """Справочник в СТАРОЙ форме: недостающие колонки добавляются, данные целы.

    Дефект (найден при повторной проверке): первая правка create_table()
    просто пропускала существующую таблицу. Если справочник приходил из
    прежней версии без нужных колонок, падение уезжало из миграции в
    наполнение справочников и выглядело как 'no such column' уже после того,
    как перенос данных считался завершённым.
    """
    import sqlite3 as sq
    from app.config import DB_PATH
    from app.db import migrations
    from app.db.database import engine

    # Пересоздаём directions вручную в старой форме: без sort_order,
    # с записью, внесённой оператором.
    engine.dispose()
    conn = sq.connect(str(DB_PATH))
    try:
        conn.execute("DROP TABLE directions")
        conn.execute("CREATE TABLE directions(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO directions(name) VALUES ('Код оператора 777')")
        conn.commit()
    finally:
        conn.close()

    raw = engine.raw_connection()
    try:
        migrations.ensure_bootstrap_tables(raw)
        raw.commit()
    finally:
        raw.close()

    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(directions)")]
        assert "sort_order" in cols, f"колонка sort_order не добавлена: {cols}"
        kept = conn.exec_driver_sql(
            "SELECT count(*) FROM directions WHERE name='Код оператора 777'"
        ).scalar()
        assert kept == 1, "запись оператора потеряна при дополнении справочника"


# =====================================================================
# ЗАЩИТА ВЫПУЩЕННЫХ ВЕРСИЙ (ТЗ п.54, 85)
# =====================================================================


def test_shared_physical_file_survives_deletion_of_one_document(db, project, tmp_path):
    """Общий файл не удаляется вместе с одним из документов.

    add_version() не создаёт копию, если такой файл уже лежит в архиве
    (ТЗ п.84), поэтому две версии разных документов могут указывать на один
    путь. Удаление одного документа не должно стирать доказательство
    у другого (ТЗ п.54, 90).
    """
    first = tmp_path / "Схема.pdf"
    first.write_bytes("содержимое A".encode())
    doc_a = add_file_to_archive(db, first, project.id)

    second = tmp_path / "Схема.pdf"
    second.write_bytes("содержимое B".encode())
    doc_b = add_file_to_archive(db, second, project.id)
    add_version(db, doc_a.id, second)

    stored_paths = [v.stored_path for v in db.query(ArchiveFileVersion).all()]
    assert len(set(stored_paths)) < len(stored_paths), (
        "ожидалась общая копия файла между документами"
    )

    delete_archive_document(db, doc_b.id)

    assert find_missing_files(db) == [], (
        "после удаления одного документа файл второго остался в базе без копии"
    )
    db.expire_all()
    assert len(db.query(ArchiveFileVersion).all()) == 2


def test_issued_document_version_blocks_project_deletion(db, project):
    """Выпущенная версия — исторический результат (ТЗ п.85), проект с ней неудалим."""
    from app.core.services.project_service import (
        ProjectError, can_delete_project, delete_project, project_statistics,
    )
    from app.config import utcnow

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, issued_at=utcnow(), is_actual=True,
        )
    )
    db.commit()

    allowed, stats = can_delete_project(db, project.id)
    assert allowed is False, "проект с выпущенным документом можно удалить"
    assert stats["issued_versions"] == 1
    assert project_statistics(db, project.id)["issued_versions"] == 1

    with pytest.raises(ProjectError, match="выпущенных версий"):
        delete_project(db, project.id)

    db.rollback()
    assert db.query(DocumentVersion).count() == 1, (
        "выпущенная версия исчезла при попытке удаления проекта"
    )


def test_database_itself_protects_issued_version_from_cascade(db, project):
    """Защита обеспечена самой БД, а не только проверкой сервиса.

    Проверка сервиса обходится прямым db.delete() — то есть любым другим
    вызывающим кодом, включая каскад удаления проекта.
    """
    from app.config import utcnow

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    issued = DocumentVersion(
        document_id=document.id, version_no=1,
        payload={"номер": "12"}, issued_at=utcnow(), is_actual=True,
    )
    db.add(issued)
    db.commit()

    db.delete(document)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    assert db.get(DocumentVersion, issued.id) is not None, (
        "БД позволила каскадом стереть выпущенную версию"
    )


def test_migration_003_protects_issued_versions_of_legacy_v2_database(tmp_path):
    """Миграция переводит настоящую v2-таблицу на RESTRICT, не теряя выпуски.

    Проверяется не «сработала ли миграция», а три свойства результата:
    версия схемы, действие внешнего ключа и сохранность выпуска.
    """
    own_engine, applied, db_path = _migrate(tmp_path, "v2.db", with_version=True)

    # Возвращаем таблицу к тому виду, какой был в v2: CASCADE вместо RESTRICT,
    # с одной выпущенной версией. create_all в _migrate создаёт её уже с
    # защитой, поэтому заменяем её вручную.
    from app.db.database import Base

    Base.metadata.create_all(bind=own_engine)
    raw = sqlite3.connect(db_path)
    try:
        raw.executescript(
            """
            CREATE TABLE document_versions__v2 (
                id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL, form_version_id INTEGER,
                payload JSON NOT NULL, issued_at DATETIME,
                is_actual BOOLEAN NOT NULL, created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(document_id) REFERENCES documents (id)
                    ON DELETE CASCADE,
                FOREIGN KEY(form_version_id) REFERENCES normative_forms (id)
                    ON DELETE RESTRICT
            );
            INSERT INTO document_versions__v2 SELECT * FROM document_versions;
            DROP TABLE document_versions;
            ALTER TABLE document_versions__v2 RENAME TO document_versions;
            INSERT INTO document_versions (id, document_id, version_no, payload,
                                          issued_at, is_actual, created_at)
            VALUES (1, 1, 1, '{"номер": "15"}',
                    '2026-09-30 09:00:00.000000', 1,
                    '2026-09-30 09:00:00.000000');
            PRAGMA user_version = 2;
            """
        )
        raw.commit()
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [
            row[6] for row in raw.execute("PRAGMA foreign_key_list(document_versions)")
            if row[3] == "document_id"
        ] == ["CASCADE"], "тест не воспроизводит незащищённую схему v2"
    finally:
        raw.close()

    assert apply_migrations(own_engine) == [
        "protect_issued_document_versions", "restore_version_number_uniqueness",
        "document_dates_and_links", "material_test_act_links",
    ]

    raw = sqlite3.connect(db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        actions = {
            row[3]: row[6]
            for row in raw.execute("PRAGMA foreign_key_list(document_versions)")
        }
        assert actions["document_id"] == "RESTRICT"
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        kept = raw.execute(
            "SELECT payload, issued_at FROM document_versions WHERE id = 1"
        ).fetchone()
        assert kept is not None, "выпущенная версия потеряна при миграции"
        assert kept[0] == '{"номер": "15"}'
        assert kept[1] is not None, "дата выпуска потеряна при миграции"
    finally:
        raw.close()

    assert apply_migrations(own_engine) == [], "миграция 003 не идемпотентна"


# =====================================================================
# СВЕРКА СХЕМЫ С МОДЕЛЬЮ (ТЗ п.97)
# =====================================================================


def test_schema_mismatch_stops_startup_with_readable_report(db):
    """База с новыми именами и старыми колонками останавливает запуск.

    Миграции узнают унаследованную схему по именам таблиц, поэтому такая
    база проходит мимо них без следа. Без сверки с моделью программа
    рапортовала бы об успехе и упала бы позже, при первом обращении.
    """
    from app.db.database import engine, init_db
    from app.db.migrations import schema_problems, verify_schema

    verify_schema(engine)  # на только что созданной схеме расхождений нет

    # Таблица пересоздаётся копированием, а не переименованием: при RENAME
    # SQLite переписывает ссылки на documents во всех остальных таблицах,
    # и следующая фикстура падает на отсутствующей documents__keep.
    # foreign_keys выключен на время подмены, иначе DROP не пройдёт.
    raw = engine.raw_connection()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN")
        raw.execute(
            """
            CREATE TABLE documents__stale (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
                doc_type VARCHAR NOT NULL, number VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
            """
        )
        raw.execute("DROP TABLE documents")
        raw.execute("ALTER TABLE documents__stale RENAME TO documents")
        raw.execute("COMMIT")
    except Exception:
        raw.execute("ROLLBACK")
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()

    with engine.connect() as conn:
        problems = schema_problems(conn)
    assert problems, "расхождение схемы не обнаружено"
    assert any("documents" in p and "form_version_id" in p for p in problems), (
        f"не назван конкретный столбец: {problems}"
    )

    with pytest.raises(RuntimeError, match="не соответствует программе"):
        verify_schema(engine)

    # Проверяется именно init_db — тот путь, который проходит программа при
    # запуске. Ранняя версия теста звала verify_schema напрямую и потому
    # не замечала, что отключение проверки в init_db не ловится.
    with pytest.raises(RuntimeError, match="не соответствует программе"):
        init_db()


def test_schema_problems_empty_on_healthy_database(db):
    """Здоровая база не должна давать ложных срабатываний проверки."""
    from app.db.database import engine
    from app.db.migrations import schema_problems, verify_schema

    with engine.connect() as conn:
        assert schema_problems(conn) == []
    verify_schema(engine)


def test_schema_problems_detect_missing_table_and_dangling_fk(db):
    """Проверка замечает и отсутствующую таблицу, и внешний ключ в никуда."""
    from app.db.database import engine
    from app.db.migrations import schema_problems

    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE history_events")
    with engine.connect() as conn:
        problems = schema_problems(conn)
    assert any("history_events" in p for p in problems), problems


@pytest.mark.gui
def test_operator_sees_schema_mismatch_reason_on_startup(
    db, monkeypatch, gui_support, qapp
):
    """При сбое схемы оператор видит причину, а не молчаливую неудачу.

    Приложение собирается как оконное (console=False), поэтому
    необработанное исключение не даёт сообщения на экране: без проверки
    отказ выглядел бы как «окно не открылось».
    """
    import main as app_main

    def broken_init_db():
        raise RuntimeError(
            "Схема базы не соответствует программе:\n"
            "  - в таблице documents нет колонки form_version_id\n"
            "Обновление остановлено, чтобы не работать с повреждёнными данными. "
            "Сообщите текст оператору и верните резервную копию из storage/backups."
        )

    monkeypatch.setattr(app_main, "init_db", broken_init_db)
    # QApplication создавать второй раз нельзя: в Qt он синглтон, и
    # повторный QApplication(sys.argv) роняет процесс с segfault.
    monkeypatch.setattr(
        app_main.QApplication, "__new__", lambda cls, *a, **k: qapp
    )
    assert app_main.main() == 1

    assert len(gui_support["critical"]) == 1, "оператор не получил сообщение о сбое"
    text = gui_support["critical"][0][2]
    assert "form_version_id" in text, "в сообщении нет сути проблемы"
    assert "backups" in text, "в сообщении нет подсказки, что делать"


def test_schema_check_detects_lost_foreign_keys_and_uniqueness(db):
    """Сверка замечает пропажу внешних ключей и уникальности.

    Проверки только имён таблиц и колонок недостаточно: таблица может
    сохранить форму, потеряв все внешние ключи. Для document_versions это
    означало бы, что защита выпуска от каскада (ТЗ п.85) не существует,
    и сверка молчала бы.
    """
    from app.db.database import engine
    from app.db.migrations import schema_problems

    raw = engine.raw_connection()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN")
        raw.execute(
            """
            CREATE TABLE document_versions__plain (
                id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL, form_version_id INTEGER,
                payload JSON NOT NULL, issued_at DATETIME,
                is_actual BOOLEAN NOT NULL, created_at DATETIME NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
        raw.execute("DROP TABLE document_versions")
        raw.execute("ALTER TABLE document_versions__plain RENAME TO document_versions")
        raw.execute("COMMIT")
    except Exception:
        raw.execute("ROLLBACK")
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()

    with engine.connect() as conn:
        problems = schema_problems(conn)
    assert any(
        "внешнего ключа document_id -> documents" in p for p in problems
    ), f"пропажа внешнего ключа не замечена: {problems}"
    assert any(
        "внешнего ключа form_version_id -> normative_forms" in p for p in problems
    ), problems
    assert any("нет уникальности" in p for p in problems), problems


def test_schema_check_detects_wrong_foreign_key_action(db):
    """Сверка замечает внешний ключ, у которого не то действие удаления.

    Особенно важно для RESTRICT на document_versions: CASCADE вместо него
    означает возврат к тихой потере выпущенных версий.
    """
    from app.db.database import engine
    from app.db.migrations import schema_problems

    raw = engine.raw_connection()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN")
        raw.execute(
            """
            CREATE TABLE document_versions__cascade (
                id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL, form_version_id INTEGER,
                payload JSON NOT NULL, issued_at DATETIME,
                is_actual BOOLEAN NOT NULL, created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (document_id, version_no),
                FOREIGN KEY(document_id) REFERENCES documents (id)
                    ON DELETE CASCADE,
                FOREIGN KEY(form_version_id) REFERENCES normative_forms (id)
                    ON DELETE RESTRICT
            )
            """
        )
        raw.execute(
            "INSERT INTO document_versions__cascade "
            "SELECT id, document_id, version_no, form_version_id, payload, "
            "issued_at, is_actual, created_at FROM document_versions"
        )
        raw.execute("DROP TABLE document_versions")
        raw.execute(
            "ALTER TABLE document_versions__cascade RENAME TO document_versions"
        )
        raw.execute("COMMIT")
    except Exception:
        raw.execute("ROLLBACK")
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()

    with engine.connect() as conn:
        problems = schema_problems(conn)
    assert any(
        "удаляет по CASCADE" in p and "ожидалось RESTRICT" in p for p in problems
    ), f"неверное действие внешнего ключа не замечено: {problems}"


def test_issued_version_survives_because_database_rejects_delete(db, project):
    """Отказ даёт сама БД, а не побочный эффект ограничения NOT NULL.

    Раньше ORM шёл с UPDATE document_id=NULL, и удаление отклонялось только
    потому, что колонка NOT NULL. Это «испортить и упасть»: с nullable
    колонкой версия осиротела бы. Теперь отказ приходит от DELETE.
    """
    from sqlalchemy import event
    from sqlalchemy.exc import IntegrityError

    from app.config import utcnow

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, issued_at=utcnow(), is_actual=True,
        )
    )
    db.commit()

    statements: list[str] = []
    engine = db.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    try:
        db.delete(document)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert not any(
        s.upper().startswith("UPDATE DOCUMENT_VERSIONS") for s in statements
    ), f"ORM попытался испортить версию перед отказом: {statements}"
    assert any("DELETE FROM DOCUMENTS" in s.upper() for s in statements), statements


def _set_version(engine, version: int) -> None:
    """Проставить user_version, как если бы базу обновила прежняя версия."""
    from app.db.migrations import set_user_version

    raw = engine.raw_connection()
    try:
        set_user_version(raw, version)
    finally:
        raw.close()


def _rebuild_document_versions(engine, *, with_unique: bool) -> None:
    """Пересоздать document_versions, с ограничением уникальности или без.

    Нужно, чтобы получить состояние, оставляемое пересборкой таблицы:
    UNIQUE, объявленный в CREATE TABLE, становится автоиндексом, который
    нельзя удалить обычным DROP INDEX.
    """
    unique = "UNIQUE (document_id, version_no)," if with_unique else ""
    raw = engine.raw_connection()
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN")
        raw.execute(
            f"""
            CREATE TABLE document_versions__manual (
                id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL, form_version_id INTEGER,
                payload JSON NOT NULL, issued_at DATETIME,
                is_actual BOOLEAN NOT NULL, created_at DATETIME NOT NULL,
                PRIMARY KEY (id), {unique}
                FOREIGN KEY(document_id) REFERENCES documents (id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(form_version_id) REFERENCES normative_forms (id)
                    ON DELETE RESTRICT
            )
            """
        )
        raw.execute(
            "INSERT INTO document_versions__manual "
            "SELECT id, document_id, version_no, form_version_id, payload, "
            "issued_at, is_actual, created_at FROM document_versions"
        )
        raw.execute("DROP TABLE document_versions")
        raw.execute(
            "ALTER TABLE document_versions__manual RENAME TO document_versions"
        )
        raw.execute("COMMIT")
    except Exception:
        raw.execute("ROLLBACK")
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()


def test_migration_004_restores_version_number_uniqueness(db, project):
    """Ограничение уникальности номера версии восстанавливается.

    Миграция 003 пересобирала таблицу и теряла UNIQUE, объявленный в модели.
    Обнаружить это можно было только сверкой схемы: колонки на месте, а
    ограничения нет, поэтому два выпуска с одинаковым номером проходили.
    """
    from app.config import utcnow
    from app.db.database import engine
    from app.db.migrations import _missing_unique, apply_migrations

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, issued_at=utcnow(), is_actual=True,
        )
    )
    db.commit()

    # Снимаем ограничение — состояние после пересборки таблицы. UNIQUE,
    # объявленный в самой таблице, является автоиндексом, и DROP INDEX для
    # него запрещён: единственный способ убрать — пересобрать таблицу.
    _rebuild_document_versions(engine, with_unique=False)
    _set_version(engine, 3)  # состояние после обновления на 003

    raw = engine.raw_connection()
    try:
        assert _missing_unique(
            raw, "document_versions", ("document_id", "version_no")
        ), "подготовка не сняла ограничение"
    finally:
        raw.close()

    assert "restore_version_number_uniqueness" in apply_migrations(engine)

    raw = engine.raw_connection()
    try:
        assert not _missing_unique(
            raw, "document_versions", ("document_id", "version_no")
        ), "ограничение не восстановлено"
    finally:
        raw.close()

    assert apply_migrations(engine) == [], "миграция 004 не идемпотентна"

    db.expire_all()
    assert db.get(DocumentVersion, 1) is not None, "выпуск потерян при миграции"
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"повтор": True}, is_actual=False,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migration_004_refuses_to_guess_when_duplicates_exist(db, project):
    """При повторах в данных ограничение не ставится молча.

    Иначе оператор получил бы отказ, не понимая причины, либо потерял бы
    данные. Сообщение обязано называть, что именно мешает.
    """
    from app.db.database import engine
    from app.db.migrations import apply_migrations

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, is_actual=False,
        )
    )
    db.commit()

    # Порча вносится в обход ограничения: сначала снимается оно, и только
    # потом появляется повтор. Так БД приходит к оператору извне, а не
    # через собственный код, который ограничение не дал бы обойти.
    _rebuild_document_versions(engine, with_unique=False)
    _set_version(engine, 3)
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"повтор": True}, is_actual=False,
        )
    )
    db.commit()

    with pytest.raises(RuntimeError, match="повторов"):
        apply_migrations(engine)


def test_migrations_alone_restore_uniqueness_without_model_help(db, project):
    """Миграции обязаны восстанавливать ограничение сами.

    create_all создаёт автоиндекс у только что созданной таблицы, поэтому
    сверка, идущая после него, увидела бы уже исправленную БД и пропустила
    бы миграцию, оставив ограничение ненавязанным. Миграции проверяются на
    БД, где таблица создана вручную, без участия create_all.
    """
    from app.db.migrations import _missing_unique, apply_migrations

    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, is_actual=True,
        )
    )
    db.commit()

    # Таблица собирается вручную, ровно как её оставляет пересборка.
    engine = db.get_bind()
    _rebuild_document_versions(engine, with_unique=False)
    _set_version(engine, 3)

    conn = engine.raw_connection()
    try:
        assert _missing_unique(
            conn, "document_versions", ("document_id", "version_no")
        ), "подготовка не сняла ограничение"
    finally:
        conn.close()

    # Миграция 004 обязана поставить его, не дожидаясь create_all.
    assert "restore_version_number_uniqueness" in apply_migrations(engine)

    conn = engine.raw_connection()
    try:
        assert not _missing_unique(
            conn, "document_versions", ("document_id", "version_no")
        ), "миграции не восстановили уникальность без create_all"
    finally:
        conn.close()


def test_migration_005_adds_document_links(db, project):
    """Связи между документами появляются при обновлении.

    Без них не проверяется логика дат (ТЗ п.87), поэтому обновление с версии
    4 обязано добавить таблицу, не меняя имеющиеся данные.
    """
    from app.db.database import engine
    from app.db.migrations import apply_migrations, table_exists

    aosr = Document(project_id=project.id, doc_type="АОСР", number="1")
    db.add(aosr)
    db.commit()

    # Схема в состоянии до обновления: таблицы связей нет.
    raw = engine.raw_connection()
    try:
        raw.execute("DROP TABLE document_links")
        raw.execute("PRAGMA user_version = 4")
        raw.commit()
        assert not table_exists(raw, "document_links")
    finally:
        raw.close()

    assert "document_dates_and_links" in apply_migrations(engine)

    raw = engine.raw_connection()
    try:
        assert table_exists(raw, "document_links")
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        raw.close()

    # Данные оператора на месте, связь документов записывается и читается.
    aook = Document(project_id=project.id, doc_type="АООК", number="1")
    db.add(aook)
    db.commit()
    db.add(DocumentLink(document_id=aook.id, related_document_id=aosr.id,
                        link_role="Завершает акт"))
    db.commit()
    db.refresh(aook)
    assert aook.document_links[0].related_document.number == "1"
    assert db.query(Document).count() == 2
