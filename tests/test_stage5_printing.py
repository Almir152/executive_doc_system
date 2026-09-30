"""Печатная форма документа. ТЗ п.55–62, 42, 43, 63, 64, 81.

Проверяется не «наличие файла», а содержание печати: номер и дата
документа (ТЗ п.42, 43), разделы нормативной формы целиком и в своём
порядке (ТЗ п.62), фиксированные размеры полей и шрифтов (ТЗ п.59, 60),
нумерация страниц внизу справа без слова «стр.» (ТЗ п.81).
"""

from datetime import date

import pytest
from pypdf import PdfReader

from app.core import domain
from app.db.models import NormativeForm
from app.core.services import (
    document_service, form_service, link_service, printing, storage_service,
)


REQUIRED = {
    "object_name": "ЖК Северный, корпус 2",
    "address": "г. Москва, ул. Северная, 1",
    "work_description": "Армирование стен и перекрытий",
    "section_refs": "КЖ",
    "work_period": "с 01.04.2024 по 30.04.2024",
        # ТЗ п.43, 87: срок работ двумя датами для проверки зависимостей.
        "period_start": "01.04.2024",
        "period_end": "30.04.2024",
    "work_volume": "120 м² бетона Б25",
    "has_defects": "Нет",
    "conclusion": "Работы выполнены в полном объёме",
    "work_performer": "ООО «Строй»",
}


@pytest.fixture
def source_pdf(tmp_path):
    """Файл схемы для загрузки в архив (ТЗ п.47)."""
    path = tmp_path / "Схема 12.pdf"
    path.write_bytes(b"fake-pdf-content-for-archive")
    return path


@pytest.fixture
def aosr(db, project):
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 1)
    )
    form_service.save_draft(db, document.id, dict(REQUIRED))
    return document


def _text(pdf_path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(str(pdf_path)).pages)


@pytest.fixture
def printed(db, aosr, tmp_path):
    path = tmp_path / "АОСР.pdf"
    printing.render_document_pdf(db, aosr, path)
    return path


# =====================================================================
# ФАЙЛ И ФОРМАТ (ТЗ п.55)
# =====================================================================


def test_pdf_is_created(printed):
    assert printed.exists()
    assert printed.stat().st_size > 1000


def test_page_format_is_a4_portrait(printed):
    page = PdfReader(str(printed)).pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    a4 = printing.A4
    assert width == pytest.approx(a4[0], abs=2)
    assert height == pytest.approx(a4[1], abs=2)
    assert height > width, "основная ориентация — книжная (ТЗ п.55)"


# =====================================================================
# РЕКВИЗИТЫ И ДАННЫЕ (ТЗ п.42, 43)
# =====================================================================


def test_pdf_contains_document_number(printed):
    assert "№ 1" in _text(printed)


def test_pdf_contains_document_date(printed):
    assert "01.05.2024" in _text(printed)


