"""Комплекты и выгрузка. ТЗ п.69–83, 72, 76–81, 92."""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from app.core import domain
from app.core.services import (
    document_service, form_service, issue_service, link_service, package_service,
    project_service, storage_service,
)
from app.db.models import Document

REQUIRED_BY_TYPE = {
    domain.DOC_TYPE_AOU_SITO: {
        "object_name": "ЖК Северный, корпус 2",
        "address": "г. Москва, ул. Северная, 1",
        "work_description": "Проверка систем теплоснабжения",
        "work_volume": "2 узла",
        "contract_number": "12/2024",
        "conclusion": "Система соответствует проекту",
        "violation_basis": "Отсутствует",
    },
    domain.DOC_TYPE_AOOK: {
        "object_name": "ЖК Северный, корпус 2",
        "address": "г. Москва, ул. Северная, 1",
        "work_volume": "120 м² бетона Б25",
        "base_documents": "Договор № 12/2024",
        "decisions": "Признать работы качественными",
    },
}

REQUIRED = {
    "object_name": "ЖК Северный, корпус 2",
    "address": "г. Москва, ул. Северная, 1",
    "work_description": "Армирование стен и перекрытий",
    "section_refs": "КЖ",
    "work_period": "с 01.04.2024 по 30.04.2024",
    "work_volume": "120 м² бетона Б25",
    "has_defects": "Нет",
    "conclusion": "Работы выполнены в полном объёме",
    "work_performer": "ООО «Строй»",
}


def _issued(db, project, number: str, doc_type: str = domain.DOC_TYPE_AOSR):
    """Выпущенный документ с номером — то, что готово к комплекту."""
    document = document_service.create_document(
        db, project.id, doc_type=doc_type, number=number, doc_date=date(2024, 5, 1)
    )
    payload = REQUIRED_BY_TYPE.get(doc_type, REQUIRED)
    # ТЗ п.64: блок представителя эксплуатации оставлен пустым, решение
    # оператора — убрать его из печати.
    choice = (
        form_service.MISSING_OMIT_BLOCK
        if form_service.needs_exploitation_decision(db, document.id, payload)
        else None
    )
    form_service.save_draft(db, document.id, dict(payload), exploitation_choice=choice)
    issue_service.issue_document(db, document.id, doc_date=date(2024, 5, 1))
    return document


@pytest.fixture(autouse=True)
def project_section(db, project):
    """Раздел КЖ в проекте: документы ссылаются на него (ТЗ п.21)."""
    from app.db.models import ProjectSection, SectionKind

    db.add(ProjectSection(
        project_id=project.id, kind_id=db.query(SectionKind).first().id,
        code="КЖ", name="Конструкции",
    ))
    db.commit()


@pytest.fixture
def issued(db, project):
    return _issued(db, project, "1")


def _archive(db, project, name: str, category: str = domain.ARCHIVE_CATEGORY_SCHEMES):
    """Файл в архиве проекта (ТЗ п.47, 84).

    Содержимое уникально для каждого файла: архив хранит одну физическую
    копию по содержимому (ТЗ п.92), и одинаковые «заглушки» слились бы в
    один архивный документ.
    """
    source = Path(storage_service.ARCHIVE_DIR) / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"файл архива: {name}".encode("utf-8"))
    return storage_service.add_file_to_archive(db, source, project.id, category=category)


def _link(db, document, archive, role: str):
    return link_service.link_document_to_archive(
        db, document_id=document.id, archive_document_id=archive.id, link_role=role
    )


# =====================================================================
# ТЗ п.70, 71: ПАПКИ КОМПЛЕКТОВ И ИСТОРИЧЕСКИЕ ВЫГРУЗКИ
# =====================================================================


def test_root_folder_and_inner_folder(db, project, issued, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert package.folder_name == "Комплект 01"
    assert Path(package.absolute_path) == tmp_path / "Комплекты" / "Комплект 01"
    assert (Path(package.absolute_path) / package_service.REGISTRY_FILE_NAME).exists()


def test_operator_names_root_folder(db, project, issued, tmp_path):
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, root_name="Выгрузки ООО Заказчик"
    )

    assert Path(package.absolute_path).parent.name == "Выгрузки ООО Заказчик"


