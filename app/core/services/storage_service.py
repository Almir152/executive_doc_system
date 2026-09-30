"""Единое архивное хранилище. ТЗ п.50, 51, 53, 84, 90, 91, 92, 109.

Логика разделена на два уровня:

* ArchiveDocument — логический документ: реквизиты, категория, сроки действия;
* ArchiveFileVersion — физическая версия файла.

Одна физическая копия на версию. Повторная загрузка того же содержимого
возвращает существующую версию и не создаёт новых файлов на диске (п.92).
Новая редакция документа создаёт новую версию, а не перезаписывает старую
(п.53, 91), поэтому ранее сформированные комплекты остаются корректными.
"""

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ARCHIVE_DIR
from app.core import domain
from app.db.models import ArchiveDocument, ArchiveFileVersion

# ТЗ п.50: архив делится логически на четыре части. Перечень берётся из ТЗ.
ARCHIVE_CATEGORIES = domain.ARCHIVE_CATEGORIES
ARCHIVE_CATEGORY_MATERIALS = domain.ARCHIVE_CATEGORY_MATERIALS
ARCHIVE_CATEGORY_DEFAULT = ARCHIVE_CATEGORY_MATERIALS


class StorageError(Exception):
    """Ошибка хранилища с текстом, пригодным для показа оператору."""


