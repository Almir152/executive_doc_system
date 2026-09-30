"""Контекст для ИИ-агента — только через разрешённые интерфейсы (ТЗ п.103).

ИИ получает ровно столько данных, сколько система ему предоставила: никакого
доступа к диску, файлам и базе напрямую. Здесь собирается проверяемый набор
сведений о проекте, а ИИ работает уже с ним (ТЗ п.101, 102).

Поэтому контекст — обычный словарь из заранее оговорённых ключей. Это
позволяет и проверить границу (тест сравнивает ключи с белым списком), и
заменить модель, не трогая бизнес-логику документов (ТЗ п.101).
"""

from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy.orm import Session

#: Ключи, которые ИИ имеет право видеть. Всё остальное — недоступно (ТЗ п.103).
CONTEXT_KEYS = (
    "project", "documents", "links", "archive", "materials", "normative",
    "requested_documents", "request",
)

#: Ключи карточки проекта.
PROJECT_KEYS = ("id", "title", "address", "direction", "sections")

#: Номер документа в свободном тексте: «АОСР №15», «акт N 7», «№3».
NUMBER_PATTERN = re.compile(r"(?:№|\bN|\bN°)\s*(\d+[\w\-]*)", re.IGNORECASE)

#: Слова, которыми оператор называет тип документа (ТЗ п.102).
TYPE_WORDS = {
    "аоср": "АОСР",
    "акт освидетельствования скрытых работ": "АОСР",
    "аook": "АООК",
    "акт освидетельствования скрытых работ итоговый": "АООК",
    "аоусито": "АОУСИТО",
    "акт испытаний": "АКТ_ИСПЫТАНИЙ",
    "акт испытания": "АКТ_ИСПЫТАНИЙ",
}


def build_context(
    db: Session, project_id: int, request: str | None = None
) -> dict:
    """Собрать контекст проекта для ИИ-агента (ТЗ п.102, 103).

    В контекст попадают реквизиты документов, логические связи, сведения об
    архиве и материалах — без путей на диске и без содержимого файлов.
    """
    from app.db.models import (
        Document, DocumentArchiveLink, DocumentLink, Material, NormativeForm, Project,
    )

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Проект не найден: {project_id}")

    documents = (
        db.query(Document).filter(Document.project_id == project_id)
        .order_by(Document.doc_type, Document.number, Document.id).all()
    )
    context = {
        "project": {
            "id": project.id,
            "title": project.title,
            "address": project.address or "",
            "direction": project.direction.name if project.direction else "",
            "sections": [
                section.name for section in project.sections
            ] if project.sections else [],
        },
        "documents": [_document_row(db, document) for document in documents],
        "links": [
            {
                "from": link.document_id,
                "to": link.related_document_id,
                "role": link.link_role,
            }
            for link in db.query(DocumentLink).filter(
                DocumentLink.document_id.in_([d.id for d in documents] or [0])
            ).all()
        ],
        "archive": _archive_rows(db, DocumentArchiveLink, documents),
        "materials": _material_rows(db, Material, project_id),
        "normative": _normative_rows(db, NormativeForm),
        "requested_documents": [],
        "request": (request or "").strip(),
    }
    context["requested_documents"] = find_document_ids(
        context["request"], context["documents"]
    )
    return context


def _document_row(db: Session, document) -> dict:
    """Реквизиты документа для ИИ: без содержимого и путей (ТЗ п.103)."""
    from app.core.services import form_service
    from app.core.validators import PERIOD_END_KEY, PERIOD_START_KEY

    version = form_service.actual_version(db, document.id)
    payload = (version.payload if version else None) or {}
    return {
        "id": document.id,
        "type": document.doc_type,
        "type_label": document.type_label,
        "number": document.number or "",
        "doc_date": document.doc_date.isoformat() if document.doc_date else "",
        "status": document.status,
        "issued": version is not None and version.issued_at is not None,
        "period": {
            "start": payload.get(PERIOD_START_KEY, ""),
            "end": payload.get(PERIOD_END_KEY, ""),
        },
        "has_defects": payload.get("has_defects", ""),
    }


