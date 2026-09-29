"""Операции над проектом. ТЗ п.17, 86, 109."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ArchiveDocument, Document, Project


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
    return {"documents": documents, "archive_documents": archive}


def can_delete_project(db: Session, project_id: int) -> tuple[bool, dict[str, int]]:
    """Можно ли удалить проект.

    Проект с архивными документами удалить нельзя: файлы являются
    доказательством по ТЗ п.54, 109, и их удаление необратимо.
    """
    stats = project_statistics(db, project_id)
    return stats["archive_documents"] == 0, stats


def delete_project(db: Session, project_id: int) -> None:
    """Удалить проект. Отказ, если в проекте остались архивные документы."""
    project = db.get(Project, project_id)
    if project is None:
        raise ProjectError(f"Проект не найден: {project_id}")

    allowed, stats = can_delete_project(db, project_id)
    if not allowed:
        raise ProjectError(
            f"В проекте {stats['archive_documents']} архивных документов. "
            "Сначала очистите архив проекта: файлы не удаляются автоматически."
        )

    db.delete(project)
    db.commit()
