"""Операции над проектом. ТЗ п.17, 86, 109."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    ArchiveDocument, Document, DocumentVersion, HistoryEvent, Organization, Project,
    ProjectSection, Representative, SectionKind,
)


class ProjectError(Exception):
    """Ошибка операции над проектом с текстом для оператора."""


def project_statistics(db: Session, project_id: int) -> dict[str, int]:
    """Счётчики для подтверждения удаления (ТЗ п.109)."""
    documents = db.scalar(
        select(func.count()).select_from(Document).where(Document.project_id == project_id)
    ) or 0
    archive = db.scalar(
        select(func.count())
        .select_from(ArchiveDocument)
        .where(ArchiveDocument.project_id == project_id)
    ) or 0
    # Выпущенные версии — историческое доказательство (ТЗ п.54, 85). Их
    # наличие тоже должно останавливать удаление, иначе оператор уносит
    # проектом все выпуски документов разом.
    issued = db.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            Document.project_id == project_id,
            DocumentVersion.issued_at.isnot(None),
        )
    ) or 0
    return {
        "documents": documents,
        "archive_documents": archive,
        "issued_versions": issued,
    }


def can_delete_project(db: Session, project_id: int) -> tuple[bool, dict[str, int]]:
    """Можно ли удалить проект.

    Проект с архивными документами удалить нельзя: файлы являются
    доказательством по ТЗ п.54, 109, и их удаление необратимо.

    Проект с выпущенными документами тоже нельзя: выпуск — исторический
    результат (ТЗ п.85), и удаление проекта уничтожило бы его запись.
    """
    stats = project_statistics(db, project_id)
    allowed = stats["archive_documents"] == 0 and stats["issued_versions"] == 0
    return allowed, stats


def delete_project(db: Session, project_id: int) -> None:
    """Удалить проект. Отказ, если в проекте есть архив или выпуски."""
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectError(f"Проект не найден: {project_id}")

    allowed, stats = can_delete_project(db, project_id)
    if not allowed and stats["archive_documents"]:
        raise ProjectError(
            f"В проекте {stats['archive_documents']} архивных документов. "
            "Сначала очистите архив проекта: файлы не удаляются автоматически."
        )
    if not allowed and stats["issued_versions"]:
        raise ProjectError(
            f"В проекте {stats['issued_versions']} выпущенных версий документов. "
            "Выпуск — исторический результат (ТЗ п.54, 85) и не удаляется "
            "вместе с проектом."
        )

    db.delete(project)
    db.commit()


# =====================================================================
# КАРТОЧКА ПРОЕКТА (ТЗ п.17)
# =====================================================================


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def update_project_card(
    db: Session,
    project_id: int,
    *,
    title: str,
    address: str | None = None,
    customer_org_id: int | None = None,
    general_contractor_org_id: int | None = None,
    organization_ids: list[int] | None = None,
) -> Project:
    """Изменить статические данные карточки (ТЗ п.17).

    Статические данные редактируются только отдельной операцией: у проекта
    нет кнопки неявного сохранения при закрытии, иначе правка могла бы
    потеряться молча.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectError(f"Проект не найден: {project_id}")

    if not title.strip():
        raise ProjectError("Наименование проекта обязательно (ТЗ п.17).")

    project.title = title.strip()
    project.address = (address or "").strip() or None
    project.customer_org_id = customer_org_id
    project.general_contractor_org_id = general_contractor_org_id
    if organization_ids is not None:
        _set_project_organizations(db, project, organization_ids)

    # История пишется в той же транзакции, что и правка: иначе карточка
    # изменилась бы, а в истории события не оказалось бы (ТЗ п.86).
    record_event(
        db, project_id, "project_card_updated",
        f"Изменена карточка проекта: {project.title}",
        entity_type="project", entity_id=project_id,
    )
    db.commit()
    return project


def _set_project_organizations(
    db: Session, project: Project, organization_ids: list[int]
) -> None:
    """Перечень необходимых организаций (ТЗ п.17)."""
    from app.db.models import Organization, project_organizations

    unique = sorted({int(i) for i in organization_ids if i is not None})
    found = set(
        db.scalars(
            select(Organization.id).where(Organization.id.in_(unique))
        ).all()
    ) if unique else set()
    missing = [i for i in unique if i not in found]
    if missing:
        raise ProjectError(
            f"Организации не найдены в справочнике: {missing}. "
            "Сначала заведите их в справочнике организаций."
        )
    db.execute(
        project_organizations.delete().where(
            project_organizations.c.project_id == project.id
        )
    )
    for organization_id in unique:
        db.execute(
            project_organizations.insert().values(
                project_id=project.id, organization_id=organization_id
            )
        )


# =====================================================================
# ПРОЕКТНАЯ ДОКУМЕНТАЦИЯ (ТЗ п.21, 22)
# =====================================================================