def test_root_name_must_be_a_folder_name(db, project, issued, tmp_path):
    with pytest.raises(package_service.PackageError, match="обязательно"):
        package_service.create_package(db, project.id, base_dir=tmp_path, root_name="  ")
    with pytest.raises(package_service.PackageError, match="не имя папки"):
        package_service.create_package(db, project.id, base_dir=tmp_path, root_name="а/б")


def test_new_package_does_not_change_previous(db, project, issued, tmp_path):
    first = package_service.create_package(db, project.id, base_dir=tmp_path)
    registry = Path(first.absolute_path) / package_service.REGISTRY_FILE_NAME
    before = registry.read_bytes()

    second = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert second.folder_name == "Комплект 02"
    assert registry.read_bytes() == before


def test_folder_number_skips_folder_created_outside(db, project, issued, tmp_path):
    """Папка, созданная мимо программы, не перезаписывается (ТЗ п.71)."""
    root = package_service.ensure_root(package_service.resolve_root(tmp_path))
    (root / "Комплект 01").mkdir()

    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert package.folder_name == "Комплект 02"


def test_package_is_outside_working_storage(db, project, issued, tmp_path):
    """Рабочее хранилище и папка комплектов — разные сущности (ТЗ п.72)."""
    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    folder = Path(package.absolute_path)
    assert storage_service.ARCHIVE_DIR not in folder.parents
    assert storage_service.ARCHIVE_DIR != folder
    assert not str(folder).startswith(str(storage_service.ARCHIVE_DIR))


def test_previous_package_stays_after_new_one_created(db, project, tmp_path):
    first = _issued(db, project, "1")
    created_first = package_service.create_package(db, project.id, base_dir=tmp_path)
    first_folder = Path(created_first.absolute_path)

    issue_service.start_revision(db, first.id)
    form_service.save_draft(db, first.id, dict(REQUIRED, work_volume="200 м²"))

    second = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert first_folder.is_dir()
    assert len(list(first_folder.iterdir())) > 0
    assert second.folder_name != created_first.folder_name


# =====================================================================
# ТЗ п.73: НЕДОСТУПНАЯ ПАПКА КОМПЛЕКТОВ
# =====================================================================


def test_file_instead_of_root_folder_offers_choice_again(db, project, issued, tmp_path):
    (tmp_path / "Комплекты").write_text("не папка", encoding="utf-8")

    with pytest.raises(package_service.PackageError, match="заново"):
        package_service.create_package(db, project.id, base_dir=tmp_path)


def test_unavailable_root_reasons(tmp_path):
    assert package_service.check_root_available(tmp_path / "нет") == "папка комплектов не создана"

    plain = tmp_path / "файл"
    plain.write_text("x", encoding="utf-8")
    assert package_service.check_root_available(plain) == "на месте папки комплектов находится файл"

    root = tmp_path / "Комплекты"
    root.mkdir()
    assert package_service.check_root_available(root) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root игнорирует права доступа")
def test_readonly_root_is_reported(db, project, issued, tmp_path):
    root = package_service.ensure_root(package_service.resolve_root(tmp_path))
    root.chmod(0o500)
    try:
        with pytest.raises(package_service.PackageError, match="заново"):
            package_service.create_package(db, project.id, base_dir=tmp_path)
    finally:
        root.chmod(0o700)


def test_working_data_survives_failed_export(db, project, issued, tmp_path):
    """Неудачная выгрузка не повреждает рабочую базу (ТЗ п.73)."""
    (tmp_path / "Комплекты").write_text("не папка", encoding="utf-8")

    with pytest.raises(package_service.PackageError):
        package_service.create_package(db, project.id, base_dir=tmp_path)

    document = db.get(Document, issued.id)
    assert document.status == domain.DOC_STATUS_ISSUED
    assert form_service.actual_version(db, document.id) is not None


def test_missing_package_folder_is_detected(db, project, issued, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    shutil.rmtree(package.absolute_path)

    assert package_service.package_exists_on_disk(package) is False
    assert [p.id for p in package_service.missing_package_paths(db, project.id)] == [package.id]


# =====================================================================
# ТЗ п.69, 76: ВЫБОР ДОКУМЕНТОВ И РЕЕСТР ВЫГРУЗКИ
# =====================================================================


def test_operator_selects_documents(db, project, issued, tmp_path):
    second = _issued(db, project, "2", domain.DOC_TYPE_AOOK)

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, document_ids=[second.id]
    )
    numbers = [e.document_number for e in package_service.package_entries(db, package.id)]

    assert numbers == ["2"]


