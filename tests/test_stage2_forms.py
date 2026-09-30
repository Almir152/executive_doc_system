"""Нормативные формы как данные (ТЗ п.24, 96; Этап 2).

Проверяется не только то, что формы описаны, но и что описание отражает
требования ТЗ к структуре конкретных документов: п.25-28 для АОСР,
п.30-32 для АООК, п.35-41 для актов испытаний. Ошибочная структура не
должна попадать в базу (ТЗ п.24: статическая структура формы не должна
произвольно изменяться).
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import text

from app.core import domain
from app.core.forms import (
    BLOCK_DECISIONS,
    BLOCK_DOCUMENT_LIST,
    BLOCK_FIXED_TEXT,
    BLOCK_PARTICIPANTS,
    FormDefinitionError,
    NormativeFormDefinition,
    validate_against_spec,
)
from app.db.form_definitions import FORM_DEFINITIONS
from app.db.forms_service import (
    current_form,
    form_by_version,
    load_form_definitions,
    parse_form,
)

RAW = {d["doc_type"]: d for d in FORM_DEFINITIONS}


def _definition(doc_type: str) -> NormativeFormDefinition:
    return NormativeFormDefinition.from_dict(RAW[doc_type])


def _section(raw: dict, number: int) -> dict:
    """Раздел по номеру, а не по индексу: порядок разделов не фиксирован."""
    for s in raw["sections"]:
        if s["number"] == number:
            return s
    raise AssertionError(f"в описании нет раздела {number}")


def _block(raw: dict, section_number: int, key: str) -> dict:
    for b in _section(raw, section_number)["blocks"]:
        if b.get("key") == key:
            return b
    raise AssertionError(f"в разделе {section_number} нет блока {key}")


# =====================================================================
# Состав справочника форм
# =====================================================================


def test_forms_exist_for_every_numbered_document_type():
    """Каждому нумеруемому типу документа соответствует форма (ТЗ п.42)."""
    assert set(RAW) == set(domain.NUMBERED_DOC_TYPES), (
        f"формы есть не для всех типов документов: "
        f"нет {set(domain.NUMBERED_DOC_TYPES) - set(RAW)}"
    )


@pytest.mark.parametrize("doc_type", sorted(RAW))
def test_form_definition_is_valid(doc_type):
    """Описание формы разбирается и соответствует требованиям ТЗ."""
    definition = _definition(doc_type)
    problems = validate_against_spec(definition)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("doc_type", sorted(RAW))
def test_form_has_basis_and_layout(doc_type):
    """У формы указано нормативное основание и параметры печати."""
    definition = _definition(doc_type)
    assert definition.basis, f"у формы {doc_type} не указано основание"
    assert definition.layout.paper == "A4"
    assert definition.layout.orientation == "portrait"


def test_layout_follows_printing_requirements():
    """Параметры вёрстки соответствуют ТЗ п.59-61."""
    for doc_type in RAW:
        layout = _definition(doc_type).layout
        assert layout.font_family == "Times New Roman", doc_type
        assert layout.font_size == 11, doc_type
        assert layout.editable_font_style == "italic", doc_type
        assert 10 <= layout.heading_font_size <= 14, doc_type
        assert (layout.margin_top, layout.margin_left,
                layout.margin_right, layout.margin_bottom) == (20, 10, 10, 10), doc_type


def test_target_pages_match_requirements():
    """Целевой объём в страницах задан по ТЗ п.56-58."""
    assert _definition(domain.DOC_TYPE_AOSR).layout.target_pages == "2"
    assert _definition(domain.DOC_TYPE_AOOK).layout.target_pages == "2-4"
    assert _definition(domain.DOC_TYPE_AOU_SITO).layout.target_pages == "2-4"
    assert _definition(domain.DOC_TYPE_TEST_ACT).layout.target_pages == "1"


# =====================================================================
# АОСР — ТЗ п.25-28
# =====================================================================


def test_aosr_requires_work_performer_entered_by_hand():
    """П.25: лицо, выполнявшее работы, обязательно и не подставляется системой."""
    performer = _definition(domain.DOC_TYPE_AOSR).field("work_performer")
    assert performer.required
    assert performer.source == "manual"
    assert "выполнявшее" in performer.label


def test_aosr_nrs_is_optional_but_present():
    """П.26: НРС включается по умолчанию, но может остаться пустым."""
    nrs = _definition(domain.DOC_TYPE_AOSR).field("nrs")
    assert not nrs.required


def test_aosr_point7_is_filled_manually():
    """П.27: пункт 7 заполняется оператором и не подменяется системой."""
    point7 = _definition(domain.DOC_TYPE_AOSR).blocks_of(7)
    assert any(b.source == "manual" and not b.fixed for b in point7)


def test_aosr_point3_lists_documents_with_threshold_five():
    """П.28: перечни пункта 3 переключаются на реестр после пяти документов."""
    lists = [b for b in _definition(domain.DOC_TYPE_AOSR).blocks_of(3)
             if b.kind == BLOCK_DOCUMENT_LIST]
    assert lists
    assert all(b.threshold == 5 for b in lists)


def test_aosr_exploitation_representative_is_not_added_automatically():
    """П.40: в обычный АОСР представитель эксплуатирующей не добавляется."""
    block = _definition(domain.DOC_TYPE_AOSR).field("exploitation_rep")
    assert block.source == "manual"
    assert not block.required


def test_aosr_violations_are_detected():
    """Проверка ловит вымышленные формы, нарушающие п.25-28."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])

    # п.25: исполнитель работ не обязателен
    _block(broken, 10, "work_performer")["required"] = False
    # п.26: НРС сделан обязательным
    _block(broken, 8, "nrs")["required"] = True
    # п.27: пункт 7 подставляется системой
    _block(broken, 7, "item7")["source"] = "project"
    # п.28: перечня в пункте 3 нет вовсе
    _section(broken, 3)["blocks"] = []

    problems = validate_against_spec(NormativeFormDefinition.from_dict(broken))
    joined = "\n".join(problems)
    assert "обязательно (ТЗ п.25)" in joined
    assert "НРС не должен быть обязательным" in joined
    assert "пункт 7" in joined
    assert "не содержит перечня документов" in joined


