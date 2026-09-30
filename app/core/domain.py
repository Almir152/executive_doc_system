"""Доменные константы из технического задания.

Единственное место, где зафиксированы перечни из ТЗ. Модели, справочники,
валидаторы и печать берут значения отсюда, чтобы не расходились.
"""

# --- Раздел 14. Направления проектов -----------------------------------
DIRECTION_GENERAL = "Общестроительные работы"
DIRECTION_INNER_NETWORKS = "Внутренние инженерные сети"
DIRECTION_OUTER_NETWORKS = "Наружные инженерные сети"
DIRECTIONS = (DIRECTION_GENERAL, DIRECTION_INNER_NETWORKS, DIRECTION_OUTER_NETWORKS)

# --- Раздел 22. Справочник разделов проектной документации --------------
# Справочник расширяемый: это стартовый набор, а не ограничение.
SECTION_KINDS_INITIAL = (
    ("ГП", "Генеральный план"),
    ("АР", "Архитектурный раздел"),
    ("КЖ", "Конструкции железобетонные"),
    ("КМ", "Конструкции металлические"),
    ("ВК", "Внутренние санитарно-технические системы"),
    ("ВВ", "Внутренние системы водоснабжения и водоотведения"),
    ("ОТ", "Отопление, вентиляция и кондиционирование"),
    ("ТС", "Технологические системы"),
    ("НВ", "Наружные сети водоснабжения"),
    ("НК", "Наружные сети канализации"),
    ("ЭС", "Электроснабжение"),
)

# --- Раздел 44. Справочник типов материалов ------------------------------

MATERIAL_TYPES_INITIAL = (
    ("PIPE", "Труба"),
    ("FITTING", "Соединительные детали"),
    ("VALVE", "Запорная арматура"),
    ("CABLE", "Кабельная продукция"),
    ("EQUIPMENT", "Оборудование"),
    ("OTHER", "Прочие материалы"),
)

# --- Раздел 35. Виды актов испытаний ------------------------------------
# Акты испытаний применяются только к инженерным сетям.
DIRECTIONS_WITH_TEST_ACTS = (DIRECTION_INNER_NETWORKS, DIRECTION_OUTER_NETWORKS)

# --- Раздел 37. Виды испытаний -------------------------------------------
TEST_TYPE_INSPECTION = "Испытание"
TEST_TYPE_FLUSHING = "Промывка"
TEST_TYPE_BLOWING = "Продувка"
TEST_TYPE_HYDRAULIC = "Гидравлические испытания"
TEST_TYPE_MANOMETRIC = "Манометрические испытания"
TEST_TYPE_OTHER = "Другое предусмотренное испытание"
TEST_TYPES = (
    TEST_TYPE_INSPECTION,
    TEST_TYPE_FLUSHING,
    TEST_TYPE_BLOWING,
    TEST_TYPE_HYDRAULIC,
    TEST_TYPE_MANOMETRIC,
    TEST_TYPE_OTHER,
)

# --- Раздел 42. Типы нумеруемых документов ------------------------------
DOC_TYPE_AOSR = "АОСР"
DOC_TYPE_AOOK = "АООК"
DOC_TYPE_AOU_SITO = "АОУСИТО"
DOC_TYPE_TEST_ACT = "АКТ_ИСПЫТАНИЙ"

NUMBERED_DOC_TYPES = (
    DOC_TYPE_AOSR,
    DOC_TYPE_AOOK,
    DOC_TYPE_AOU_SITO,
    DOC_TYPE_TEST_ACT,
)

DOC_TYPE_LABELS = {
    DOC_TYPE_AOSR: "АОСР",
    DOC_TYPE_AOOK: "АООК",
    DOC_TYPE_AOU_SITO: "АОУСИТО",
    DOC_TYPE_TEST_ACT: "Акт испытаний",
}

# --- Раздел 85. Состояния документа -------------------------------------
DOC_STATUS_DRAFT = "draft"       # рабочий, до выпуска
DOC_STATUS_ISSUED = "issued"     # выпущен, версия зафиксирована
DOC_STATUSES = (DOC_STATUS_DRAFT, DOC_STATUS_ISSUED)

# --- Раздел 50. Логические части архива ---------------------------------
ARCHIVE_CATEGORY_MATERIALS = "Материалы и документы качества"
ARCHIVE_CATEGORY_SCHEMES = "Исполнительные схемы"
ARCHIVE_CATEGORY_PROTOCOLS = "Протоколы и обследования"
ARCHIVE_CATEGORY_PROJECT = "Архив проекта"
ARCHIVE_CATEGORIES = (
    ARCHIVE_CATEGORY_MATERIALS,
    ARCHIVE_CATEGORY_SCHEMES,
    ARCHIVE_CATEGORY_PROTOCOLS,
    ARCHIVE_CATEGORY_PROJECT,
)