def test_nothing_selected_is_reported(db, project, issued, tmp_path):
    with pytest.raises(package_service.PackageError, match="ни одного документа"):
        package_service.create_package(db, project.id, base_dir=tmp_path, document_ids=[])


def test_document_from_another_project_is_reported(db, project, issued, tmp_path):
    with pytest.raises(package_service.PackageError, match="нет в проекте"):
        package_service.create_package(db, project.id, base_dir=tmp_path, document_ids=[99999])


def test_excluded_document_is_not_in_registry(db, project, issued, tmp_path):
    excluded = _issued(db, project, "99", domain.DOC_TYPE_AOOK)

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, document_ids=[issued.id]
    )
    numbers = [e.document_number for e in package_service.package_entries(db, package.id)]

    assert numbers == ["1"]
    assert "99" not in numbers
    assert excluded.id not in {
        e.document_id for e in package_service.package_entries(db, package.id)
    }


def test_project_without_documents_is_reported(db, project, tmp_path):
    with pytest.raises(package_service.PackageError, match="нет документов"):
        package_service.create_package(db, project.id, base_dir=tmp_path)


def test_missing_project_is_reported(db, tmp_path):
    with pytest.raises(package_service.PackageError, match="Проект не найден"):
        package_service.create_package(db, 99999, base_dir=tmp_path)


# =====================================================================
# ТЗ п.77, 78: НОМЕР ДОКУМЕНТА И НОМЕР СТРОКИ РЕЕСТРА
# =====================================================================


def test_document_number_differs_from_row_number(db, project, tmp_path):
    _issued(db, project, "15")
    for number in ("1", "2", "3"):
        _issued(db, project, number, domain.DOC_TYPE_AOOK)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    entries = package_service.package_entries(db, package.id)

    # Документ «АОСР № 15» может стоять в строке 4 — это разные номера.
    aosr = next(e for e in entries if e.document_number == "15")
    assert aosr.register_row_no != 15
    assert aosr.register_row_no == 1


def test_rows_start_from_one_in_every_package(db, project, tmp_path):
    first = _issued(db, project, "5")
    second = _issued(db, project, "3")

    one = package_service.create_package(
        db, project.id, base_dir=tmp_path, document_ids=[first.id],
    )
    two = package_service.create_package(
        db, project.id, base_dir=tmp_path, document_ids=[first.id, second.id],
    )

    rows_one = [
        (e.register_row_no, e.document_number)
        for e in package_service.package_entries(db, one.id)
    ]
    rows_two = [
        (e.register_row_no, e.document_number)
        for e in package_service.package_entries(db, two.id)
    ]

    # Новая выгрузка нумерует строки заново, номер документа не меняется.
    # Порядок строк — по номеру документа, а не по порядку создания.
    assert rows_one == [(1, "5")]
    assert rows_two == [(1, "3"), (2, "5")]


def test_row_numbers_are_unique_and_dense(db, project, issued, tmp_path):
    scheme = _archive(db, project, "Схема 1.pdf")
    _link(db, issued, scheme, domain.LINK_ROLE_SCHEME)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    rows = [e.register_row_no for e in package_service.package_entries(db, package.id)]

    assert rows == list(range(1, len(rows) + 1))


def test_row_order_follows_document_types(db, project, tmp_path):
    """Строки идут в порядке видов документов, а не по порядку создания."""
    _issued(db, project, "2", domain.DOC_TYPE_AOU_SITO)
    aosr = _issued(db, project, "3", domain.DOC_TYPE_AOSR)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    entries = package_service.package_entries(db, package.id)

    assert [e.doc_type for e in entries] == [
        domain.DOC_TYPE_AOSR, domain.DOC_TYPE_AOU_SITO,
    ]
    assert entries[0].document_id == aosr.id


# =====================================================================
# ТЗ п.75: ДВА ВАРИАНТА ВЫГРУЗКИ
# =====================================================================