def test_aosr_list_without_threshold_is_detected():
    """П.28: у перечня без порога не задан переход на реестр после 5 документов."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    for b in _section(broken, 3)["blocks"]:
        b.pop("threshold", None)

    problems = validate_against_spec(NormativeFormDefinition.from_dict(broken))
    assert any("порог 5" in p for p in problems), problems


def test_aosr_threshold_repeated_is_detected():
    """П.28: произвольный порог не соответствует согласованному правилу."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    for b in _section(broken, 3)["blocks"]:
        b["threshold"] = 10

    problems = validate_against_spec(NormativeFormDefinition.from_dict(broken))
    assert any("порог 5" in p for p in problems), problems


# =====================================================================
# АООК — ТЗ п.30-32
# =====================================================================


def test_aook_section5_has_two_archive_lists():
    """П.30: в разделе 5 схемы и результаты испытаний — два отдельных пункта."""
    lists = [b for b in _definition(domain.DOC_TYPE_AOOK).blocks_of(5)
             if b.kind == BLOCK_DOCUMENT_LIST]
    assert {b.link_role for b in lists} == {"geodetic_scheme", "quality_evidence"}


def test_aook_archive_documents_are_links_not_copies():
    """П.30: документы берутся из архива и физически не копируются."""
    for b in _definition(domain.DOC_TYPE_AOOK).blocks_of(5):
        if b.kind == BLOCK_DOCUMENT_LIST:
            assert b.source == "linked"
            assert b.link_role, f"у пункта {b.key} не задана роль архивной связи"


