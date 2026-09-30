"""Резервное копирование, восстановление и перенос (ТЗ п.74, 98).

Резервная копия — отдельная сущность, не смешанная с пользовательскими
комплектами (ТЗ п.74). Копия должна позволить восстановить проект на другом
компьютере, поэтому в неё входят база, физические файлы архива, настройки и
манифест с контрольными суммами. Связи, версии и история лежат в самой базе
и копируются вместе с ней.

Состав копии:

* ``app.db`` — согласованный снимок базы (не копирование файла, а бэкап
  SQLite: незавершённая транзакция в копию не попадёт);
* ``files/`` — файлы архива в том же виде, в каком их видит снимок базы;
* ``settings.json`` — необходимые настройки;
* ``manifest.json`` — что именно скопировано, с хешами для проверки.

Все сведения манифеста берутся из самого снимка, а не из рабочей сессии:
иначе при изменении базы между снимком и копированием файлов манифест
описывал бы не ту копию, которая лежит на диске.

В базе пути файлов архива абсолютные (ТЗ п.49). Поэтому при восстановлении
на другом компьютере пути в восстановленной базе переписываются на новый
каталог архива — иначе проект открылся бы, но файлы не нашлись бы.

Пользовательские комплекты в копию не входят: они выгружаются отдельно
(ТЗ п.72, 74) и на другом компьютере могут быть не нужны.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import (
    ARCHIVE_DIR, BACKUP_DIR, DB_PATH, SETTINGS_PATH, ensure_dirs, utcnow,
)
from app.core.services.storage_service import calculate_hash

BACKUP_PREFIX = "Резервная копия"
SAFETY_PREFIX = "до восстановления"
MANIFEST_NAME = "manifest.json"
DB_NAME = "app.db"
FILES_DIR = "files"
SETTINGS_NAME = "settings.json"
BACKUP_FORMAT_VERSION = 1

#: Что должно попасть в манифест для проверки полноты копии (ТЗ п.98).
REQUIRED_SECTIONS = ("projects", "documents", "document_links", "versions", "events")

#: Что считается в копии: ключ манифеста → таблица базы (ТЗ п.98).
COUNT_TABLES = {
    "projects": "projects",
    "documents": "documents",
    "archive_documents": "archive_documents",
    "document_links": "document_links",
    "archive_links": "document_archive_links",
    "material_links": "material_test_act_links",
    "versions": "document_versions",
    "events": "history_events",
}

FILE_VERSIONS_TABLE = "archive_file_versions"


class BackupError(Exception):
    """Ошибка резервного копирования или восстановления."""


# =====================================================================
# СОЗДАНИЕ КОПИИ (ТЗ п.74, 98)
# =====================================================================


def next_backup_folder(root: Path | str = BACKUP_DIR) -> str:
    """Имя следующей папки копии: «Резервная копия 01», 02, … (ТЗ п.74).

    Прежние копии не перезаписываются: их используют для восстановления
    (ТЗ п.71 по аналогии — история копий и есть история проекта).
    """
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise BackupError(f"Каталог копий недоступен: {root}")
    used = {item.name for item in root.iterdir()} if root.is_dir() else set()
    index = 1
    while f"{BACKUP_PREFIX} {index:02d}" in used:
        index += 1
    return f"{BACKUP_PREFIX} {index:02d}"


def create_backup(
    db: Session | None = None,
    *,
    base_dir: Path | str = BACKUP_DIR,
    db_path: Path | str = DB_PATH,
    archive_dir: Path | str = ARCHIVE_DIR,
    settings_path: Path | str = SETTINGS_PATH,
    note: str | None = None,
) -> Path:
    """Создать резервную копию. Возвращает путь к папке копии.

    Копия снимается через механизм бэкапа SQLite: простое копирование файла
    при открытой базе может поймать полузаписанную транзакцию (ТЗ п.98).

    ``db`` не обязателен: копия снимается по снимку базы, поэтому её можно
    сделать и до подключения ORM — так делает ``create_update_backup``.
    """
    if db is not None:
        _ensure_session_saved(db)
    db_path = Path(db_path)
    staging: Path | None = None
    try:
        ensure_dirs()
        if not db_path.is_file():
            raise BackupError(f"Файл базы не найден: {db_path}")

        root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        folder = root / next_backup_folder(root)
        staging = root / f".{folder}.сборка"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        snapshot = staging / DB_NAME
        _snapshot_database(db_path, snapshot)
        # Описание копии и файлы берутся из снимка: манифест обязан
        # соответствовать тому, что действительно лежит в папке.
        files = _copy_archive_files(snapshot, archive_dir, staging / FILES_DIR)
        copied_settings = _copy_settings(settings_path, staging / SETTINGS_NAME)
        manifest = _build_manifest(
            snapshot, files, copied_settings=copied_settings, note=note
        )
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        folder.mkdir(parents=True, exist_ok=False)
        for item in sorted(staging.iterdir()):
            shutil.move(str(item), str(folder / item.name))
    except OSError as exc:
        # Файловая ошибка не должна доходить до слота Qt: необработанное
        # исключение там закрывает приложение целиком, и оператор видит
        # только исчезновение окна без объяснения (ТЗ п.98).
        raise BackupError(_storage_problem("создать резервную копию", exc)) from exc
    finally:
        # staging может быть не создан: тогда удалять нечего, а попытка
        # удалить None упала бы в обход except OSError.
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return folder


def _storage_problem(action: str, exc: Exception) -> str:
    """Файловая ошибка хранилища как текст для оператора.

    Формулировки системных исключений на английском и без указания на папку,
    поэтому к ним добавляется название операции и то, что оператор может
    сделать: проверить права и свободное место.
    """
    return (
        f"Не удалось {action}: {exc}\n\n"
        "Проверьте, что у папки программы есть права на запись и на диске "
        "свободно место. Рабочие данные не изменены."
    )


def _ensure_session_saved(db: Session) -> None:
    """Копия снимается только из сохранённого состояния (ТЗ п.86, 98).

    Снимок базы делает отдельное подключение, поэтому несохранённая правка
    в снимок не попала бы, а файлы архива скопировались по её пути: в копии
    разошлись бы счётчики и состав файлов.
    """
    if db.new or db.dirty or db.deleted:
        raise BackupError(
            "Есть несохранённые изменения — сначала завершите работу с "
            "документами, затем сделайте резервную копию (ТЗ п.86, 98)."
        )


def create_update_backup(
    *,
    base_dir: Path | str = BACKUP_DIR,
    db_path: Path | str = DB_PATH,
    archive_dir: Path | str = ARCHIVE_DIR,
    settings_path: Path | str = SETTINGS_PATH,
) -> Path:
    """Копия перед обновлением программы (ТЗ п.97, 98).

    Схема базы меняется только через миграцию (ТЗ п.97), а миграция может
    пересобрать таблицы. Поэтому до неё снимается копия, которую оператор
    сможет вернуть, если обновление пойдёт не так.

    Копия делается по файлу базы, без ORM: на момент обновления схема может
    ещё соответствовать прежней версии программы.
    """
    return create_backup(
        None,
        base_dir=base_dir,
        db_path=db_path,
        archive_dir=archive_dir,
        settings_path=settings_path,
        note="Перед обновлением программы",
    )


def _snapshot_database(source: Path, target: Path) -> None:
    """Согласованный снимок БД через API SQLite (ТЗ п.98)."""
    raw = sqlite3.connect(str(source))
    try:
        destination = sqlite3.connect(str(target))
        try:
            raw.backup(destination)
            destination.commit()
        finally:
            destination.close()
    except sqlite3.Error as exc:  # pragma: no cover - зависит от ФС
        raise BackupError(f"Не удалось снять копию базы: {exc}") from exc
    finally:
        raw.close()


def _read_only(snapshot: Path) -> sqlite3.Connection:
    """Открыть снимок базы только для чтения."""
    return sqlite3.connect(f"file:{Path(snapshot).as_posix()}?mode=ro", uri=True)


def _file_version_rows(snapshot: Path) -> list[tuple[int, str]]:
    """Какие файлы архива нужны копии: (версия, путь) из снимка (ТЗ п.49)."""
    raw = _read_only(snapshot)
    try:
        tables = _tables(raw)
        if FILE_VERSIONS_TABLE not in tables:
            return []
        return [
            (int(version_id), str(stored_path))
            for version_id, stored_path in raw.execute(
                f"SELECT id, stored_path FROM {FILE_VERSIONS_TABLE} ORDER BY id"
            )
        ]
    except sqlite3.Error as exc:
        raise BackupError(f"Снимок базы не прочитан: {exc}") from exc
    finally:
        raw.close()


def _tables(raw: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _copy_archive_files(snapshot: Path, archive_dir: Path, target: Path) -> list[dict]:
    """Скопировать файлы, на которые ссылается снимок базы (ТЗ п.49, 98).

    Копируются только используемые файлы: осиротевшие в каталоге архива
    в копию не попадают и при восстановлении не мешают. Вернённые записи
    и есть раздел манифеста с файлами.
    """
    archive_root = Path(archive_dir).resolve()
    entries: list[dict] = []
    for version_id, stored_path in _file_version_rows(snapshot):
        source = Path(stored_path).resolve()
        if not source.is_file():
            raise BackupError(
                f"Файл архива отсутствует на диске: {source}. Копия неполна — "
                "сначала восстановите файл (ТЗ п.98)."
            )
        try:
            relative = source.relative_to(archive_root)
        except ValueError as exc:
            raise BackupError(
                f"Файл архива лежит вне каталога архива: {source} "
                "(ТЗ п.49, 98)."
            ) from exc
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        entries.append({
            # Идентификатор версии нужен, чтобы при восстановлении на другом
            # компьютере переписать в базе путь на новое место файла.
            "archive_file_version_id": version_id,
            "stored_path": str(source),
            "relative": relative.as_posix(),
            "file_hash": calculate_hash(destination),
            "size": destination.stat().st_size,
        })
    return entries


def _copy_settings(settings_path: Path, target: Path) -> bool:
    """Скопировать настройки. Отсутствие настроек — не ошибка (ТЗ п.98)."""
    settings_path = Path(settings_path)
    if not settings_path.is_file():
        return False
    shutil.copy2(settings_path, target)
    return True


def _build_manifest(
    snapshot: Path,
    files: list[dict],
    *,
    copied_settings: bool,
    note: str | None,
) -> dict:
    """Манифест копии: что скопировано и чем это можно проверить (ТЗ п.98)."""
    from app.db.migrations import SCHEMA_VERSION

    return {
        "format": BACKUP_FORMAT_VERSION,
        "created_at": utcnow().isoformat(sep=" ", timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
        "database": DB_NAME,
        "database_hash": calculate_hash(snapshot),
        "settings_included": copied_settings,
        # ТЗ п.72, 74: комплекты пользователя — отдельная сущность, в копию
        # резервного назначения они не входят.
        "packages_included": False,
        "files": files,
        "counts": storage_counts(snapshot),
        "note": (note or "").strip() or None,
    }


def storage_counts(db_or_snapshot: Session | Path) -> dict[str, int]:
    """Сколько чего унесено в копию (ТЗ п.98: связи, версии, история).

    Принимается и сессия, и путь к снимку базы: манифест описывает именно
    снимок, поэтому при создании копии считается по нему.
    """
    if isinstance(db_or_snapshot, (str, Path)):
        raw = _read_only(Path(db_or_snapshot))
        try:
            tables = _tables(raw)
            counts = {
                key: (raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      if table in tables else 0)
                for key, table in COUNT_TABLES.items()
            }
        except sqlite3.Error as exc:
            raise BackupError(f"Снимок базы не прочитан: {exc}") from exc
        finally:
            raw.close()
        return counts

    from sqlalchemy import func, select  # noqa: PLC0415 - только для сессии

    from app.db.models import (
        ArchiveDocument, Document, DocumentArchiveLink, DocumentLink, DocumentVersion,
        HistoryEvent, MaterialTestActLink, Project,
    )

    models = {
        "projects": Project,
        "documents": Document,
        "archive_documents": ArchiveDocument,
        "document_links": DocumentLink,
        "archive_links": DocumentArchiveLink,
        "material_links": MaterialTestActLink,
        "versions": DocumentVersion,
        "events": HistoryEvent,
    }
    return {
        key: (db_or_snapshot.scalar(select(func.count()).select_from(model)) or 0)
        for key, model in models.items()
    }


# =====================================================================
# ПРОВЕРКА КОПИИ (ТЗ п.98)
# =====================================================================


def read_manifest(backup_path: Path | str) -> dict:
    """Манифест копии. Отсутствие манифеста — ошибка: копия не наша."""
    manifest_path = Path(backup_path) / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(
            f"В папке нет {MANIFEST_NAME}: это не резервная копия "
            "(ТЗ п.74, 98)."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackupError(f"Манифест копии повреждён: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BackupError("Манифест копии повреждён: неверный формат.")
    return manifest


def verify_backup(backup_path: Path | str) -> list[str]:
    """Проверить копию перед восстановлением. Пустой список — можно."""
    backup_path = Path(backup_path)
    problems: list[str] = []
    if not backup_path.is_dir():
        return [f"Папка копии не найдена: {backup_path}"]
    try:
        manifest = read_manifest(backup_path)
    except BackupError as exc:
        return [str(exc)]

    if manifest.get("format") != BACKUP_FORMAT_VERSION:
        problems.append(
            f"Формат копии {manifest.get('format')} не поддерживается "
            f"(ожидается {BACKUP_FORMAT_VERSION})."
        )
    database = backup_path / manifest.get("database", DB_NAME)
    database_ok = database.is_file()
    if not database_ok:
        problems.append(f"В копии нет базы: {database.name}")
    else:
        expected = manifest.get("database_hash")
        if expected and calculate_hash(database) != expected:
            problems.append("Контрольная сумма базы не совпадает: копия изменена.")
        problems.extend(_database_problems(database))

    files_dir = backup_path / FILES_DIR
    for entry in manifest.get("files", []):
        relative = entry.get("relative", "")
        if _unsafe_relative(relative):
            problems.append(f"Недопустимый путь в манифесте: {relative}")
            continue
        path = files_dir / relative
        if not path.is_file():
            problems.append(f"Нет файла архива: {relative}")
        elif entry.get("file_hash") and calculate_hash(path) != entry["file_hash"]:
            problems.append(f"Файл изменён или повреждён: {relative}")
    if database_ok:
        problems.extend(_manifest_database_problems(database, manifest))

    if manifest.get("settings_included") and not (
        backup_path / SETTINGS_NAME
    ).is_file():
        problems.append("Настройки указаны в манифесте, но отсутствуют в копии.")

    missing = [
        section for section in REQUIRED_SECTIONS
        if section not in manifest.get("counts", {})
    ]
    if missing:
        problems.append(
            "В манифесте нет сведений о: " + ", ".join(missing) + " (ТЗ п.98)."
        )
    return problems


def _unsafe_relative(relative: str) -> bool:
    """Путь из манифеста не должен выходить за пределы папки копии."""
    if not relative:
        return True
    candidate = Path(relative)
    return candidate.is_absolute() or ".." in candidate.parts


def _database_problems(database: Path) -> list[str]:
    """Целостность базы из копии (ТЗ п.98)."""
    problems: list[str] = []
    raw = sqlite3.connect(str(database))
    try:
        integrity = raw.execute("PRAGMA integrity_check").fetchall()
        if integrity and integrity[0][0] != "ok":
            problems.append(f"База повреждена: {integrity[0][0]}")
        if raw.execute("PRAGMA foreign_key_check").fetchall():
            problems.append("В базе копии есть нарушения внешних ключей.")
    except sqlite3.Error as exc:
        problems.append(f"Базу копии не открыть: {exc}")
    finally:
        raw.close()
    return problems


def _manifest_database_problems(database: Path, manifest: dict) -> list[str]:
    """Каждая версия файла в базе копии должна быть в копии и в манифесте.

    Иначе копия формально «без ошибок», но проект откроется без части
    файлов архива (ТЗ п.49, 98).
    """
    problems: list[str] = []
    raw = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables = _tables(raw)
        if FILE_VERSIONS_TABLE not in tables:
            return problems
        rows = raw.execute(
            f"SELECT id, stored_path FROM {FILE_VERSIONS_TABLE} ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        # Нечитаемая база уже отмечена в _database_problems.
        return problems
    finally:
        raw.close()

    entries = {
        entry.get("archive_file_version_id"): entry
        for entry in manifest.get("files", [])
        if entry.get("archive_file_version_id") is not None
    }
    for version_id, stored_path in rows:
        entry = entries.get(int(version_id))
        if entry is None:
            problems.append(
                f"В копии нет файла версии {version_id} "
                f"({Path(stored_path).name})."
            )
            continue
        source_name = Path(str(entry.get("stored_path") or "")).name
        if source_name and source_name != Path(stored_path).name:
            problems.append(
                f"Файл версии {version_id} в манифесте не соответствует базе."
            )
    return problems


# =====================================================================
# ВОССТАНОВЛЕНИЕ И ПЕРЕНОС (ТЗ п.98)
# =====================================================================


def restore_backup(
    backup_path: Path | str,
    *,
    db_path: Path | str = DB_PATH,
    archive_dir: Path | str = ARCHIVE_DIR,
    settings_path: Path | str = SETTINGS_PATH,
    safety_dir: Path | str | None = None,
) -> Path:
    """Восстановить проект из копии. Возвращает путь к папке safety-копии.

    Порядок обязателен: сначала копия проверяется целиком, текущие данные
    откладываются в сторону, и только потом устанавливается копия. Поэтому
    неудачное восстановление не оставляет систему без данных (ТЗ п.98).

    Пути файлов архива в восстановленной базе переписываются на новый каталог:
    в базе они абсолютные (ТЗ п.49), поэтому без этого проект на другом
    компьютере не нашёл бы своих файлов.

    Вызывающий обязан сначала освободить соединения с базой
    (``app.db.database.release_database()``): подменять файл базы, пока с ним
    работают, нельзя.
    """
    backup_path = Path(backup_path)
    db_path = Path(db_path)
    archive_dir = Path(archive_dir)
    settings_path = Path(settings_path)

    problems = verify_backup(backup_path)
    if problems:
        raise BackupError(
            "Резервная копия не пригодна для восстановления:\n"
            + "\n".join(f"• {problem}" for problem in problems)
        )
    manifest = read_manifest(backup_path)
    if manifest.get("packages_included"):
        raise BackupError(
            "Копия содержит пользовательские комплекты — это не резервная "
            "копия проекта (ТЗ п.72, 74)."
        )

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        archive_dir.mkdir(parents=True, exist_ok=True)
        _ensure_database_not_busy(db_path)
        # Текущие данные сохраняются: восстановление обратимо.
        safety = _make_safety_copy(safety_dir, db_path, archive_dir, settings_path)

        _install_database(backup_path / manifest.get("database", DB_NAME), db_path, manifest,
                          archive_dir)
        _restore_files(backup_path / FILES_DIR, archive_dir)
        if manifest.get("settings_included") and (backup_path / SETTINGS_NAME).is_file():
            shutil.copy2(backup_path / SETTINGS_NAME, settings_path)

        # Файлов, которых нет в копии, в восстановленном архиве лишние: база
        # на них не ссылается, и оператор увидел бы их как чужие.
        expected = {
            (archive_dir / entry["relative"]).resolve()
            for entry in manifest.get("files", [])
            if not _unsafe_relative(entry.get("relative", ""))
        }
        for path in sorted(archive_dir.rglob("*")):
            if path.is_file() and path.resolve() not in expected:
                path.unlink()
        _prune_empty_dirs(archive_dir)
    except OSError as exc:
        # Файловая ошибка не должна доходить до слота Qt: там необработанное
        # исключение закрывает приложение целиком (ТЗ п.98).
        raise BackupError(_storage_problem("восстановить из копии", exc)) from exc
    return safety


def _ensure_database_not_busy(db_path: Path) -> None:
    """База должна быть свободна: иначе подмена файла разорвёт работу (ТЗ п.54)."""
    if not db_path.is_file():
        return
    raw = sqlite3.connect(str(db_path), timeout=0.5)
    try:
        raw.execute("BEGIN EXCLUSIVE")
        raw.rollback()
    except sqlite3.Error as exc:
        raise BackupError(
            f"База {db_path.name} занята: {exc}. Закройте второй экземпляр "
            "программы и повторите восстановление."
        ) from exc
    finally:
        raw.close()


def _install_database(
    source: Path, db_path: Path, manifest: dict, archive_dir: Path
) -> None:
    """Установить базу из копии: переписать пути, проверить, заменить файл."""
    staging = db_path.parent / f".{db_path.name}.восстановление"
    if staging.exists():
        staging.unlink()
    try:
        shutil.copy2(source, staging)
        _remap_stored_paths(staging, manifest, archive_dir)
        problems = _database_problems(staging)
        if problems:
            raise BackupError(
                "Восстановленная база не прошла проверку:\n"
                + "\n".join(f"• {problem}" for problem in problems)
            )
        os.replace(staging, db_path)
    finally:
        if staging.exists():
            staging.unlink()
    _drop_journal_sidecars(db_path)


def _remap_stored_paths(database: Path, manifest: dict, archive_dir: Path) -> None:
    """Переписать в базе пути файлов архива на новый каталог (ТЗ п.49, 98)."""
    updates = [
        (str((Path(archive_dir) / entry["relative"]).resolve()),
         int(entry["archive_file_version_id"]))
        for entry in manifest.get("files", [])
        if entry.get("archive_file_version_id") is not None
        and not _unsafe_relative(entry.get("relative", ""))
    ]
    if not updates:
        return
    raw = sqlite3.connect(str(database))
    try:
        raw.execute("BEGIN IMMEDIATE")
        for stored_path, version_id in updates:
            raw.execute(
                f"UPDATE {FILE_VERSIONS_TABLE} SET stored_path = ? WHERE id = ?",
                (stored_path, version_id),
            )
        raw.commit()
    except sqlite3.Error as exc:
        raw.rollback()
        raise BackupError(f"Не удалось перенести пути файлов архива: {exc}") from exc
    finally:
        raw.close()


def _drop_journal_sidecars(db_path: Path) -> None:
    """Убрать остатки журнала от прежней базы (ТЗ п.54)."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _make_safety_copy(
    safety_dir: Path | str | None,
    db_path: Path,
    archive_dir: Path,
    settings_path: Path,
) -> Path:
    """Отложить текущие данные перед восстановлением (ТЗ п.98)."""
    root = Path(safety_dir) if safety_dir else db_path.parent / "backups"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f"{SAFETY_PREFIX} {next_index(root, SAFETY_PREFIX):02d}"
    folder.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        # Снимок, а не копирование файла: незавершённая транзакция в
        # отложенную базу не попадёт.
        _snapshot_database(db_path, folder / DB_NAME)
    if settings_path.is_file():
        shutil.copy2(settings_path, folder / SETTINGS_NAME)
    if archive_dir.is_dir():
        shutil.copytree(archive_dir, folder / FILES_DIR, dirs_exist_ok=True)
    return folder