@pytest.fixture
def package_facts(db, project):
    """Два акта, общая схема, своя схема первого акта, общее приложение."""
    first = _issued(db, project, "1")
    second = _issued(db, project, "2")
    shared = _archive(db, project, "Схема общая.pdf")
    own = _archive(db, project, "Схема 1.pdf")
    attachment = _archive(db, project, "Протокол.pdf", domain.ARCHIVE_CATEGORY_PROTOCOLS)
    for document in (first, second):
        _link(db, document, shared, domain.LINK_ROLE_SCHEME)
        _link(db, document, attachment, domain.LINK_ROLE_ATTACHMENT)
    _link(db, first, own, domain.LINK_ROLE_SCHEME)
    return {
        "acts": (first, second),
        "shared": shared,
        "own": own,
        "attachment": attachment,
    }


def test_variant_one_order_acts_schemes_attachments(db, project, package_facts):
    acts = package_facts["acts"]

    plan = package_service.build_plan(db, project.id, acts, variant=domain.EXPORT_VARIANT_ALL)
    kinds = [entry.doc_type for entry in plan.entries]

    assert kinds[:2] == [domain.DOC_TYPE_AOSR, domain.DOC_TYPE_AOSR]
    assert kinds[2:] == [
        domain.ARCHIVE_CATEGORY_SCHEMES,
        domain.ARCHIVE_CATEGORY_SCHEMES,
        domain.ARCHIVE_CATEGORY_PROTOCOLS,
    ]


def test_variant_two_groups_by_act(db, project, package_facts):
    acts = package_facts["acts"]

    plan = package_service.build_plan(db, project.id, acts, variant=domain.EXPORT_VARIANT_BY_ACT)
    first, second = [
        entry for entry in plan.entries if entry.document is not None
    ][:2]
    rows_first = [e.row_no for e in plan.entries if e.parent is first]
    rows_second = [e.row_no for e in plan.entries if e.parent is second]

    assert (first.document_number, second.document_number) == ("1", "2")
    assert rows_first and rows_second
    assert first.row_no < min(rows_first) < second.row_no < min(rows_second)


def test_unknown_variant_is_reported(db, project, issued, tmp_path):
    with pytest.raises(package_service.PackageError, match="Неизвестный вариант"):
        package_service.create_package(db, project.id, base_dir=tmp_path, variant="третий")


def test_variant_two_keeps_attachment_affiliation(db, project, package_facts, tmp_path):
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, variant=domain.EXPORT_VARIANT_BY_ACT,
    )
    entries = package_service.package_entries(db, package.id)

    attachments = [e for e in entries if e.doc_type == domain.ARCHIVE_CATEGORY_PROTOCOLS]
    assert len(attachments) == 2
    assert {e.parent_entry.document_number for e in attachments} == {"1", "2"}
    assert all(e.is_attachment for e in attachments)


def test_file_is_copied_once_but_affiliation_kept(db, project, package_facts, tmp_path):
    """Файл двух актов копируется один раз, принадлежность видна в реестре."""
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, variant=domain.EXPORT_VARIANT_BY_ACT,
    )
    folder = Path(package.absolute_path)
    entries = package_service.package_entries(db, package.id)
    protocol_rows = [e for e in entries if e.doc_type == domain.ARCHIVE_CATEGORY_PROTOCOLS]

    assert len(list(folder.rglob("Протокол.pdf"))) == 1
    assert len(protocol_rows) == 2
    assert {row.parent_entry.document_number for row in protocol_rows} == {"1", "2"}


def test_variant_two_folders(db, project, package_facts, tmp_path):
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, variant=domain.EXPORT_VARIANT_BY_ACT,
    )
    folder = Path(package.absolute_path)

    assert (folder / "АОСР № 1" / "Схемы" / "Схема общая.pdf").exists()
    assert (folder / "АОСР № 1" / "Схемы" / "Схема 1.pdf").exists()
    assert (folder / "Документы" / "АОСР № 1.pdf").exists()
    assert (folder / "Документы" / "АОСР № 2.pdf").exists()


def test_variant_one_folders(db, project, package_facts, tmp_path):
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, variant=domain.EXPORT_VARIANT_ALL,
    )
    folder = Path(package.absolute_path)

    assert (folder / "Схемы" / "Схема общая.pdf").exists()
    assert (folder / "Схемы" / "Схема 1.pdf").exists()
    assert (folder / "Приложения" / "Протокол.pdf").exists()


# =====================================================================
# ТЗ п.79: ПОРОГ РЕЕСТРА ПРИЛОЖЕНИЙ
# =====================================================================