def test_aook_section8_is_fixed_and_not_editable():
    """П.31: формулировка раздела 8 фиксирована и не редактируется."""
    blocks = _definition(domain.DOC_TYPE_AOOK).blocks_of(8)
    assert blocks, "раздел 8 пуст"
    for b in blocks:
        assert b.kind == BLOCK_FIXED_TEXT, f"раздел 8 содержит редактируемый блок {b.key}"
        assert b.fixed
        assert b.text


def test_aook_has_no_separate_conclusion_section():
    """П.31: отдельного раздела «Заключение» в АООК нет."""
    definition = _definition(domain.DOC_TYPE_AOOK)
    assert not definition.has_section(11)
    assert all("заключение" not in s.title.lower() for s in definition.sections)


def test_aook_point9_is_structured_decisions():
    """П.32: пункт 9 — структура решений а-г, а не произвольный текст."""
    decisions = [b for b in _definition(domain.DOC_TYPE_AOOK).blocks_of(9)
                  if b.kind == BLOCK_DECISIONS]
    assert decisions
    choices = decisions[0].ordered_choices
    assert len(choices) == 4
    assert [c[0] for c in choices] == ["а", "б", "в", "г"]


def test_aook_violations_are_detected():
    """Проверка ловит форму АООК, нарушающую п.30-32."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOOK])
    sections = {s["number"]: s for s in broken["sections"]}

    # п.30: убран перечень результатов испытаний
    sections[5]["blocks"] = [b for b in sections[5]["blocks"]
                             if b.get("link_role") != "quality_evidence"]
    # п.31: формулировка стала редактируемой
    sections[8]["blocks"] = [{"kind": "text", "key": "compliance_statement",
                              "label": "Формулировка", "source": "manual"}]
    # п.32: решения заменены произвольным текстом
    sections[9]["blocks"] = [{"kind": "text", "key": "decisions",
                              "label": "Заключение", "source": "manual"}]

    problems = validate_against_spec(NormativeFormDefinition.from_dict(broken))
    joined = "\n".join(problems)
    assert "(ТЗ п.30)" in joined
    assert "(ТЗ п.31)" in joined
    assert "(ТЗ п.32)" in joined


# =====================================================================
# Акты испытаний — ТЗ п.35-41
# =====================================================================


def test_test_act_limited_to_engineering_networks():
    """П.35: акт испытаний доступен только для инженерных сетей."""
    system_type = _definition(domain.DOC_TYPE_TEST_ACT).field("system_type")
    assert set(system_type.ordered_choices) == {
        "внутренние инженерные сети", "наружные инженерные сети"}


def test_test_act_kinds_match_specification():
    """П.37: перечень видов испытаний совпадает с ТЗ."""
    kinds = _definition(domain.DOC_TYPE_TEST_ACT).field("test_kind")
    assert set(kinds.ordered_choices) == {
        "испытания", "промывка", "продувка", "гидравлические испытания",
        "манометрические испытания", "другое предусмотренное испытание"}


def test_test_act_date_is_entered_by_operator():
    """П.38: дату вводит оператор, система не назначает её сама."""
    date_field = _definition(domain.DOC_TYPE_TEST_ACT).field("doc_date")
    assert date_field.source == "manual"
    assert date_field.required


def test_test_act_participants_are_chosen_per_act():
    """П.39: состав участников задаётся оператором для каждого акта."""
    participants = [b for b in _definition(domain.DOC_TYPE_TEST_ACT).field.__self__
                    .sections[3].blocks if b.kind == BLOCK_PARTICIPANTS]
    assert participants
    assert participants[0].source == "manual"


def test_test_act_uses_sp_reference_without_variant_switcher():
    """П.41: основание — СП 73.13330.2016, переключателя форм нет."""
    definition = _definition(domain.DOC_TYPE_TEST_ACT)
    assert "73.13330.2016" in definition.basis
    keys = {b.key for s in definition.sections for b in s.blocks}
    assert not any("selector" in k or "variant" in k for k in keys)


def test_exploitation_representative_allowed_in_act_and_aou_sito():
    """П.40: представитель эксплуатирующей есть в акте и АОУСИТО."""
    for doc_type in (domain.DOC_TYPE_TEST_ACT, domain.DOC_TYPE_AOU_SITO):
        assert _definition(doc_type).field("exploitation_rep") is not None


# =====================================================================
# Защита структуры описания
# =====================================================================


def test_unknown_keys_are_rejected():
    """Опечатка в описании не остаётся незамеченной."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    broken["sections"][0]["blocks"][0]["sorce"] = "manual"
    with pytest.raises(FormDefinitionError, match="неизвестные свойства поля"):
        NormativeFormDefinition.from_dict(broken)


