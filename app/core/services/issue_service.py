"""Выпуск документа и новые редакции. ТЗ п.85, 96, 53, 91, 93, 43, 86.

До выпуска документ рабочий и правится. Выпуск фиксирует версию: её
содержимое не меняется вследствие последующего редактирования, а новая
работа идёт в новой версии (ТЗ п.93). Проверки обязательных полей при
выпуске подключены здесь (ТЗ п.96) — раньше они существовали, но никем не
вызывались.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import utcnow
from app.core import domain
from app.core.services import document_service, form_service
from app.core.services.project_service import record_event
from app.db.models import Document, DocumentVersion


class IssueError(Exception):
    """Невозможность выпустить документ.

    Список ``problems`` — незаполненные поля и нарушения проверок. Он
    показывается оператору целиком: исправлять по одному за раз неудобно.
    """

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])

    def report(self) -> str:
        if not self.problems:
            return str(self)
        return str(self) + "\n• " + "\n• ".join(self.problems)


def list_versions(db: Session, document_id: int) -> list[DocumentVersion]:
    """Все версии документа по возрастанию номера (ТЗ п.91)."""
    return list(
        db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no)
        ).all()
    )


def issued_versions(db: Session, document_id: int) -> list[DocumentVersion]:
    """Зафиксированные версии документа (ТЗ п.85)."""
    return [v for v in list_versions(db, document_id) if v.issued_at is not None]


def is_frozen(version: DocumentVersion | None) -> bool:
    """Зафиксирована ли версия (ТЗ п.85, 93)."""
    return version is not None and version.issued_at is not None


def next_version_no(db: Session, document_id: int) -> int:
    """Следующий номер версии документа.

    Считается как «максимум плюс один», а не количество версий: номера
    должны продолжаться и после выпуска, когда черновика уже нет.
    """
    current = db.scalar(
        select(func.max(DocumentVersion.version_no)).where(
            DocumentVersion.document_id == document_id
        )
    )
    return int(current or 0) + 1


def issue_document(
    db: Session,
    document_id: int,
    *,
    doc_date: date | None = None,
    extra_problems: list[str] | None = None,
) -> DocumentVersion:
    """Выпустить документ: зафиксировать версию (ТЗ п.85).

    Выпуск отказывает, если форма заполнена не полностью (ТЗ п.96), не
    указана дата (ТЗ п.43) или нарушены проверки комплекта, переданные
    вызывающим в ``extra_problems``. Ничего не меняется при отказе.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise IssueError(f"Документ не найден: {document_id}")
    if document.status == domain.DOC_STATUS_ISSUED:
        raise IssueError(
            f"{document.type_label} № {document.number} уже выпущен. "
            "Зафиксированная версия не изменяется (ТЗ п.85). "
            "Для новой редакции нажмите «Новая редакция»."
        )

    payload = form_service.load_draft(db, document_id)
    problems = list(extra_problems or [])
    if not payload:
        problems.append(
            "Форма документа пуста: нет данных, которые можно зафиксировать "
            "(ТЗ п.85)."
        )
    problems.extend(form_service.check_payload(db, document_id, payload))

    # Дату система не назначает сама: она либо уже введена оператором, либо
    # передана сюда. Подставлять «сегодня» нельзя (ТЗ п.43).
    effective_date = doc_date if doc_date is not None else document.doc_date
    if effective_date is None:
        problems.append("Дата документа не заполнена (ТЗ п.43).")
    else:
        try:
            effective_date = document_service.validate_document_date(effective_date)
        except document_service.DocumentNumberError as exc:
            problems.append(str(exc))

    if problems:
        raise IssueError(
            "Документ не выпущен: не выполнены обязательные условия.", problems
        )

    version = form_service.draft_version(db, document_id)
    if version is None:  # pragma: no cover — пустой payload уже пойман выше
        raise IssueError(
            "Документ не выпущен: не найдена версия с данными (ТЗ п.85)."
        )

    version.issued_at = utcnow()
    version.is_actual = True
    document.status = domain.DOC_STATUS_ISSUED
    document.doc_date = effective_date

    record_event(
        db,
        document.project_id,
        domain.HISTORY_DOCUMENT_ISSUED,
        f"Выпущен {document.type_label} № {document.number}, версия {version.version_no} "
        f"от {effective_date:%d.%m.%Y} (ТЗ п.85)",
        entity_type="document",
        entity_id=document.id,
        payload={
            "version_id": version.id,
            "version_no": version.version_no,
            "document_number": document.number,
            "issued_at": version.issued_at.isoformat(),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise IssueError(
            "Не удалось зафиксировать версию документа (ТЗ п.85)."
        ) from exc
    return version


def start_revision(db: Session, document_id: int) -> DocumentVersion:
    """Начать новую редакцию выпущенного документа (ТЗ п.53, 91, 93).

    Зафиксированная версия остаётся в истории без изменений; правится
    новая. Номер новой версии продолжает последовательность, а не
    начинается с единицы.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise IssueError(f"Документ не найден: {document_id}")
    if document.status != domain.DOC_STATUS_ISSUED:
        raise IssueError(
            f"{document.type_label} № {document.number} не выпущен, редакция "
            "не требуется: документ и так рабочий (ТЗ п.85)."
        )

    fixed = issued_versions(db, document_id)
    if not fixed:  # pragma: no cover — статус и версии расходятся только при ручной правке БД
        raise IssueError(
            "Документ отмечен выпущенным, но зафиксированной версии нет. "
            "Данные требуют разбора (ТЗ п.85)."
        )
    source = fixed[-1]

    revision = DocumentVersion(
        document_id=document_id,
        version_no=next_version_no(db, document_id),
        form_version_id=source.form_version_id,
        payload=dict(source.payload),
        is_actual=True,
    )
    db.add(revision)
    document.status = domain.DOC_STATUS_DRAFT

    record_event(
        db,
        document.project_id,
        domain.HISTORY_DOCUMENT_REVISION,
        f"Начата редакция {document.type_label} № {document.number}: "
        f"версия {revision.version_no} (ТЗ п.93)",
        entity_type="document",
        entity_id=document.id,
        payload={
            "version_id": revision.id,
            "version_no": revision.version_no,
            "based_on_version_no": source.version_no,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise IssueError("Не удалось создать новую редакцию (ТЗ п.93).") from exc
    return revision