def _act_with_attachments(db, project, count: int):
    document = _issued(db, project, "1")
    for index in range(1, count + 1):
        archive = _archive(
            db, project, f"Протокол {index}.pdf", domain.ARCHIVE_CATEGORY_PROTOCOLS
        )
        _link(db, document, archive, domain.LINK_ROLE_ATTACHMENT)
    return document


def test_four_attachments_go_right_after_act(db, project, tmp_path):
    _act_with_attachments(db, project, 4)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    types = [e.doc_type for e in package_service.package_entries(db, package.id)]

    assert types.count(domain.ARCHIVE_CATEGORY_PROTOCOLS) == 4
    assert package_service.REGISTER_OF_ATTACHMENTS not in types


def test_five_attachments_create_register(db, project, tmp_path):
    _act_with_attachments(db, project, 5)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    types = [e.doc_type for e in package_service.package_entries(db, package.id)]

    assert types[0] == domain.DOC_TYPE_AOSR
    assert types[1] == package_service.REGISTER_OF_ATTACHMENTS
    assert types.count(domain.ARCHIVE_CATEGORY_PROTOCOLS) == 5


def test_register_row_sits_between_act_and_attachments(db, project, tmp_path):
    _act_with_attachments(db, project, 6)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    entries = package_service.package_entries(db, package.id)
    register = next(e for e in entries if e.doc_type == package_service.REGISTER_OF_ATTACHMENTS)
    attachments = [e for e in entries if e.doc_type == domain.ARCHIVE_CATEGORY_PROTOCOLS]

    assert register.register_row_no == 2
    assert all(register.register_row_no < e.register_row_no for e in attachments)


def test_register_is_created_per_act_in_variant_two(db, project, tmp_path):
    for number in ("1", "2"):
        document = _issued(db, project, number)
        for index in range(1, 6):
            archive = _archive(
                db, project, f"Протокол {number}-{index}.pdf",
                domain.ARCHIVE_CATEGORY_PROTOCOLS,
            )
            _link(db, document, archive, domain.LINK_ROLE_ATTACHMENT)

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, variant=domain.EXPORT_VARIANT_BY_ACT,
    )
    types = [e.doc_type for e in package_service.package_entries(db, package.id)]

    assert types.count(package_service.REGISTER_OF_ATTACHMENTS) == 2


def test_schemes_also_get_register_above_threshold(db, project, tmp_path):
    """Порог реестра действует и на перечень схем (ТЗ п.28)."""
    document = _issued(db, project, "1")
    for index in range(1, 6):
        archive = _archive(db, project, f"Схема {index}.pdf")
        _link(db, document, archive, domain.LINK_ROLE_SCHEME)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    types = [e.doc_type for e in package_service.package_entries(db, package.id)]

    assert types[1] == package_service.REGISTER_OF_ATTACHMENTS


# =====================================================================
# ТЗ п.81: СКВОЗНАЯ НУМЕРАЦИЯ СТРАНИЦ
# =====================================================================


def test_page_numbering_creates_combined_pdf(db, project, tmp_path):
    _issued(db, project, "1")
    _issued(db, project, "2")

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, page_numbering=True
    )
    folder = Path(package.absolute_path)

    assert (folder / "Документы" / package_service.COMBINED_PDF_NAME).exists()
    assert package.page_numbering is True


def test_combined_pdf_has_continuous_numbering(db, project, tmp_path):
    from pypdf import PdfReader

    _issued(db, project, "1")
    _issued(db, project, "2")

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, page_numbering=True
    )
    combined = Path(package.absolute_path) / "Документы" / package_service.COMBINED_PDF_NAME
    reader = PdfReader(str(combined))

    numbers = [
        page.extract_text().strip().splitlines()[-1].strip()
        for page in reader.pages
    ]
    assert numbers == [str(index) for index in range(1, len(reader.pages) + 1)]


def test_single_document_has_no_combined_pdf(db, project, tmp_path):
    _issued(db, project, "1")

    package = package_service.create_package(
        db, project.id, base_dir=tmp_path, page_numbering=True
    )
    folder = Path(package.absolute_path)

    # При одном документе отдельные файлы и есть комплект: дублировать их
    # вторым «общим» файлом незачем.
    assert not (folder / "Документы" / package_service.COMBINED_PDF_NAME).exists()
    assert (folder / "Документы" / "АОСР № 1.pdf").exists()