def add_section(
    db: Session,
    project_id: int,
    *,
    kind_id: int,
    code: str,
    name: str,
    organization_id: int | None = None,
    designer_rep_id: int | None = None,
    sheets: str | None = None,
    required_details: str | None = None,
) -> ProjectSection:
    """Добавить раздел проектной документации.

    Количество разделов не ограничено (ТЗ п.21), но код раздела внутри
    проекта уникален: иначе в комплекте появятся две неразличимые папки.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectError(f"Проект не найден: {project_id}")

    code = code.strip()
    name = name.strip()
    if not code:
        raise ProjectError("Код раздела обязателен (ТЗ п.21).")
    if not name:
        raise ProjectError("Наименование раздела обязательно (ТЗ п.21).")

    existing = db.scalar(
        select(func.count())
        .select_from(ProjectSection)
        .where(
            ProjectSection.project_id == project_id,
            ProjectSection.code == code,
        )
    )
    if existing:
        raise ProjectError(
            f"Раздел с кодом «{code}» уже есть в проекте. "
            "Код раздела должен быть уникален в пределах проекта."
        )

    section = ProjectSection(
        project_id=project_id,
        kind_id=kind_id,
        code=code,
        name=name,
        organization_id=organization_id,
        designer_rep_id=designer_rep_id,
        sheets=(sheets or "").strip() or None,
        required_details=(required_details or "").strip() or None,
    )
    db.add(section)
    try:
        db.commit()
    except IntegrityError as exc:
        # Предварительная проверка выше не защищает от гонки: уникальность
        # кода обеспечивает сама БД, и её нарушение должно доходить до
        # оператора как понятный отказ, а не как исключение SQLAlchemy.
        db.rollback()
        raise ProjectError(
            f"Раздел с кодом «{code}» уже есть в проекте. "
            "Код раздела должен быть уникален в пределах проекта."
        ) from exc
    return section


def list_sections(db: Session, project_id: int) -> list[ProjectSection]:
    return list(
        db.scalars(
            select(ProjectSection)
            .where(ProjectSection.project_id == project_id)
            .order_by(ProjectSection.code)
        ).all()
    )


def update_section(
    db: Session,
    section_id: int,
    *,
    kind_id: int | None = None,
    organization_id: int | None = None,
    designer_rep_id: int | None = None,
    sheets: str | None = None,
    required_details: str | None = None,
    name: str | None = None,
) -> ProjectSection:
    """Изменить реквизиты раздела (ТЗ п.21).

    Код раздела не меняется: он участвует в именах папок комплекта (ТЗ п.70),
    поэтому переименование потребовало бы пересборки уже выпущенного.
    """
    section = db.get(ProjectSection, section_id)
    if section is None:
        raise ProjectError(f"Раздел не найден: {section_id}")

    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError("Наименование раздела обязательно (ТЗ п.21).")
        section.name = name

    if kind_id is not None and kind_id != section.kind_id:
        if db.get(SectionKind, kind_id) is None:
            raise ProjectError(
                f"Вид раздела не найден: {kind_id}. "
                "Сначала заведите его в справочнике (ТЗ п.22)."
            )
        section.kind_id = kind_id

    if organization_id is not None:
        if db.get(Organization, organization_id) is None:
            raise ProjectError(
                f"Организация не найдена: {organization_id}. "
                "Сначала заведите её в справочнике (ТЗ п.18)."
            )
        section.organization_id = organization_id

    if designer_rep_id is not None:
        if db.get(Representative, designer_rep_id) is None:
            raise ProjectError(
                f"Представитель не найден: {designer_rep_id}. "
                "Сначала заведите его в справочнике (ТЗ п.19)."
            )
        section.designer_rep_id = designer_rep_id

    if sheets is not None:
        section.sheets = sheets.strip() or None
    if required_details is not None:
        section.required_details = required_details.strip() or None

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ProjectError(f"Не удалось сохранить раздел: {exc}") from exc
    return section


def delete_section(db: Session, section_id: int) -> None:
    """Удалить раздел.

    Отказ, если раздел уже задействован в выпущенном документе: раздел —
    часть исторического результата (ТЗ п.54, 85).
    """
    section = db.get(ProjectSection, section_id)
    if section is None:
        raise ProjectError(f"Раздел не найден: {section_id}")

    issued = db.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            Document.project_id == section.project_id,
            DocumentVersion.issued_at.isnot(None),
        )
    )
    if issued:
        raise ProjectError(
            f"В проекте {issued} выпущенных версий документов. Раздел "
            "относится к историческому результату и не удаляется (ТЗ п.54, 85)."
        )
    db.delete(section)
    db.commit()


# =====================================================================
# ИСТОРИЯ ПРОЕКТА (ТЗ п.86)
# =====================================================================


def record_event(
    db: Session,
    project_id: int,
    event_type: str,
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    payload: dict | None = None,
) -> HistoryEvent:
    """Записать событие истории проекта.

    Событие пишется в ту же транзакцию, что и сама операция, иначе история
    расходилась бы с данными (ТЗ п.86). Коммит вызывает вызывающий: иногда
    событие — часть более крупной операции.
    """
    if not message.strip():
        raise ProjectError("Описание события обязательно.")
    event = HistoryEvent(
        project_id=project_id,
        event_type=event_type,
        message=message.strip(),
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    db.add(event)
    return event


def list_events(db: Session, project_id: int, limit: int = 200) -> list[HistoryEvent]:
    return list(
        db.scalars(
            select(HistoryEvent)
            .where(HistoryEvent.project_id == project_id)
            .order_by(HistoryEvent.created_at.desc(), HistoryEvent.id.desc())
            .limit(limit)
        ).all()
    )
