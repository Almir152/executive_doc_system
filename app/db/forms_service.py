"""Загрузка нормативных форм в справочник (ТЗ п.24, 96).

Формы поставляются как данные и версионируются. Ключевое правило ТЗ п.96:
если форма изменяется, появляется новая версия, а документы, сформированные
по прежней версии, сохраняют её. Поэтому обновление формы никогда не
перезаписывает уже существующую версию: при расхождении содержимого
загрузка отказывается и требует нового номера версии.

Также действует запрет удаления формы, на которую ссылаются документы
(ТЗ п.86, 109): иначе выпущенный документ остался бы без формы, по которой
он был сформирован.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.forms import (
    FormDefinitionError,
    NormativeFormDefinition,
    describe,
    validate_against_spec,
)
from app.db.form_definitions import FORM_DEFINITIONS
from app.db.models import NormativeForm

logger = logging.getLogger(__name__)


class FormVersionConflict(RuntimeError):
    """Определение изменилось, но версия уже занята другой формой.

    Требуется новая версия: молча перезаписать её нельзя, иначе ранее
    выпущенные документы изменятся вместе с формой (ТЗ п.96).
    """


def load_form_definitions(session: Session, definitions=FORM_DEFINITIONS) -> dict:
    """Загрузить определения форм в справочник.

    Идемпотентна: повторный запуск с тем же содержимым ничего не меняет.
    Возвращает отчёт для журнала запуска.
    """
    report = {"created": [], "unchanged": [], "skipped": []}

    for raw in definitions:
        definition = NormativeFormDefinition.from_dict(raw)
        doc_type = definition.doc_type

        existing = session.scalars(
            select(NormativeForm).where(
                NormativeForm.doc_type == doc_type,
                NormativeForm.version == definition.version,
            )
        ).one_or_none()

        if existing is None:
            # Перед вставкой проверяется правило ТЗ: структура формы, не
            # соответствующая требованиям, в базу не попадает.
            problems = validate_against_spec(definition)
            if problems:
                report["skipped"].append(
                    {"doc_type": doc_type, "version": definition.version,
                     "problems": problems}
                )
                logger.warning(
                    "форма %s v%s не загружена: %s", doc_type, definition.version,
                    "; ".join(problems),
                )
                continue
            form = NormativeForm(
                doc_type=doc_type,
                version=definition.version,
                title=definition.title,
                basis=definition.basis,
                is_current=True,
                definition=definition.to_dict(),
            )
            session.add(form)
            # Только одна версия типа документа считается актуальной.
            session.execute(
                NormativeForm.__table__.update()
                .where(
                    NormativeForm.__table__.c.doc_type == doc_type,
                    NormativeForm.__table__.c.version != definition.version,
                )
                .values(is_current=False)
            )
            report["created"].append(f"{doc_type} v{definition.version}")
            logger.info("загружена форма %s", describe(definition))
            continue

        # Сравниваются разобранные формы, а не исходные словари: JSON не
        # различает кортежи и списки, и сравнение словарей выдавало бы
        # расхождение при каждом запуске.
        try:
            stored = parse_form(existing)
        except FormDefinitionError as exc:
            logger.error(
                "сохранённая форма %s v%s не разбирается: %s", doc_type,
                definition.version, exc,
            )
        else:
            if stored == definition:
                report["unchanged"].append(f"{doc_type} v{definition.version}")
                continue

        report["skipped"].append({
            "doc_type": doc_type,
            "version": definition.version,
            "problems": [
                f"версия {definition.version} уже занята другой формой; "
                f"укажите новый номер версии (ТЗ п.96)"
            ],
        })
        logger.error(
            "форма %s v%s не совпадает с уже сохранённой — требуется новая версия",
            doc_type, definition.version,
        )

    session.commit()
    return report


def current_form(session: Session, doc_type: str) -> NormativeForm | None:
    """Актуальная версия формы для типа документа."""
    return session.scalars(
        select(NormativeForm)
        .where(NormativeForm.doc_type == doc_type, NormativeForm.is_current.is_(True))
        .order_by(NormativeForm.version.desc())
    ).first()


def form_by_version(session: Session, doc_type: str, version: int) -> NormativeForm | None:
    """Конкретная версия формы.

    Используется при открытии ранее выпущенного документа: он должен
    показываться по той форме, по которой был сформирован (ТЗ п.96).
    """
    return session.scalars(
        select(NormativeForm).where(
            NormativeForm.doc_type == doc_type,
            NormativeForm.version == version,
        )
    ).one_or_none()


def parse_form(form: NormativeForm) -> NormativeFormDefinition:
    """Разобрать сохранённое описание формы."""
    return NormativeFormDefinition.from_dict(form.definition)


def assert_form_in_use_guard(session: Session, form: NormativeForm) -> None:
    """Запретить удаление формы, используемой документами.

    ТЗ п.86 и 109: выпущенный документ обязан сохранять форму, по которой
    он был сформирован, поэтому форма, на которую ссылаются документы,
    не удаляется.
    """
    from app.db.models import Document

    used = session.scalars(
        select(Document.id).where(Document.form_version_id == form.id).limit(1)
    ).first()
    if used is not None:
        raise RuntimeError(
            f"форма {form.doc_type} v{form.version} используется документами "
            f"и не может быть удалена (ТЗ п.96)"
        )
