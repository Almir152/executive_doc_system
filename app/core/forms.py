"""Нормативные формы документов как данные (ТЗ п.24, 96).

Форма отделена от программного кода: она описана в справочнике
``normative_forms`` и версионируется. Изменение согласованной структуры
порождает новую версию, а документы, сформированные ранее, сохраняют ту
версию, по которой были выпущены.

Описание формы (поле ``definition``) построено так, чтобы различать:

* что оператор вводит вручную, а что система подставляет сама;
* что является неизменяемой частью согласованной формы, а что можно править;
* где документ ссылается на архив, а не содержит данные.

Именно эти различия требуются пунктами 25-28 и 30-32 ТЗ, поэтому они
выражены в структуре, а не в коде интерфейса.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core import domain

# Версия формата описания формы. Повышается, только если меняется сам
# формат; описание конкретной формы версионируется отдельно.
DEFINITION_SCHEMA = 1

# --------------------------------------------------------------------------
# Типы блоков
# --------------------------------------------------------------------------

BLOCK_FIELD = "field"              # одно поле, вводимое или подставляемое
BLOCK_TEXT = "text"                # произвольный текст
BLOCK_FIXED_TEXT = "fixed_text"    # неизменяемая формулировка (п.31)
BLOCK_DOCUMENT_LIST = "document_list"   # перечень архивных документов
BLOCK_PARTICIPANTS = "participants"     # состав участников, задаётся оператором (п.39)
BLOCK_DECISIONS = "decisions"      # фиксированная структура решений а-г (п.32)

BLOCK_KINDS = frozenset({
    BLOCK_FIELD, BLOCK_TEXT, BLOCK_FIXED_TEXT,
    BLOCK_DOCUMENT_LIST, BLOCK_PARTICIPANTS, BLOCK_DECISIONS,
})

# --------------------------------------------------------------------------
# Источники значения поля
#
# "project"       - реквизит проекта;
# "manual"        - вводится оператором, система не подставляет;
# "representative" - представитель проекта с указанной ролью;
# "document"      - реквизит самого документа (номер, дата);
# "linked"        - данные связанных документов.
# --------------------------------------------------------------------------
SOURCE_PROJECT = "project"
SOURCE_MANUAL = "manual"
SOURCE_REPRESENTATIVE = "representative"
SOURCE_DOCUMENT = "document"
SOURCE_LINKED = "linked"
# Разделы проектной документации: оператор выбирает один или несколько
# разделов там, где это предусмотрено формой (ТЗ п.21).
SOURCE_SECTION = "section"

FIELD_SOURCES = frozenset({
    SOURCE_PROJECT, SOURCE_MANUAL, SOURCE_REPRESENTATIVE,
    SOURCE_DOCUMENT, SOURCE_LINKED, SOURCE_SECTION,
})

# Тип значения поля. Дата вводится оператором в формате дд.мм.гггг и
# хранится текстом: системе нельзя менять дату самостоятельно (ТЗ п.43).
FIELD_TYPE_TEXT = "text"
FIELD_TYPE_NUMBER = "number"
FIELD_TYPE_DATE = "date"
FIELD_TYPES = (FIELD_TYPE_TEXT, FIELD_TYPE_NUMBER, FIELD_TYPE_DATE)

# --------------------------------------------------------------------------
# Параметры печати (ТЗ п.55-61)
# --------------------------------------------------------------------------
PAPER_A4 = "A4"
ORIENTATION_PORTRAIT = "portrait"
ORIENTATION_LANDSCAPE = "landscape"

DEFAULT_LAYOUT = {
    "paper": PAPER_A4,
    "orientation": ORIENTATION_PORTRAIT,
    # ТЗ п.59: Times New Roman 11 pt, regular. Заполняемый текст — курсив.
    "font_family": "Times New Roman",
    "font_size": 11,
    "editable_font_style": "italic",
    "heading_font_size": 12,
    # ТЗ п.60: поля в миллиметрах.
    "margin_top": 20,
    "margin_left": 10,
    "margin_right": 10,
    "margin_bottom": 10,
    # Допустимая автоматическая корректировка полей, ТЗ п.60.
    "margin_tolerance_top": 2,
    "margin_tolerance_bottom": -4,
}


class FormDefinitionError(ValueError):
    """Описание формы не соответствует ожидаемой структуре.

    Вызывается до сохранения формы: ошибочная структура не должна попасть
    в базу и молча сломать построение документов.
    """


@dataclass(frozen=True)
class FormLayout:
    """Параметры вёрстки формы (ТЗ п.55-61)."""

    paper: str = PAPER_A4
    orientation: str = ORIENTATION_PORTRAIT
    font_family: str = "Times New Roman"
    font_size: int = 11
    editable_font_style: str = "italic"
    heading_font_size: int = 12
    margin_top: int = 20
    margin_left: int = 10
    margin_right: int = 10
    margin_bottom: int = 10
    margin_tolerance_top: int = 2
    margin_tolerance_bottom: int = -4
    target_pages: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> "FormLayout":
        values = dict(DEFAULT_LAYOUT)
        if data:
            unknown = set(data) - set(cls.__dataclass_fields__)
            if unknown:
                raise FormDefinitionError(
                    f"неизвестные параметры вёрстки: {sorted(unknown)}"
                )
            values.update(data)
        if values["paper"] != PAPER_A4:
            raise FormDefinitionError(
                f"основной формат бумаги — {PAPER_A4} (ТЗ п.55), получено {values['paper']!r}"
            )
        if values["orientation"] not in (ORIENTATION_PORTRAIT, ORIENTATION_LANDSCAPE):
            raise FormDefinitionError(f"недопустимая ориентация {values['orientation']!r}")
        if values["font_family"] not in ("Calibri", "Arial", "ISO", "Times New Roman"):
            raise FormDefinitionError(
                f"шрифт {values['font_family']!r} не разрешён ТЗ п.59"
            )
        if values["font_size"] != 11:
            raise FormDefinitionError(
                f"основной шрифт 11 pt (ТЗ п.59), получено {values['font_size']}"
            )
        for margin in ("margin_top", "margin_left", "margin_right", "margin_bottom"):
            if not isinstance(values[margin], int) or values[margin] < 0:
                raise FormDefinitionError(f"поле {margin} должно быть неотрицательным целым")
        return cls(**values)


@dataclass(frozen=True)
class FormField:
    """Одно поле формы.

    ``source`` определяет, откуда берётся значение. Для ``manual`` система
    не формирует значение самостоятельно — это требование ТЗ п.27 для
    пункта 7 АОСР и п.38 для дат.
    """

    key: str
    label: str
    kind: str = BLOCK_FIELD
    source: str = SOURCE_MANUAL
    required: bool = False
    role: str = ""                 # для source="representative"
    link_role: str = ""            # для блока перечня документов
    note: str = ""
    # Неизменяемая формулировка: система не даёт её править (ТЗ п.31).
    fixed: bool = False
    # Текст неизменяемой формулировки (kind="fixed_text").
    text: str = ""
    # Правило «до 5 документов — напрямую, больше 5 — реестр» (ТЗ п.28).
    threshold: int | None = None
    label_fill: bool = False       # печатается ли подпись «ФИО»
    label_sign: bool = False       # печатается ли строка подписи
    ordered_choices: tuple[str, ...] = ()
    # ТЗ п.64: незаполненный блок представителя эксплуатации можно убрать
    # из печатной формы, если это допускает конкретная форма (п.40).
    omittable_if_empty: bool = False
    # Тип значения: текст, число или дата (ТЗ п.43, 87).
    value_type: str = FIELD_TYPE_TEXT

    @classmethod
    def from_dict(cls, data: dict) -> "FormField":
        if not isinstance(data, dict):
            raise FormDefinitionError(f"поле должно быть объектом, получено {type(data).__name__}")
        # choices — внешнее имя, ordered_choices — имя в структуре: заменяем
        # до проверки, иначе внешний ключ выглядит лишним.
        if "choices" in data:
            data = dict(data)
            data["ordered_choices"] = tuple(data.pop("choices"))
        if "type" in data:
            data = dict(data)
            data["value_type"] = data.pop("type")
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise FormDefinitionError(f"неизвестные свойства поля: {sorted(unknown)}")
        if "key" not in data or "label" not in data:
            raise FormDefinitionError("поле требует key и label")
        kind = data.get("kind", BLOCK_FIELD)
        if kind not in BLOCK_KINDS:
            raise FormDefinitionError(f"неизвестный вид блока {kind!r}")
        if data.get("source", SOURCE_MANUAL) not in FIELD_SOURCES:
            raise FormDefinitionError(
                f"неизвестный источник значения {data['source']!r}"
            )
        if data.get("value_type", FIELD_TYPE_TEXT) not in FIELD_TYPES:
            raise FormDefinitionError(
                f"неизвестный тип значения поля {data.get('value_type')!r} "
                f"у {data.get('key')!r}; допустимо: {', '.join(FIELD_TYPES)}"
            )
        if kind == BLOCK_FIXED_TEXT and not data.get("text"):
            raise FormDefinitionError(
                f"неизменяемая формулировка {data.get('key')!r} должна содержать текст"
            )
        if kind == BLOCK_DECISIONS and not data.get("ordered_choices"):
            raise FormDefinitionError(
                f"структура решений {data.get('key')!r} должна содержать варианты"
            )
        return cls(**{k: v for k, v in data.items() if k in known})



@dataclass(frozen=True)
class FormSection:
    """Раздел формы с фиксированным номером и заголовком.

    Нумерация разделов значима: АООК опирается на разделы 5, 8 и 9
    (ТЗ п.30-32), поэтому номер хранится числом, а не частью заголовка.
    """

    number: int
    title: str
    blocks: tuple[FormField, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "FormSection":
        if not isinstance(data, dict):
            raise FormDefinitionError(f"раздел должен быть объектом, получено {type(data).__name__}")
        for required in ("number", "title"):
            if required not in data:
                raise FormDefinitionError(f"раздел требует {required}")
        if not isinstance(data["number"], int):
            raise FormDefinitionError("номер раздела должен быть целым числом")
        blocks = data.get("blocks", ())
        if not isinstance(blocks, (list, tuple)):
            raise FormDefinitionError(f"блоки раздела должны быть списком: {blocks!r}")
        return cls(
            number=data["number"],
            title=data["title"],
            blocks=tuple(FormField.from_dict(b) for b in blocks),
        )


@dataclass(frozen=True)
class NormativeFormDefinition:
    """Разобранное и проверенное описание нормативной формы."""

    doc_type: str
    version: int
    title: str
    basis: str
    layout: FormLayout
    sections: tuple[FormSection, ...]
    notes: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict) -> "NormativeFormDefinition":
        if not isinstance(data, dict):
            raise FormDefinitionError(
                f"описание формы должно быть объектом, получено {type(data).__name__}"
            )
        schema = data.get("schema")
        if schema != DEFINITION_SCHEMA:
            raise FormDefinitionError(
                f"описание формы имеет схему {schema!r}, ожидается {DEFINITION_SCHEMA}"
            )
        for required in ("doc_type", "version", "title", "sections"):
            if required not in data:
                raise FormDefinitionError(f"в описании формы нет {required!r}")
        doc_type = data["doc_type"]
        if doc_type not in domain.NUMBERED_DOC_TYPES:
            raise FormDefinitionError(
                f"код документа {doc_type!r} не является нумеруемым (ТЗ п.42): "
                f"{list(domain.NUMBERED_DOC_TYPES)}"
            )
        if not isinstance(data["version"], int) or data["version"] < 1:
            raise FormDefinitionError("версия формы должна быть целым числом от 1")
        sections = data.get("sections")
        if not isinstance(sections, list) or not sections:
            raise FormDefinitionError("форма должна содержать хотя бы один раздел")
        parsed = tuple(FormSection.from_dict(s) for s in sections)

        numbers = [s.number for s in parsed]
        duplicates = {n for n in numbers if numbers.count(n) > 1}
        if duplicates:
            raise FormDefinitionError(f"номера разделов повторяются: {sorted(duplicates)}")

        known = {"schema", "doc_type", "version", "title", "basis",
                 "layout", "sections", "notes"}
        unknown = set(data) - known
        if unknown:
            raise FormDefinitionError(f"неизвестные свойства описания: {sorted(unknown)}")

        return cls(
            doc_type=doc_type,
            version=data["version"],
            title=data["title"],
            basis=data.get("basis", ""),
            layout=FormLayout.from_dict(data.get("layout")),
            sections=parsed,
            notes=tuple(data.get("notes", ())),
            raw=data,
        )

    def section(self, number: int) -> FormSection:
        for s in self.sections:
            if s.number == number:
                return s
        raise KeyError(f"в форме {self.doc_type} нет раздела {number}")

    def has_section(self, number: int) -> bool:
        return any(s.number == number for s in self.sections)

    def field(self, key: str) -> FormField:
        for s in self.sections:
            for b in s.blocks:
                if b.key == key:
                    return b
        raise KeyError(f"в форме {self.doc_type} нет поля {key!r}")

    def blocks_of(self, section_number: int) -> tuple[FormField, ...]:
        return self.section(section_number).blocks

    @property
    def manual_fields(self) -> tuple[FormField, ...]:
        """Поля, которые оператор обязан ввести сам."""
        return tuple(
            b for s in self.sections for b in s.blocks
            if b.source == SOURCE_MANUAL and b.required
        )

    def to_dict(self) -> dict:
        """Каноническое представление формы, пригодное для JSON.

        Нужно для сравнения с содержимым базы: JSON не различает кортежи и
        списки, поэтому сравнение исходных словарей давало бы ложное
        расхождение при каждом запуске.
        """
        return {
            "schema": self.raw.get("schema", DEFINITION_SCHEMA),
            "doc_type": self.doc_type,
            "version": self.version,
            "title": self.title,
            "basis": self.basis,
            "layout": _layout_to_dict(self.layout),
            "notes": list(self.notes),
            "sections": [
                {
                    "number": s.number,
                    "title": s.title,
                    "blocks": [_field_to_dict(b) for b in s.blocks],
                }
                for s in self.sections
            ],
        }


def _layout_to_dict(layout: "FormLayout") -> dict:
    data = {
        k: getattr(layout, k)
        for k in FormLayout.__dataclass_fields__
    }
    # Незаданные значения параметров не сохраняются: они берутся из
    # DEFAULT_LAYOUT, иначе каждое сохранение раздувало бы описание.
    defaults = FormLayout()
    return {k: v for k, v in data.items() if getattr(defaults, k) != v}


def _field_to_dict(field_def: "FormField") -> dict:
    data: dict = {"key": field_def.key, "label": field_def.label}
    if field_def.kind != BLOCK_FIELD:
        data["kind"] = field_def.kind
    if field_def.source != SOURCE_MANUAL:
        data["source"] = field_def.source
    if field_def.required:
        data["required"] = True
    if field_def.value_type != FIELD_TYPE_TEXT:
        data["type"] = field_def.value_type
    for name in ("role", "link_role", "note", "text"):
        value = getattr(field_def, name)
        if value:
            data[name] = value
    if field_def.fixed:
        data["fixed"] = True
    if field_def.threshold is not None:
        data["threshold"] = field_def.threshold
    if field_def.label_fill:
        data["label_fill"] = True
    if field_def.label_sign:
        data["label_sign"] = True
    if field_def.ordered_choices:
        data["choices"] = list(field_def.ordered_choices)
    if field_def.omittable_if_empty:
        data["omittable_if_empty"] = True
    return data


# --------------------------------------------------------------------------
# Правила ТЗ, проверяемые по описанию формы
# --------------------------------------------------------------------------

def validate_aosr_definition(definition: NormativeFormDefinition) -> list[str]:
    """Проверить структуру АОСР по ТЗ п.25-28.

    Возвращает список нарушений; пустой список означает соответствие.
    """
    problems: list[str] = []

    # п.25: «лицо, непосредственно выполнявшее освидетельствуемые работы»
    # обязательно.
    if not definition.has_section(3):
        problems.append("в АОСР нет раздела 3 (ТЗ п.28)")
    else:
        point3 = definition.blocks_of(3)
        lists = [b for b in point3 if b.kind == BLOCK_DOCUMENT_LIST]
        if not lists:
            problems.append(
                "раздел 3 АОСР не содержит перечня документов (ТЗ п.28)"
            )
        for b in lists:
            if b.threshold != 5:
                problems.append(
                    f"в перечне «{b.label}» не задан порог 5 документов: "
                    f"до 5 — прямое перечисление, больше 5 — реестр (ТЗ п.28)"
                )

    # п.26: НРС включается по умолчанию, но может остаться незаполненным.
    nrs = _find_field(definition, "nrs")
    if nrs is None:
        problems.append("в АОСР нет поля НРС (ТЗ п.26)")
    elif nrs.required:
        problems.append("НРС не должен быть обязательным: оператор вправе оставить его пустым (ТЗ п.26)")

    # п.27: пункт 7 заполняется вручную, система не подменяет содержание.
    if not definition.has_section(7):
        problems.append("в АОСР нет пункта 7 (ТЗ п.27)")
    else:
        manual7 = [b for b in definition.blocks_of(7) if b.source == SOURCE_MANUAL]
        if not manual7:
            problems.append(
                "пункт 7 АОСР должен заполняться оператором вручную (ТЗ п.27)"
            )
        for b in manual7:
            if b.fixed:
                problems.append(f"пункт 7 не может быть неизменяемым блоком: {b.key}")

    # п.25: исполнитель работ — обязательное поле.
    performer = _find_field(definition, "work_performer")
    if performer is None:
        problems.append("в АОСР нет поля «лицо, выполнявшее работы» (ТЗ п.25)")
    elif not performer.required:
        problems.append("поле «лицо, выполнявшее работы» обязательно (ТЗ п.25)")
    elif performer.source == SOURCE_PROJECT:
        problems.append(
            "поле «лицо, выполнявшее работы» не должно подставляться из проекта: "
            "исполнитель работ не обязательно представитель проекта (ТЗ п.25)"
        )

    return problems


def validate_aook_definition(definition: NormativeFormDefinition) -> list[str]:
    """Проверить структуру АООК по ТЗ п.30-33."""
    problems: list[str] = []

    # п.30: в разделе 5 два пункта — схемы и результаты испытаний.
    if not definition.has_section(5):
        problems.append("в АООК нет раздела 5 (ТЗ п.30)")
    else:
        roles = {
            b.link_role for b in definition.blocks_of(5)
            if b.kind == BLOCK_DOCUMENT_LIST
        }
        for required_role in ("geodetic_scheme", "quality_evidence"):
            if required_role not in roles:
                problems.append(
                    f"в разделе 5 АООК нет пункта с архивами «{required_role}» (ТЗ п.30)"
                )

    # п.31: в разделе 8 только фиксированная формулировка, пользователь
    # не может её произвольно менять.
    if not definition.has_section(8):
        problems.append("в АООК нет раздела 8 (ТЗ п.31)")
    else:
        blocks8 = definition.blocks_of(8)
        fixed = [b for b in blocks8 if b.kind == BLOCK_FIXED_TEXT]
        editable = [b for b in blocks8 if b.kind in (BLOCK_TEXT, BLOCK_FIELD)]
        if not fixed:
            problems.append(
                "раздел 8 АООК должен содержать фиксированную формулировку (ТЗ п.31)"
            )
        for b in fixed:
            if not b.fixed:
                problems.append(f"блок {b.key} раздела 8 должен быть неизменяемым (ТЗ п.31)")
        for b in editable:
            problems.append(
                f"раздел 8 АООК не должен содержать редактируемый блок {b.key}: "
                f"формулировка фиксированная (ТЗ п.31)"
            )

    # п.32: пункт 9 — структура решений а-г, а не произвольный текст.
    if not definition.has_section(9):
        problems.append("в АООК нет пункта 9 (ТЗ п.32)")
    else:
        point9 = [b for b in definition.blocks_of(9) if b.kind == BLOCK_DECISIONS]
        if not point9:
            problems.append(
                "пункт 9 АООК должен быть структурой решений а-г, а не текстом (ТЗ п.32)"
            )
        else:
            choices = point9[0].ordered_choices
            if not choices:
                problems.append("не заданы варианты решений а-г для пункта 9 (ТЗ п.32)")

    return problems


def validate_test_act_definition(definition: NormativeFormDefinition) -> list[str]:
    """Проверить структуру акта испытаний по ТЗ п.35-39."""
    problems: list[str] = []

    if definition.doc_type != domain.DOC_TYPE_TEST_ACT:
        problems.append("проверяется форма акта испытаний, а не другого документа")

    # п.37: виды испытаний. Формулировки сверяются дословно с перечнем ТЗ:
    # «гидравлические испытания» и «гидравлические» — разные виды.
    kinds_field = _find_field(definition, "test_kind")
    if kinds_field is None:
        problems.append("не задан вид испытаний (ТЗ п.37)")
    else:
        required_kinds = {
            "испытания",
            "промывка",
            "продувка",
            "гидравлические испытания",
            "манометрические испытания",
            "другое предусмотренное испытание",
        }
        available = {c.lower() for c in kinds_field.ordered_choices}
        missing = required_kinds - available
        if missing:
            problems.append(
                f"не заданы виды испытаний из ТЗ п.37: {sorted(missing)}"
            )

    # п.38: дата вводится оператором, система не назначает её сама.
    date_field = _find_field(definition, "doc_date")
    if date_field is not None and date_field.source != SOURCE_MANUAL:
        problems.append(
            "дата акта испытаний должна вводиться оператором (ТЗ п.38)"
        )

    # п.39: состав участников задаётся оператором для каждого акта.
    participants = [b for b in
                    (bl for s in definition.sections for bl in s.blocks)
                    if b.kind == BLOCK_PARTICIPANTS]
    if not participants:
        problems.append("в акте испытаний нет состава участников (ТЗ п.39)")
    for b in participants:
        if b.source != SOURCE_MANUAL:
            problems.append(
                f"состав участников задаётся оператором (ТЗ п.39), а не системой: {b.key}"
            )

    # п.41: форма акта канализации и водостоков — по СП 73.13330.2016,
    # без внутреннего переключателя между формами.
    if "73.13330.2016" not in definition.basis:
        problems.append(
            "основание акта должно ссылаться на СП 73.13330.2016 (ТЗ п.41), "
            f"получено {definition.basis!r}"
        )
    if any(b.kind == BLOCK_FIXED_TEXT and b.key == "form_variant_selector"
           for s in definition.sections for b in s.blocks):
        problems.append(
            "переключатель между альтернативными формами не предусмотрен (ТЗ п.41)"
        )

    return problems


VALIDATORS = {
    domain.DOC_TYPE_AOSR: validate_aosr_definition,
    domain.DOC_TYPE_AOOK: validate_aook_definition,
    domain.DOC_TYPE_TEST_ACT: validate_test_act_definition,
}


def validate_against_spec(definition: NormativeFormDefinition) -> list[str]:
    """Применить проверку, соответствующую типу документа."""
    validator = VALIDATORS.get(definition.doc_type)
    if validator is None:
        return []
    return validator(definition)


def _find_field(definition: NormativeFormDefinition, key: str) -> FormField | None:
    for s in definition.sections:
        for b in s.blocks:
            if b.key == key:
                return b
    return None


def format_field_label(field_def: FormField) -> str:
    """Подпись поля для печати, включая единицы измерения и примечания."""
    label = field_def.label
    if field_def.link_role:
        label = f"{label} (архив: {field_def.link_role})"
    if field_def.note:
        label = f"{label}\n{field_def.note}"
    return label


def describe(definition: NormativeFormDefinition) -> str:
    """Краткое описание формы для журнала и проверок."""
    blocks = sum(len(s.blocks) for s in definition.sections)
    return (
        f"{definition.doc_type} v{definition.version}: «{definition.title}», "
        f"разделов {len(definition.sections)}, блоков {blocks}, "
        f"основание: {definition.basis or 'не указано'}"
    )
