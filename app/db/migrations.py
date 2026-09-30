"""Версионирование схемы БД. ТЗ п.97, 110.

Схема не редактируется «по месту»: каждое изменение оформляется миграцией с
номером, применяется один раз и фиксируется в ``PRAGMA user_version``.

Почему таблицы пересоздаются, а не ALTER-ятся: SQLite не умеет менять
ограничения и внешние ключи существующей таблицы. Поэтому миграция идёт по
стандартной схеме: создать новую таблицу, перенести данные, удалить старую,
переименовать новую.
"""

import logging
import os
import sqlite3

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, Time, Unicode
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateTable

from app.core import domain

logger = logging.getLogger(__name__)

# Текущая версия схемы. Увеличивать при добавлении миграции.
SCHEMA_VERSION = 6

# Справочники и формы, которые миграция создаёт сама, до переноса данных.
# Миграция не должна зависеть от того, что create_all уже отработал.
BOOTSTRAP_MODELS = (
    "Direction", "Organization", "Representative",
    "SectionKind", "MaterialType", "NormativeForm",
)


# =====================================================================
# Вспомогательные функции
# =====================================================================

def _ddl(element) -> str:
    """Скомпилировать DDL-элемент SQLAlchemy в текст для SQLite."""
    return str(element.compile(dialect=sqlite.dialect())).strip()


def create_table(conn, table) -> None:
    """Создать таблицу, если её нет, и дополнить недостающими колонками.

    Отказоустойчивость обязательна: справочники (направления, виды разделов)
    могли прийти из прежней версии программы, и повторный CREATE TABLE на них
    обрывал бы миграцию целиком — вместе с данными оператора.

    Но одной идемпотентности мало: справочник может прийти в СТАРОЙ форме, то
    есть без нужных колонок. Молча пропустить такую таблицу тоже нельзя —
    падение случилось бы позже и в невнятном месте («no such column» уже во
    время наполнения). Поэтому недостающие колонки добавляются на месте, с
    сохранением уже внесённых оператором записей.

    Все обязательные колонки справочников объявлены со значением по
    умолчанию, поэтому ADD COLUMN допустим: SQLite не разрешает добавлять
    NOT NULL без DEFAULT.
    """
    if not table_exists(conn, table.name):
        conn.execute(_ddl(CreateTable(table)))
        return

    existing = set(table_columns(conn, table.name))
    for column in table.columns:
        if column.name in existing or column.primary_key:
            continue
        # CreateColumn, а не Column.compile: одиночная колонка компилируется
        # как "directions.sort_order", что для ALTER TABLE является синтаксисом.
        definition = _ddl(CreateColumn(column))
        conn.execute(
            f"ALTER TABLE {table.name} ADD COLUMN {_with_default(definition, column)}"
        )
        logger.warning(
            "миграция: в справочник %s добавлена отсутствовавшая колонка %s",
            table.name, column.name,
        )


def create_indexes(conn) -> None:
    """Создать отсутствующие индексы.

    Пересобранные миграцией таблицы теряют свои индексы, а create_all их
    не вернёт: таблица уже существует и он её пропускает.
    """
    from app.db.database import Base

    for table in Base.metadata.sorted_tables:
        if not table_exists(conn, table.name):
            continue
        for index in table.indexes:
            conn.execute(_ddl(CreateIndex(index, if_not_exists=True)))


def ensure_bootstrap_tables(conn) -> None:
    """Создать справочники, на которые опирается перенос данных."""
    from app.db import models

    for name in BOOTSTRAP_MODELS:
        create_table(conn, getattr(models, name).__table__)


def table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def table_columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# Значения по умолчанию для добавления обязательной колонки в существующую
# таблицу. SQLite требует DEFAULT при ADD COLUMN NOT NULL, а объявленные в
# моделях умолчания заданы на стороне Python и в DDL не попадают.
_SQLITE_FALLBACK_DEFAULTS = (
    ((Integer, Boolean), "0"),
    ((DateTime, Date, Time), "'1970-01-01 00:00:00'"),
    ((String, Text, Unicode), "''"),
)


