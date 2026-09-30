"""Единые справочники: организации, представители, разделы, типы материалов.

ТЗ п.18–20, 22, 44. Справочники общие для всех проектов: повторный ввод
одних и тех же реквизитов не требуется (ТЗ п.19).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Material, MaterialType, Organization, Project, ProjectSection, Representative,
    SectionKind,
)


class DirectoryError(Exception):
    """Ошибка операции со справочником с текстом для оператора."""


# =====================================================================
# ОРГАНИЗАЦИИ (ТЗ п.18)
# =====================================================================


def list_organizations(db: Session, search: str = "") -> list[Organization]:
    """Справочник организаций с поиском (ТЗ п.18, 20).

    Сравнение выполняется в Python: функция lower() в SQLite не приводит
    регистр кириллицы, и поиск по «монтаж» молча не находил «Монтаж».
    """
    organizations = db.query(Organization).order_by(Organization.short_name).all()
    return [
        organization for organization in organizations
        if _matches(search, organization.short_name, organization.inn, organization.ogrn)
    ]


def save_organization(
    db: Session,
    organization_id: int | None = None,
    *,
    short_name: str,
    ogrn: str | None = None,
    inn: str | None = None,
    address: str | None = None,
    phone: str | None = None,
    fax: str | None = None,
    sro: str | None = None,
    nopriz: str | None = None,
) -> Organization:
    """Создать или изменить организацию (ТЗ п.18).

    Поле «полное наименование» отдельно не используется — по ТЗ его нет.
    """
    short_name = (short_name or "").strip()
    if not short_name:
        raise DirectoryError("Краткое наименование организации обязательно.")

    fields = {
        "short_name": short_name,
        "ogrn": _clean(ogrn),
        "inn": _clean(inn),
        "address": _clean(address),
        "phone": _clean(phone),
        "fax": _clean(fax),
        "sro": _clean(sro),
        "nopriz": _clean(nopriz),
    }

    if organization_id is None:
        organization = Organization(**fields)
        db.add(organization)
    else:
        organization = db.get(Organization, organization_id)
        if organization is None:
            raise DirectoryError(f"Организация не найдена: {organization_id}")
        for key, value in fields.items():
            setattr(organization, key, value)

    _reject_duplicate_requisites(db, organization_id, fields["inn"], fields["ogrn"])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DirectoryError(
            "Организация с такими ИНН и ОГРН уже есть в справочнике. "
            "Повторный ввод одних и тех же реквизитов не требуется (ТЗ п.19)."
        ) from exc
    return organization


def organization_usage(db: Session, organization_id: int) -> dict[str, int]:
    """Где используется организация: нужно для отказа в удалении."""
    as_customer = db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.customer_org_id == organization_id)
    ) or 0
    as_contractor = db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.general_contractor_org_id == organization_id)
    ) or 0
    in_projects = db.scalar(
        select(func.count())
        .select_from(ProjectSection)
        .where(ProjectSection.organization_id == organization_id)
    ) or 0
    representatives = db.scalar(
        select(func.count())
        .select_from(Representative)
        .where(Representative.organization_id == organization_id)
    ) or 0
    return {
        "projects": as_customer + as_contractor,
        "sections": in_projects,
        "representatives": representatives,
    }


def delete_organization(db: Session, organization_id: int) -> None:
    """Удалить организацию, только если она нигде не используется.

    Удаление организации каскадом снесло бы её представителей, а в проектах
    оставило бы пустые ссылки — поэтому отказ показывается оператору явно.
    """
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise DirectoryError(f"Организация не найдена: {organization_id}")

    usage = organization_usage(db, organization_id)
    problems = []
    if usage["projects"]:
        problems.append(f"карточек проектов: {usage['projects']}")
    if usage["sections"]:
        problems.append(f"разделов проектной документации: {usage['sections']}")
    if usage["representatives"]:
        problems.append(f"представителей: {usage['representatives']}")
    if problems:
        raise DirectoryError(
            "Организация используется в данных, удаление невозможно "
            f"({', '.join(problems)}). Сначала уберите её оттуда."
        )
    db.delete(organization)
    _commit_or_report(db, "Не удалось удалить организацию.")


# =====================================================================
# ПРЕДСТАВИТЕЛИ (ТЗ п.19)
# =====================================================================


def list_representatives(
    db: Session, organization_id: int | None = None
) -> list[Representative]:
    query = db.query(Representative).order_by(
        Representative.organization_id, Representative.position,
        Representative.full_name,
    )
    if organization_id is not None:
        query = query.filter(Representative.organization_id == organization_id)
    return query.all()


def save_representative(
    db: Session,
    representative_id: int | None = None,
    *,
    organization_id: int,
    position: str,
    full_name: str,
    phone: str | None = None,
    email: str | None = None,
) -> Representative:
    """Создать или изменить представителя (ТЗ п.19)."""
    position = (position or "").strip()
    full_name = (full_name or "").strip()
    if db.get(Organization, organization_id) is None:
        raise DirectoryError(
            f"Организация не найдена: {organization_id}. "
            "Сначала заведите организацию в справочнике."
        )
    if not position:
        raise DirectoryError("Должность представителя обязательна.")
    if not full_name:
        raise DirectoryError("Фамилия, имя представителя обязательны.")

    fields = {
        "organization_id": organization_id,
        "position": position,
        "full_name": full_name,
        "phone": _clean(phone),
        "email": _clean(email),
    }
    if representative_id is None:
        representative = Representative(**fields)
        db.add(representative)
    else:
        representative = db.get(Representative, representative_id)
        if representative is None:
            raise DirectoryError(f"Представитель не найден: {representative_id}")
        for key, value in fields.items():
            setattr(representative, key, value)

    db.commit()
    return representative


def delete_representative(db: Session, representative_id: int) -> None:
    """Удалить представителя, если он не указан проектировщиком раздела."""
    representative = db.get(Representative, representative_id)
    if representative is None:
        raise DirectoryError(f"Представитель не найден: {representative_id}")

    sections = db.scalar(
        select(func.count())
        .select_from(ProjectSection)
        .where(ProjectSection.designer_rep_id == representative_id)
    ) or 0
    if sections:
        raise DirectoryError(
            f"Представитель указан проектировщиком в {sections} разделах "
            "проектной документации (ТЗ п.21). Удаление потеряет эти сведения."
        )
    db.delete(representative)
    _commit_or_report(db, "Не удалось удалить представителя.")


# =====================================================================
# СПРАВОЧНИК РАЗДЕЛОВ (ТЗ п.22)
# =====================================================================


def list_section_kinds(db: Session) -> list[SectionKind]:
    return db.query(SectionKind).order_by(SectionKind.code).all()


def save_section_kind(
    db: Session,
    section_kind_id: int | None = None,
    *,
    code: str,
    name: str,
) -> SectionKind:
    """Создать или изменить вид раздела (ТЗ п.22, расширяемый)."""
    code = (code or "").strip().upper()
    name = (name or "").strip()
    if not code:
        raise DirectoryError("Код раздела обязателен (ТЗ п.22).")
    if not name:
        raise DirectoryError("Наименование раздела обязательно (ТЗ п.22).")

    if section_kind_id is None:
        kind = SectionKind(code=code, name=name)
        db.add(kind)
    else:
        kind = db.get(SectionKind, section_kind_id)
        if kind is None:
            raise DirectoryError(f"Вид раздела не найден: {section_kind_id}")
        kind.code = code
        kind.name = name

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DirectoryError(
            f"Вид раздела с кодом «{code}» уже есть в справочнике (ТЗ п.22)."
        ) from exc
    return kind


def delete_section_kind(db: Session, section_kind_id: int) -> None:
    """Удалить вид раздела, если ни один раздел его не использует."""
    kind = db.get(SectionKind, section_kind_id)
    if kind is None:
        raise DirectoryError(f"Вид раздела не найден: {section_kind_id}")

    used = db.scalar(
        select(func.count())
        .select_from(ProjectSection)
        .where(ProjectSection.kind_id == section_kind_id)
    ) or 0
    if used:
        raise DirectoryError(
            f"Вид раздела «{kind.code}» используется в {used} разделах "
            "проектной документации. Удаление невозможно."
        )
    db.delete(kind)
    _commit_or_report(db, "Не удалось удалить вид раздела.")


# =====================================================================
# СПРАВОЧНИК ТИПОВ МАТЕРИАЛОВ (ТЗ п.44)
# =====================================================================


def list_material_types(db: Session) -> list[MaterialType]:
    return db.query(MaterialType).order_by(MaterialType.code).all()


def save_material_type(
    db: Session,
    material_type_id: int | None = None,
    *,
    code: str,
    name: str,
) -> MaterialType:
    """Создать или изменить тип материала (ТЗ п.44)."""
    code = (code or "").strip().upper()
    name = (name or "").strip()
    if not code:
        raise DirectoryError("Код типа материала обязателен.")
    if not name:
        raise DirectoryError("Наименование типа материала обязательно.")

    if material_type_id is None:
        material_type = MaterialType(code=code, name=name)
        db.add(material_type)
    else:
        material_type = db.get(MaterialType, material_type_id)
        if material_type is None:
            raise DirectoryError(f"Тип материала не найден: {material_type_id}")
        material_type.code = code
        material_type.name = name

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DirectoryError(
            f"Тип материала с кодом «{code}» уже есть в справочнике."
        ) from exc
    return material_type


def delete_material_type(db: Session, material_type_id: int) -> None:
    """Удалить тип материала, если он не задействован ни в одном материале."""
    material_type = db.get(MaterialType, material_type_id)
    if material_type is None:
        raise DirectoryError(f"Тип материала не найден: {material_type_id}")

    used = db.scalar(
        select(func.count())
        .select_from(Material)
        .where(Material.material_type_id == material_type_id)
    ) or 0
    if used:
        raise DirectoryError(
            f"Тип материала «{material_type.code}» задействован в {used} "
            "материалах (ТЗ п.44). Удаление невозможно."
        )
    db.delete(material_type)
    _commit_or_report(db, "Не удалось удалить тип материала.")


# =====================================================================
# ВСПОМОГАТЕЛЬНОЕ
# =====================================================================


def _clean(value: str | None) -> str | None:
    """Пустая строка от оператора сохраняется как отсутствие значения."""
    value = (value or "").strip()
    return value or None


def _matches(search: str, *values: str | None) -> bool:
    """Подстрока без учёта регистра; регистр кириллицы учитывается."""
    needle = (search or "").strip().lower()
    if not needle:
        return True
    return any(needle in (value or "").lower() for value in values)


def _reject_duplicate_requisites(
    db: Session,
    organization_id: int | None,
    inn: str | None,
    ogrn: str | None,
) -> None:
    """Отказ на повторный ввод реквизитов (ТЗ п.19).

    Ограничение уникальности в БД построено на паре (ИНН, ОГРН), а пустой ОГРН
    в SQLite не конфликтует с пустым: две организации с одинаковым ИНН и без ОГРН
    прошли бы незамеченными. Поэтому дубль ищется явно.
    """
    if organization_id is not None:
        existing = db.query(Organization).filter(
            Organization.id != organization_id
        ).all()
    else:
        existing = db.query(Organization).all()

    for organization in existing:
        if inn and organization.inn == inn:
            raise DirectoryError(
                f"ИНН {inn} уже указан у организации «{organization.short_name}». "
                "Повторный ввод одинаковых реквизитов не требуется (ТЗ п.19)."
            )
        if ogrn and organization.ogrn == ogrn:
            raise DirectoryError(
                f"ОГРН {ogrn} уже указан у организации «{organization.short_name}». "
                "Повторный ввод одинаковых реквизитов не требуется (ТЗ п.19)."
            )


def _commit_or_report(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DirectoryError(f"{message} Запись используется в других данных.") from exc
