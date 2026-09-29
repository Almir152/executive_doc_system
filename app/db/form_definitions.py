"""Определения нормативных форм документов (ТЗ п.24, 30, 34, 41).

Каждая форма описана как данные и загружается в справочник
``normative_forms`` под управлением версий (ТЗ п.96).

Описание содержит структуру, привязки к данным проекта и те формулировки,
которые дословно заданы ТЗ. Текст форм, утверждённых Минстроем, в ТЗ не
приведён и не выдумывается: в таких местах стоит ``basis`` со ссылкой на
нормативный документ, а окончательная согласованная формулировка вносится
оператором в ту же версию формы.

Соответствие правил ТЗ проверяется функциями в ``app.core.forms``:
описание, прошедшее проверку, не может быть сохранено в базу.
"""

from __future__ import annotations

from app.core import domain
from app.core.forms import (
    BLOCK_DECISIONS,
    BLOCK_DOCUMENT_LIST,
    BLOCK_FIELD,
    BLOCK_FIXED_TEXT,
    BLOCK_PARTICIPANTS,
    BLOCK_TEXT,
    DEFINITION_SCHEMA,
    ORIENTATION_PORTRAIT,
    PAPER_A4,
    SOURCE_LINKED,
    SOURCE_MANUAL,
    SOURCE_PROJECT,
    SOURCE_REPRESENTATIVE,
)

FORM_VERSION = 1

# Роли представителей, ТЗ п.17-19 и 40.
ROLE_CUSTOMER = "customer"
ROLE_CONTRACTOR = "contractor"
ROLE_DEVELOPER = "developer"
ROLE_DESIGNER = "designer"
ROLE_SUPERVISION = "supervision"
ROLE_SUBCONTRACTOR = "subcontractor"
ROLE_EXPLOITATION = "exploitation"

