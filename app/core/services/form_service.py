"""Форма документа: данные полей, блоки подписантов, черновик. ТЗ п.63, 64, 66.

Форма открывается в рабочей области (ТЗ п.65) и хранится как версия
документа: незавершённая форма не теряется при закрытии (ТЗ п.66), а
выпущенная версия остаётся неизменной (ТЗ п.54, 85).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import domain
from app.db.models import Document, DocumentVersion, NormativeForm, SignatureBlock

BLOCK_HANDED_OVER = "Сдал"
BLOCK_ACCEPTED = "Принял"
SIGNATURE_BLOCKS = (BLOCK_HANDED_OVER, BLOCK_ACCEPTED)

# ТЗ п.64: решение по незаполненному представителю эксплуатации.
MISSING_KEEP_PLACE = "keep_place"
MISSING_OMIT_BLOCK = "omit_block"
MISSING_CHOICES = (MISSING_KEEP_PLACE, MISSING_OMIT_BLOCK)


class FormError(Exception):
    """Ошибка работы с формой с текстом для оператора."""


def get_signature_block(db: Session, document_id: int, block: str) -> SignatureBlock | None:
    if block not in SIGNATURE_BLOCKS:
        raise FormError(f"Неизвестный блок подписантов: {block}")
    return db.scalar(
        select(SignatureBlock).where(
            SignatureBlock.document_id == document_id,
            SignatureBlock.block == block,
        )
    )


def save_signature_block(
    db: Session,
    document_id: int,
    block: str,
    *,
    position: str | None,
    full_name: str | None,
    sign_place: str | None,
) -> SignatureBlock:
    """Сохранить один из двух независимых блоков (ТЗ п.63).

    Блоки независимы: заполненный «Сдал» не требует заполненного «Принял».
    """
    if block not in SIGNATURE_BLOCKS:
        raise FormError(f"Неизвестный блок подписантов: {block}")
    if db.get(Document, document_id) is None:
        raise FormError(f"Документ не найден: {document_id}")

    values = {
        "position": (position or "").strip() or None,
        "full_name": (full_name or "").strip() or None,
        "sign_place": (sign_place or "").strip() or None,
    }
    existing = get_signature_block(db, document_id, block)
    if existing is None:
        existing = SignatureBlock(document_id=document_id, block=block, **values)
        db.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise FormError(f"Не удалось сохранить блок «{block}».") from exc
    return existing


def set_exploitation_missing_choice(db: Session, document_id: int, choice: str) -> str:
    """Запомнить решение по незаполненному представителю (ТЗ п.64).

    Решение относится к конкретному документу, а не к форме или проекту.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    if choice not in MISSING_CHOICES:
        raise FormError(
            "Решение по представителю эксплуатации должно быть одним из двух: "
            "оставить место для ручного заполнения или убрать блок из печати (ТЗ п.64)."
        )
    document.exploitation_missing_choice = choice
    db.commit()
    return choice


def _draft_version(db: Session, document_id: int) -> DocumentVersion | None:
    """Текущая рабочая версия документа (невыпущенная)."""
    return db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.issued_at.is_(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )


def current_definition(db: Session, document: Document) -> dict | None:
    """Описание формы документа: закреплённое или текущее (ТЗ п.96).

    У документа может быть закреплена конкретная версия формы: она
    сохраняется, даже когда в справочнике появилась новая.
    """
    if document.form_version is not None:
        return document.form_version.definition
    return db.scalar(
        select(NormativeForm)
        .where(
            NormativeForm.doc_type == document.doc_type,
            NormativeForm.is_current.is_(True),
        )
        .order_by(NormativeForm.version.desc())
    ).definition if _has_current_form(db, document.doc_type) else None


def _has_current_form(db: Session, doc_type: str) -> bool:
    return db.scalar(
        select(NormativeForm.id).where(
            NormativeForm.doc_type == doc_type,
            NormativeForm.is_current.is_(True),
        ).limit(1)
    ) is not None


def check_payload(
    db: Session, document_id: int, payload: dict
) -> list[str]:
    """Незаполненные обязательные поля формы.

    Проверяются по описанию нормативной формы (ТЗ п.96): обязательность
    задана формой, а не кодом. Отсутствие описания — тоже проблема: без него
    выпустить документ нельзя, ведь неизвестно, что заполнено.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")

    definition = current_definition(db, document)
    if not definition:
        return [
            f"Для вида «{domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)}» "
            "нет загруженной нормативной формы. Выпуск невозможен (ТЗ п.96)."
        ]

    problems: list[str] = []
    values = payload or {}
    seen = 0
    for section in definition.get("sections", []):
        # Поля описания лежат в blocks; fields — синоним, встречающийся в
        # сторонних описаниях форм.
        fields = list(section.get("blocks") or []) + list(section.get("fields") or [])
        for field in fields:
            key = field.get("key")
            if not key:
                continue
            seen += 1
            if not field.get("required"):
                continue
            if _is_empty(values.get(key)):
                problems.append(
                    f"{field.get('label') or key} — обязательное поле (ТЗ п.96)."
                )
    if not seen:
        return [
            f"Описание формы «{definition.get('title') or document.doc_type}» "
            "не содержит полей. Выпуск невозможен: нечего проверять (ТЗ п.96)."
        ]
    return problems


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def save_draft(
    db: Session,
    document_id: int,
    payload: dict,
    *,
    validate: bool = False,
) -> DocumentVersion:
    """Сохранить незавершённую форму (ТЗ п.66).

    Черновик по определению заполнен частично, поэтому по умолчанию
    обязательные поля не проверяются. Явно запрошенная проверка выполняется
    всегда — по ней оператор узнаёт, что ещё нужно доделать.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    if document.status == domain.DOC_STATUS_ISSUED:
        raise FormError(
            "Документ выпущен: его версия зафиксирована и не изменяется "
            "(ТЗ п.54, 85). Создайте новый документ для новой редакции."
        )

    if validate:
        problems = check_payload(db, document_id, payload)
        if problems:
            raise FormError(
                "Форма заполнена не полностью:\n• " + "\n• ".join(problems)
            )

    version = _draft_version(db, document_id)
    if version is None:
        next_no = 1
        version = DocumentVersion(
            document_id=document_id, version_no=next_no, payload={}
        )
        db.add(version)
    version.payload = dict(payload)
    version.is_actual = True
    if document.form_version_id is not None:
        version.form_version_id = document.form_version_id

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise FormError("Не удалось сохранить форму.") from exc
    return version


def load_draft(db: Session, document_id: int) -> dict:
    """Данные незавершённой формы или пустой набор (ТЗ п.66)."""
    version = _draft_version(db, document_id)
    return dict(version.payload) if version is not None else {}


def has_unsaved_work(db: Session, document_id: int, payload: dict) -> bool:
    """Есть ли несохранённые изменения формы (ТЗ п.66)."""
    return dict(payload or {}) != load_draft(db, document_id)
