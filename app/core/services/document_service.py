"""Создание документов проекта: нумерация и даты. ТЗ п.42, 43, 49.

Нумерация применяется к АОСР, АООК, АОУСИТО и актам испытаний. Система
предлагает следующий последовательный номер, оператор может его изменить.
Дата документа вводится оператором, система проверяет логические зависимости
дат и не меняет дату сама (ТЗ п.43).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services.project_service import record_event
from app.db.models import Document, Project


class DocumentNumberError(Exception):
    """Ошибка нумерации или дат с текстом для оператора."""


def next_document_number(db: Session, project_id: int, doc_type: str) -> str:
    """Следующий свободный номер документа вида doc_type в проекте (ТЗ п.42).

    Номер предлагается как наименьший незанятый, чтобы после удаления
    черновика последовательность не начиналась заново и не оставляла
    пропусков в комплекте.
    """
    if doc_type not in domain.NUMBERED_DOC_TYPES:
        raise DocumentNumberError(
            f"Вид документа {doc_type} не нумеруется по ТЗ п.42. "
            f"Нумеруются: {', '.join(domain.DOC_TYPE_LABELS[t] for t in domain.NUMBERED_DOC_TYPES)}."
        )

    used = set(
        db.scalars(
            select(Document.number).where(
                Document.project_id == project_id, Document.doc_type == doc_type
            )
        ).all()
    )
    for candidate in range(1, len(used) + 2):
        if str(candidate) not in used:
            return str(candidate)
    return str(len(used) + 1)  # pragma: no cover — цикл выше всегда находит


def validate_document_number(db: Session, project_id: int, doc_type: str, number: str) -> str:
    """Проверить номер документа перед сохранением (ТЗ п.42)."""
    number = (number or "").strip()
    if not number:
        raise DocumentNumberError("Номер документа обязателен (ТЗ п.42).")
    if doc_type in domain.NUMBERED_DOC_TYPES and not re.fullmatch(r"\d+", number):
        raise DocumentNumberError(
            f"Номер документа «{number}» должен быть числом. "
            "Нумерация АОСР, АООК, АОУСИТО и актов испытаний — последовательная (ТЗ п.42)."
        )
    duplicate = db.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.project_id == project_id,
            Document.doc_type == doc_type,
            Document.number == number,
        )
    ) or 0
    if duplicate:
        raise DocumentNumberError(
            f"Документ с номером «{number}» уже есть в проекте. "
            "Номер должен быть уникален в пределах вида документа (ТЗ п.42)."
        )
    return number


def validate_document_date(doc_date: date | None) -> date | None:
    """Проверить дату документа (ТЗ п.43).

    Дата не приводится к «сегодняшнему дню»: система не имеет права менять
    дату документа, введённую оператором.
    """
    if doc_date is None:
        return None
    if isinstance(doc_date, datetime):
        doc_date = doc_date.date()
    if not isinstance(doc_date, date):
        raise DocumentNumberError(f"Недопустимое значение даты: {doc_date!r}")
    if doc_date > date.today():
        raise DocumentNumberError(
            f"Дата документа {doc_date:%d.%m.%Y} позже сегодняшней. "
            "Проверьте введённое значение (ТЗ п.43)."
        )
    return doc_date


def create_document(
    db: Session,
    project_id: int,
    *,
    doc_type: str,
    number: str | None = None,
    doc_date: date | None = None,
    form_version_id: int | None = None,
    status: str = domain.DOC_STATUS_DRAFT,
) -> Document:
    """Создать документ проекта с проверкой номера и даты (ТЗ п.42, 43)."""
    if db.get(Project, project_id) is None:
        raise DocumentNumberError(f"Проект не найден: {project_id}")
    if doc_type not in domain.DOC_TYPE_LABELS:
        raise DocumentNumberError(f"Неизвестный вид документа: {doc_type}")
    if status not in domain.DOC_STATUSES:
        raise DocumentNumberError(f"Неизвестный статус документа: {status}")

    if number is None:
        number = next_document_number(db, project_id, doc_type)
    number = validate_document_number(db, project_id, doc_type, number)
    doc_date = validate_document_date(doc_date)

    document = Document(
        project_id=project_id,
        doc_type=doc_type,
        number=number,
        doc_date=doc_date,
        status=status,
        form_version_id=form_version_id,
    )
    db.add(document)
    try:
        db.flush()
        # ТЗ п.86: появление документа — событие истории проекта.
        record_event(
            db, project_id, domain.HISTORY_DOCUMENT_CREATED,
            f"Создан документ {document.type_label} № {number} (ТЗ п.86)",
            entity_type="document", entity_id=document.id,
            payload={
                "doc_type": doc_type, "number": number,
                "doc_date": doc_date.isoformat() if doc_date else None,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DocumentNumberError(
            f"Не удалось создать документ с номером «{number}»: номер уже занят."
        ) from exc
    db.refresh(document)
    return document


def update_document_card(
    db: Session,
    document: Document,
    *,
    number: str | None = None,
    doc_date: date | None = None,
    project_id: int | None = None,
) -> Document:
    """Изменить номер и дату документа (ТЗ п.42, 43, 85, 86).

    Номер выпущенного документа менять нельзя: он уже использован в
    комплекте (ТЗ п.85). Событие истории пишется здесь же, в той же
    транзакции, что и правка, — иначе правка из любого другого входа
    осталась бы в истории незаметной (ТЗ п.86).
    """
    project_id = project_id if project_id is not None else document.project_id
    if number is not None and number != document.number:
        if any(version.issued_at is not None for version in document.versions):
            raise DocumentNumberError(
                "Номер выпущенного документа менять нельзя: он уже использован "
                "в комплекте (ТЗ п.85)."
            )
        document.number = validate_document_number(
            db, project_id, document.doc_type, number
        )
    document.doc_date = validate_document_date(doc_date)
    try:
        db.flush()
        record_event(
            db, project_id, domain.HISTORY_DOCUMENT_UPDATED,
            f"Изменены реквизиты документа "
            f"{document.type_label} № {document.number} (ТЗ п.42, 43, 86)",
            entity_type="document", entity_id=document.id,
            payload={
                "number": document.number,
                "doc_date": (
                    document.doc_date.isoformat() if document.doc_date else None
                ),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DocumentNumberError(
            f"Не удалось сохранить реквизиты документа № {number}: номер уже занят."
        ) from exc
    db.refresh(document)
    return document


def list_documents(db: Session, project_id: int) -> list[Document]:
    """Документы проекта: сначала вид в порядке ТЗ п.42, внутри — по номеру.

    Сортировка выполняется в Python и сравнивает номера как числа: при
    сравнении строк «10» встало бы перед «2», и перечень выглядел бы
    неверно. Виды идут в порядке ТЗ (АОСР, АООК, АОУСИТО, акты испытаний),
    а не по алфавиту.
    """
    documents = list(
        db.scalars(
            select(Document).where(Document.project_id == project_id)
        ).all()
    )
    type_order = {
        doc_type: index
        for index, doc_type in enumerate(domain.NUMBERED_DOC_TYPES)
    }
    return sorted(
        documents,
        key=lambda d: (
            type_order.get(d.doc_type, len(type_order)),
            _number_key(d.number),
        ),
    )


def _number_key(number: str | None) -> tuple[int, int, str]:
    """Числовой ключ сортировки; нечисловой номер уходит в конец по тексту."""
    number = (number or "").strip()
    if number.isdigit():
        return (0, int(number), "")
    return (1, 0, number)
