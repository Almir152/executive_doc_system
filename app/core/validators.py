"""Логические зависимости дат. ТЗ п.43, 87.

Система проверяет зависимости дат, но никогда не меняет дату сама: дату
вводит оператор (ТЗ п.43).
"""

from datetime import date, datetime

class ValidationError(Exception):
    pass

def validate_aook_dates(aook_start: datetime, aook_end: datetime, aosr_dates):
    """
    Раздел 87 ТЗ:
    - Дата окончания АООК не может быть раньше окончания любого связанного АОСР.
    - Дата начала АООК не может быть позже начала связанного АОСР.

    aosr_dates может быть None или пустым: у АООК без связей ограничений
    по датам нет, и это нормальное состояние, а не ошибка ввода.
    """
    if aook_start > aook_end:
        raise ValidationError("Дата начала АООК не может быть позже даты окончания АООК!")

    for aosr_start, aosr_end in aosr_dates or ():
        if aook_end < aosr_end:
            raise ValidationError(
                f"Ошибка даты АООК: Дата окончания АООК ({aook_end.strftime('%d.%m.%Y')}) "
                f"не может быть раньше даты окончания связанного АОСР ({aosr_end.strftime('%d.%m.%Y')})!"
            )
        if aook_start > aosr_start:
            raise ValidationError(
                f"Ошибка даты АООК: Дата начала АООК ({aook_start.strftime('%d.%m.%Y')}) "
                f"не может быть позже даты начала связанного АОСР ({aosr_start.strftime('%d.%m.%Y')})!"
            )


# Формат даты в форме и в печати (ТЗ п.55-61).
DATE_FORMAT = "%d.%m.%Y"

# Ключи срока работ в данных формы (ТЗ п.43, 87).
PERIOD_START_KEY = "period_start"
PERIOD_END_KEY = "period_end"


def parse_date(value) -> date | None:
    """Дата из значения формы или из объекта date.

    Принимаются ``date``, ``datetime`` и строки в формате ``дд.мм.гггг``,
    которые вводит оператор. Нераспознанное значение датой не считается:
    выдумывать дату система не вправе (ТЗ п.43), поэтому вернётся None, а
    проверка сообщит, что срок не задан.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (DATE_FORMAT, "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def period_from_payload(payload: dict) -> tuple[date | None, date | None]:
    """Срок работ из данных формы (ТЗ п.43, 87)."""
    payload = payload or {}
    return (
        parse_date(payload.get(PERIOD_START_KEY)),
        parse_date(payload.get(PERIOD_END_KEY)),
    )


def format_date(value) -> str:
    """Дата для сообщения оператору (ТЗ п.83)."""
    parsed = parse_date(value)
    return parsed.strftime(DATE_FORMAT) if parsed else "—"


def period_problems(
    label: str,
    start: date | None,
    end: date | None,
    related: list[tuple[str, date | None, date | None]],
) -> list[str]:
    """Проверить срок документа по срокам связанных актов (ТЗ п.87).

    ``related`` — пары (обозначение связанного акта, его начало, его
    окончание). Пустой список означает, что связей нет: ограничений тогда
    нет, и это нормальное состояние, а не ошибка.
    """
    problems: list[str] = []
    if start is None or end is None:
        return problems
    if start > end:
        problems.append(
            f"{label}: начало периода ({format_date(start)}) позже окончания "
            f"({format_date(end)}) (ТЗ п.87)."
        )
    for related_label, related_start, related_end in related:
        if related_end is not None and end < related_end:
            problems.append(
                f"{label}: окончание периода ({format_date(end)}) раньше "
                f"окончания связанного акта {related_label} "
                f"({format_date(related_end)}) (ТЗ п.87)."
            )
        if related_start is not None and start > related_start:
            problems.append(
                f"{label}: начало периода ({format_date(start)}) позже начала "
                f"связанного акта {related_label} ({format_date(related_start)}) "
                "(ТЗ п.87)."
            )
    return problems


def document_period(db, document) -> tuple[date | None, date | None]:
    """Срок работ документа по актуальной версии (ТЗ п.43, 87).

    Берётся та версия, которую увидит проверка: у выпущенного документа —
    зафиксированная, у рабочего — черновик.
    """
    from app.core.services.form_service import actual_version, draft_version

    version = actual_version(db, document.id) or draft_version(db, document.id)
    if version is None:
        return None, None
    return period_from_payload(version.payload or {})


def check_document_dates(db, document) -> list[str]:
    """Логические зависимости дат документа (ТЗ п.43, 87).

    Для итогового акта срок сверяется со сроками актов, которые он
    завершает. Дата самого документа при этом не проверяется на «не раньше
   »: ТЗ не требует, а менять или придумывать дату система не вправе.
    """
    from app.core.services.link_service import MaterialError, finalized_acts

    label = f"{document.type_label} № {document.number}"
    start, end = document_period(db, document)
    if start is None and end is None:
        return []
    if start is not None and end is None or start is None and end is not None:
        return [
            f"{label}: задан только один конец периода работ. Период указывается "
            "двумя датами (ТЗ п.43, 87)."
        ]

    try:
        related_documents = finalized_acts(db, document.id)
    except MaterialError:  # pragma: no cover - защита от рассинхронизации
        related_documents = []
    if not related_documents:
        return period_problems(label, start, end, [])

    related = [
        (
            f"{item.type_label} № {item.number}",
            document_period(db, item)[0],
            document_period(db, item)[1],
        )
        for item in related_documents
    ]
    return period_problems(label, start, end, related)
