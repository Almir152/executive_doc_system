"""Печатная форма документа. ТЗ п.55–62, 42, 43, 63, 64, 28, 30, 79, 81.

Вёрстка задана ТЗ и не подстраивается под содержимое: поля, шрифты и
размеры зафиксированы, а разделы выводятся ровно в том порядке и составе,
которые заданы описанием нормативной формы (ТЗ п.62). Уменьшение шрифта ради
«ровно двух страниц» не применяется принципиально (ТЗ п.56).

Объём страниц из ТЗ (2 для АОСР, 2–4 для АООК и АОУСИТО, 1 для прочих актов)
— цель вёрстки, а не условие проверки: структура формы важнее числа
страниц (ТЗ п.56, 62).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, PageBreak, Paragraph,
    Spacer, Table, TableStyle,
)
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services import form_service, issue_service
from app.db.models import (
    ArchiveDocument, Document, DocumentArchiveLink, Project, SignatureBlock,
)

# --- ТЗ п.60. Поля страницы, мм -----------------------------------------
MARGIN_TOP_MM = 20
MARGIN_LEFT_MM = 10
MARGIN_RIGHT_MM = 10
MARGIN_BOTTOM_MM = 10
# Допустимая автоматическая корректировка полей, мм (ТЗ п.60).
MARGIN_ADJUST_PLUS_MM = 2
MARGIN_ADJUST_MINUS_MM = 4

# --- ТЗ п.59. Шрифты -----------------------------------------------------
BASE_FONT_SIZE = 11          # базовый размер шаблонной фразы
FILL_FONT_SIZE = 11          # заполняемый текст
# Допустимые по ТЗ п.59 гарнитуры: Calibri, Arial, ISO, Times New Roman.
# Times New Roman — по умолчанию. На машине без него подставляется
# метрически совместимый шрифт: вёрстка по полям от этого не «плывёт».
FONT_FAMILY = "FormFont"
# Гарнитуры по ТЗ п.59: Times New Roman по умолчанию, при его отсутствии —
# метрически совместимый Liberation Serif либо DejaVu Serif. Имена файлов
# искать приходится самостоятельно: раскладка каталогов со шрифтами
# различается у дистрибутивов Linux и у Windows.
FONT_CANDIDATES = (
    ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf",
     "LiberationSerif-Italic.ttf", "LiberationSerif-BoldItalic.ttf"),
    ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf",
     "DejaVuSerif-Oblique.ttf", "DejaVuSerif-BoldOblique.ttf"),
    ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
)
# Где искать шрифты: системные каталоги Windows и Linux, каталог
# пользователя и каталог reportlab как последний довод.
_FONT_DIRS = (
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts/liberation",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/dejavu",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
)
_FONT_DIR_DEPTH = 3
_font_found: dict = {}

# --- ТЗ п.61. Размеры заголовков ----------------------------------------
HEADING_MIN_PT = 10
HEADING_MAX_PT = 14
SECONDARY_MIN_PT = 6
SECONDARY_MAX_PT = 9
# Заголовок жирный курсив и на 2–4 pt больше базового размера (ТЗ п.61).
HEADING_EXTRA_MIN_PT = 2
HEADING_EXTRA_MAX_PT = 4

# Маркеры выбора решения. Только ASCII: символы «☒/☐» отсутствуют, например,
# в Liberation Serif и в части системных Times New Roman, и на листе
# получились бы пустые квадраты вместо отметки. Буквы и скобки есть в любой
# из гарнитур, разрешённых ТЗ п.59.
MARK_UNCHECKED = "[ ]"
MARK_CHECKED = "[ X ]"
EMPTY_PLACEHOLDER = "__________"


class PrintError(Exception):
    """Ошибка печати с текстом для оператора."""


# =====================================================================
# ШРИФТЫ (ТЗ п.59)
# =====================================================================

_font_ready = False
_font_source = ""


def _vera_dir() -> Path:
    return Path(os.path.dirname(__import__("reportlab").__file__)) / "fonts"


def _find_font(name: str) -> str | None:
    """Найти файл шрифта по имени в каталогах системы.

    Прямой список путей ненадёжен: у разных дистрибутивов шрифты лежат в
    разных местах, поэтому сначала проверяются типовые каталоги, затем —
    ограниченный обход вглубь.
    """
    if name in _font_found:
        return _font_found[name]
    for directory in _FONT_DIRS:
        if not directory:
            continue
        path = os.path.join(directory, name)
        if os.path.exists(path):
            _font_found[name] = path
            return path
    for directory in _FONT_DIRS:
        if not directory or not os.path.isdir(directory):
            continue
        root = Path(directory)
        try:
            for depth in range(_FONT_DIR_DEPTH):
                base = root if depth == 0 else root
                pattern = "/".join(["*"] * (depth + 1)) + "/" + name
                for found in base.glob(pattern):
                    _font_found[name] = str(found)
                    return str(found)
        except OSError:  # pragma: no cover — каталог шрифтов недоступен
            continue
    _font_found[name] = None
    return None


def _register_family(regular: str, bold: str, italic: str, bold_italic: str) -> None:
    """Зарегистрировать начертания гарнитуры печати (ТЗ п.59)."""
    pdfmetrics.registerFont(TTFont(FONT_FAMILY, regular))
    pdfmetrics.registerFont(TTFont(FONT_FAMILY + "-Bold", bold))
    pdfmetrics.registerFont(TTFont(FONT_FAMILY + "-Italic", italic))
    pdfmetrics.registerFont(TTFont(FONT_FAMILY + "-BoldItalic", bold_italic))
    pdfmetrics.registerFontFamily(
        FONT_FAMILY, normal=FONT_FAMILY, bold=FONT_FAMILY + "-Bold",
        italic=FONT_FAMILY + "-Italic", boldItalic=FONT_FAMILY + "-BoldItalic",
    )


def register_fonts() -> str:
    """Зарегистрировать гарнитуру печати; вернуть путь к выбранному шрифту.

    Без шрифта с кириллицей PDF получился бы из квадратов, поэтому
    подходящий файл ищется обязательно, а в крайнем случае берётся
    Bitstream Vera из поставки reportlab.
    """
    global _font_ready, _font_source
    if _font_ready:
        return _font_source

    for names in FONT_CANDIDATES:
        paths = [_find_font(name) for name in names]
        if not paths[0]:
            continue
        try:
            _register_family(
                paths[0], paths[1] or paths[0], paths[2] or paths[0],
                paths[3] or paths[0],
            )
        except Exception:  # pragma: no cover — битый файл шрифта
            continue
        _font_ready = True
        _font_source = paths[0]
        return paths[0]

    vera = _vera_dir()
    try:
        _register_family(
            str(vera / "Vera.ttf"), str(vera / "VeraBd.ttf"),
            str(vera / "VeraIt.ttf"), str(vera / "VeraBI.ttf"),
        )
    except Exception as exc:  # pragma: no cover — повреждённая поставка
        raise PrintError(
            "Не найден шрифт с поддержкой кириллицы. Установите Times New Roman "
            "или Bitstream Vera — печать без него невозможна (ТЗ п.59)."
        ) from exc
    _font_ready = True
    _font_source = str(vera / "Vera.ttf")
    return _font_source


def font_source() -> str:
    """Путь к фактически используемому шрифту (для диагностики и тестов)."""
    register_fonts()
    return _font_source


def styles() -> dict:
    """Стили печати. Открыто для сервисов комплекта (реестр выгрузки)."""
    return _styles()


def escape(text) -> str:
    """Экранировать текст для reportlab."""
    return _esc(text)


def _styles() -> dict:
    """Стили печати по ТЗ п.59, 61.

    Размеры заданы константами ТЗ, а не «подгоняются» под объём данных:
    уменьшение текста ради числа страниц запрещено (ТЗ п.56, 59).
    """
    register_fonts()
    heading_size = BASE_FONT_SIZE + HEADING_EXTRA_MIN_PT
    secondary_size = SECONDARY_MAX_PT
    return {
        "title": ParagraphStyle(
            "title", fontName=FONT_FAMILY + "-BoldItalic",
            fontSize=HEADING_MAX_PT - 1, leading=HEADING_MAX_PT,
            alignment=TA_CENTER, spaceAfter=4,
        ),
        "heading": ParagraphStyle(
            "heading", fontName=FONT_FAMILY + "-BoldItalic",
            fontSize=heading_size, leading=heading_size + 3,
            alignment=TA_JUSTIFY, spaceBefore=8, spaceAfter=4,
        ),
        # Шаблонная фраза формы: базовый размер, regular (ТЗ п.59).
        "label": ParagraphStyle(
            "label", fontName=FONT_FAMILY, fontSize=BASE_FONT_SIZE,
            leading=BASE_FONT_SIZE + 3, alignment=TA_JUSTIFY,
        ),
        # Заполняемый текст: тот же размер, regular italic (ТЗ п.59).
        "fill": ParagraphStyle(
            "fill", fontName=FONT_FAMILY + "-Italic", fontSize=FILL_FONT_SIZE,
            leading=FILL_FONT_SIZE + 3, alignment=TA_JUSTIFY,
        ),
        "secondary": ParagraphStyle(
            "secondary", fontName=FONT_FAMILY, fontSize=secondary_size,
            leading=secondary_size + 2, alignment=TA_JUSTIFY,
        ),
        "cell": ParagraphStyle(
            "cell", fontName=FONT_FAMILY, fontSize=BASE_FONT_SIZE - 1,
            leading=BASE_FONT_SIZE + 1, alignment=TA_LEFT,
        ),
        "center": ParagraphStyle(
            "center", fontName=FONT_FAMILY, fontSize=BASE_FONT_SIZE,
            leading=BASE_FONT_SIZE + 3, alignment=TA_CENTER,
        ),
        "sign": ParagraphStyle(
            "sign", fontName=FONT_FAMILY, fontSize=BASE_FONT_SIZE,
            leading=BASE_FONT_SIZE + 4, alignment=TA_LEFT,
        ),
    }


# =====================================================================
# НУМЕРАЦИЯ СТРАНИЦ (ТЗ п.81)
# =====================================================================

class _NumberedCanvas:
    """Сквозная нумерация страниц внизу справа, без слова «стр.» (ТЗ п.81).

    Нумерация относится к выгружаемому PDF и не меняет исходную форму
    документа (ТЗ п.81).
    """

    def __new__(cls, path, pagesize=A4, number_pages: bool = True, margin_mm: float = MARGIN_RIGHT_MM):
        from reportlab.pdfgen import canvas as canvas_module

        class Canvas(canvas_module.Canvas):
            def __init__(self):
                super().__init__(str(path), pagesize=pagesize)
                self._saved_states = []

            def showPage(self):
                self._saved_states.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._saved_states)
                for state in self._saved_states:
                    self.__dict__.update(state)
                    if number_pages:
                        self._draw_number(total)
                    super().showPage()
                super().save()

            def _draw_number(self, total):
                page = self._pageNumber
                if total <= 1 and not number_pages:
                    return
                self.setFont(FONT_FAMILY, SECONDARY_MAX_PT)
                self.drawRightString(
                    pagesize[0] - margin_mm * mm, margin_mm * mm / 2 + 2, str(page)
                )

        return Canvas()


# =====================================================================
# СБОРКА ФОРМЫ
# =====================================================================

def _esc(text) -> str:
    """Экранировать текст для reportlab."""
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _format_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def _project_value(project: Project, key: str) -> str:
    """Реквизит из карточки проекта (ТЗ п.17)."""
    if key == "object_name":
        return project.title or ""
    if key == "address":
        return project.address or ""
    if key == "customer_org":
        return project.customer.short_name if project.customer else ""
    if key == "general_contractor_org":
        return project.general_contractor.short_name if project.general_contractor else ""
    return ""


def _archive_label(document: ArchiveDocument) -> str:
    """Наименование архивного документа для перечня в форме (ТЗ п.30)."""
    parts = [document.original_name or ""]
    if document.number:
        parts.append(f"№ {document.number}")
    if document.doc_date:
        parts.append(f"от {_format_date(document.doc_date)}")
    return " ".join(p for p in parts if p).strip()


def _linked_documents(
    db: Session, document: Document, identifier: str
) -> list[DocumentArchiveLink]:
    """Связи документа по роли, названной в описании формы.

    Описание формы и база хранят роль по-разному, поэтому роль из описания
    переводится в доменную: без перевода перечень вышел бы пустым при любых
    привязанных документах (ТЗ п.30, 47).
    """
    roles = domain.link_roles_for_form(identifier)
    return sorted(
        (link for link in document.archive_links if link.link_role in roles),
        key=lambda link: (link.order_no, link.archive_document.number or ""),
    )


def _signature_blocks(db: Session, document_id: int) -> list[SignatureBlock]:
    return [
        block for block in (
            form_service.get_signature_block(db, document_id, name)
            for name in form_service.SIGNATURE_BLOCKS
        )
        if block is not None
    ]


def _value_of(db: Session, document: Document, block: dict, payload: dict) -> str:
    """Значение поля формы для печати.

    Источники данных те же, что и в редакторе (ТЗ п.62): карточка проекта,
    ручной ввод, разделы, представители. Незаполненное поле печатается
    пустым местом, а не пропускается (ТЗ п.64).
    """
    key = block.get("key")
    raw = payload.get(key)
    # Значение, введённое оператором в форму, важнее данных карточки: печать
    # обязана показать то, что заполнено в документе (ТЗ п.17, 62). Карточка
    # подставляется только там, где поле оставлено пустым.
    if raw is None and block.get("source") == "project":
        raw = _project_value(document.project, key)
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(item).strip() for item in raw if str(item).strip())
    if raw is None:
        return ""
    return str(raw).strip()


def _block_flowables(
    db: Session, document: Document, block: dict, payload: dict, styles: dict
) -> list:
    """Поток элементов одного блока формы.

    Структура блока не перестраивается: ТЗ запрещает менять нормативную
    форму ради удобства или числа страниц (ТЗ п.62).
    """
    kind = block.get("kind", "value")
    label = block.get("label") or block.get("key") or ""
    flow: list = []

    if kind == "fixed_text":
        # Фиксированная формулировка формы: не изменяется оператором
        # (ТЗ п.31, 62).
        flow.append(Paragraph(_esc(label), styles["label"]))
        flow.append(Paragraph(_esc(block.get("text", "")), styles["label"]))
        return flow

    if kind == "document_list":
        return _document_list_flowables(db, document, block, styles)

    if kind == "decisions":
        flow.append(Paragraph(_esc(label), styles["label"]))
        chosen = str(payload.get(block.get("key")) or "").strip()
        for choice in block.get("choices", []):
            mark = MARK_CHECKED if str(choice) == chosen else MARK_UNCHECKED
            flow.append(Paragraph(f"{mark} {_esc(choice)}", styles["fill"]))
        if not chosen:
            flow.append(Paragraph(
                "Решение не выбрано оператором.", styles["secondary"]
            ))
        return flow

    if kind == "participants":
        return _participants_flowables(db, document, block, payload, styles)

    if kind == "text":
        # Зона для рукописного заполнения (ТЗ п.27: пункт 7 заполняется
        # оператором, система не подменяет его содержимое). Введённый текст
        # печатается, незаполненная зона остаётся пустыми строками.
        flow.append(Paragraph(_esc(label), styles["label"]))
        value = _value_of(db, document, block, payload)
        if value:
            flow.append(Paragraph(_esc(value), styles["fill"]))
        else:
            for _ in range(2):
                flow.append(Paragraph(EMPTY_PLACEHOLDER, styles["fill"]))
        return flow

    value = _value_of(db, document, block, payload)
    flow.append(Paragraph(_esc(label), styles["label"]))
    if value:
        flow.append(Paragraph(_esc(value), styles["fill"]))
    elif block.get("source") != "project" or block.get("required"):
        # Пустое место остаётся: ручной ввод при печати (ТЗ п.64).
        flow.append(Paragraph(EMPTY_PLACEHOLDER, styles["fill"]))
    return flow


def _document_list_flowables(
    db: Session, document: Document, block: dict, styles: dict
) -> list:
    """Перечень связанных архивных документов (ТЗ п.30, 47, 48).

    Документы перечисляются как архивные связи и не копируются (ТЗ п.30,
    92). При количестве сверх порога выводится реестр (ТЗ п.28, 79).
    """
    links = _linked_documents(db, document, block.get("link_role") or "")
    threshold = block.get("threshold") or domain.REGISTER_THRESHOLD
    label = block.get("label") or ""
    flow: list = [Paragraph(_esc(label), styles["label"])]

    if not links:
        flow.append(Paragraph("Документы не привязаны.", styles["secondary"]))
        return flow

    if len(links) > threshold:
        # Большое количество документов: реестр (ТЗ п.28, 79).
        flow.append(Paragraph(
            f"Реестр документов ({len(links)}):", styles["label"]
        ))
    rows = []
    for index, link in enumerate(links, start=1):
        rows.append([
            Paragraph(str(index), styles["cell"]),
            Paragraph(_esc(_archive_label(link.archive_document)), styles["cell"]),
        ])
    table = Table(rows, colWidths=[12 * mm, None], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    flow.append(table)
    return flow


def _participants_flowables(
    db: Session, document: Document, block: dict, payload: dict, styles: dict
) -> list:
    """Участники испытаний и подписи (ТЗ п.39, 40, 63).

    Состав задаётся оператором для каждого акта отдельно; фиксированного
    состава в системе нет (ТЗ п.39).
    """
    label = block.get("label") or ""
    flow: list = [Paragraph(_esc(label), styles["label"])]
    raw = payload.get(block.get("key"))
    items = []
    if isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw if str(item).strip()]
    elif isinstance(raw, str) and raw.strip():
        items = [line.strip() for line in raw.splitlines() if line.strip()]

    if items:
        for index, item in enumerate(items, start=1):
            flow.append(Paragraph(f"{index}. {_esc(item)}", styles["fill"]))
    else:
        for _ in range(3):
            flow.append(Paragraph(f"{EMPTY_PLACEHOLDER}  ____________________", styles["fill"]))
    return flow


def _signatures_flowables(
    db: Session, document: Document, styles: dict
) -> list:
    """Два независимых блока подписантов (ТЗ п.63)."""
    flow: list = []
    for block in _signature_blocks(db, document.id):
        flow.append(Paragraph(_esc(block.block), styles["heading"]))
        rows = [
            ["Должность", block.position or EMPTY_PLACEHOLDER],
            ["ФИО", block.full_name or EMPTY_PLACEHOLDER],
            ["Место подписи", block.sign_place or EMPTY_PLACEHOLDER],
        ]
        table = Table(
            [[Paragraph(_esc(left), styles["cell"]), Paragraph(_esc(right), styles["fill"])]
             for left, right in rows],
            colWidths=[40 * mm, None], hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.lightgrey),
        ]))
        flow.append(table)
        flow.append(Spacer(1, 4))
    return flow


def printable_payload(db: Session, document: Document) -> tuple[dict, bool]:
    """Содержимое для печати и признак, что это выпущенная версия.

    На печать идёт то, что оператор видит: рабочая редакция, если она
    открыта, иначе зафиксированная версия выпуска (ТЗ п.85, 93). Для
    комплекта правило другое — там выгружается зафиксированное состояние
    (ТЗ п.91), за него отвечает form_service.actual_payload.
    """
    draft = form_service.load_draft(db, document.id)
    if draft:
        return draft, False
    fixed = [v for v in issue_service.list_versions(db, document.id) if v.issued_at]
    if fixed:
        return dict(fixed[-1].payload), True
    return {}, False


def _document_story(db: Session, document: Document, styles: dict) -> list:
    """Печатная форма одного документа по его нормативному описанию.

    Разделы выводятся в порядке описания и целиком: пропуск или перестановка
    раздела изменили бы нормативную форму (ТЗ п.62).
    """
    definition = form_service.current_definition(db, document) or {}
    payload, printed_issued = printable_payload(db, document)
    story: list = []

    story.append(Paragraph(
        _esc(definition.get("title") or document.type_label), styles["title"]
    ))
    # Реквизиты: номер и дата документа (ТЗ п.42, 43). Дата не подставляется:
    # её вводит оператор, иначе в печати появилось бы «сегодня».
    date_text = _format_date(document.doc_date) or "дата не указана"
    if printed_issued:
        status_text = "выпущен"
    elif issue_service.issued_versions(db, document.id):
        status_text = "рабочая редакция, не выпущена"
    else:
        status_text = "рабочий документ"
    story.append(Paragraph(
        f"{_esc(document.type_label)} № {_esc(document.number)} от {_esc(date_text)} "
        f"({_esc(status_text)})", styles["center"]
    ))
    if definition.get("basis"):
        story.append(Paragraph(_esc(definition["basis"]), styles["center"]))
    story.append(Spacer(1, 6))

    for section in definition.get("sections", []):
        number = section.get("number")
        title = section.get("title") or ""
        heading = f"{number}. {title}" if number is not None else title
        section_flow: list = [Paragraph(_esc(heading), styles["heading"])]
        blocks = list(section.get("blocks") or []) + list(section.get("fields") or [])
        for block in blocks:
            if not block.get("key") and block.get("kind") != "fixed_text":
                continue
            section_flow.extend(_block_flowables(db, document, block, payload, styles))
        # Раздел с подписями выводится из блоков подписантов, а не из полей
        # формы: в описании он есть, но подписанты хранятся отдельно (п.63).
        if any(block.get("label") == domain.SIGN_BLOCK_GIVER for block in blocks) or \
                "подписи" in (title or "").lower():
            section_flow.extend(_signatures_flowables(db, document, styles))
        # Короткий раздел не должен разрываться между страницами; длинный
        # разрывается сам — иначе на страницу не поместилось бы ничего.
        if len(section_flow) <= 4:
            story.append(KeepTogether(section_flow))
        else:
            story.extend(section_flow)

    # Решение по п.64 применяется к печатной форме: убранный блок в PDF не
    # выводится, оставленное пустое место — выводится.
    story.append(Spacer(1, 8))
    story.append(Paragraph(_esc(_footer_basis(document, definition)), styles["secondary"]))
    return story


def _footer_basis(document: Document, definition: dict) -> str:
    return (
        f"Форма: {definition.get('title') or document.type_label}"
        f"{' версия ' + str(definition['version']) if definition.get('version') else ''}."
        " Структура формы соответствует нормативному описанию и не изменяется "
        "программой (ТЗ п.62)."
    )


def page_size(definition: dict):
    """Размер и ориентация страницы по описанию формы (ТЗ п.55)."""
    layout = (definition or {}).get("layout") or {}
    orientation = (layout.get("orientation") or "portrait").lower()
    if orientation in ("landscape", "альбомная"):
        return landscape(A4)
    return A4


def render_documents_pdf(
    db: Session,
    documents: list[Document],
    output_path: Path | str,
    *,
    page_numbers: bool = False,
) -> Path:
    """Собрать PDF по документам. ТЗ п.55–62, 81.

    Несколько документов печатаются в один файл: так сквозная нумерация
    страниц идёт по всему комплекту (ТЗ п.81).
    """
    if not documents:
        raise PrintError("Не выбрано ни одного документа для печати.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()

    stories: list = []
    for index, document in enumerate(documents):
        if index:
            stories.append(PageBreak())
        stories.extend(_document_story(db, document, styles))

    definition = form_service.current_definition(db, documents[0]) or {}
    size = page_size(definition)
    doc = BaseDocTemplate(
        str(output_path), pagesize=size,
        leftMargin=MARGIN_LEFT_MM * mm, rightMargin=MARGIN_RIGHT_MM * mm,
        topMargin=MARGIN_TOP_MM * mm, bottomMargin=MARGIN_BOTTOM_MM * mm,
        title=definition.get("title") or "Печатная форма",
        author="Исполнительная документация",
    )
    frame = Frame(
        MARGIN_LEFT_MM * mm, MARGIN_BOTTOM_MM * mm,
        size[0] - (MARGIN_LEFT_MM + MARGIN_RIGHT_MM) * mm,
        size[1] - (MARGIN_TOP_MM + MARGIN_BOTTOM_MM) * mm,
        id="form",
    )
    doc.addPageTemplates([PageTemplate(id="form", frames=[frame])])
    doc.build(
        stories,
        canvasmaker=lambda *args, **kwargs: _NumberedCanvas(
            output_path, pagesize=size, number_pages=page_numbers
        ),
    )
    return output_path


def render_project_report(
    db: Session, project: Project, output_path: Path | str
) -> Path:
    """Ведомость состава проекта по рабочему дереву (ТЗ п.16).

    ТЗ требует дерево «Акты испытаний / АОСР / АООК / АОУСИТО / Связанные
    документы / Комплекты / История» (ТЗ п.16) и печать документов по формам
    (ТЗ п.55–62). Отдельного требования печатать само дерево в ТЗ нет, поэтому
    это внутренний отчёт оператора: состав проекта на бумаге. Поля и шрифты —
    те же, что у печатных форм (ТЗ п.59, 60).
    """
    from app.core.services import package_service

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()

    documents = (
        db.query(Document)
        .filter(Document.project_id == project.id)
        .order_by(Document.doc_type, Document.number, Document.id)
        .all()
    )
    by_type: dict[str, list[Document]] = {}
    for document in documents:
        by_type.setdefault(document.doc_type, []).append(document)

    story: list = [
        Paragraph("СОСТАВ ПРОЕКТА", styles["title"]),
        Paragraph(_esc(project.title or ""), styles["center"]),
        Paragraph(_esc(project.address or ""), styles["center"]),
        Spacer(1, 6),
    ]

    for doc_type in domain.NUMBERED_DOC_TYPES:
        items = by_type.get(doc_type, [])
        story.append(Paragraph(_esc(doc_type), styles["heading"]))
        if not items:
            story.append(Paragraph("документов нет", styles["fill"]))
            continue
        for document in items:
            version = form_service.actual_version(db, document.id)
            issued = version is not None and version.issued_at is not None
            story.append(Paragraph(
                f"{_esc(document.number or 'без номера')} — "
                f"{'выпущен' if issued else 'черновик'}"
                + (f", дата {_esc(_format_date(document.doc_date))}"
                   if document.doc_date else ""),
                styles["fill"],
            ))

    schemes = _archive_by_role(db, project.id, domain.LINK_ROLE_SCHEME)
    others = _archive_by_role(db, project.id, domain.LINK_ROLE_ATTACHMENT)
    story.append(Paragraph("Связанные документы", styles["heading"]))
    if schemes:
        for name in schemes:
            story.append(Paragraph(f"исполнительная схема: {_esc(name)}",
                                    styles["fill"]))
    for name in others:
        story.append(Paragraph(f"приложение: {_esc(name)}", styles["fill"]))
    if not schemes and not others:
        story.append(Paragraph("файлы не привязаны", styles["fill"]))

    packages = package_service.list_packages(db, project.id)
    story.append(Paragraph("Комплекты", styles["heading"]))
    if packages:
        for package in packages:
            story.append(Paragraph(
                f"{_esc(package.folder_name)} — "
                f"{_esc(domain.EXPORT_VARIANT_LABELS.get(package.export_variant, ''))}"
                + (", с пометкой об ошибках" if package.has_errors_file else ""),
                styles["fill"],
            ))
    else:
        story.append(Paragraph("комплектов нет", styles["fill"]))

    size = A4
    doc = BaseDocTemplate(
        str(path), pagesize=size,
        leftMargin=MARGIN_LEFT_MM * mm, rightMargin=MARGIN_RIGHT_MM * mm,
        topMargin=MARGIN_TOP_MM * mm, bottomMargin=MARGIN_BOTTOM_MM * mm,
        title="Состав проекта",
        author="Исполнительная документация",
    )
    frame = Frame(
        MARGIN_LEFT_MM * mm, MARGIN_BOTTOM_MM * mm,
        size[0] - (MARGIN_LEFT_MM + MARGIN_RIGHT_MM) * mm,
        size[1] - (MARGIN_TOP_MM + MARGIN_BOTTOM_MM) * mm,
        id="project",
    )
    doc.addPageTemplates([PageTemplate(id="project", frames=[frame])])
    doc.build(story)
    return path


def _archive_by_role(db: Session, project_id: int, role: str) -> list[str]:
    """Имена архивных файлов, связанных с документами проекта (ТЗ п.49)."""
    document_ids = [
        row.id for row in db.query(Document.id).filter(
            Document.project_id == project_id
        )
    ]
    if not document_ids:
        return []
    links = (
        db.query(DocumentArchiveLink)
        .filter(
            DocumentArchiveLink.document_id.in_(document_ids),
            DocumentArchiveLink.link_role == role,
        )
        .all()
    )
    names: list[str] = []
    for link in links:
        archive = db.get(ArchiveDocument, link.archive_document_id)
        if archive is not None:
            names.append(archive.original_name)
    return sorted(set(names))


def render_document_pdf(
    db: Session,
    document: Document,
    output_path: Path | str,
    *,
    page_numbers: bool = False,
    record_history: bool = False,
) -> Path:
    """PDF одного документа.

    ``record_history`` пишет в историю проекта событие о сохранённом PDF
    (ТЗ п.86). Сборка комплекта его не выставляет: там историю пишет сам
    комплект вместе с реестром, иначе в истории было бы по событию на
    каждый документ.
    """
    path = render_documents_pdf(
        db, [document], output_path, page_numbers=page_numbers
    )
    if record_history:
        _record_pdf_saved(db, document, path)
    return path


def _record_pdf_saved(db: Session, document: Document, path: Path) -> None:
    """Отметить в истории сохранённый PDF документа (ТЗ п.86)."""
    from app.core import domain
    from app.core.services.project_service import record_event

    issued = any(version.issued_at is not None for version in document.versions)
    kind = "исторический PDF" if issued else "PDF черновика"
    record_event(
        db, document.project_id, domain.HISTORY_PDF_SAVED,
        f"Сохранён {kind} {document.type_label} № {document.number}: "
        f"{Path(path).name} (ТЗ п.86)",
        entity_type="document", entity_id=document.id,
        payload={
            "path": str(path),
            "file_name": Path(path).name,
            "issued": issued,
        },
    )
    db.commit()


def page_count(pdf_path: Path | str) -> int:
    """Число страниц PDF; используется для сверки с целями ТЗ п.56–58."""
    from pypdf import PdfReader

    return len(PdfReader(str(pdf_path)).pages)


def target_pages(definition: dict) -> str:
    """Целевой объём из описания формы (ТЗ п.56–58), для диагностики."""
    layout = (definition or {}).get("layout") or {}
    return str(layout.get("target_pages") or "")