def _with_default(definition: str, column) -> str:
    """Дописать DEFAULT к обязательной колонке, если его нет в DDL."""
    if "NOT NULL" not in definition.upper() or "DEFAULT" in definition.upper():
        return definition
    for types, value in _SQLITE_FALLBACK_DEFAULTS:
        if isinstance(column.type, types):
            marker = " NOT NULL"
            index = definition.upper().rindex(marker)
            return f"{definition[:index]} DEFAULT {value}{definition[index:]}"
    return definition


def file_type_from_name(*names: str) -> str:
    """Определить тип файла по расширению."""
    for name in names:
        if name and "." in name:
            ext = name.rsplit(".", 1)[-1].strip().lower()
            if ext and ext.isalnum() and len(ext) <= 5:
                return ext
    return ""


# =====================================================================
# Миграция 001: унаследованная схема -> схема этапа 1
# =====================================================================

PROJECTS_V1_DDL = """
CREATE TABLE projects__new (
    id INTEGER NOT NULL,
    direction_id INTEGER NOT NULL,
    title VARCHAR NOT NULL,
    address VARCHAR,
    customer_org_id INTEGER,
    general_contractor_org_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(direction_id) REFERENCES directions (id) ON DELETE RESTRICT,
    FOREIGN KEY(customer_org_id) REFERENCES organizations (id) ON DELETE SET NULL,
    FOREIGN KEY(general_contractor_org_id) REFERENCES organizations (id) ON DELETE SET NULL
)
"""

DOCUMENTS_V1_DDL = """
CREATE TABLE documents__new (
    id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    doc_type VARCHAR NOT NULL,
    number VARCHAR NOT NULL,
    doc_date DATE,
    status VARCHAR NOT NULL,
    form_version_id INTEGER,
    exploitation_missing_choice VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
    FOREIGN KEY(form_version_id) REFERENCES normative_forms (id) ON DELETE RESTRICT
)
"""

ARCHIVE_DOCUMENTS_V1_DDL = """
CREATE TABLE archive_documents__new (
    id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    category VARCHAR NOT NULL,
    file_type VARCHAR NOT NULL,
    original_name VARCHAR NOT NULL,
    number VARCHAR,
    doc_date DATE,
    validity_from DATE,
    validity_to DATE,
    note TEXT,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    -- RESTRICT, а не CASCADE: архивный файл является доказательством и не
    -- должен исчезать вместе с проектом (ТЗ п.54, 109). Проект с архивом
    -- удаляется только после явной очистки архива оператором.
    FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT
)
"""

ARCHIVE_FILE_VERSIONS_V1_DDL = """
CREATE TABLE archive_file_versions__new (
    id INTEGER NOT NULL,
    archive_document_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    stored_path VARCHAR NOT NULL,
    file_hash VARCHAR NOT NULL,
    file_size INTEGER,
    is_actual BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (archive_document_id, version_no),
    FOREIGN KEY(archive_document_id) REFERENCES archive_documents (id) ON DELETE CASCADE
)
"""

DOCUMENT_ARCHIVE_LINKS_V1_DDL = """
CREATE TABLE document_archive_links__new (
    id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    archive_document_id INTEGER NOT NULL,
    archive_version_id INTEGER,
    link_role VARCHAR NOT NULL,
    order_no INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (document_id, archive_document_id, link_role),
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE,
    FOREIGN KEY(archive_document_id) REFERENCES archive_documents (id) ON DELETE CASCADE,
    FOREIGN KEY(archive_version_id) REFERENCES archive_file_versions (id) ON DELETE RESTRICT
)
"""

