from datetime import datetime

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
