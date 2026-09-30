"""Материалы и связи документов с архивом. ТЗ п.44–48, 49, 51, 52.

Материалы и документы качества — разные вещи: материал заводится в проекте,
а документ качества лежит в архиве и привязывается к конкретному акту
(ТЗ п.45). Исполнительные схемы и протоколы хранятся в архиве один раз и
связываются со многими актами (ТЗ п.47, 48): файл не копируется на каждую
связь (ТЗ п.49).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import domain
from app.db.models import (
    ArchiveDocument, ArchiveFileVersion, Document, DocumentArchiveLink, Material,
    MaterialType, Project,
)


class MaterialError(Exception):
    """Ошибка операции с материалами и связями с текстом для оператора."""


# =====================================================================
# МАТЕРИАЛЫ (ТЗ п.44)
# =====================================================================


def list_materials(db: Session, project_id: int, search: str = "") -> list[Material]:
    """Материалы проекта с поиском (ТЗ п.44)."""
    materials = list(
        db.scalars(
            select(Material).where(Material.project_id == project_id)
            .order_by(Material.name)
        ).all()
    )
    needle = (search or "").strip().lower()
    if not needle:
        return materials
    return [
        material for material in materials
        if needle in material.name.lower()
        or (material.material_type and needle in material.material_type.name.lower())
    ]


def save_material(
    db: Session,
    project_id: int,
    *,
    name: str,
    material_type_id: int,
    material_id: int | None = None,
    unit: str | None = None,
    quantity: float | None = None,
    note: str | None = None,
) -> Material:
    """Добавить или изменить материал проекта (ТЗ п.44)."""
    if db.get(Project, project_id) is None:
        raise MaterialError(f"Проект не найден: {project_id}")

    name = (name or "").strip()
    if not name:
        raise MaterialError("Наименование материала обязательно (ТЗ п.44).")
    if db.get(MaterialType, material_type_id) is None:
        raise MaterialError(
            f"Тип материала не найден: {material_type_id}. "
            "Сначала заведите его в справочнике (ТЗ п.44)."
        )

    fields = {
        "name": name,
        "unit": (unit or "").strip() or None,
        "quantity": quantity,
        "note": (note or "").strip() or None,
    }

    if material_id is None:
        material = Material(
            project_id=project_id, material_type_id=material_type_id, **fields
        )
        db.add(material)
    else:
        material = db.get(Material, material_id)
        if material is None:
            raise MaterialError(f"Материал не найден: {material_id}")
        if material.project_id != project_id:
            raise MaterialError("Материал принадлежит другому проекту.")
        for key, value in fields.items():
            setattr(material, key, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise MaterialError(f"Не удалось сохранить материал: {exc}") from exc
    return material


def delete_material(db: Session, material_id: int) -> None:
    """Удалить материал из проекта (ТЗ п.44).

    Материал — запись реестра проекта. Отдельной связи «акт → материал» в
    модели нет: акт ссылается на архивные документы качества, схемы и
    протоколы (ТЗ п.45, 49). Поэтому удаление ограничено только целостностью
    базы.
    """
    material = db.get(Material, material_id)
    if material is None:
        raise MaterialError(f"Материал не найден: {material_id}")

    db.delete(material)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise MaterialError(
            "Материал используется в других данных, удаление невозможно."
        ) from exc


# =====================================================================
# СВЯЗИ ДОКУМЕНТОВ С АРХИВОМ (ТЗ п.45, 47, 48, 49)
# =====================================================================

_ROLE_BY_CATEGORY = {
    domain.ARCHIVE_CATEGORY_MATERIALS: domain.LINK_ROLE_QUALITY,
    domain.ARCHIVE_CATEGORY_SCHEMES: domain.LINK_ROLE_SCHEME,
    domain.ARCHIVE_CATEGORY_PROTOCOLS: domain.LINK_ROLE_PROTOCOL,
}


def link_roles_for_category(category: str) -> tuple[str, ...]:
    """Роли связи, допустимые для категории архива (ТЗ п.45, 47, 48)."""
    if category in _ROLE_BY_CATEGORY:
        return (_ROLE_BY_CATEGORY[category],)
    return domain.LINK_ROLES


def link_document_to_archive(
    db: Session,
    *,
    document_id: int,
    archive_document_id: int,
    link_role: str,
    order_no: int = 0,
    pin_version: bool = True,
) -> DocumentArchiveLink:
    """Связать документ с архивным документом (ТЗ п.49).

    Файл не копируется: связь ссылается на уже загруженный архивный документ.
    При ``pin_version`` закрепляется текущая версия файла, чтобы новая
    редакция архива не изменила уже выданный комплект (ТЗ п.91).
    """
    document = db.get(Document, document_id)
    if document is None:
        raise MaterialError(f"Документ не найден: {document_id}")
    archive_document = db.get(ArchiveDocument, archive_document_id)
    if archive_document is None:
        raise MaterialError(f"Документ архива не найден: {archive_document_id}")
    if link_role not in domain.LINK_ROLES:
        raise MaterialError(f"Неизвестная роль связи: {link_role}")
    if archive_document.project_id is not None and (
        archive_document.project_id != document.project_id
    ):
        raise MaterialError(
            "Документ архива относится к другому проекту. Связывать документы "
            "разных проектов нельзя (ТЗ п.49)."
        )

    version_id = None
    if pin_version:
        version = _current_version(db, archive_document)
        if version is None:
            raise MaterialError(
                f"У документа архива «{archive_document.original_name}» нет "
                "загруженного файла. Сначала загрузите файл в архив (ТЗ п.84)."
            )
        version_id = version.id

    link = DocumentArchiveLink(
        document_id=document_id,
        archive_document_id=archive_document_id,
        archive_version_id=version_id,
        link_role=link_role,
        order_no=order_no,
    )
    db.add(link)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise MaterialError(
            f"Связь «{archive_document.original_name}» с ролью «{link_role}» "
            "уже есть. Повторная связь не создаётся (ТЗ п.49)."
        ) from exc
    return link


def _current_version(
    db: Session, archive_document: ArchiveDocument
) -> ArchiveFileVersion | None:
    return db.scalar(
        select(ArchiveFileVersion)
        .where(
            ArchiveFileVersion.archive_document_id == archive_document.id,
            ArchiveFileVersion.is_actual.is_(True),
        )
        .order_by(ArchiveFileVersion.version_no.desc())
    )


def unlink_document_from_archive(db: Session, link_id: int) -> None:
    """Удалить связь, не удаляя сам архивный документ (ТЗ п.52)."""
    link = db.get(DocumentArchiveLink, link_id)
    if link is None:
        raise MaterialError(f"Связь не найдена: {link_id}")
    db.delete(link)
    db.commit()


def list_document_links(
    db: Session, document_id: int
) -> list[DocumentArchiveLink]:
    """Связи документа в порядке роли и номера (ТЗ п.49)."""
    return list(
        db.scalars(
            select(DocumentArchiveLink)
            .where(DocumentArchiveLink.document_id == document_id)
            .order_by(DocumentArchiveLink.link_role, DocumentArchiveLink.order_no)
        ).all()
    )


def archive_links_count(db: Session, archive_document_id: int) -> int:
    """Счётчик связей архивного документа (ТЗ п.51)."""
    return db.scalar(
        select(func.count())
        .select_from(DocumentArchiveLink)
        .where(DocumentArchiveLink.archive_document_id == archive_document_id)
    ) or 0