# Соответствие старых кодов типов документов новым (ТЗ п.42).
LEGACY_DOC_TYPE_MAP = {
    "AOSR": domain.DOC_TYPE_AOSR,
    "АОСР": domain.DOC_TYPE_AOSR,
    "AOOK": domain.DOC_TYPE_AOOK,
    "АООК": domain.DOC_TYPE_AOOK,
    "AOU_SITO": domain.DOC_TYPE_AOU_SITO,
    "АОУСИТО": domain.DOC_TYPE_AOU_SITO,
    "TEST_ACT": domain.DOC_TYPE_TEST_ACT,
    "АКТ_ИСПЫТАНИЙ": domain.DOC_TYPE_TEST_ACT,
}

DEFAULT_LINK_ROLE = domain.LINK_ROLE_ATTACHMENT


DOCUMENT_VERSIONS_V3_DDL = """
CREATE TABLE document_versions__new (
    id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    form_version_id INTEGER,
    payload JSON NOT NULL,
    issued_at DATETIME,
    is_actual BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_document_version_no UNIQUE (document_id, version_no),
    FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE RESTRICT,
    FOREIGN KEY(form_version_id) REFERENCES normative_forms (id) ON DELETE RESTRICT
)
"""


def _begin_rebuild(conn, table: str, ddl: str) -> None:
    """Освободить имя таблицы и создать под неё новую (``{table}__new``)."""
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}__old")
    conn.execute(ddl)


def _finish_rebuild(conn, new_table: str, final_name: str, old_table: str) -> None:
    """Подменить старую таблицу новой и удалить старую.

    Имена задаются явно: при переносе данных меняется и имя таблицы
    (archive_files -> archive_documents, document_file_links ->
    document_archive_links).
    """
    conn.execute(f"ALTER TABLE {new_table} RENAME TO {final_name}")
    conn.execute(f"DROP TABLE {old_table}")


def _rebuild(conn, table: str, ddl: str, insert_sql: str, params=()) -> None:
    """Пересоздать таблицу целиком.

    ``insert_sql`` читает переименованную таблицу, поэтому источник указывается
    шаблоном ``{src}`` — после шага переименования её новое имя.
    """
    _begin_rebuild(conn, table, ddl)
    conn.execute(insert_sql.format(src=f"{table}__old"), params)
    _finish_rebuild(conn, f"{table}__new", table, f"{table}__old")


# Заголовок служебного проекта для записей, оставшихся без проекта.
ORPHAN_PROJECT_TITLE = "Записи без проекта (перенесено при миграции)"


def _ensure_orphan_project(conn) -> int:
    """Проект-приёмник для записей, оставшихся без проекта.

    Унаследованная схема не требовала проект у документа и архивного файла,
    поэтому такие записи в ней возможны. Новая схема требует проект всегда.
    Удалять данные нельзя (ТЗ п.54), а молча приписывать их к чужому проекту
    значит выдумать то, чего оператор не указывал, поэтому создаётся отдельный
    видимый проект, который оператор разберёт вручную.
    """
    row = conn.execute(
        "SELECT id FROM projects WHERE title = ?", (ORPHAN_PROJECT_TITLE,)
    ).fetchone()
    if row is not None:
        return row[0]

    direction = conn.execute("SELECT id FROM directions ORDER BY sort_order, id LIMIT 1").fetchone()
    if direction is None:
        conn.execute(
            "INSERT INTO directions (name, sort_order) VALUES (?, 100)",
            (domain.DIRECTION_GENERAL,),
        )
        direction = conn.execute("SELECT id FROM directions LIMIT 1").fetchone()
    now = "CURRENT_TIMESTAMP"
    cursor = conn.execute(
        "INSERT INTO projects "
        "(direction_id, title, address, customer_org_id, general_contractor_org_id, "
        f" created_at, updated_at) VALUES (?,?,NULL,NULL,NULL,{now},{now})",
        (direction[0], ORPHAN_PROJECT_TITLE),
    )
    logger.warning(
        "миграция: создан проект «%s» для записей без проекта", ORPHAN_PROJECT_TITLE
    )
    return cursor.lastrowid