_AOSR: dict = {
    "schema": DEFINITION_SCHEMA,
    "doc_type": domain.DOC_TYPE_AOSR,
    "version": FORM_VERSION,
    "title": "Акт освидетельствования скрытых работ",
    "basis": "приказ Минстроя России № 344/пр (ТЗ п.24)",
    "layout": {
        "paper": PAPER_A4,
        "orientation": ORIENTATION_PORTRAIT,
        # ТЗ п.56: целевой результат — 2 страницы A4.
        "target_pages": "2",
    },
    "notes": (
        "Форма согласована с заказчиком (ТЗ п.24): статическая структура "
        "не изменяется произвольно. Любое изменение структуры оформляется "
        "новой версией формы (ТЗ п.96).",
        "Текст, утверждённый приказом № 344/пр, в ТЗ не приведён и в этой "
        "версии не воспроизводится: в разделе 8 оставлено поле для внесения "
        "согласованной формулировки.",
    ),
    "sections": [
        {
            "number": 1,
            "title": "Сведения об объекте и работах",
            "blocks": [
                {"key": "object_name", "label": "Наименование объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "address", "label": "Адрес объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "customer_org", "label": "Заказчик",
                 "source": SOURCE_PROJECT, "role": ROLE_CUSTOMER},
                {"key": "general_contractor_org", "label": "Генеральный подрядчик",
                 "source": SOURCE_PROJECT, "role": ROLE_CONTRACTOR},
                {"key": "contractor_org", "label": "Подрядчик, выполняющий работы",
                 "source": SOURCE_PROJECT, "role": ROLE_SUBCONTRACTOR},
                {"key": "work_description",
                 "label": "Наименование и объём освидетельствуемых работ",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "section_refs",
                 "label": "Номера разделов проекта, к которым относятся работы",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "work_period",
                 "label": "Период выполнения работ (с ... по ...)",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            # ТЗ п.28: до 5 документов перечисляются напрямую, больше 5 —
            # автоматически создаётся реестр.
            "number": 3,
            "title": "Документы, подтверждающие качество и выполнение работ",
            "blocks": [
                {"kind": BLOCK_DOCUMENT_LIST, "key": "quality_documents",
                 "label": "Документы о качестве материалов и конструкций",
                 "source": SOURCE_LINKED, "link_role": "quality",
                 "threshold": 5,
                 "note": "до 5 документов перечисляются непосредственно; "
                         "более 5 — создаётся реестр; при возврате "
                         "к 5 и менее реестр убирается"},
                {"kind": BLOCK_DOCUMENT_LIST, "key": "quality_evidence",
                 "label": "Результаты экспертиз, обследований и испытаний",
                 "source": SOURCE_LINKED, "link_role": "quality_evidence",
                 "threshold": 5},
                {"kind": BLOCK_DOCUMENT_LIST, "key": "protocols",
                 "label": "Протоколы испытаний",
                 "source": SOURCE_LINKED, "link_role": "protocol", "threshold": 5},
            ],
        },
        {
            "number": 4,
            "title": "Сведения о применённых материалах и конструкциях",
            "blocks": [
                {"kind": BLOCK_DOCUMENT_LIST, "key": "materials",
                 "label": "Применённые материалы и конструкции",
                 "source": SOURCE_LINKED, "link_role": "material",
                 "threshold": 5},
            ],
        },
        {
            "number": 5,
            "title": "Характеристика освидетельствуемых работ",
            "blocks": [
                {"key": "work_volume",
                 "label": "Объём и основные характеристики работ",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "deviations",
                 "label": "Отступления от проекта, не отражённые в разделе 3",
                 "source": SOURCE_MANUAL},
            ],
        },
        {
            "number": 6,
            "title": "Сведения о несоответствиях и замечаниях",
            "blocks": [
                {"key": "has_defects", "label": "Выявлены несоответствия",
                 "kind": BLOCK_FIELD, "source": SOURCE_MANUAL, "required": True,
                 "choices": ["нет", "да"]},
                {"key": "defects_list", "label": "Перечень несоответствий",
                 "kind": BLOCK_TEXT, "source": SOURCE_MANUAL,
                 "note": "заполняется, если выявлены несоответствия"},
            ],
        },
        {
            # ТЗ п.27: пункт 7 заполняется оператором вручную, система не
            # подменяет его содержание.
            "number": 7,
            "title": "Дополнительные сведения",
            "blocks": [
                {"kind": BLOCK_TEXT, "key": "item7", "label": "Сведения, указанные оператором",
                 "source": SOURCE_MANUAL,
                 "note": "заполняется оператором вручную; система не подменяет "
                         "содержание пункта 7"},
            ],
        },
        {
            "number": 8,
            "title": "Нормативное соответствие",
            "blocks": [
                # ТЗ п.26: НРС включается по умолчанию, но может остаться
                # незаполненным, поэтому поле не обязательное.
                {"key": "nrs", "label": "НРС", "source": SOURCE_MANUAL,
                 "required": False,
                 "note": "включается по умолчанию; оператор вправе оставить "
                         "незаполненным"},
            ],
        },
        {
            "number": 9,
            "title": "Заключение",
            "blocks": [
                {"kind": BLOCK_TEXT, "key": "conclusion",
                 "label": "Заключение о допуске к последующим работам",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            "number": 10,
            "title": "Подписи",
            "blocks": [
                # ТЗ п.25: лицо, непосредственно выполнявшее освидетельствуемые
                # работы, обязательно и не подставляется из проекта: исполнитель
                # работ не обязательно представитель проекта.
                {"key": "work_performer",
                 "label": "Лицо, непосредственно выполнявшее освидетельствуемые работы",
                 "source": SOURCE_MANUAL, "required": True, "label_fill": True,
                 "label_sign": True,
                 "note": "обязательное поле (ТЗ п.25)"},
                {"key": "customer_rep", "label": "Представитель заказчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CUSTOMER,
                 "label_fill": True, "label_sign": True},
                {"key": "contractor_rep", "label": "Представитель подрядчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CONTRACTOR,
                 "label_fill": True, "label_sign": True},
                {"key": "supervision_rep", "label": "Представитель технадзора",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_SUPERVISION,
                 "label_fill": True, "label_sign": True},
                # ТЗ п.40: представитель эксплуатирующей организации в обычный
                # АОСР автоматически не добавляется.
                {"key": "exploitation_rep", "label": "Представитель эксплуатирующей организации",
                 "source": SOURCE_MANUAL, "required": False,
                 "note": "в обычный АОСР не добавляется автоматически (ТЗ п.40)"},
            ],
        },
    ],
}

_AOOK: dict = {
    "schema": DEFINITION_SCHEMA,
    "doc_type": domain.DOC_TYPE_AOOK,
    "version": FORM_VERSION,
    "title": "Акт освидетельствования и приёмки ответственных конструкций",
    "basis": "приказ Минстроя России № 344/пр (ТЗ п.29-32)",
    "layout": {
        "paper": PAPER_A4,
        "orientation": ORIENTATION_PORTRAIT,
        # ТЗ п.57: целевой диапазон 2-4 страницы A4.
        "target_pages": "2-4",
    },
    "notes": (
        "АООК — самостоятельный документ, использующий данные проекта и "
        "связанные АОСР (ТЗ п.29).",
        "Перечни раздела 5 ссылаются на архив: документы не копируются "
        "физически (ТЗ п.30).",
    ),
    "sections": [
        {
            "number": 1,
            "title": "Сведения об объекте",
            "blocks": [
                {"key": "object_name", "label": "Наименование объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "address", "label": "Адрес объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "general_contractor_org", "label": "Генеральный подрядчик",
                 "source": SOURCE_PROJECT, "role": ROLE_CONTRACTOR},
                {"key": "customer_org", "label": "Заказчик",
                 "source": SOURCE_PROJECT, "role": ROLE_CUSTOMER},
            ],
        },
        {
            "number": 2,
            "title": "Основание для приёмки",
            "blocks": [
                {"key": "base_documents", "label": "Основание (договор, ТЗ, график)",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            "number": 3,
            "title": "Перечень принятых конструкций и работ",
            "blocks": [
                {"kind": BLOCK_DOCUMENT_LIST, "key": "accepted_works",
                 "label": "Принятые конструкции, работы и их реквизиты",
                 "source": SOURCE_LINKED, "link_role": "accepted_work",
                 "threshold": 5,
                 "note": "до 5 позиций перечисляются непосредственно; "
                         "более 5 — создаётся реестр"},
            ],
        },
        {
            # ТЗ п.30: в разделе 5 сохраняются два пункта — геодезические
            # исполнительные схемы и результаты экспертиз, обследований,
            # лабораторных и иных испытаний. Документы выбираются из
            # архивов и отображаются как отдельные архивные связи.
            "number": 5,
            "title": "Приложения к акту",
            "blocks": [
                {"kind": BLOCK_DOCUMENT_LIST, "key": "geodetic_schemes",
                 "label": "а) Геодезические исполнительные схемы",
                 "source": SOURCE_LINKED, "link_role": "geodetic_scheme",
                 "threshold": 5,
                 "note": "документы выбираются из архивов и физически не "
                         "копируются (ТЗ п.30)"},
                {"kind": BLOCK_DOCUMENT_LIST, "key": "quality_evidence",
                 "label": "б) Результаты экспертиз, обследований, лабораторных "
                          "и иных испытаний",
                 "source": SOURCE_LINKED, "link_role": "quality_evidence",
                 "threshold": 5,
                 "note": "удаление связи не удаляет сам документ (ТЗ п.30)"},
            ],
        },
        {
            "number": 7,
            "title": "Объём принятых работ",
            "blocks": [
                {"key": "work_volume",
                 "label": "Объём выполненных работ в соответствии с проектом",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            # ТЗ п.31: отдельного раздела «Заключение» в актуальной
            # согласованной структуре нет. Раздел 8 содержит фиксированную
            # формулировку, которая не должна произвольно изменяться.
            "number": 8,
            "title": "Подтверждение соответствия",
            "blocks": [
                {"kind": BLOCK_FIXED_TEXT, "key": "compliance_statement",
                 "label": "Соответствие предъявленных строительных конструкций",
                 "fixed": True,
                 "text": "Предъявленные строительные конструкции соответствуют "
                         "техническим регламентам, нормативным правовым актам "
                         "и проектной документации."},
            ],
        },
        {
            # ТЗ п.32: пункт 9 с решениями а-г — обязательная структура,
            # а не произвольный текстовый блок.
            "number": 9,
            "title": "Решения по итогам приёмки",
            "blocks": [
                {"kind": BLOCK_DECISIONS, "key": "decisions",
                 "label": "Принято решение",
                 "source": SOURCE_MANUAL, "required": True,
                 "choices": [
                     "а) принять и разрешить последующие работы",
                     "б) принять с замечаниями",
                     "в) принять частично, отдельные конструкции не приняты",
                     "г) не принять, работы не выполнены либо выполнены с "
                     "существенными нарушениями",
                 ]},
            ],
        },
        {
            "number": 10,
            "title": "Подписи",
            "blocks": [
                {"key": "customer_rep", "label": "Представитель заказчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CUSTOMER,
                 "label_fill": True, "label_sign": True},
                {"key": "contractor_rep", "label": "Представитель подрядчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CONTRACTOR,
                 "label_fill": True, "label_sign": True},
                {"key": "designer_rep", "label": "Представитель проектной организации",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_DESIGNER,
                 "label_fill": True, "label_sign": True},
            ],
        },
    ],
}

_AOU_SITO: dict = {
    "schema": DEFINITION_SCHEMA,
    "doc_type": domain.DOC_TYPE_AOU_SITO,
    "version": FORM_VERSION,
    "title": "Акт освидетельствования и приёмки устранения скрытых дефектов "
             "нарушений, выявленных при приёмке ответственных конструкций",
    "basis": "приказ Минстроя России № 344/пр (ТЗ п.34)",
    "layout": {
        "paper": PAPER_A4,
        "orientation": ORIENTATION_PORTRAIT,
        "target_pages": "2-4",
    },
    "notes": (
        "АОУСИТО — самостоятельный документ, использующий единые проектные "
        "данные (ТЗ п.34).",
        "Представитель эксплуатирующей организации в АОУСИТО допускается "
        "(ТЗ п.34, 40).",
    ),
    "sections": [
        {
            "number": 1,
            "title": "Сведения об объекте и устранённых нарушениях",
            "blocks": [
                {"key": "object_name", "label": "Наименование объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "address", "label": "Адрес объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "contract_number", "label": "Номер и дата договора",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "violation_basis",
                 "label": "Основание для устранения (акт, письмо, предписание)",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "work_description",
                 "label": "Перечень устранённых нарушений и дефектов",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            "number": 2,
            "title": "Выполненные работы и применённые материалы",
            "blocks": [
                {"kind": BLOCK_DOCUMENT_LIST, "key": "quality_documents",
                 "label": "Документы о качестве материалов и конструкций",
                 "source": SOURCE_LINKED, "link_role": "quality", "threshold": 5},
                {"kind": BLOCK_DOCUMENT_LIST, "key": "correction_evidence",
                 "label": "Документы, подтверждающие устранение",
                 "source": SOURCE_LINKED, "link_role": "quality_evidence",
                 "threshold": 5},
            ],
        },
        {
            "number": 3,
            "title": "Объём выполненных работ",
            "blocks": [
                {"key": "work_volume", "label": "Объём выполненных работ",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "cost", "label": "Стоимость устранения, руб.",
                 "source": SOURCE_MANUAL},
            ],
        },
        {
            "number": 4,
            "title": "Заключение",
            "blocks": [
                {"kind": BLOCK_TEXT, "key": "conclusion",
                 "label": "Заключение: устранённые нарушения сняты / не сняты",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            "number": 5,
            "title": "Подписи",
            "blocks": [
                # ТЗ п.34, 40: представитель эксплуатирующей организации
                # в АОУСИТО допускается.
                {"key": "exploitation_rep",
                 "label": "Представитель эксплуатирующей организации",
                 "source": SOURCE_MANUAL, "required": False,
                 "label_fill": True, "label_sign": True,
                 "note": "роль предусмотрена для АОУСИТО (ТЗ п.34, 40)"},
                {"key": "customer_rep", "label": "Представитель заказчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CUSTOMER,
                 "label_fill": True, "label_sign": True},
                {"key": "contractor_rep", "label": "Представитель подрядчика",
                 "source": SOURCE_REPRESENTATIVE, "role": ROLE_CONTRACTOR,
                 "label_fill": True, "label_sign": True},
            ],
        },
    ],
}

_TEST_ACT: dict = {
    "schema": DEFINITION_SCHEMA,
    "doc_type": domain.DOC_TYPE_TEST_ACT,
    "version": FORM_VERSION,
    "title": "Акт испытания инженерной системы",
    "basis": "СП 73.13330.2016 (ТЗ п.41)",
    "layout": {
        "paper": PAPER_A4,
        "orientation": ORIENTATION_PORTRAIT,
        # ТЗ п.58: для остальных актов целевой вариант — 1 страница A4.
        "target_pages": "1",
    },
    "notes": (
        "Акты испытаний — отдельный тип документов, доступный только во "
        "внутренних и наружных инженерных сетях (ТЗ п.35).",
        "Акт создаётся только по запросу оператора: наличие инженерной "
        "системы не является основанием для его создания (ТЗ п.36).",
        "Форма по СП 73.13330.2016; переключатель между альтернативными "
        "формами не предусмотрен (ТЗ п.41).",
    ),
    "sections": [
        {
            "number": 1,
            "title": "Сведения об испытываемой системе",
            "blocks": [
                {"key": "object_name", "label": "Наименование объекта",
                 "source": SOURCE_PROJECT, "required": True},
                {"key": "address", "label": "Адрес объекта",
                 "source": SOURCE_PROJECT, "required": True},
                # ТЗ п.35: тип испытаний ограничен инженерными сетями.
                {"key": "system_type", "label": "Вид инженерной системы",
                 "source": SOURCE_MANUAL, "required": True,
                 "choices": ["внутренние инженерные сети",
                             "наружные инженерные сети"],
                 "note": "для общестроительных работ акт испытаний не "
                         "используется (ТЗ п.35)"},
                # ТЗ п.37: виды испытаний.
                {"key": "test_kind", "label": "Вид испытания",
                 "source": SOURCE_MANUAL, "required": True,
                 "choices": ["испытания", "промывка", "продувка",
                             "гидравлические испытания",
                             "манометрические испытания",
                             "другое предусмотренное испытание"]},
                {"key": "system_name", "label": "Наименование системы, узла, участка",
                 "source": SOURCE_MANUAL, "required": True},
            ],
        },
        {
            "number": 2,
            "title": "Условия и результаты испытаний",
            "blocks": [
                # ТЗ п.38: дата вводится оператором, система проверяет
                # логическую корректность, но не назначает её.
                {"key": "doc_date", "label": "Дата испытаний",
                 "source": SOURCE_MANUAL, "required": True,
                 "note": "вводится оператором; система не назначает дату "
                         "самостоятельно (ТЗ п.38)"},
                {"key": "conditions", "label": "Условия испытаний",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "instrumentation", "label": "Применённые приборы и оборудование",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "results", "label": "Результаты испытаний",
                 "source": SOURCE_MANUAL, "required": True},
                {"key": "deviations", "label": "Отклонения от нормативных значений",
                 "source": SOURCE_MANUAL},
            ],
        },
        {
            "number": 3,
            "title": "Заключение",
            "blocks": [
                {"kind": BLOCK_DECISIONS, "key": "decisions",
                 "label": "Результат испытаний",
                 "source": SOURCE_MANUAL, "required": True,
                 "choices": [
                     "а) система принята, соответствует требованиям",
                     "б) система принята с замечаниями",
                     "в) система не принята",
                 ]},
            ],
        },
        {
            "number": 4,
            "title": "Участники испытаний и подписи",
            "blocks": [
                # ТЗ п.39: состав участников задаётся оператором отдельно для
                # каждого акта, фиксированного состава нет.
                {"kind": BLOCK_PARTICIPANTS, "key": "participants",
                 "label": "Участники испытаний",
                 "source": SOURCE_MANUAL, "required": True,
                 "label_sign": True,
                 "note": "состав задаётся оператором отдельно для каждого "
                         "акта (ТЗ п.39)"},
                # ТЗ п.40: представитель эксплуатирующей организации
                # предусмотрен в актах испытаний.
                {"key": "exploitation_rep",
                 "label": "Представитель эксплуатирующей организации",
                 "source": SOURCE_MANUAL, "required": False,
                 "label_fill": True, "label_sign": True,
                 "note": "роль предусмотрена в актах испытаний (ТЗ п.40)"},
            ],
        },
    ],
}

# Определения в порядке, в котором они появляются в интерфейсе.
FORM_DEFINITIONS: tuple[dict, ...] = (_AOSR, _AOOK, _AOU_SITO, _TEST_ACT)
