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

import copy

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
    SOURCE_SECTION,
)

# Структура формы меняется новой версией (ТЗ п.96).
FORM_VERSION = 2

# АОСР: версия 2 — в пункте 3 появился выбор разделов проекта (ТЗ п.21).
# Версия 3: добавлены сроки работ двумя датами для проверки логических
# зависимостей дат (ТЗ п.43, 87).
FORM_VERSION_SECTIONS = 3

# АОСР: версия 4 — рекомендуемый образец (приложение № 3 к приказу № 344/пр
# в редакции приказа № 369/пр от 23.06.2025): в шапку добавлены реквизиты
# участников, в перечни — приложения к акту, в подписях — представитель
# проектировщика. Текст образца приводится полностью (ТЗ п.24).
FORM_VERSION_AOSR_OFFICIAL = 4

# АОСР: версия 5 — краткий печатный вариант того же образца. ТЗ п.62 запрещает
# сокращать структуру формы ради удобства печати, поэтому объём печати
# изменён структурно и оформлен отдельной версией: полный образец (версия 4)
# остаётся доступен оператору (ТЗ п.24, 96).
FORM_VERSION_AOSR_SHORT = 5

# АОУСИТО и акты испытаний: версия 2. В версии 1 не было указания, что
# незаполненный блок представителя эксплуатации можно убрать из печати, —
# без этого ТЗ п.64 («если это допускается конкретной формой») выполнять
# было нечего. В АОСР и АООК такой возможности нет: роль там не
# предусмотрена (ТЗ п.40). Структура изменена, значит версия новая (п.96).
FORM_VERSION_OMITTABLE_REP = 2

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
    "version": FORM_VERSION_SECTIONS,
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
                 "source": SOURCE_SECTION, "required": True,
                 "note": "оператор выбирает один или несколько разделов (ТЗ п.21)"},
                {"key": "work_period",
                 "label": "Период выполнения работ (с ... по ...)",
                 "source": SOURCE_MANUAL, "required": True},
                # ТЗ п.43, 87: срок работ двумя датами, чтобы систему можно
                # было проверить на логические зависимости с другими актами.
                # Дата документа (doc_date) остаётся отдельной и вводится
                # оператором, автоматически не меняется.
                {"key": "period_start", "type": "date",
                 "label": "Начало периода выполнения работ",
                 "source": SOURCE_MANUAL, "required": True,
                 "note": "дата начала работ; система проверяет её по АООК (ТЗ п.87)"},
                {"key": "period_end", "type": "date",
                 "label": "Окончание периода выполнения работ",
                 "source": SOURCE_MANUAL, "required": True,
                 "note": "дата окончания работ; система проверяет её по АООК (ТЗ п.87)"},
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
            "title": "Основание и срок приёмки",
            "blocks": [
                {"key": "base_documents", "label": "Основание (договор, ТЗ, график)",
                 "source": SOURCE_MANUAL, "required": True},
                # ТЗ п.43, 87: дата окончания АООК не может быть раньше
                # окончания связанного АОСР, начало — позже его начала.
                {"key": "period_start", "type": "date",
                 "label": "Начало периода работ, принятого актом",
                 "source": SOURCE_MANUAL, "required": True,
                 "note": "не может быть позже начала связанного АОСР (ТЗ п.87)"},
                {"key": "period_end", "type": "date",
                 "label": "Окончание периода работ, принятого актом",
                 "source": SOURCE_MANUAL, "required": True,
                 "note": "не может быть раньше окончания связанного АОСР (ТЗ п.87)"},
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
    "version": FORM_VERSION_OMITTABLE_REP,
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
                 "omittable_if_empty": True,
                 "note": "роль предусмотрена для АОУСИТО (ТЗ п.34, 40); "
                         "незаполненный блок можно убрать из печати (ТЗ п.64)"},
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
    "version": FORM_VERSION_OMITTABLE_REP,
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
                 "omittable_if_empty": True,
                 "note": "роль предусмотрена в актах испытаний (ТЗ п.40); "
                         "незаполненный блок можно убрать из печати (ТЗ п.64)"},
            ],
        },
    ],
}

