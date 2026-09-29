"""Операции над проектом. ТЗ п.17, 86, 109."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ArchiveDocument, Document, DocumentVersion, Project


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