def test_duplicate_section_numbers_are_rejected():
    """Два раздела с одним номером делают ссылки на раздел неоднозначными."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    broken["sections"].append(copy.deepcopy(broken["sections"][0]))
    with pytest.raises(FormDefinitionError, match="номера разделов повторяются"):
        NormativeFormDefinition.from_dict(broken)


def test_form_for_unknown_document_type_is_rejected():
    """Форму нельзя описать для типа документа вне ТЗ п.42."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    broken["doc_type"] = "ПРОИЗВОЛЬНЫЙ"
    with pytest.raises(FormDefinitionError, match="не является нумеруемым"):
        NormativeFormDefinition.from_dict(broken)


def test_forbidden_font_is_rejected():
    """Шрифт вне перечня ТЗ п.59 не принимается."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    broken["layout"] = {"font_family": "Comic Sans MS"}
    with pytest.raises(FormDefinitionError, match="не разрешён"):
        NormativeFormDefinition.from_dict(broken)


def test_empty_fixed_text_is_rejected():
    """Фиксированная формулировка без текста бессмысленна (п.31)."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOOK])
    _block(broken, 8, "compliance_statement")["text"] = ""
    with pytest.raises(FormDefinitionError, match="должна содержать текст"):
        NormativeFormDefinition.from_dict(broken)


# =====================================================================
# Загрузка в справочник и версионирование (ТЗ п.96)
# =====================================================================


def test_forms_are_loaded_into_directory(db):
    """Формы появляются в справочнике и читаются из него.

    Номер версии сверяется с объявленным в определении, а не с единицей:
    структура форм меняется (ТЗ п.96), и тест не должен ломаться на этом.
    """
    load_form_definitions(db)
    declared = {doc_type: raw["version"] for doc_type, raw in RAW.items()}
    for doc_type in domain.NUMBERED_DOC_TYPES:
        form = current_form(db, doc_type)
        assert form is not None, f"нет формы для {doc_type}"
        assert form.is_current
        assert form.version == declared[doc_type]
        assert parse_form(form).doc_type == doc_type


def test_loading_is_idempotent(db):
    """Повторная загрузка ничего не меняет и не плодит версии."""
    load_form_definitions(db)
    report = load_form_definitions(db)
    assert report["created"] == []
    assert report["skipped"] == []
    count = db.execute(text("SELECT count(*) FROM normative_forms")).scalar()
    assert count == len(RAW)


def test_loading_twice_reports_no_conflict(db):
    """Одно и то же описание не считается конфликтом версий."""
    load_form_definitions(db)
    report = load_form_definitions(db)
    assert len(report["unchanged"]) == len(RAW)


def test_changed_form_requires_new_version(db):
    """П.96: изменение формы создаёт новую версию, а не перезаписывает старую."""
    load_form_definitions(db)
    base = RAW[domain.DOC_TYPE_AOSR]["version"]
    mutated = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    mutated["sections"][0]["blocks"].append(
        {"key": "extra", "label": "Дополнительное поле"})

    report = load_form_definitions(db, [mutated])
    assert report["created"] == []
    assert report["skipped"], "изменение принято под прежним номером версии"

    stored = form_by_version(db, domain.DOC_TYPE_AOSR, base)
    assert "extra" not in parse_form(stored).section(1).blocks[0].key
    assert current_form(db, domain.DOC_TYPE_AOSR).version == base