def _archive_rows(db: Session, link_model, documents: Iterable) -> list[dict]:
    """Связи документов с архивом: роли и имена файлов, без путей (ТЗ п.49)."""
    document_ids = [document.id for document in documents]
    if not document_ids:
        return []
    from app.db.models import ArchiveDocument

    rows = []
    for link in db.query(link_model).filter(
        link_model.document_id.in_(document_ids)
    ).all():
        archive = db.get(ArchiveDocument, link.archive_document_id)
        rows.append({
            "document_id": link.document_id,
            "role": link.link_role,
            "archive_id": link.archive_document_id,
            "name": archive.original_name if archive else "",
            "file_type": archive.file_type if archive else "",
            "version_no": link.archive_version.version_no
            if link.archive_version else None,
        })
    return rows


def _material_rows(db: Session, material_model, project_id: int) -> list[dict]:
    """Материалы проекта и акты испытаний, которым они отнесены (ТЗ п.44)."""
    from app.db.models import Document, MaterialTestActLink

    rows = []
    for material in db.query(material_model).filter(
        material_model.project_id == project_id
    ).all():
        act_ids = [
            link.document_id for link in db.query(MaterialTestActLink).filter(
                MaterialTestActLink.material_id == material.id
            ).all()
        ]
        acts = db.query(Document).filter(Document.id.in_(act_ids or [0])).all()
        rows.append({
            "id": material.id,
            "name": material.name,
            "material_type_id": material.material_type_id,
            "quantity": material.quantity or "",
            "test_act_ids": act_ids,
            "test_acts": [
                f"{act.type_label} № {act.number}" for act in acts
            ],
        })
    return rows


def _normative_rows(db: Session, form_model) -> list[dict]:
    """Нормативные формы системы: версия и основание (ТЗ п.96, 106)."""
    rows = []
    for form in db.query(form_model).filter(
        form_model.is_current.is_(True)
    ).order_by(form_model.doc_type).all():
        rows.append({
            "doc_type": form.doc_type,
            "version": form.version,
            "title": form.title,
            "basis": form.basis or "",
        })
    return rows


def find_document_ids(request: str, documents: Iterable[dict]) -> list[int]:
    """Какие документы названы в запросе оператора (ТЗ п.102).

    «Проверь комплект АОСР №15» — типичная формулировка: ИИ должен работать
    с указанным документом, а не со всем проектом. Номера сравниваются без
    учёта регистра и лишних пробелов.
    """
    if not request:
        return []
    wanted = {
        match.group(1).lstrip("0") or "0"
        for match in NUMBER_PATTERN.finditer(request)
    }
    # Тип из запроса уточняет номер: «АОСР №1» — это АОСР №1, а не АООК №1.
    lowered = request.lower()
    doc_type = None
    for word, value in sorted(
        TYPE_WORDS.items(), key=lambda item: -len(item[0])
    ):
        if word in lowered:
            doc_type = value
            break
    if not wanted and not doc_type:
        return []
    found = []
    for document in documents:
        if doc_type and document.get("type") != doc_type:
            continue
        number = (document.get("number") or "").lstrip("0") or "0"
        if wanted and number not in wanted:
            continue
        found.append(document["id"])
    return found


def describe_context(context: dict) -> str:
    """Краткая сводка контекста для интерфейса и журнала (ТЗ п.102)."""
    project = context.get("project", {})
    parts = [
        f"Проект: {project.get('title', '')}",
        f"Документов: {len(context.get('documents', []))}",
        f"Связей: {len(context.get('links', []))}",
        f"Файлов архива: {len(context.get('archive', []))}",
        f"Материалов: {len(context.get('materials', []))}",
    ]
    requested = context.get("requested_documents") or []
    if requested:
        parts.append("Запрошены документы: " + ", ".join(str(i) for i in requested))
    return "; ".join(parts)


def assert_context_is_allowed(context: dict) -> None:
    """Проверка границы: в контексте нет ничего лишнего (ТЗ п.103).

    Используется в тестах и может вызываться перед отправкой контекста
    внешней модели: контекст намеренно собран из белого списка ключей.
    """
    unknown = set(context) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"в контекст попали лишние данные: {sorted(unknown)}")