def test_without_page_numbering_no_combined_pdf(db, project, tmp_path):
    _issued(db, project, "1")
    _issued(db, project, "2")

    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert package.page_numbering is False
    assert not (
        Path(package.absolute_path) / "Документы" / package_service.COMBINED_PDF_NAME
    ).exists()


# =====================================================================
# ТЗ п.83: ФАЙЛ ОШИБОК ТОЛЬКО ДЛЯ ТЕКУЩЕЙ ВЫГРУЗКИ
# =====================================================================


def _draft_document(db, project, number: str = "1"):
    """Документ, который оператор забыл выпустить (ТЗ п.82)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number=number, doc_date=date(2024, 5, 1)
    )
    form_service.save_draft(db, document.id, dict(REQUIRED))
    return document


def test_errors_file_appears_only_in_problem_package(db, project, tmp_path):
    _draft_document(db, project)

    problem = package_service.create_package(db, project.id, base_dir=tmp_path, allow_errors=True)
    problem_folder = Path(problem.absolute_path)
    assert (problem_folder / "Ошибки выгрузки.txt").exists()
    assert problem.has_errors_file is True

    _issued(db, project, "7")
    clean = package_service.create_package(
        db, project.id, base_dir=tmp_path, allow_errors=True,
        document_ids=[d.id for d in package_service.project_documents(db, project.id) if d.number == "7"],
    )

    assert not (Path(clean.absolute_path) / "Ошибки выгрузки.txt").exists()
    assert (problem_folder / "Ошибки выгрузки.txt").exists()


def test_clean_package_has_no_errors_file(db, project, issued, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert package.has_errors_file is False
    assert not (Path(package.absolute_path) / "Ошибки выгрузки.txt").exists()


def test_check_blocks_export_before_folder_is_made(db, project, tmp_path):
    _draft_document(db, project)

    with pytest.raises(package_service.PackageError, match="проверку"):
        package_service.create_package(db, project.id, base_dir=tmp_path)

    root = package_service.resolve_root(tmp_path)
    assert not root.exists() or list(root.iterdir()) == []


def test_errors_file_lists_problems(db, project, tmp_path):
    _draft_document(db, project)

    package = package_service.create_package(db, project.id, base_dir=tmp_path, allow_errors=True)
    text = (Path(package.absolute_path) / "Ошибки выгрузки.txt").read_text(encoding="utf-8")

    assert "Обнаружено замечаний" in text
    assert "не выпущен" in text
    # Не блокирующее замечание тоже попадает в отчёт (ТЗ п.82).
    assert "внимание" in text


# =====================================================================
# ТЗ п.91: ЗАФИКСИРОВАННАЯ ВЕРСИЯ ДОКУМЕНТА И ФАЙЛА
# =====================================================================


def test_registry_entry_fixes_document_version(db, project, issued, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    entry = package_service.package_entries(db, package.id)[0]

    assert entry.document_version_id is not None
    assert entry.document_version.issued_at is not None

    issue_service.start_revision(db, issued.id)
    new_version = form_service.save_draft(
        db, issued.id, dict(REQUIRED, work_volume="200 м² бетона Б25")
    )

    assert entry.document_version_id != new_version.id
    assert entry.document_version.payload["work_volume"] == REQUIRED["work_volume"]


def test_package_uses_issued_content_not_draft(db, project, issued, tmp_path):
    """В комплект идёт выпущенная версия, а не текущий черновик (ТЗ п.91)."""
    issue_service.start_revision(db, issued.id)
    form_service.save_draft(db, issued.id, dict(REQUIRED, work_volume="999 м² бетона Б25"))

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    entry = package_service.package_entries(db, package.id)[0]

    assert entry.document_version.payload["work_volume"] == REQUIRED["work_volume"]
    assert entry.document_version_id is not None


def test_archive_file_version_is_pinned(db, project, issued, tmp_path):
    """В выгрузку идёт зафиксированная версия файла, а не новая (ТЗ п.91)."""
    archive = _archive(db, project, "Схема 1.pdf")
    _link(db, issued, archive, domain.LINK_ROLE_SCHEME)

    new_source = Path(storage_service.ARCHIVE_DIR) / "Схема 1 новая.pdf"
    new_source.write_bytes("новая редакция схемы".encode("utf-8"))
    storage_service.add_version(db, archive.id, new_source)

    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    copied = next(Path(package.absolute_path).rglob("Схема 1.pdf"))

    assert copied.read_text(encoding="utf-8") == "файл архива: Схема 1.pdf"


# =====================================================================
# ТЗ п.86: ИСТОРИЯ ВЫГРУЗКИ
# =====================================================================


def test_history_records_package(db, project, issued, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)

    events = project_service.list_events(db, project.id)
    exported = [e for e in events if e.event_type == domain.HISTORY_PACKAGE_EXPORTED]

    assert len(exported) == 1
    assert package.folder_name in exported[0].message
    assert exported[0].entity_type == "package"
    assert exported[0].entity_id == package.id
    assert exported[0].payload["variant"] == domain.EXPORT_VARIANT_ALL


def test_history_records_variant_and_numbering(db, project, issued, tmp_path):
    package = package_service.create_package(
        db, project.id, base_dir=tmp_path,
        variant=domain.EXPORT_VARIANT_BY_ACT, page_numbering=True,
    )

    event = next(
        e for e in project_service.list_events(db, project.id)
        if e.event_type == domain.HISTORY_PACKAGE_EXPORTED
    )

    assert event.payload["variant"] == domain.EXPORT_VARIANT_BY_ACT
    assert event.payload["page_numbering"] is True
    assert event.payload["folder_name"] == package.folder_name


def test_failed_export_writes_no_history(db, project, tmp_path):
    _draft_document(db, project)

    with pytest.raises(package_service.PackageError):
        package_service.create_package(db, project.id, base_dir=tmp_path)

    kinds = {e.event_type for e in project_service.list_events(db, project.id)}
    assert domain.HISTORY_PACKAGE_EXPORTED not in kinds


def test_list_packages_newest_first(db, project, issued, tmp_path):
    first = package_service.create_package(db, project.id, base_dir=tmp_path)
    second = package_service.create_package(db, project.id, base_dir=tmp_path)

    assert [p.id for p in package_service.list_packages(db, project.id)] == [second.id, first.id]
    assert package_service.package_count(db, project.id) == 2


def test_existing_folder_is_never_written_into(db, project, issued, tmp_path, monkeypatch):
    """Столкновение имён папок останавливает выгрузку (ТЗ п.71).

    В папке, занятой прежней выгрузкой, лежит чужое содержимое: если бы
    новая выгрузка туда попала, прежняя была бы изменена.
    """
    root = package_service.ensure_root(package_service.resolve_root(tmp_path))
    busy = root / "Комплект 01"
    busy.mkdir()
    (busy / package_service.REGISTRY_FILE_NAME).write_text("прежняя выгрузка", encoding="utf-8")
    (busy / "Документы").mkdir()
    before = sorted(
        (str(item.relative_to(busy)), item.stat().st_size if item.is_file() else None)
        for item in busy.rglob("*")
    )

    monkeypatch.setattr(
        package_service, "next_folder_name", lambda root_dir, prefix=None: busy.name
    )

    with pytest.raises(package_service.PackageError, match="не перезаписывается"):
        package_service.create_package(db, project.id, base_dir=tmp_path)

    after = sorted(
        (str(item.relative_to(busy)), item.stat().st_size if item.is_file() else None)
        for item in busy.rglob("*")
    )
    assert after == before
    assert (busy / package_service.REGISTRY_FILE_NAME).read_text(encoding="utf-8") == (
        "прежняя выгрузка"
    )


def test_package_folder_is_created_without_overwrite(db, project, issued, tmp_path, monkeypatch):
    """Гонка двух выгрузок не приводит к смешиванию комплектов (ТЗ п.71)."""
    first = package_service.create_package(db, project.id, base_dir=tmp_path)
    folder = Path(first.absolute_path)
    marker = folder / package_service.REGISTRY_FILE_NAME
    before = marker.read_bytes()

    real_mkdir = Path.mkdir

    def racing_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if self == folder and not exist_ok:
            raise FileExistsError(str(self))
        return real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)
    monkeypatch.setattr(
        package_service, "next_folder_name", lambda root, prefix=None: folder.name
    )
    monkeypatch.setattr(package_service.shutil, "rmtree", lambda path, **kwargs: None)

    with pytest.raises(package_service.PackageError, match="не перезаписывается"):
        package_service.create_package(db, project.id, base_dir=tmp_path)

    assert marker.read_bytes() == before
