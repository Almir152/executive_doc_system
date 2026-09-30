"""Тесты выбора справочных значений с поиском. ТЗ п.20.

Справочные значения выбираются из справочника с поиском («шторка»); свободного
ввода для них нет.
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget

from app.ui.reference_picker import (
    NO_SELECTION, ReferenceMultiPicker, ReferencePicker,
)

ORGANIZATIONS = [
    ("ООО «Строймонтаж» (ИНН 7701000001)", 1),
    ("ООО «Жилстрой» (ИНН 7701000002)", 2),
    ("АО «Промресурс» (ИНН 7701000003)", 3),
]


# =====================================================================
# ВЫПАДАЮЩИЙ СПИСОК
# =====================================================================


def test_picker_lists_reference_values(qapp):
    """В списке только значения справочника (ТЗ п.20)."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)

    assert picker.count() == len(ORGANIZATIONS) + 1, "плюс строка «не выбрано»"
    assert picker.current_data() is None


def test_picker_returns_selected_value(qapp):
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.setCurrentIndex(2)

    assert picker.current_data() == 2
    assert "Жилстрой" in picker.current_text()


def test_picker_qt_name_returns_same_value(qapp):
    """``currentData()`` не должен молча терять выбор (ТЗ п.20).

    Дефект: формы читали значение через унаследованный ``currentData()``,
    который у ReferencePicker всегда возвращал None, и выбор считался
    несделанным.
    """
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.set_current_data(2)

    assert picker.currentData() == picker.current_data() == 2


def test_picker_starts_unselected(qapp):
    """Значение по умолчанию не выбрано: подставлять наугад нельзя."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)

    assert picker.is_empty()
    assert picker.current_text() == NO_SELECTION


def test_picker_preselects_value(qapp):
    """При открытии диалога показывается текущее значение раздела."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.set_current_data(3)

    assert picker.current_data() == 3
    assert "Промресурс" in picker.current_text()


def test_picker_keeps_selection_when_list_is_rebuilt(qapp):
    """Перестроение списка не теряет выбранное значение."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.set_current_data(2)

    picker.set_reference_items(ORGANIZATIONS + [("ООО «Новый», ИНН 4", 4)])

    assert picker.current_data() == 2


def test_picker_ignores_unknown_value(qapp):
    """Значения вне справочника выбрать нельзя: остаётся пустое."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.set_current_data(999)

    assert picker.is_empty()


def test_picker_search_is_case_insensitive_for_cyrillic(qapp):
    """Поиск не должен зависеть от регистра кириллицы (ТЗ п.20)."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)
    completer = picker.completer()

    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    # Набор текста сужает сам список выбора, а не только подсказку.
    picker.lineEdit().textEdited.emit("СТРОЙМОНТАЖ")
    shown = [picker.itemText(row) for row in range(picker.count())]
    assert shown == ["ООО «Строймонтаж» (ИНН 7701000001)"], shown

    # Очистка строки возвращает полный список справочника.
    picker.lineEdit().textEdited.emit("")
    assert picker.count() == len(ORGANIZATIONS) + 1


def test_picker_has_no_free_input(qapp):
    """Свободный ввод для справочного значения недоступен (ТЗ п.20)."""
    picker = ReferencePicker()
    picker.set_reference_items(ORGANIZATIONS)

    assert picker.isEditable(), "строка поиски нужна для набора"
    picker.lineEdit().setText("ООО «Неизвестная»")
    assert picker.current_data() is None, "набранный текст не должен становиться значением"


# =====================================================================
# НЕСКОЛЬКО ЗНАЧЕНИЙ
# =====================================================================


def test_multi_picker_selects_several_values(qapp):
    """Отмечается несколько значений сразу (ТЗ п.17, 21)."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)

    picker.list.item(0).setCheckState(Qt.CheckState.Checked)
    picker.list.item(2).setCheckState(Qt.CheckState.Checked)

    assert picker.selected_values() == [1, 3]


def test_multi_picker_search_hides_other_values(qapp):
    """Поиск сужает список, но отметки не сбрасывает (ТЗ п.20)."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.list.item(0).setCheckState(Qt.CheckState.Checked)

    picker.search.setText("жилстрой")

    visible = [
        picker.list.item(r).text() for r in range(picker.list.count())
        if not picker.list.item(r).isHidden()
    ]
    assert len(visible) == 1 and "Жилстрой" in visible[0]
    assert picker.selected_values() == [1], "отметка не должна теряться при поиске"


def test_multi_picker_search_works_for_lowercase_cyrillic(qapp):
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)

    picker.search.setText("жилстрой")

    assert picker.list.item(1).isHidden() is False
    assert picker.list.item(0).isHidden() is True


def test_multi_picker_checks_only_found_values(qapp):
    """«Отметить найденное» не отмечает то, что скрыто поиском."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.search.setText("жилстрой")

    picker.select_all.click()

    assert picker.selected_values() == [2]


def test_multi_picker_restores_selection(qapp):
    """Значения, отмеченные ранее, восстанавливаются (ТЗ п.17)."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)
    picker.set_selected_values([1, 3])

    picker.set_reference_items(ORGANIZATIONS)

    assert picker.selected_values() == [1, 3]


def test_multi_picker_survives_empty_directory(qapp):
    """Пустой справочник не должен ломать диалог."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items([])

    assert picker.selected_values() == []
    picker.search.setText("что угодно")
    assert isinstance(picker.list, QListWidget)


def test_multi_picker_summary_reports_count(qapp):
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)
    assert "0" in picker.summary.text()

    picker.list.item(0).setCheckState(Qt.CheckState.Checked)
    assert "1" in picker.summary.text()


@pytest.mark.parametrize("needle", ["жилс", "ЖИЛС", "жилстрой"])
def test_multi_picker_partial_cyrillic(qapp, needle):
    """Поиск идёт по подстроке в любом регистре (ТЗ п.20)."""
    picker = ReferenceMultiPicker()
    picker.set_reference_items(ORGANIZATIONS)

    picker.search.setText(needle)

    shown = [
        picker.list.item(r).text() for r in range(picker.list.count())
        if not picker.list.item(r).isHidden()
    ]
    assert shown and all("Жилстрой" in text for text in shown)