def _migrate_projects(conn) -> None:
    cols = table_columns(conn, "projects")
    if "direction" not in cols:
        return  # уже переведённая схема

    # Направления, встречающиеся в данных, но отсутствующие в справочнике,
    # не теряются: для них создаётся запись справочника.
    legacy_directions = [r[0] for r in conn.execute(
        "SELECT DISTINCT direction FROM projects WHERE direction IS NOT NULL"
    ).fetchall()]
    for name in legacy_directions:
        row = conn.execute("SELECT id FROM directions WHERE name=?", (name,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO directions (name, sort_order) VALUES (?, 100)", (name,)
            )

    _rebuild(
        conn, "projects", PROJECTS_V1_DDL,
        "INSERT INTO projects__new "
        "(id, direction_id, title, address, customer_org_id, "
        " general_contractor_org_id, created_at, updated_at) "
        "SELECT p.id, d.id, p.title, p.address, NULL, NULL, "
        "       COALESCE(p.created_at, CURRENT_TIMESTAMP), "
        "       COALESCE(p.created_at, CURRENT_TIMESTAMP) "
        "FROM {src} p LEFT JOIN directions d ON d.name = p.direction",
    )


def _migrate_archive(conn) -> None:
    if not table_exists(conn, "archive_files"):
        return
    cols = table_columns(conn, "archive_files")
    has_project = "project_id" in cols
    has_category = "category" in cols

    # Архивный документ обязан принадлежать проекту (ТЗ п.89, 90). В ранней
    # версии таблица не знала о проекте, и файлы могли существовать вовсе без
    # него, поэтому при отсутствии проекта создаётся проект-приёмник.
    fallback_project = None
    if not has_project:
        fallback_project = conn.execute("SELECT MIN(id) FROM projects").fetchone()[0]

    # Перенос выполняется построчно: тип файла и категория вычисляются в Python,
    # потому что в унаследованной таблице category не было, а в file_type лежала
    # категория архива.
    _begin_rebuild(conn, "archive_files", ARCHIVE_DOCUMENTS_V1_DDL)
    conn.execute(ARCHIVE_FILE_VERSIONS_V1_DDL)
    orphan_project = None
    for row in conn.execute("SELECT * FROM archive_files__old").fetchall():
        row = dict(zip(cols, row))
        raw_type = row.get("file_type") or ""
        if has_category and row.get("category"):
            category = row["category"]
        elif raw_type in domain.ARCHIVE_CATEGORIES:
            category = raw_type
        else:
            category = domain.ARCHIVE_CATEGORY_PROJECT
        file_type = file_type_from_name(
            row.get("original_name") or "", row.get("stored_path") or ""
        )
        created = row.get("created_at") or "1970-01-01 00:00:00"
        project_id = row.get("project_id") if has_project else None
        project_id = project_id or fallback_project
        if project_id is None:
            # Файл существует, а проекта нет: приёмник создаётся один раз.
            if orphan_project is None:
                orphan_project = _ensure_orphan_project(conn)
            project_id = orphan_project
        # Размер файла, если он доступен на диске: иначе останется неизвестным.
        try:
            file_size = os.path.getsize(row["stored_path"])
        except OSError:
            file_size = None
        conn.execute(
            "INSERT INTO archive_documents__new "
            "(id, project_id, category, file_type, original_name, number, doc_date,"
            " validity_from, validity_to, note, created_at) "
            "VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,?)",
            (row["id"], project_id, category, file_type,
             row["original_name"], created),
        )
        conn.execute(
            "INSERT INTO archive_file_versions__new "
            "(id, archive_document_id, version_no, stored_path, file_hash, "
            " file_size, is_actual, created_at) "
            "VALUES (?,?,?,?,?,?,1,?)",
            (row["id"], row["id"], row.get("version") or 1,
             row["stored_path"], row["file_hash"], file_size, created),
        )
    _finish_rebuild(conn, "archive_documents__new", "archive_documents",
                    "archive_files__old")
    # Версии файлов переезжают из временной таблицы в постоянную.
    conn.execute(
        "ALTER TABLE archive_file_versions__new RENAME TO archive_file_versions"
    )


def _migrate_documents(conn) -> None:
    cols = table_columns(conn, "documents")
    if not cols or "status" in cols:
        return
    _begin_rebuild(conn, "documents", DOCUMENTS_V1_DDL)
    orphan_project = None
    for row in conn.execute("SELECT * FROM documents__old").fetchall():
        rec = dict(zip(cols, row))
        doc_type = LEGACY_DOC_TYPE_MAP.get(
            (rec.get("doc_type") or "").strip(), rec.get("doc_type")
        )
        created = rec.get("created_at") or "1970-01-01 00:00:00"
        project_id = rec.get("project_id")
        if project_id is None:
            # Унаследованная схема разрешала документ без проекта.
            if orphan_project is None:
                orphan_project = _ensure_orphan_project(conn)
            project_id = orphan_project
        conn.execute(
            "INSERT INTO documents__new "
            "(id, project_id, doc_type, number, doc_date, status, form_version_id,"
            " exploitation_missing_choice, created_at, updated_at) "
            "VALUES (?,?,?,?,NULL,?,NULL,NULL,?,?)",
            (rec["id"], project_id, doc_type, rec["number"],
             domain.DOC_STATUS_DRAFT, created, created),
        )
    _finish_rebuild(conn, "documents__new", "documents", "documents__old")


def _migrate_links(conn) -> None:
    if not table_exists(conn, "document_file_links"):
        return
    cols = table_columns(conn, "document_file_links")
    _begin_rebuild(conn, "document_file_links", DOCUMENT_ARCHIVE_LINKS_V1_DDL)
    rows = conn.execute("SELECT * FROM document_file_links__old").fetchall()
    kept = 0
    dropped = 0
    for row in rows:
        rec = dict(zip(cols, row))
        document_id = rec.get("document_id")
        # file_id в старой схеме был одновременно и archive_files.id, и — после
        # миграции архива — archive_documents.id, поскольку id сохраняются.
        file_id = rec.get("file_id")

        # Унаследованная БД могла остаться с выключенными внешними ключами, то
        # есть содержать ссылки в никуда. Такие ссылки не переносятся: иначе
        # после миграции база останется несогласованной. Они не восстановимы,
        # поэтому фиксируются в журнале миграции.
        if conn.execute(
            "SELECT 1 FROM documents WHERE id=?", (document_id,)
        ).fetchone() is None:
            dropped += 1
            continue
        if conn.execute(
            "SELECT 1 FROM archive_documents WHERE id=?", (file_id,)
        ).fetchone() is None:
            dropped += 1
            continue

        version = conn.execute(
            "SELECT id FROM archive_file_versions WHERE archive_document_id=? "
            "ORDER BY version_no LIMIT 1", (file_id,)
        ).fetchone()
        kept += 1
        conn.execute(
            "INSERT INTO document_archive_links__new "
            "(id, document_id, archive_document_id, archive_version_id, link_role,"
            " order_no, created_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (kept, document_id, file_id,
             version[0] if version else None, DEFAULT_LINK_ROLE, kept),
        )
    if dropped:
        logger.warning(
            "миграция: %d связей указывали на несуществующие записи и не перенесены", dropped
        )
    _finish_rebuild(conn, "document_archive_links__new",
                    "document_archive_links", "document_file_links__old")


