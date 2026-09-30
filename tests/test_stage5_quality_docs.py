"""Документы качества: вид, сроки действия, поиск, выбор актов. ТЗ п.44–48."""

from datetime import date

import pytest

from app.core import domain
from app.core.services import (
    document_service, link_service, storage_service,
)
from app.core.services.storage_service import StorageError
from app.db.models import ArchiveDocument, DocumentArchiveLink, Project


@pytest.fixture
def acts(db, project):
    """Три акта одного проекта: документ качества выбирается не для всех."""
    return [
        document_service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, number=number,
        )
        for number in ("15", "18", "4")
    ]


def _quality(db, project, tmp_path, name="Сертификат качества.pdf", **kwargs):
    source = tmp_path / name
    source.write_bytes(f"%PDF-1.4 {name}".encode())
    return storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS, **kwargs,
    )


# =====================================================================
# ВИД ДОКУМЕНТА КАЧЕСТВА (ТЗ п.45)
# =====================================================================


@pytest.mark.parametrize("quality_type", domain.QUALITY_DOC_TYPES)
def test_each_quality_kind_is_accepted(db, project, tmp_path, quality_type):
    """Архив качества: паспорта, сертификаты, декларации, другие (ТЗ п.45)."""
    needs_dates = quality_type in domain.QUALITY_DOC_TYPES_WITH_VALIDITY
    document = _quality(
        db, project, tmp_path, name=f"{quality_type}.pdf",
        quality_type=quality_type,
        validity_from=date(2024, 1, 1) if needs_dates else None,
        validity_to=date(2025, 1, 1) if needs_dates else None,
    )

    assert quality_type in document.note
    if needs_dates:
        assert document.validity_from == date(2024, 1, 1)
        assert document.validity_to == date(2025, 1, 1)


def test_unknown_quality_kind_is_rejected(db, project, tmp_path):
    with pytest.raises(StorageError, match="Неизвестный вид документа качества"):
        _quality(db, project, tmp_path, quality_type="Копия паспорта")


def test_quality_kind_outside_materials_is_rejected(db, project, tmp_path):
    """Вид качества не приписывается схеме или протоколу (ТЗ п.45)."""
    source = tmp_path / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 scheme")

    with pytest.raises(StorageError, match="только для части «Материалы"):
        storage_service.add_file_to_archive(
            db, src_path=source, project_id=project.id,
            category=domain.ARCHIVE_CATEGORY_SCHEMES,
            quality_type=domain.QUALITY_DOC_CERTIFICATE,
        )


def test_quality_document_is_chosen_for_specific_act(db, project, tmp_path, acts):
    """Документ качества прикрепляется к конкретному акту, а не ко всем (ТЗ п.45)."""
    certificate = _quality(
        db, project, tmp_path, quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1),
    )

    link_service.link_document_to_archive(
        db, document_id=acts[0].id,
        archive_document_id=certificate.id,
        link_role=domain.LINK_ROLE_QUALITY,
    )

    for act in acts[1:]:
        links = db.query(DocumentArchiveLink).filter(
            DocumentArchiveLink.document_id == act.id
        ).all()
        assert links == [], (
            "документ качества не должен прикрепляться к актам, которые "
            "оператор не выбрал (ТЗ п.45)"
        )


def test_same_quality_document_can_serve_several_acts(db, project, tmp_path, acts):
    """Явный выбор для каждого акта: связь many-to-many (ТЗ п.45, 49)."""
    certificate = _quality(
        db, project, tmp_path, quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1),
    )

    for act in (acts[0], acts[1]):
        link_service.link_document_to_archive(
            db, document_id=act.id,
            archive_document_id=certificate.id,
            link_role=domain.LINK_ROLE_QUALITY,
        )

    assert len(certificate.links) == 2
    assert len(certificate.versions) == 1, "файл качества хранится один раз"


# =====================================================================
# СРОКИ ДЕЙСТВИЯ (ТЗ п.46)
# =====================================================================


@pytest.mark.parametrize(
    "quality_type", domain.QUALITY_DOC_TYPES_WITH_VALIDITY
)
def test_validity_dates_are_required(db, project, tmp_path, quality_type):
    with pytest.raises(StorageError, match="обязательны дата начала"):
        _quality(
            db, project, tmp_path, quality_type=quality_type,
            validity_from=date(2024, 1, 1),
        )


def test_validity_dates_are_kept_for_passport(db, project, tmp_path):
    """У паспорта срок действия не обязателен (ТЗ п.46)."""
    document = _quality(db, project, tmp_path, quality_type=domain.QUALITY_DOC_PASSPORT)

    assert document.validity_from is None
    assert document.validity_to is None


def test_reversed_validity_period_is_rejected(db, project, tmp_path):
    with pytest.raises(StorageError, match="раньше даты начала"):
        _quality(
            db, project, tmp_path, quality_type=domain.QUALITY_DOC_DECLARATION,
            validity_from=date(2025, 1, 1), validity_to=date(2024, 1, 1),
        )


def test_validity_dates_require_quality_kind(db, project, tmp_path):
    with pytest.raises(StorageError, match="вместе с видом документа качества"):
        _quality(db, project, tmp_path, validity_from=date(2024, 1, 1))


