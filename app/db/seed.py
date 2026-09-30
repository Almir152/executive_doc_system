"""Начальное наполнение справочников. ТЗ п.14, 22, 44.

Справочники заполняются один раз и с того момента редактируются пользователем
(ТЗ п.20, п.22). Функция идемпотентна: повторный запуск не создаёт дублей.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import domain
from app.db.models import Direction, MaterialType, SectionKind

logger = logging.getLogger(__name__)


def _ensure(session: Session, model, code_field: str, rows) -> int:
    """Добавить отсутствующие записи справочника. Возвращает число добавленных."""
    added = 0
    existing = {getattr(r, code_field) for r in session.scalars(select(model))}
    for index, (code, name) in enumerate(rows):
        if code in existing:
            continue
        if model is Direction:
            session.add(Direction(name=name, sort_order=index))
        else:
            session.add(model(code=code, name=name))
        added += 1
    return added


def seed_reference_data(session: Session) -> dict[str, int]:
    """Заполнить справочники ТЗ. Возвращает количество добавленного."""
    counts = {
        "directions": _ensure(
            session, Direction, "name",
            [(name, name) for name in domain.DIRECTIONS],
        ),
        "section_kinds": _ensure(
            session, SectionKind, "code", domain.SECTION_KINDS_INITIAL
        ),
        "material_types": _ensure(
            session, MaterialType, "code", domain.MATERIAL_TYPES_INITIAL
        ),
    }
    session.commit()
    if any(counts.values()):
        logger.info("справочники дополнены: %s", counts)

    # Нормативные формы — тоже справочник: они версионируются и загружаются
    # из данных, а не из кода (ТЗ п.24, 96).
    from app.db.forms_service import load_form_definitions

    forms = load_form_definitions(session)
    if forms["created"]:
        logger.info("загружены нормативные формы: %s", forms["created"])
    for item in forms["skipped"]:
        logger.error("форма %s v%s не загружена: %s", item["doc_type"],
                     item["version"], "; ".join(item["problems"]))
    counts["normative_forms_created"] = len(forms["created"])
    counts["normative_forms_skipped"] = len(forms["skipped"])
    return counts