def migration_001(conn) -> None:
    """Унаследованная схема -> схема этапа 1."""
    ensure_bootstrap_tables(conn)
    _migrate_projects(conn)
    _migrate_archive(conn)
    _migrate_documents(conn)
    _migrate_links(conn)
    create_indexes(conn)


def migration_002(conn) -> None:
    """Индексы на внешние ключи, по которым идут выборки интерфейса.

    SQLite не создаёт индексы на внешних ключах сам, а первая версия схемы
    описывала индексы только там, где они требовались уникальностью. Из-за
    этого список документов и архива проекта (ТЗ п.30, 47) при большом числе
    записей читался полным просмотром таблицы.
    """
    create_indexes(conn)


def migration_003(conn) -> None:
    """Выпущенные версии документов защищены от каскадного удаления.

    Таблица пересобирается, потому что SQLite не умеет менять действие
    внешнего ключа существующей таблицы (ТЗ п.85, 54).
    """
    if not table_exists(conn, "document_versions"):
        return
    cols = table_columns(conn, "document_versions")
    if "document_id" not in cols:
        return

    # Решение принимается по модели, а не по одному признаку. Ранний выход
    # «FK уже RESTRICT» пропускал восстановление UNIQUE: таблица, пересобранная
    # прежней версией этой миграции без ограничения, считалась готовой.
    needs_rebuild = False
    for fk in conn.execute("PRAGMA foreign_key_list(document_versions)").fetchall():
        # (id, seq, table, from, to, on_update, on_delete, match)
        if fk[3] == "document_id" and (fk[6] or "").upper() != "RESTRICT":
            needs_rebuild = True
    if _missing_unique(conn, "document_versions", ("document_id", "version_no")):
        needs_rebuild = True
    if not needs_rebuild:
        return

    duplicates = conn.execute(
        "SELECT document_id, version_no, count(*) FROM document_versions "
        "GROUP BY document_id, version_no HAVING count(*) > 1"
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            "Нельзя восстановить ограничение уникальности версий: в базе "
            f"{len(duplicates)} повторов (document_id, version_no), "
            f"начиная с {duplicates[0][:2]}. Данные требуют разбора оператором; "
            "ничего не изменено."
        )

    _begin_rebuild(conn, "document_versions", DOCUMENT_VERSIONS_V3_DDL)
    conn.execute(
        "INSERT INTO document_versions__new "
        "(id, document_id, version_no, form_version_id, payload, issued_at,"
        " is_actual, created_at) "
        "SELECT id, document_id, version_no, form_version_id, payload, issued_at,"
        "       is_actual, created_at FROM document_versions__old"
    )
    _finish_rebuild(conn, "document_versions__new", "document_versions",
                    "document_versions__old")
    create_indexes(conn)