def test_quality_details_can_be_set_after_upload(db, project, tmp_path):
    """Реквизиты уточняются без второй копии файла (ТЗ п.45, 46, 92)."""
    document = _quality(db, project, tmp_path, name="Паспорт арматуры.pdf")
    before = len(document.versions)

    storage_service.set_quality_details(
        db, document.id, domain.QUALITY_DOC_PASSPORT, date(2024, 3, 1), None
    )

    stored = db.get(ArchiveDocument, document.id)
    assert "Паспорт" in stored.note
    assert stored.validity_from == date(2024, 3, 1)
    assert len(stored.versions) == before


def test_quality_details_validation_applies_after_upload(db, project, tmp_path):
    document = _quality(db, project, tmp_path, name="Декларация.pdf")

    with pytest.raises(StorageError, match="обязательны дата начала"):
        storage_service.set_quality_details(
            db, document.id, domain.QUALITY_DOC_DECLARATION, date(2024, 1, 1), None
        )


def test_validity_text_is_readable_for_operator(db, project, tmp_path):
    document = _quality(
        db, project, tmp_path, quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 15), validity_to=date(2025, 3, 20),
    )

    assert storage_service.quality_validity_text(document) == (
        "действует 15.01.2024 — 20.03.2025"
    )


# =====================================================================
# ПОИСК ПО АРХИВУ (ТЗ п.44)
# =====================================================================


def test_archive_search_by_name_number_and_kind(db, project, tmp_path):
    _quality(db, project, tmp_path, name="Сертификат арматуры.pdf",
             quality_type=domain.QUALITY_DOC_CERTIFICATE,
             validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1))
    _quality(db, project, tmp_path, name="Паспорт бетона.pdf",
             quality_type=domain.QUALITY_DOC_PASSPORT)
    other = tmp_path / "СХЕМА №12.pdf"
    other.write_bytes(b"%PDF-1.4 scheme")
    storage_service.add_file_to_archive(
        db, src_path=other, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )

    by_name = storage_service.search_archive_documents(db, project.id, search="армат")
    assert [d.original_name for d in by_name] == ["Сертификат арматуры.pdf"]

    by_kind = storage_service.search_archive_documents(
        db, project.id, quality_type=domain.QUALITY_DOC_CERTIFICATE
    )
    assert [d.original_name for d in by_kind] == ["Сертификат арматуры.pdf"]

    by_category = storage_service.search_archive_documents(
        db, project.id, category=domain.ARCHIVE_CATEGORY_SCHEMES
    )
    assert [d.original_name for d in by_category] == ["СХЕМА №12.pdf"]


def test_archive_search_is_case_insensitive(db, project, tmp_path):
    _quality(db, project, tmp_path, name="ПАСПОРТ бетона.pdf")

    assert storage_service.search_archive_documents(db, project.id, search="паспорт")


def test_archive_search_sees_other_projects_files(db, project, direction, tmp_path):
    """Поиск не выдаёт файлы чужого проекта (ТЗ п.44, 90)."""
    other = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other)
    db.commit()
    _quality(db, project, tmp_path, name="Сертификат арматуры.pdf")
    _quality(db, other, tmp_path, name="Сертификат бетона.pdf")

    found = storage_service.search_archive_documents(db, project.id, search="сертификат")

    assert [d.original_name for d in found] == ["Сертификат арматуры.pdf"]


def test_duplicate_upload_does_not_copy_file_twice(db, project, tmp_path):
    """Повторная загрузка уточняет реквизиты, копия не создаётся (ТЗ п.92)."""
    source = tmp_path / "Сертификат арматуры.pdf"
    source.write_bytes(b"%PDF-1.4 certificate")

    first = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
        quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1),
    )
    second = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
        quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1),
    )

    assert second.id == first.id
    assert len(second.versions) == 1
    assert len(list(storage_service.ARCHIVE_DIR.glob("*.pdf"))) == 1


# =====================================================================
# ОДИН ФАЙЛ — МНОГО СВЯЗЕЙ (ТЗ п.47, 48)
# =====================================================================


@pytest.mark.parametrize(
    "category,role",
    [
        (domain.ARCHIVE_CATEGORY_SCHEMES, domain.LINK_ROLE_SCHEME),
        (domain.ARCHIVE_CATEGORY_PROTOCOLS, domain.LINK_ROLE_PROTOCOL),
    ],
)
def test_one_file_serves_many_acts(db, project, tmp_path, acts, category, role):
    """Схема и протокол хранятся один раз и связываются со многими актами."""
    source = tmp_path / ("СХЕМА №12.pdf" if category == domain.ARCHIVE_CATEGORY_SCHEMES
                         else "Протокол испытаний.pdf")
    source.write_bytes(f"%PDF-1.4 {role}".encode())
    archive_document = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id, category=category,
    )

    for act in acts:
        link_service.link_document_to_archive(
            db, document_id=act.id,
            archive_document_id=archive_document.id, link_role=role,
        )

    assert archive_document.links_count == len(acts)
    assert len(archive_document.versions) == 1
