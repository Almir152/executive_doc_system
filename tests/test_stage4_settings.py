"""Настройки приложения. ТЗ п.65."""

import json

import pytest

from app import settings
from app.config import SETTINGS_PATH


def test_default_mode_is_working_area():
    """Без настройки форма открывается в правой рабочей области (ТЗ п.65)."""
    assert settings.form_open_mode() == settings.FORM_MODE_INSIDE


def test_mode_is_stored_between_reads():
    """Выбранный режим открытия формы сохраняется (ТЗ п.65)."""
    settings.set_form_open_mode(settings.FORM_MODE_SEPARATE)

    assert settings.form_open_mode() == settings.FORM_MODE_SEPARATE
    assert json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) == {
        "form_open_mode": settings.FORM_MODE_SEPARATE
    }


def test_unknown_mode_falls_back_to_working_area():
    """Неизвестный режим из файла настроек не ломает запуск (ТЗ п.65)."""
    SETTINGS_PATH.write_text(
        json.dumps({"form_open_mode": "как-нибудь"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert settings.form_open_mode() == settings.FORM_MODE_INSIDE


def test_broken_settings_file_is_not_fatal():
    """Испорченный файл настроек не мешает работать приложению."""
    SETTINGS_PATH.write_text("{это не json", encoding="utf-8")

    assert settings.form_open_mode() == settings.FORM_MODE_INSIDE


def test_other_settings_are_not_lost():
    """Запись одной настройки не стирает остальные."""
    settings.set_setting("font_name", "Arial")

    settings.set_form_open_mode(settings.FORM_MODE_SEPARATE)

    assert settings.get_setting("font_name") == "Arial"


def test_invalid_mode_is_rejected():
    """Неизвестный режим не записывается молча."""
    with pytest.raises(ValueError):
        settings.set_form_open_mode("не то")