def _missing_unique(conn, table: str, columns: tuple) -> bool:
    """Есть ли уникальный индекс, покрывающий все указанные колонки."""
    for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
        # (seq, name, unique, origin, partial)
        if not index[2]:
            continue
        indexed = {
            r[2] for r in conn.execute(f"PRAGMA index_info({index[1]})").fetchall()
        }
        if set(columns) <= indexed:
            return False
    return True


def migration_004(conn) -> None:
    """Вернуть уникальность номера версии документа.

    Миграция 003 пересобирала таблицу, и её DDL не содержал ограничения
    UNIQUE (document_id, version_no), которое есть в модели. Ограничение
    пропало у всех, кто обновился до 003, а версия схемы при этом стала 3,
    поэтому исправлять пришлось отдельной миграцией (ТЗ п.85).
    """
    if not table_exists(conn, "document_versions"):
        return
    cols = table_columns(conn, "document_versions")
    if "document_id" not in cols or "version_no" not in cols:
        return
    if not _missing_unique(conn, "document_versions", ("document_id", "version_no")):
        return

    duplicates = conn.execute(
        "SELECT document_id, version_no, count(*) FROM document_versions "
        "GROUP BY document_id, version_no HAVING count(*) > 1"
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            "Нельзя восстановить ограничение уникальности версий: в базе "
            f"{len(duplicates)} повторов (document_id, version_no), "
            f"начиная с {duplicates[0][:2]}. Данные требуют разбора оператором; "
            "ничего не изменено."
        )

    # Уникальный индекс добавляется без пересборки таблицы: ограничение
    # уникальности в SQLite выражается именно индексом, и ALTER TABLE
    # здесь не нужен, а значит не затронуты ни данные, ни внешние ключи.
    conn.execute(
        "CREATE UNIQUE INDEX uq_document_version_no "
        "ON document_versions (document_id, version_no)"
    )