# --- Раздел 45. Виды документов качества ---------------------------------
QUALITY_DOC_PASSPORT = "Паспорт"
QUALITY_DOC_CERTIFICATE = "Сертификат"
QUALITY_DOC_DECLARATION = "Декларация"
QUALITY_DOC_OTHER = "Другой документ качества"
QUALITY_DOC_TYPES = (
    QUALITY_DOC_PASSPORT,
    QUALITY_DOC_CERTIFICATE,
    QUALITY_DOC_DECLARATION,
    QUALITY_DOC_OTHER,
)

# --- Раздел 46. Для каких типов хранятся сроки действия -----------------
# Дата начала/окончания действия обязательна для сертификатов и деклараций.
QUALITY_DOC_TYPES_WITH_VALIDITY = (QUALITY_DOC_CERTIFICATE, QUALITY_DOC_DECLARATION)

# --- Раздел 30 / 47 / 48. Роли связей ------------------------------------
LINK_ROLE_MATERIAL = "Материал"
LINK_ROLE_QUALITY = "Документ качества"
LINK_ROLE_SCHEME = "Исполнительная схема"
LINK_ROLE_PROTOCOL = "Протокол"
LINK_ROLE_ATTACHMENT = "Приложение"
LINK_ROLE_TEST_PROTOCOL = "Протокол испытаний"
LINK_ROLE_SURVEY = "Обследование"
LINK_ROLES = (
    LINK_ROLE_MATERIAL,
    LINK_ROLE_QUALITY,
    LINK_ROLE_SCHEME,
    LINK_ROLE_PROTOCOL,
    LINK_ROLE_ATTACHMENT,
    LINK_ROLE_TEST_PROTOCOL,
    LINK_ROLE_SURVEY,
)

# --- Раздел 40. Роли представителей --------------------------------------
ROLE_EXPLOITATION = "Представитель эксплуатирующей организации"
ROLE_PERFORMER = "Лицо, непосредственно выполнявшее работы"
ROLE_CUSTOMER = "Представитель заказчика"
ROLE_CONTRACTOR = "Представитель подрядчика"
ROLE_DESIGNER = "Проектировщик"
REPRESENTATIVE_ROLES = (
    ROLE_EXPLOITATION,
    ROLE_PERFORMER,
    ROLE_CUSTOMER,
    ROLE_CONTRACTOR,
    ROLE_DESIGNER,
)

# --- Раздел 28 / 79. Порог реестра ---------------------------------------
# До 5 документов перечисляются непосредственно, 5 и более — создаётся реестр.
REGISTER_THRESHOLD = 5
# Для приложений АОСР порог другой: 1-4 напрямую, 5 и более — реестр.
ATTACHMENT_REGISTER_THRESHOLD = 5

# --- Раздел 63. Блоки подписантов ----------------------------------------
SIGN_BLOCK_GIVER = "Сдал"
SIGN_BLOCK_RECEIVER = "Принял"
SIGN_BLOCKS = (SIGN_BLOCK_GIVER, SIGN_BLOCK_RECEIVER)

# --- Раздел 75. Варианты выгрузки ----------------------------------------
EXPORT_VARIANT_ALL = "all"          # все акты -> все схемы -> приложения
EXPORT_VARIANT_BY_ACT = "by_act"    # по каждому АОСР его схемы и приложения
EXPORT_VARIANTS = (EXPORT_VARIANT_ALL, EXPORT_VARIANT_BY_ACT)

EXPORT_VARIANT_LABELS = {
    EXPORT_VARIANT_ALL: "Вариант 1: все акты, все схемы, приложения",
    EXPORT_VARIANT_BY_ACT: "Вариант 2: по каждому АОСР его схемы и приложения",
}

# --- Раздел 24 / 41. Нормативные основания -------------------------------
AOSR_BASIS = "приказ Минстроя России № 344/пр"
AOU_SITO_BASIS = "приказ Минстроя России № 344/пр"
TEST_ACT_BASIS = "СП 73.13330.2016"

# --- Раздел 86. Типы событий истории -------------------------------------
HISTORY_PROJECT_CREATED = "project_created"
HISTORY_DOCUMENT_CREATED = "document_created"
HISTORY_DOCUMENT_ISSUED = "document_issued"
HISTORY_DOCUMENT_UPDATED = "document_updated"
HISTORY_LINK_ADDED = "link_added"
HISTORY_LINK_REMOVED = "link_removed"
HISTORY_ARCHIVE_FILE_ADDED = "archive_file_added"
HISTORY_ARCHIVE_VERSION_ADDED = "archive_version_added"
HISTORY_PACKAGE_EXPORTED = "package_exported"
HISTORY_AI_PROPOSED = "ai_proposed"
HISTORY_AI_ACTION_APPLIED = "ai_action_applied"

# --- Раздел 64. Поведение при незаполненном представителе эксплуатации ---
EXPLOITATION_MISSING_KEEP = "keep"      # оставить пустое место
EXPLOITATION_MISSING_REMOVE = "remove"  # убрать блок из печатной формы
EXPLOITATION_MISSING_CHOICES = (EXPLOITATION_MISSING_KEEP, EXPLOITATION_MISSING_REMOVE)
