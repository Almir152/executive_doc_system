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
from app.core.services.project_service import record_event
from app.db.models import (
    ArchiveDocument, ArchiveFileVersion, Document, DocumentArchiveLink, DocumentLink,
    Material, MaterialType, Project,
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
        db.flush()
        record_event(
            db, document.project_id, domain.HISTORY_LINK_ADDED,
            f"{document.type_label} № {document.number}: связь «{link_role}» "
            f"с файлом «{archive_document.original_name}» (ТЗ п.45, 86)",
            entity_type="document", entity_id=document.id,
            payload={
                "link_role": link_role,
                "archive_document_id": archive_document.id,
                "archive_version_id": version_id,
                "link_id": link.id,
            },
        )
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
    document = link.document
    name = link.archive_document.original_name if link.archive_document else "—"
    db.delete(link)
    db.flush()
    record_event(
        db, document.project_id, domain.HISTORY_LINK_REMOVED,
        f"{document.type_label} № {document.number}: связь «{link.link_role}» "
        f"с файлом «{name}» удалена; файл остаётся в архиве (ТЗ п.52, 86)",
        entity_type="document", entity_id=document.id,
        payload={
            "link_role": link.link_role,
            "archive_document_id": link.archive_document_id,
        },
    )
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


# =====================================================================
# СВЯЗИ МЕЖДУ ДОКУМЕНТАМИ (ТЗ п.43, 87, 88, 89)
# =====================================================================

def link_documents(
    db: Session,
    *,
    document_id: int,
    related_document_id: int,
    link_role: str = domain.LINK_ROLE_FINALIZES,
    order_no: int = 0,
) -> DocumentLink:
    """Связать два документа проекта между собой (ТЗ п.87).

    Итоговый акт ссылается на акты, которые он завершает: без этой связи
    нельзя проверить, что дата окончания АООК не раньше окончания связанного
    АОСР. Файлы при этом не копируются — связь логическая (ТЗ п.47, 48, 49).
    """
    document = db.get(Document, document_id)
    if document is None:
        raise MaterialError(f"Документ не найден: {document_id}")
    related = db.get(Document, related_document_id)
    if related is None:
        raise MaterialError(f"Связываемый документ не найден: {related_document_id}")
    if link_role not in domain.DOCUMENT_LINK_ROLES:
        raise MaterialError(f"Неизвестная роль связи документов: {link_role}")
    if document_id == related_document_id:
        raise MaterialError(
            "Документ нельзя связать с самим собой (ТЗ п.87)."
        )
    if related.project_id != document.project_id:
        raise MaterialError(
            "Связывать документы разных проектов нельзя (ТЗ п.49)."
        )
    _check_finalizes_pair(document, related, link_role)

    link = DocumentLink(
        document_id=document_id,
        related_document_id=related_document_id,
        link_role=link_role,
        order_no=order_no,
    )
    db.add(link)
    try:
        db.flush()
        record_event(
            db, document.project_id, domain.HISTORY_LINK_ADDED,
            f"{document.type_label} № {document.number} завершает "
            f"{related.type_label} № {related.number} (ТЗ п.87)",
            entity_type="document", entity_id=document.id,
            payload={
                "link_role": link_role,
                "related_document_id": related.id,
                "related_number": related.number,
                "link_id": link.id,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise MaterialError(
            f"Связь {document.type_label} № {document.number} с "
            f"{related.type_label} № {related.number} уже есть. Повторная связь "
            "не создаётся (ТЗ п.87)."
        ) from exc
    db.refresh(link)
    return link


def _check_finalizes_pair(document: Document, related: Document, link_role: str) -> None:
    """Итоговый акт завершает только акты скрытых работ (ТЗ п.87).

    Обратное направление не имеет смысла по ТЗ: срок работы сравнивают с
    актами, а не наоборот, поэтому такая связь отвергается сразу, а не
    обнаруживается при выгрузке.
    """
    if link_role != domain.LINK_ROLE_FINALIZES:
        return
    if document.doc_type == domain.DOC_TYPE_AOSR:
        raise MaterialError(
            "Акту скрытых работ нельзя объявить завершающим: роль «"
            f"{domain.LINK_ROLE_FINALIZES}» указывается у итогового акта "
            "(ТЗ п.87)."
        )
    if related.doc_type != domain.DOC_TYPE_AOSR:
        raise MaterialError(
            f"{document.type_label} может завершать только акты освидетельствования "
            f"скрытых работ, а указан {related.type_label} (ТЗ п.87)."
        )


def unlink_documents(db: Session, link_id: int) -> None:
    """Удалить связь документов, сами документы сохраняются (ТЗ п.52, 87)."""
    link = db.get(DocumentLink, link_id)
    if link is None:
        raise MaterialError(f"Связь не найдена: {link_id}")
    document = link.document
    related = link.related_document
    message = (
        f"{document.type_label} № {document.number}: связь с "
        f"{related.type_label} № {related.number} удалена (ТЗ п.87)"
    )
    db.delete(link)
    db.flush()
    record_event(
        db, document.project_id, domain.HISTORY_LINK_REMOVED, message,
        entity_type="document", entity_id=document.id,
        payload={
            "link_role": link.link_role,
            "related_document_id": related.id,
            "related_number": related.number,
        },
    )
    db.commit()


def list_document_relations(
    db: Session, document_id: int
) -> list[DocumentLink]:
    """Связи документа с другими документами (ТЗ п.87)."""
    return list(
        db.scalars(
            select(DocumentLink)
            .where(DocumentLink.document_id == document_id)
            .order_by(DocumentLink.link_role, DocumentLink.order_no)
        ).all()
    )


def finalized_acts(db: Session, document_id: int) -> list[Document]:
    """Акты, завершённые итоговым документом (ТЗ п.87)."""
    return [link.related_document for link in list_document_relations(db, document_id)]


def final_acts_of(db: Session, document_id: int) -> list[Document]:
    """Итоговые акты, завершающие указанный акт (ТЗ п.87)."""
    return list(
        db.scalars(
            select(Document)
            .join(
                DocumentLink,
                DocumentLink.document_id == Document.id,
            )
            .where(DocumentLink.related_document_id == document_id)
            .order_by(Document.number)
        ).all()
    )


def project_acts_to_finalize(
    db: Session, project_id: int, doc_type: str
) -> list[Document]:
    """Акты проекта, которые оператор может указать как завершаемые.

    Уже связанные акты не предлагаются повторно (ТЗ п.87).
    """
    query = select(Document).where(
        Document.project_id == project_id, Document.doc_type == doc_type
    )
    order = Document.doc_date.is_(None), Document.number
    return list(db.scalars(query.order_by(*order)).all())