def migration_005(conn) -> None:
    """Связи между документами проекта. ТЗ п.43, 87, 89.

    Добавлена таблица ``document_links``: итоговый акт ссылается на акты,
    которые он завершает. Без этой связи нельзя проверить, что АООК не
    заканчивается раньше связанного АОСР (ТЗ п.87).

    Сроки работ хранятся в данных формы (``period_start`` / ``period_end``
    в версии документа) — там же, где остальные поля формы, поэтому второго
    места хранения не создаётся.

    Существующие данные не меняются: новая таблица пуста.
    """
    from app.db.models import DocumentLink

    create_table(conn, DocumentLink.__table__)
    create_indexes(conn)


def migration_006(conn) -> None:
    """Связь строки материала с актом испытаний. ТЗ п.44, 45, 49.

    Добавлена таблица ``material_test_act_links``: материал участвует в
    конкретных актах испытаний. Связь явная и ручная — сертификат или иной
    документ качества прикрепляется к конкретному акту, а не ко всем актам
    материода сразу (ТЗ п.45).

    Существующие данные не меняются: новая таблица пуста.
    """
    from app.db.models import MaterialTestActLink

    create_table(conn, MaterialTestActLink.__table__)
    create_indexes(conn)


# =====================================================================
# Реестр миграций
# =====================================================================

MIGRATIONS = [
    (1, "legacy_to_stage1", migration_001),
    (2, "add_lookup_indexes", migration_002),
    (3, "protect_issued_document_versions", migration_003),
    (4, "restore_version_number_uniqueness", migration_004),
    (5, "document_dates_and_links", migration_005),
    (6, "material_test_act_links", migration_006),
]