def next_index(root: Path | str, prefix: str) -> int:
    """Следующий свободный номер папки с заданным префиксом."""
    root = Path(root)
    used = {item.name for item in root.iterdir()} if root.is_dir() else set()
    index = 1
    while f"{prefix} {index:02d}" in used:
        index += 1
    return index


def _restore_files(files_dir: Path, archive_dir: Path) -> None:
    if not files_dir.is_dir():
        return
    for entry in sorted(files_dir.rglob("*")):
        if not entry.is_file():
            continue
        relative = entry.relative_to(files_dir)
        target = archive_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)


def _prune_empty_dirs(root: Path) -> None:
    """Убрать опустевшие каталоги архива после восстановления."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


# =====================================================================
# ПРОСМОТР КОПИЙ (ТЗ п.74)
# =====================================================================


def list_backups(base_dir: Path | str = BACKUP_DIR) -> list[dict]:
    """Резервные копии в порядке от новых к старым."""
    root = Path(base_dir)
    if not root.is_dir():
        return []
    items = []
    for folder in sorted(root.iterdir(), reverse=True):
        if not folder.is_dir() or not folder.name.startswith(BACKUP_PREFIX):
            continue
        manifest_path = folder / MANIFEST_NAME
        created: object = folder.stat().st_mtime
        counts: dict = {}
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(manifest.get("created_at", ""))
                counts = manifest.get("counts", {})
            except (json.JSONDecodeError, ValueError):
                created = folder.stat().st_mtime
        items.append({
            "path": folder,
            "name": folder.name,
            "created_at": created if isinstance(created, datetime) else utcnow(),
            "counts": counts,
            "size": _folder_size(folder),
        })
    return items


def _folder_size(folder: Path) -> int:
    return sum(
        path.stat().st_size for path in folder.rglob("*") if path.is_file()
    )


def describe_backup(backup_path: Path | str) -> str:
    """Человекочитаемое описание копии для оператора (ТЗ п.74)."""
    manifest = read_manifest(backup_path)
    counts = manifest.get("counts", {})
    created = manifest.get("created_at", "")
    note = manifest.get("note")
    text = (
        f"{Path(backup_path).name}\n"
        f"Создана: {created}\n"
        f"Схема: {manifest.get('schema_version')}\n"
        f"Проектов: {counts.get('projects', '—')}, "
        f"документов: {counts.get('documents', '—')}, "
        f"связей: {counts.get('document_links', 0) + counts.get('archive_links', 0)}, "
        f"версий: {counts.get('versions', '—')}, "
        f"событий истории: {counts.get('events', '—')}\n"
        f"Файлов архива: {len(manifest.get('files', []))}"
    )
    return f"{text}\nКомментарий: {note}" if note else text
