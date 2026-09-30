"""Настройки приложения. ТЗ п.65.

Режим открытия формы выбирается один раз для приложения: «внутри правой
рабочей области» либо «отдельно». Значение хранится рядом с базой, а не в
коде: настройка согласована для всех документов проекта (ТЗ п.65).

Испорченный или отсутствующий файл не должен мешать запуску — при чтении
ошибка не возникает, настройка просто возвращается к значению по
умолчанию.
"""

import json
import logging

from app.config import SETTINGS_PATH

# Режимы открытия формы (ТЗ п.65).
FORM_MODE_INSIDE = "inside"
FORM_MODE_SEPARATE = "separate"
FORM_MODES = (FORM_MODE_INSIDE, FORM_MODE_SEPARATE)
FORM_MODE_LABELS = {
    FORM_MODE_INSIDE: "В правой рабочей области",
    FORM_MODE_SEPARATE: "Отдельно",
}

log = logging.getLogger(__name__)


def _read() -> dict:
    try:
        text = SETTINGS_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("Файл настроек повреждён, применены значения по умолчанию: %s",
                    SETTINGS_PATH)
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_setting(key: str, default=None):
    """Значение настройки или значение по умолчанию."""
    return _read().get(key, default)


def set_setting(key: str, value) -> None:
    """Записать настройку, не потеряв остальные."""
    data = _read()
    data[key] = value
    _write(data)


def form_open_mode() -> str:
    """Режим открытия формы (ТЗ п.65); по умолчанию — рабочая область."""
    mode = _read().get("form_open_mode")
    return mode if mode in FORM_MODES else FORM_MODE_INSIDE


def set_form_open_mode(mode: str) -> None:
    """Задать режим открытия формы (ТЗ п.65)."""
    if mode not in FORM_MODES:
        raise ValueError(f"Неизвестный режим открытия формы: {mode}")
    set_setting("form_open_mode", mode)