def get_user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_user_version(conn, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def pending_migrations(from_version: int, to_version: int = SCHEMA_VERSION) -> list:
    return [m for m in MIGRATIONS if from_version < m[0] <= to_version]


def apply_migrations(engine, target: int = SCHEMA_VERSION) -> list[str]:
    """Применить недостающие миграции, вернуть список имён.

    Каждая миграция выполняется в своей транзакции: ошибка откатывает только
    её, ранее применённые миграции остаются.
    """
    applied: list[str] = []
    raw = engine.raw_connection()
    conn = raw
    try:
        current = get_user_version(conn)
        if current > target:
            raise RuntimeError(
                f"схема БД версии {current} новее поддерживаемой {target}"
            )
        for version, name, fn in pending_migrations(current, target):
            logger.info("применяю миграцию %03d %s", version, name)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            try:
                conn.execute("BEGIN")
                fn(conn)
                set_user_version(conn, version)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # pragma: no cover
                    pass
                raise
            applied.append(name)
    finally:
        try:
            conn.execute("PRAGMA legacy_alter_table=OFF")
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:  # pragma: no cover
            pass
        raw.close()
    return applied


def verify_schema(engine) -> None:
    """Убедиться, что БД соответствует модели, иначе остановить запуск.

    Миграции узнают унаследованную схему по именам таблиц и колонок, поэтому
    база, у которой имена уже новые, а колонки старые, проходит мимо них
    без следа: миграция рапортует об успехе, а программа падает позже, при
    первом обращении к документу. Единственная надёжная защита — сравнить
    фактическую схему с моделью после создания таблиц.
    """
    raw = engine.raw_connection()
    try:
        problems = schema_problems(raw)
    finally:
        raw.close()
    if problems:
        raise RuntimeError(
            "Схема базы не соответствует программе:\n  - "
            + "\n  - ".join(problems)
            + "\nОбновление остановлено, чтобы не работать с повреждёнными данными. "
            "Сообщите текст оператору и верните резервную копию из storage/backups."
        )


def schema_problems(conn) -> list[str]:
    """Расхождения фактической схемы с моделями. Пустой список — всё на месте.

    Принимает любой вариант подключения: приводится к sqlite3, как остальные
    функции проверки этого модуля.
    """
    from app.db.models import Base

    conn = _raw(conn)
    problems: list[str] = []
    existing = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            problems.append(f"отсутствует таблица {table.name}")
            continue
        actual = table_columns(conn, table.name)
        for column in table.columns:
            if column.name not in actual:
                problems.append(
                    f"в таблице {table.name} нет колонки {column.name}"
                )
        problems.extend(_foreign_key_problems(conn, table, actual))
        problems.extend(_unique_problems(conn, table, actual))
    return problems


def _foreign_key_problems(conn, table, columns: set) -> list[str]:
    """Сверить внешние ключи: не только цели, но и действия при удалении.

    Проверки только имён таблиц недостаточно. Таблица, потерявшая все
    внешние ключи, проходит такую сверку: колонки на месте, а защиты нет.
    На document_versions это означало бы, что RESTRICT, о котором заявлено
    в ТЗ п.85, просто не существует, и удаление документа стирает выпуск.
    """
    problems: list[str] = []
    actual: dict[tuple[str, str], str] = {}
    for fk in conn.execute(f"PRAGMA foreign_key_list({table.name})").fetchall():
        # (id, seq, table, from, to, on_update, on_delete, match)
        actual[(fk[3], fk[2])] = (fk[6] or "").upper()
    for fk in table.foreign_keys:
        target_table, target_column = fk.target_fullname.split(".")
        from_column = fk.parent.name
        if from_column not in columns:
            continue  # колонка уже отмечена выше
        expected = (fk.ondelete or "").upper()
        got = actual.get((from_column, target_table))
        if got is None:
            problems.append(
                f"в таблице {table.name} нет внешнего ключа "
                f"{from_column} -> {target_table}"
            )
        elif expected and got != expected:
            problems.append(
                f"в таблице {table.name} внешний ключ {from_column} -> "
                f"{target_table} удаляет по {got}, а ожидалось {expected}"
            )
    return problems


def _unique_problems(conn, table, columns: set) -> list[str]:
    """Сверить ограничения уникальности.

    Без них, например, разрешаются два выпуска с одинаковым номером версии
    одного документа, и history становится неоднозначной (ТЗ п.85).
    """
    problems: list[str] = []
    for constraint in table.constraints:
        if constraint.__class__.__name__ != "UniqueConstraint":
            continue
        names = [c.name for c in constraint.columns if c.name in columns]
        if len(names) != len(constraint.columns):
            continue  # колонка отсутствует, отмечено выше
        covered = False
        for index in conn.execute(f"PRAGMA index_list({table.name})").fetchall():
            # (seq, name, unique, origin, partial)
            if not index[2]:
                continue
            indexed = {
                r[2] for r in conn.execute(f"PRAGMA index_info({index[1]})").fetchall()
            }
            if set(names) <= indexed:
                covered = True
                break
        if not covered:
            problems.append(
                f"в таблице {table.name} нет уникальности "
                f"({', '.join(names)})"
            )
    return problems


def _raw(conn):
    """Довести любой вариант подключения до DBAPI-соединения sqlite3.

    Принимается и SQLAlchemy ``Connection``, и ``PoolProxiedConnection``
    (результат ``engine.raw_connection()``), и готовое соединение sqlite3 —
    иначе проверка целостности падала бы вместо того, чтобы сообщить о проблеме.
    """
    seen = 0
    while seen < 4:
        driver = getattr(conn, "driver_connection", None)
        if driver is not None:
            return driver
        if isinstance(conn, sqlite3.Connection):
            return conn
        nxt = getattr(conn, "connection", None)
        if nxt is None or nxt is conn:
            return conn
        conn = nxt
        seen += 1
    return conn


def foreign_key_check(conn) -> list:
    """Нарушения внешних ключей. Пустой список — база согласована."""
    return list(_raw(conn).execute("PRAGMA foreign_key_check"))