def test_new_version_becomes_current_and_keeps_previous(db):
    """Новая версия становится актуальной, прежняя остаётся в базе."""
    load_form_definitions(db)
    previous = RAW[domain.DOC_TYPE_AOSR]["version"]
    v2 = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
    v2["version"] = previous + 1
    v2["sections"][0]["blocks"].append(
        {"key": "extra", "label": "Дополнительное поле"})

    report = load_form_definitions(db, [v2])
    assert report["created"] == [f"АОСР v{previous + 1}"]

    old = form_by_version(db, domain.DOC_TYPE_AOSR, previous)
    new = form_by_version(db, domain.DOC_TYPE_AOSR, previous + 1)
    assert old is not None and new is not None
    assert old.is_current is False
    assert new.is_current is True
    assert "extra" in {b.key for b in parse_form(new).blocks_of(1)}
    # Прежняя версия не затронута: документы, выпущенные по ней, сохранят её.
    assert "extra" not in {b.key for b in parse_form(old).blocks_of(1)}


def test_form_violating_specification_is_not_saved(db):
    """Форма, нарушающая ТЗ, не попадает в базу (п.24, 31)."""
    broken = copy.deepcopy(RAW[domain.DOC_TYPE_AOOK])
    broken["version"] = 7
    sections = {s["number"]: s for s in broken["sections"]}
    sections[8]["blocks"] = [{"kind": "text", "key": "compliance_statement",
                              "label": "Формулировка", "source": "manual"}]

    report = load_form_definitions(db, [broken])
    assert report["created"] == []
    assert report["skipped"]
    assert "фиксированную формулировку" in report["skipped"][0]["problems"][0]
    assert form_by_version(db, domain.DOC_TYPE_AOOK, 7) is None


def test_only_one_current_version_per_document_type(db):
    """Для типа документа актуальна ровно одна версия формы."""
    load_form_definitions(db)
    first = RAW[domain.DOC_TYPE_AOSR]["version"]
    for version in (first + 1, first + 2):
        nxt = copy.deepcopy(RAW[domain.DOC_TYPE_AOSR])
        nxt["version"] = version
        load_form_definitions(db, [nxt])

    rows = db.execute(
        text("SELECT version, is_current FROM normative_forms "
             "WHERE doc_type = :d ORDER BY version"),
        {"d": domain.DOC_TYPE_AOSR}).all()
    assert [r[0] for r in rows] == [first, first + 1, first + 2]
    assert [r[0] for r in rows if r[1]] == [first + 2]


def test_form_in_use_cannot_be_deleted(db):
    """Форма, на которую ссылается документ, не удаляется (ТЗ п.96)."""
    from app.db.forms_service import assert_form_in_use_guard
    from app.db.models import Direction, Document, NormativeForm, Project

    load_form_definitions(db)
    form = current_form(db, domain.DOC_TYPE_AOSR)

    # Пока форма ни на к��м не ссылается, удалять её можно.
    assert_form_in_use_guard(db, form)

    direction = db.query(Direction).first()
    if direction is None:
        direction = Direction(name="Проверка", sort_order=99)
        db.add(direction)
        db.flush()
    project = Project(title="Проверка", direction_id=direction.id)
    db.add(project)
    db.flush()
    document = Document(
        project_id=project.id,
        doc_type=domain.DOC_TYPE_AOSR,
        number="1",
        form_version_id=form.id,
    )
    db.add(document)
    db.commit()

    with pytest.raises(RuntimeError, match="используется документами"):
        assert_form_in_use_guard(db, form)
    assert NormativeForm is not None


def test_saved_definition_round_trips_through_json(db):
    """Сохранённое описание разбирается без потерь."""
    load_form_definitions(db)
    for doc_type in domain.NUMBERED_DOC_TYPES:
        form = current_form(db, doc_type)
        assert parse_form(form) == _definition(doc_type), doc_type