def test_missing_date_is_printed_as_not_set(db, project, tmp_path):
    """Дата не подставляется системой: в печати так и пишется (ТЗ п.43)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    form_service.save_draft(db, document.id, dict(REQUIRED))

    path = tmp_path / "no_date.pdf"
    printing.render_document_pdf(db, document, path)

    text = _text(path)
    assert "дата не указана" in text
    assert str(date.today().strftime("%d.%m.%Y")) not in text


def test_pdf_contains_filled_values(printed):
    text = _text(printed)
    assert "ЖК Северный, корпус 2" in text
    assert "Армирование стен и перекрытий" in text


def test_pdf_contains_project_card_values(db, aosr, tmp_path):
    """Незаполненное в форме поле берётся из карточки проекта (ТЗ п.17)."""
    from app.db.models import Organization

    organization = Organization(short_name="ООО «Заказчик»")
    db.add(organization)
    db.commit()
    aosr.project.customer_org_id = organization.id
    db.commit()

    path = tmp_path / "card.pdf"
    printing.render_document_pdf(db, aosr, path)

    assert "ООО «Заказчик»" in _text(path)


def test_form_value_wins_over_project_card(db, aosr, tmp_path):
    """Введённое оператором значение печатается, а не значение карточки."""
    aosr.project.address = "адрес из карточки"
    db.commit()

    path = tmp_path / "form_wins.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert "г. Москва, ул. Северная, 1" in text
    assert "адрес из карточки" not in text


def test_empty_value_leaves_place_for_manual_fill(db, project, tmp_path):
    """Пустое поле печатается местом для заполнения, а не исчезает (ТЗ п.64)."""
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 1)
    )
    payload = dict(REQUIRED)
    payload["has_defects"] = ""
    form_service.save_draft(db, document.id, payload)

    path = tmp_path / "empty_field.pdf"
    printing.render_document_pdf(db, document, path)

    text = _text(path)
    assert "Выявлены несоответствия" in text
    assert printing.EMPTY_PLACEHOLDER in text


# =====================================================================
# НОРМАТИВНАЯ СТРУКТУРА (ТЗ п.62)
# =====================================================================


def test_all_sections_are_printed_in_order(printed):
    """Разделы формы выводятся целиком и в порядке описания (ТЗ п.62)."""
    from app.db.form_definitions import FORM_DEFINITIONS

    text = _text(printed)
    aosr_def = next(d for d in FORM_DEFINITIONS if d["doc_type"] == domain.DOC_TYPE_AOSR)
    positions = []
    for section in aosr_def["sections"]:
        heading = f"{section['number']}. {section['title']}"
        assert heading in text, f"раздел {heading} не напечатан"
        positions.append(text.index(heading))
    assert positions == sorted(positions), "порядок разделов нарушен (ТЗ п.62)"
    assert len(positions) == len(aosr_def["sections"])


def test_fixed_text_is_printed_verbatim(db, aosr, tmp_path):
    """Фиксированная формулировка печатается как есть (ТЗ п.31, 62)."""
    from app.db.form_definitions import FORM_DEFINITIONS

    path = tmp_path / "aook.pdf"
    aook = document_service.create_document(
        db, aosr.project_id, doc_type=domain.DOC_TYPE_AOOK, doc_date=date(2024, 6, 1)
    )
    form_service.save_draft(db, aook.id, {
        "object_name": "ЖК Северный", "basis": "Основание",
        "decisions": "а) принять и разрешить последующие работы",
    })
    printing.render_document_pdf(db, aook, path)

    text = _text(path)
    assert "Предъявленные строительные конструкции соответствуют техническим" in text
    aook_def = next(d for d in FORM_DEFINITIONS if d["doc_type"] == domain.DOC_TYPE_AOOK)
    assert len(aook_def["sections"]) == 8


def test_section_number_two_is_absent_in_aook(db, aosr, tmp_path):
    """В АООК нет раздела 2 в структуре формы — печать его не выдумывает."""
    aook = document_service.create_document(
        db, aosr.project_id, doc_type=domain.DOC_TYPE_AOOK, doc_date=date(2024, 6, 1)
    )
    form_service.save_draft(db, aook.id, {"object_name": "ЖК Северный"})

    path = tmp_path / "aook2.pdf"
    printing.render_document_pdf(db, aook, path)

    text = _text(path)
    assert "1. Сведения об объекте" in text
    assert "2. Основнование" not in text
    assert "3. Перечень принятых" in text


def test_decisions_are_marked(db, aosr, tmp_path):
    aook = document_service.create_document(
        db, aosr.project_id, doc_type=domain.DOC_TYPE_AOOK, doc_date=date(2024, 6, 1)
    )
    form_service.save_draft(db, aook.id, {
        "object_name": "ЖК Северный",
        "decisions": "б) принять с замечаниями",
    })
    path = tmp_path / "decisions.pdf"
    printing.render_document_pdf(db, aook, path)

    text = _text(path)
    # Маркеры только из ASCII: символы «☒/☐» в шрифте печати отсутствуют
    # и на листе дали бы пустые квадраты.
    assert printing.MARK_CHECKED in text
    assert printing.MARK_UNCHECKED in text


# =====================================================================
# ВЁРСТКА (ТЗ п.59, 60, 61)
# =====================================================================


def test_margins_match_requirement():
    assert printing.MARGIN_TOP_MM == 20
    assert printing.MARGIN_LEFT_MM == 10
    assert printing.MARGIN_RIGHT_MM == 10
    assert printing.MARGIN_BOTTOM_MM == 10


def test_font_sizes_within_allowed_range():
    assert printing.BASE_FONT_SIZE == 11
    assert printing.FILL_FONT_SIZE == 11
    assert printing.HEADING_MIN_PT <= 13 <= printing.HEADING_MAX_PT
    extra = 13 - printing.BASE_FONT_SIZE
    assert printing.HEADING_EXTRA_MIN_PT <= extra <= printing.HEADING_EXTRA_MAX_PT
    assert printing.SECONDARY_MIN_PT <= 9 <= printing.SECONDARY_MAX_PT


def test_font_supports_cyrillic():
    """Шрифт печати обязан покрывать кириллицу, иначе PDF из квадратов."""
    source = printing.font_source()
    assert source, "шрифт печати не найден"
    from reportlab.pdfbase import pdfmetrics

    assert pdfmetrics.getFont(printing.FONT_FAMILY).stringWidth(
        "Представитель эксплуатирующей организации", 11
    ) > 0


def test_base_and_fill_styles_differ(printed):
    """Шаблонная фраза — regular, заполняемый текст — italic (ТЗ п.59)."""
    styles = printing._styles()
    assert styles["label"].fontName == printing.FONT_FAMILY
    assert styles["fill"].fontName == printing.FONT_FAMILY + "-Italic"
    assert styles["fill"].fontSize == printing.FILL_FONT_SIZE


def test_heading_is_bold_italic_and_larger(printed):
    styles = printing._styles()
    assert styles["heading"].fontName == printing.FONT_FAMILY + "-BoldItalic"
    assert styles["heading"].fontSize > printing.BASE_FONT_SIZE


def test_aosr_is_two_pages(db, aosr, tmp_path):
    """Целевой объём АОСР — 2 страницы A4 (ТЗ п.56)."""
    path = tmp_path / "aosr2.pdf"
    printing.render_document_pdf(db, aosr, path)

    assert printing.page_count(path) == 2


def test_other_act_is_one_page(db, project, tmp_path):
    """Прочие акты — целевой 1 страница (ТЗ п.58)."""
    act = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, doc_date=date(2024, 5, 1)
    )
    form_service.save_draft(db, act.id, {
        "object_name": "ЖК Северный", "address": "г. Москва",
        "system_type": "внутренние инженерные сети", "test_kind": "испытания",
        "system_name": "Сеть водоснабжения", "doc_date": "01.05.2024",
        "conditions": "Нормальная температура", "instrumentation": "Манометр",
        "results": "Испытания пройдены", "decisions": "а) система принята, соответствует требованиям",
        "participants": "Иванов И. И.\nПетров П. П.",
    }, exploitation_choice=form_service.MISSING_KEEP_PLACE)
    path = tmp_path / "act.pdf"
    printing.render_document_pdf(db, act, path)

    assert printing.page_count(path) == 1


def test_target_pages_declared_in_forms():
    """Цели объёма заданы описанием формы, а не кодом вёрстки (ТЗ п.56–58)."""
    from app.db.form_definitions import FORM_DEFINITIONS

    targets = {d["doc_type"]: d["layout"]["target_pages"] for d in FORM_DEFINITIONS}
    assert targets[domain.DOC_TYPE_AOSR] == "2"
    assert targets[domain.DOC_TYPE_AOOK] == "2-4"
    assert targets[domain.DOC_TYPE_AOU_SITO] == "2-4"
    assert targets[domain.DOC_TYPE_TEST_ACT] == "1"


# =====================================================================
# ПОДПИСАНТЫ (ТЗ П.63, 64)
# =====================================================================


def test_signature_blocks_are_printed(db, aosr, tmp_path):
    form_service.save_signature_block(
        db, aosr.id, block="Сдал", position="Прораб",
        full_name="Иванов И. И.", sign_place="г. Москва",
    )
    form_service.save_signature_block(
        db, aosr.id, block="Принял", position="Представитель заказчика",
        full_name="Петров П. П.", sign_place="г. Казань",
    )
    path = tmp_path / "sign.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert "Сдал" in text and "Принял" in text
    assert "Иванов И. И." in text
    assert "Петров П. П." in text
    assert "Место подписи" in text


def test_signature_blocks_are_independent(db, aosr, tmp_path):
    """Заполненный «Сдал» не требует заполненного «Принял» (ТЗ п.63)."""
    form_service.save_signature_block(
        db, aosr.id, block="Сдал", position="Прораб",
        full_name="Иванов И. И.", sign_place="г. Москва",
    )
    path = tmp_path / "sign2.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert "Сдал" in text
    assert "Петров" not in text


# =====================================================================
# НУМЕРАЦИЯ СТРАНИЦ (ТЗ п.81)
# =====================================================================


def test_page_numbers_are_bottom_right_without_word(db, project, tmp_path):
    """Номер страницы внизу справа, слово «стр.» не добавляется (ТЗ п.81)."""
    docs = []
    for number in (1, 2):
        act = document_service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 1)
        )
        docs.append(act)
    path = tmp_path / "numbered.pdf"
    printing.render_documents_pdf(db, docs, path, page_numbers=True)

    reader = PdfReader(str(path))
    texts = [page.extract_text() for page in reader.pages]
    joined = "\n".join(texts)
    assert "стр." not in joined
    # Номер сквозной: на каждой странице свой, по порядку (ТЗ п.81).
    for index, text in enumerate(texts, start=1):
        assert any(line.strip() == str(index) for line in text.splitlines()), (
            f"на странице {index} нет её номера"
        )


def test_page_numbers_are_off_by_default(printed):
    texts = [page.extract_text() for page in PdfReader(str(printed)).pages]
    assert not any(line.strip() == "1" for line in texts[0].splitlines())


def test_single_document_without_numbers_is_allowed(db, aosr, tmp_path):
    """Нумерация включается по желанию оператора (ТЗ п.81)."""
    path = tmp_path / "single.pdf"
    printing.render_documents_pdf(db, [aosr], path, page_numbers=False)

    assert printing.page_count(path) >= 1


# =====================================================================
# АРХИВНЫЕ СВЯЗИ (ТЗ П.30, 47)
# =====================================================================


def test_linked_documents_are_listed(db, aosr, tmp_path, source_pdf):
    archive_doc = storage_service.add_file_to_archive(
        db, source_pdf, aosr.project_id, domain.ARCHIVE_CATEGORY_MATERIALS
    )
    archive_doc.number = "25"
    archive_doc.doc_date = date(2023, 4, 1)
    db.commit()
    link_service.link_document_to_archive(
        db, document_id=aosr.id,
        archive_document_id=archive_doc.id,
        link_role=domain.LINK_ROLE_QUALITY,
    )

    path = tmp_path / "links.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert source_pdf.name in text
    assert "25" in text


def test_linked_document_files_are_not_copied(db, aosr, tmp_path, source_pdf):
    """Документы не копируются физически, они печатаются как связи (ТЗ п.30, 92)."""
    archive_doc = storage_service.add_file_to_archive(
        db, source_pdf, aosr.project_id, domain.ARCHIVE_CATEGORY_MATERIALS
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id,
        archive_document_id=archive_doc.id,
        link_role=domain.LINK_ROLE_QUALITY,
    )
    out_dir = tmp_path / "out"
    path = out_dir / "links.pdf"
    printing.render_document_pdf(db, aosr, path)

    assert list(out_dir.iterdir()) == [path]


# =====================================================================
# СООТВЕТСТВИЕ РОЛЕЙ СВЯЗЕЙ ОПИСАНИЮ ФОРМЫ (ТЗ п.30, 47)
# =====================================================================


def test_form_link_roles_resolve_to_domain_roles():
    """Идентификатор роли из описания формы переводится в роль связи.

    Без перевода перечень в печати всегда был бы пустым: в описании формы
    роль названа «quality», а связь хранится с ролью «Документ качества».
    """
    assert domain.link_roles_for_form("quality") == (domain.LINK_ROLE_QUALITY,)
    assert domain.link_roles_for_form("geodetic_scheme") == (domain.LINK_ROLE_SCHEME,)
    assert domain.LINK_ROLE_SURVEY in domain.link_roles_for_form("quality_evidence")
    assert domain.LINK_ROLE_ATTACHMENT not in domain.link_roles_for_form("accepted_work")


def test_unknown_form_link_role_is_not_silent():
    """Неизвестная роль в описании — ошибка, а не пустой перечень."""
    with pytest.raises(domain.UnknownLinkRole):
        domain.link_roles_for_form("nonexistent_role")


def test_every_form_link_role_is_known():
    """Все роли из описаний форм сопоставлены (ТЗ п.96: форма отделена от кода)."""
    from app.db.form_definitions import FORM_DEFINITIONS

    for definition in FORM_DEFINITIONS:
        for section in definition["sections"]:
            for block in section.get("blocks", []):
                identifier = block.get("link_role")
                if identifier:
                    domain.link_roles_for_form(identifier)


def test_unknown_role_in_stored_form_stops_print(db, aosr, tmp_path):
    """Неизвестная роль в сохранённом описании формы останавливает печать."""
    form = db.query(NormativeForm).filter(
        NormativeForm.doc_type == domain.DOC_TYPE_AOSR,
        NormativeForm.is_current.is_(True),
    ).one()
    definition = dict(form.definition)
    definition["sections"] = [dict(form.definition["sections"][0])]
    definition["sections"][0]["blocks"] = [
        {"kind": "document_list", "key": "quality_documents",
         "label": "Документы", "link_role": "nonexistent_role"}
    ]
    form.definition = definition
    db.commit()

    with pytest.raises(domain.UnknownLinkRole):
        printing.render_document_pdf(db, aosr, tmp_path / "x.pdf")


def test_decision_markers_exist_in_print_font():
    """Маркеры решения печатаются шрифтом, а не превращаются в пустые знаки."""
    from reportlab.pdfbase.ttfonts import TTFont

    font = TTFont("probe", printing.font_source())
    cmap = font.face.charToGlyph
    for marker in (printing.MARK_CHECKED, printing.MARK_UNCHECKED,
                   printing.EMPTY_PLACEHOLDER):
        for char in marker:
            assert cmap.get(ord(char)) is not None, f"нет глифа для {char!r} ({marker!r})"


def test_typed_text_block_is_printed_instead_of_empty_lines(db, aosr, tmp_path):
    """Введённый текст печатается; пустые строки — только для незаполненного."""
    payload = dict(REQUIRED)
    payload["conclusion"] = "Работы приняты полностью"
    form_service.save_draft(db, aosr.id, payload)

    path = tmp_path / "text_block.pdf"
    printing.render_document_pdf(db, aosr, path)

    assert "Работы приняты полностью" in _text(path)


def test_all_document_types_are_printable(db, project, tmp_path):
    """Каждый вид документа печатается: новый тип документа не должен ломать печать."""
    from datetime import date as _date

    payloads = {
        domain.DOC_TYPE_AOSR: dict(REQUIRED),
        domain.DOC_TYPE_AOOK: {
            "object_name": "ЖК Северный",
            "decisions": "а) принять и разрешить последующие работы",
        },
        domain.DOC_TYPE_AOU_SITO: {
            "object_name": "ЖК Северный",
            "violations": "Нарушения устранены",
            "works": "Прокладка сети",
        },
        domain.DOC_TYPE_TEST_ACT: {
            "object_name": "ЖК Северный", "address": "г. Москва",
            "system_type": "внутренние инженерные сети", "test_kind": "испытания",
            "system_name": "Сеть водоснабжения", "doc_date": "01.05.2024",
            "conditions": "Нормальная температура", "instrumentation": "Манометр",
            "results": "Испытания пройдены",
            "decisions": "а) система принята, соответствует требованиям",
            "participants": "Иванов И. И.",
        },
    }
    for doc_type, payload in payloads.items():
        document = document_service.create_document(
            db, project.id, doc_type=doc_type, doc_date=_date(2024, 5, 1)
        )
        form_service.save_draft(
            db, document.id, payload,
            exploitation_choice=form_service.MISSING_KEEP_PLACE,
        )
        path = tmp_path / f"{doc_type}.pdf"
        printing.render_document_pdf(db, document, path)

        assert printing.page_count(path) >= 1
        assert document.type_label.split()[0] in _text(path)


def test_printing_issued_document_uses_fixed_version(db, aosr, tmp_path):
    """Печать выпущенного документа берёт зафиксированную версию (ТЗ п.85, 91)."""
    from app.core.services import issue_service

    issue_service.issue_document(db, aosr.id, doc_date=date(2024, 5, 1))

    path = tmp_path / "issued.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert "Работы выполнены в полном объёме" in text
    assert "(выпущен)" in text


def test_printing_shows_working_revision_as_not_issued(db, aosr, tmp_path):
    """Рабочая редакция печатается и помечается как невыпущенная (ТЗ п.93)."""
    from app.core.services import issue_service

    issue_service.issue_document(db, aosr.id, doc_date=date(2024, 5, 1))
    issue_service.start_revision(db, aosr.id)
    form_service.save_draft(db, aosr.id, dict(REQUIRED, conclusion="Черновик, не выпущен"))

    path = tmp_path / "revision.pdf"
    printing.render_document_pdf(db, aosr, path)

    text = _text(path)
    assert "Черновик, не выпущен" in text
    assert "рабочая редакция, не выпущена" in text


def test_each_document_starts_on_new_page(db, project, tmp_path):
    """В комплекте каждый документ начинается с новой страницы (ТЗ п.81)."""
    from app.core.services import issue_service

    documents = []
    for _ in range(2):
        document = document_service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 1)
        )
        form_service.save_draft(db, document.id, dict(REQUIRED))
        issue_service.issue_document(db, document.id, doc_date=date(2024, 5, 1))
        documents.append(document)

    single_pages = 0
    for document in documents:
        single = tmp_path / f"one_{document.id}.pdf"
        printing.render_document_pdf(db, document, single)
        single_pages += printing.page_count(single)

    combined = tmp_path / "combined.pdf"
    printing.render_documents_pdf(db, documents, combined)

    assert printing.page_count(combined) == single_pages

    # Заголовок каждого документа открывает свою страницу, а не следует
    # в конце предыдущей.
    titles = 0
    for page in PdfReader(str(combined)).pages:
        lines = [line.strip() for line in page.extract_text().splitlines() if line.strip()]
        if lines and lines[0].startswith("Акт освидетельствования"):
            titles += 1
    assert titles == 2