# Реквизиты участников по рекомендуемому образцу: шапка акта перечисляет
# их дословно, поэтому перечень приводится в примечании поля, а не выдумывается.
AOSR_ORGANISATION_REQUISITES = (
    "фамилия, имя, отчество (последнее — при наличии), адрес места жительства, "
    "ОГРНИП, ИНН индивидуального предпринимателя; полное и (или) сокращенное "
    "наименование, ОГРН, ИНН, адрес юридического лица в пределах его места "
    "нахождения, телефон или факс; полное и (или) сокращенное наименование, "
    "ОГРН, ИНН саморегулируемой организации, членом которой является лицо "
    "(за исключением случаев, когда членство в СРО не требуется)"
)

AOSR_DESIGNER_REQUISITES = (
    "фамилия, имя, отчество (последнее — при наличии), адрес места жительства, "
    "ОГРНИП, ИНН индивидуального предпринимателя; полное и (или) сокращенное "
    "наименование, ОГРН, ИНН, адрес юридического лица в пределах его места "
    "нахождения, телефон или факс; наименование СРО в области "
    "архитектурно-строительного проектирования, членом которой является лицо "
    "(за исключением случаев, когда членство в СРО не требуется)"
)


def _aosr_by_official_sample(version: int, *, short: bool) -> dict:
    """АОСР по рекомендуемому образцу (ТЗ п.24).

    Образец — приложение № 3 к приказу Минстроя России № 344/пр от
    16.05.2023 в редакции приказа № 369/пр от 23.06.2025. Его шапка и
    заключительная часть перечисляют реквизиты участников дословно, поэтому
    они приводятся текстом, а не собираются системой.

    Нумерация разделов и ключи полей сохранены от предыдущей версии: их
    смысл задан ТЗ (п.28 — перечни документов в разделе 3, п.27 — ручной
    пункт 7, п.26 — НРС), и перенумеровывать их при добавлении реквизитов
    означало бы разорвать эту связь.

    Различие между вариантами только в объёме печати (ТЗ п.62): полный образец
    содержит реквизиты участников и приложения к акту, краткий — нет.
    """
    definition = copy.deepcopy(_AOSR)
    definition["version"] = version
    definition["basis"] = (
        "приказ Минстроя России № 344/пр от 16.05.2023, приложение № 3 "
        "(рекомендуемый образец), в редакции приказа Минстроя России "
        "№ 369/пр от 23.06.2025 (ТЗ п.24)"
    )
    definition["notes"] = (
        "Структура соответствует рекомендуемому образцу: шапка перечисляет "
        "объект капитального строительства и участников с их реквизитами, "
        "затем идут пункты 1-7 образца и подписи пяти представителей.",
        "Текст рекомендуемого образца приводится полностью; сокращённая версия "
        "оформлена отдельной версией формы, а не сокращением при печати "
        "(ТЗ п.62, 96).",
    )
    sections = definition["sections"]

    if short:
        definition["title"] = (
            "Акт освидетельствования скрытых работ (краткий печатный вариант)"
        )
        # Целевой объём п.56 обеспечивается именно этим вариантом.
        definition["layout"]["target_pages"] = "2"
        # Проектировщика в разделе 1 нет, а в образце он есть: без этого
        # раздела печать перескакивает с 1 на 3.
        _insert_section(sections, {
            "number": 2,
            "title": "Сведения об участниках",
            "blocks": [
                {"key": "designer_org", "label": "Проектная организация",
                 "source": SOURCE_PROJECT, "role": ROLE_DESIGNER,
                 "note": "реквизиты участников приводятся в полном образце "
                         "(версия 4)"},
            ],
        }, before=3)
        return definition

    definition["title"] = (
        "Акт освидетельствования скрытых работ (рекомендуемый образец)"
    )
    # Полный образец с реквизитами участников и приложениями в две страницы
    # A4 не укладывается; двухстраничный вариант доступен как версия 5.
    definition["layout"]["target_pages"] = "3-4"

    header = {
        "number": 2,
        "title": "Сведения об участниках (шапка акта)",
        "blocks": [
            {"kind": BLOCK_TEXT, "key": "customer_requisites",
             "label": "Застройщик, технический заказчик, лицо, ответственное за "
                       "эксплуатацию здания, сооружения, или региональный оператор",
             "source": SOURCE_MANUAL, "required": True, "label_sign": True,
             "note": f"указывается: {AOSR_ORGANISATION_REQUISITES}"},
            {"kind": BLOCK_TEXT, "key": "contractor_requisites",
             "label": "Лицо, осуществляющее строительство, реконструкцию, "
                       "капитальный ремонт",
             "source": SOURCE_MANUAL, "required": True, "label_sign": True,
             "note": f"указывается: {AOSR_ORGANISATION_REQUISITES}"},
            {"kind": BLOCK_TEXT, "key": "designer_requisites",
             "label": "Лицо, осуществляющее подготовку проектной документации",
             "source": SOURCE_MANUAL, "required": False, "label_sign": True,
             "note": f"указывается в случае привлечения застройщиком: "
                     f"{AOSR_DESIGNER_REQUISITES}"},
        ],
    }
    _insert_section(sections, header, before=3)

    _prepend_block(sections, 3, {
        "kind": BLOCK_TEXT, "key": "project_docs",
        "label": "Реквизиты проектной и (или) рабочей документации: номер, "
                 "реквизиты чертежа, наименование",
        "source": SOURCE_MANUAL, "required": True,
        "note": "пункт 2 образца: сведения о лицах, осуществляющих подготовку "
                "раздела проектной документации, указываются в шапке акта",
    })
    _append_block(sections, 4, {
        "kind": BLOCK_DOCUMENT_LIST, "key": "certificates",
        "label": "Реквизиты сертификатов и (или) других документов, "
                 "подтверждающих качество и безопасность материалов",
        "source": SOURCE_LINKED, "link_role": "quality", "threshold": 5,
    })
    _append_block(sections, 5, {
        "kind": BLOCK_TEXT, "key": "compliance_basis",
        "label": "Работы выполнены в соответствии с",
        "source": SOURCE_MANUAL, "required": True,
        "note": "наименования и структурные единицы технических регламентов, "
                "иные нормативные правовые акты, разделы проектной и (или) "
                "рабочей документации (пункт 6 образца)",
    })
    _append_block(sections, 7, {
        "kind": BLOCK_DOCUMENT_LIST, "key": "attachments",
        "label": "Приложения к акту",
        "source": SOURCE_LINKED, "link_role": "attachment", "threshold": 5,
        "note": "исполнительные схемы и чертежи, результаты экспертиз, "
                "обследований, лабораторных и иных испытаний",
    })
    _append_block(sections, 7, {
        "key": "exemplars", "label": "Акт составлен в количестве экземпляров",
        "source": SOURCE_MANUAL,
        "note": "заполняется при оформлении акта на бумажном носителе",
    })
    conclusion = _section_of(sections, 9)
    conclusion["title"] = "Разрешается производство последующих работ"
    for block in conclusion["blocks"]:
        if block.get("key") == "conclusion":
            block["label"] = (
                "Наименования работ, строительных конструкций, участков сетей "
                "инженерно-технического обеспечения, производство которых "
                "разрешается"
            )
    _append_block(sections, 10, {
        "key": "designer_rep",
        "label": "Представитель лица, осуществляющего подготовку проектной "
                 "документации",
        "source": SOURCE_REPRESENTATIVE, "role": ROLE_DESIGNER,
        "label_fill": True, "label_sign": True,
        "note": "указывается в случае привлечения застройщиком лица, "
                "осуществляющего подготовку проектной документации",
    })
    return definition