def calculate_hash(file_path: Path) -> str:
    """SHA-256 файла. Используется для дедупликации и контроля целостности."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def file_type_from_name(name: str) -> str:
    if name and "." in name:
        ext = name.rsplit(".", 1)[-1].strip().lower()
        if ext.isalnum() and len(ext) <= 5:
            return ext
    return ""


# =====================================================================
# Логические архивные документы
# =====================================================================

def get_archive_document(db: Session, archive_document_id: int) -> ArchiveDocument | None:
    return db.get(ArchiveDocument, archive_document_id)


def find_by_hash(db: Session, file_hash: str) -> ArchiveFileVersion | None:
    """Найти уже загруженный файл с таким же содержимым (ТЗ п.92)."""
    return db.scalars(
        select(ArchiveFileVersion).where(ArchiveFileVersion.file_hash == file_hash)
    ).first()


def _find_matching_document(
    db: Session, project_id: int, category: str, original_name: str
) -> ArchiveDocument | None:
    """Документ проекта с тем же именем в той же категории.

    Используется интерфейсом для подсказки оператору при замене редакции.
    Автоматического объединения не происходит: разные документы вправе
    иметь одинаковое имя файла.
    """
    return db.scalars(
        select(ArchiveDocument).where(
            ArchiveDocument.project_id == project_id,
            ArchiveDocument.category == category,
            ArchiveDocument.original_name == original_name,
        )
    ).first()


def add_file_to_archive(
    db: Session,
    src_path: Path,
    project_id: int,
    category: str = ARCHIVE_CATEGORY_DEFAULT,
    *,
    quality_type: str | None = None,
    validity_from=None,
    validity_to=None,
    number: str | None = None,
    doc_date=None,
) -> ArchiveDocument:
    """Добавить файл в единый архив. Возвращает логический документ.

    ТЗ п.84, 92: одна физическая копия на уникальное содержимое. Повторная
    загрузка того же файла не создаёт ни новой записи, ни нового файла на диске.

    Новый документ с тем же именем, но другим содержимым НЕ объединяется с
    существующим: разные документы могут носить одинаковое имя файла. Новая
    редакция существующего документа создаётся явно, через add_version()
    (ТЗ п.53).

    Вид документа качества и сроки его действия (ТЗ п.45, 46) относятся к
    логическому документу и вводятся оператором. Документ качества указывается
    здесь, а конкретные акты, к которым он относится, оператор выбирает
    отдельно: автоматического прикрепления ко всем актам не происходит
    (ТЗ п.45).
    """
    src_path = Path(src_path)
    if not src_path.is_file():
        raise StorageError(f"Файл не найден: {src_path}")
    if category not in domain.ARCHIVE_CATEGORIES:
        raise StorageError(f"Неизвестная категория архива: {category}")

    file_hash = calculate_hash(src_path)

    existing_version = find_by_hash(db, file_hash)
    if existing_version is not None:
        # Физический файл уже в архиве (ТЗ п.92). Новой копии не создаётся,
        # но оператор уточняет реквизиты уже загруженного документа: номер,
        # вид и сроки действия вводятся один раз (ТЗ п.45, 46).
        document = existing_version.archive_document
        if category is not None and document.project_id == project_id:
            document.category = category
        _apply_quality_details(db, document, quality_type, validity_from, validity_to)
        if number is not None:
            document.number = number or None
        if doc_date is not None:
            document.doc_date = doc_date
        db.commit()
        db.refresh(document)
        return document

    _check_quality_details(quality_type, validity_from, validity_to, category)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    # Полный хэш в имени обеспечивает уникальность, исходное имя сохраняется,
    # чтобы оператор мог опознать документ (ТЗ п.50, 51).
    dest_path = ARCHIVE_DIR / f"{file_hash}_{src_path.name}"
    if not dest_path.exists():
        shutil.copy2(src_path, dest_path)

    document = ArchiveDocument(
        project_id=project_id,
        category=category,
        file_type=file_type_from_name(src_path.name),
        original_name=src_path.name,
        number=number or None,
        doc_date=doc_date,
    )
    _apply_quality_details(db, document, quality_type, validity_from, validity_to,
                           validate=False)
    db.add(document)
    version = ArchiveFileVersion(
        archive_document=document,
        version_no=1,
        stored_path=str(dest_path),
        file_hash=file_hash,
        file_size=dest_path.stat().st_size,
        is_actual=True,
    )
    db.add(version)
    db.commit()
    db.refresh(document)
    return document


# =====================================================================
# Документы качества: вид и сроки действия (ТЗ п.45, 46)
# =====================================================================

def check_quality_details(
    quality_type: str | None,
    validity_from,
    validity_to,
    category: str,
) -> None:
    """Публичная проверка реквизитов документа качества (ТЗ п.45, 46).

    Вызывается интерфейсом до сохранения, чтобы оператор увидел понятную
    причину отказа в том же окне, где вводились данные.
    """
    _check_quality_details(quality_type, validity_from, validity_to, category)


def _check_quality_details(
    quality_type: str | None,
    validity_from,
    validity_to,
    category: str,
) -> None:
    """Проверить вид документа качества и сроки его действия (ТЗ п.45, 46).

    Вид документа качества имеет смысл только в части «Материалы и
    документы качества»: сертификат не становится исполнительной схемой.
    Для сертификатов и деклараций сроки действия обязательны, иначе нельзя
    понять, применим ли документ к работам.
    """
    if quality_type is None:
        if validity_from is not None or validity_to is not None:
            raise StorageError(
                "Срок действия указывается вместе с видом документа качества "
                "(ТЗ п.46)."
            )
        return
    if quality_type not in domain.QUALITY_DOC_TYPES:
        raise StorageError(
            f"Неизвестный вид документа качества: {quality_type}. Допустимо: "
            + ", ".join(domain.QUALITY_DOC_TYPES) + " (ТЗ п.45)."
        )
    if category != ARCHIVE_CATEGORY_MATERIALS:
        raise StorageError(
            "Вид документа качества указывается только для части «Материалы и "
            "документы качества» (ТЗ п.45)."
        )
    if quality_type in domain.QUALITY_DOC_TYPES_WITH_VALIDITY:
        if validity_from is None or validity_to is None:
            raise StorageError(
                f"Для документа качества «{quality_type}» обязательны дата "
                "начала и дата окончания действия (ТЗ п.46)."
            )
        if validity_to < validity_from:
            raise StorageError(
                "Дата окончания действия раньше даты начала: проверьте срок "
                "действия документа качества (ТЗ п.46)."
            )


def _apply_quality_details(
    db: Session,
    document: ArchiveDocument,
    quality_type: str | None,
    validity_from,
    validity_to,
    *,
    validate: bool = True,
) -> None:
    """Записать вид документа качества и сроки его действия (ТЗ п.45, 46)."""
    if validate:
        _check_quality_details(quality_type, validity_from, validity_to, document.category)
    if quality_type is not None:
        document.note = (
            f"Вид документа качества: {quality_type}"
            if not document.note
            else f"{document.note}\nВид документа качества: {quality_type}"
        )
    if validity_from is not None:
        document.validity_from = validity_from
    if validity_to is not None:
        document.validity_to = validity_to


def set_quality_details(
    db: Session,
    archive_document_id: int,
    quality_type: str,
    validity_from=None,
    validity_to=None,
) -> ArchiveDocument:
    """Уточнить вид и сроки действия уже загруженного документа (ТЗ п.45, 46).

    Копия файла при этом не меняется: правится только логическая карточка
    документа качества.
    """
    document = get_archive_document(db, archive_document_id)
    if document is None:
        raise StorageError(f"Архивный документ не найден: {archive_document_id}")
    _apply_quality_details(db, document, quality_type, validity_from, validity_to)
    db.commit()
    db.refresh(document)
    return document


def search_archive_documents(
    db: Session,
    project_id: int,
    *,
    search: str = "",
    category: str | None = None,
    quality_type: str | None = None,
) -> list[ArchiveDocument]:
    """Поиск по архиву проекта (ТЗ п.44, 45).

    Ищет по имени файла, номеру и виду документа качества. Пустые условия
    не ограничивают выборку.
    """
    query = select(ArchiveDocument).where(ArchiveDocument.project_id == project_id)
    if category:
        query = query.where(ArchiveDocument.category == category)
    if quality_type:
        query = query.where(ArchiveDocument.note.like(f"%{quality_type}%"))
    documents = list(db.scalars(query.order_by(ArchiveDocument.original_name)).all())
    if not search:
        return documents

    # Сравнение в Python: `lower()` в SQLite не приводит регистр кириллицы,
    # а поиск должен работать независимо от настроек базы (ТЗ п.44).
    needle = search.strip().casefold()
    found = []
    for document in documents:
        haystack = " ".join(
            part for part in (
                document.original_name, document.number, document.note,
            ) if part
        )
        if needle in haystack.casefold():
            found.append(document)
    return found


def quality_validity_text(document: ArchiveDocument) -> str:
    """Срок действия документа качества для интерфейса (ТЗ п.46)."""
    if document.validity_from is None and document.validity_to is None:
        return ""
    start = document.validity_from.strftime("%d.%m.%Y") if document.validity_from else "—"
    end = document.validity_to.strftime("%d.%m.%Y") if document.validity_to else "—"
    return f"действует {start} — {end}"


def add_version(db: Session, archive_document_id: int, src_path: Path) -> ArchiveFileVersion:
    """Добавить новую версию существующего архивного документа (ТЗ п.53)."""
    document = get_archive_document(db, archive_document_id)
    if document is None:
        raise StorageError(f"Архивный документ не найден: {archive_document_id}")
    src_path = Path(src_path)
    if not src_path.is_file():
        raise StorageError(f"Файл не найден: {src_path}")

    file_hash = calculate_hash(src_path)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = ARCHIVE_DIR / f"{file_hash}_{src_path.name}"
    if not dest_path.exists():
        shutil.copy2(src_path, dest_path)

    for version in document.versions:
        version.is_actual = False

    version = ArchiveFileVersion(
        archive_document=document,
        version_no=max((v.version_no for v in document.versions), default=0) + 1,
        stored_path=str(dest_path),
        file_hash=file_hash,
        file_size=dest_path.stat().st_size,
        is_actual=True,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


# =====================================================================
# Удаление
# =====================================================================

def can_delete_archive_document(
    db: Session, archive_document_id: int
) -> tuple[bool, int]:
    """ТЗ п.50, 109: используемый архивный документ удалять нельзя.

    Возвращает (можно_удалить, количество_связей).
    """
    document = get_archive_document(db, archive_document_id)
    if document is None:
        return False, 0
    links = len(document.links)
    return links == 0, links


def delete_archive_document(db: Session, archive_document_id: int) -> None:
    """Удалить архивный документ вместе с его версиями и файлами.

    Отказ, если на документ есть ссылки (ТЗ п.109): исторические комплекты
    продолжают на него ссылаться, и их восстановление стало бы невозможным.
    """
    can_delete, links = can_delete_archive_document(db, archive_document_id)
    if not can_delete:
        raise StorageError(
            f"Архивный документ используется в {links} связях и не может быть удалён"
        )
    document = get_archive_document(db, archive_document_id)
    if document is None:
        raise StorageError(f"Архивный документ не найден: {archive_document_id}")

    # Один физический файл может быть общим: add_version() не создаёт новую
    # копию, если такой файл уже лежит в архиве (ТЗ п.84), и две версии
    # разных документов способны указывать на один путь. Удалять такой файл
    # нельзя — иначе у другого документа версия останется в базе без файла,
    # то есть доказательство по ТЗ п.54 будет утрачено безвозвратно.
    paths = [Path(v.stored_path) for v in list(document.versions)]
    still_used = set()
    if paths:
        for other in db.scalars(
            select(ArchiveFileVersion.stored_path).where(
                ArchiveFileVersion.stored_path.in_([str(p) for p in paths]),
                ArchiveFileVersion.archive_document_id != archive_document_id,
            )
        ):
            still_used.add(str(other))

    for stored in paths:
        if str(stored) in still_used:
            continue
        if stored.is_file() and stored.parent == ARCHIVE_DIR:
            stored.unlink()
    db.delete(document)
    db.commit()


# =====================================================================
# Контроль целостности
# =====================================================================

def find_orphan_files(db: Session) -> list[Path]:
    """Файлы в каталоге архива, на которые не ссылается ни одна версия.

    Возвращает абсолютные пути. Нужен для проверки хранилища: появление таких
    файлов означает сбой предыдущей загрузки.
    """
    known = {Path(v.stored_path).resolve() for v in db.scalars(select(ArchiveFileVersion))}
    if not ARCHIVE_DIR.is_dir():
        return []
    return [
        path.resolve()
        for path in ARCHIVE_DIR.iterdir()
        if path.is_file() and path.name != ".gitkeep" and path.resolve() not in known
    ]


def find_missing_files(db: Session) -> list[ArchiveFileVersion]:
    """Версии, файлы которых отсутствуют на диске."""
    return [v for v in db.scalars(select(ArchiveFileVersion)) if not Path(v.stored_path).is_file()]