def _section_of(sections: list[dict], number: int) -> dict:
    for section in sections:
        if section["number"] == number:
            return section
    raise AssertionError(f"в описании формы нет раздела {number}")


def _insert_section(sections: list[dict], section: dict, *, before: int) -> None:
    position = next(
        index for index, item in enumerate(sections) if item["number"] >= before
    )
    sections.insert(position, section)


def _prepend_block(sections: list[dict], number: int, block: dict) -> None:
    _section_of(sections, number)["blocks"].insert(0, block)


def _append_block(sections: list[dict], number: int, block: dict) -> None:
    _section_of(sections, number)["blocks"].append(block)


# Полный официальный образец и краткий печатный вариант — две версии одной
# формы. Краткая версия объявлена последней и становится актуальной: печать по
# умолчанию укладывается в целевые две страницы (ТЗ п.56), а полный образец
# оператор выбирает в панели формы документа (ТЗ п.24).
_AOSR_OFFICIAL = _aosr_by_official_sample(FORM_VERSION_AOSR_OFFICIAL, short=False)
_AOSR_SHORT = _aosr_by_official_sample(FORM_VERSION_AOSR_SHORT, short=True)

# Определения в порядке, в котором они появляются в интерфейсе.
FORM_DEFINITIONS: tuple[dict, ...] = (
    _AOSR,
    _AOSR_OFFICIAL,
    _AOSR_SHORT,
    _AOOK,
    _AOU_SITO,
    _TEST_ACT,
)
