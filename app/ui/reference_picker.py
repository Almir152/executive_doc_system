"""Выбор справочных значений с поиском. ТЗ п.20.

Для справочных значений используется выпадающий механизм с поиском —
условно «шторка». Свободный ввод применяется только там, где значение не
относится к справочнику, поэтому в этом модуле нет свободного ввода: поле
в списке выбирается, а набор ограничен содержимым справочника.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QStringListModel, QSortFilterProxyModel
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

_DATA_ROLE = Qt.ItemDataRole.UserRole
_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable

NO_SELECTION = "— не выбрано —"


class _SubstringProxy(QSortFilterProxyModel):
    """Прокси, пропускающий строки, содержащие набранный текст.

    Регистр сравнивается через Python `.lower()`: кириллица в этом
    регистре приводится одинаково, а SQLite `lower()` на кириллице не
    работает, из-за чего поиск по справочнику там пришлось делать вручную.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""

    def setFilterFixedString(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        super().setFilterFixedString(self._needle)

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._needle:
            return True
        items = self.sourceModel().stringList()
        if source_row >= len(items):
            return False
        return self._needle in str(items[source_row]).lower()


class ReferencePicker(QComboBox):
    """Справочное значение с поиском по мере набора (ТЗ п.20).

    Список значений приходит из справочника и подменяется только им: если
    значения нет в справочнике, его сначала надо завести в справочнике.
    """

    def __init__(self, placeholder: str = NO_SELECTION, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._placeholder = placeholder
        self._items: list[tuple[str, object]] = []
        self._source = QStringListModel(self)
        self._proxy = _SubstringProxy(self)
        self._proxy.setSourceModel(self._source)
        self._proxy.setFilterKeyColumn(0)
        # Регистр кириллицы Qt приводит правильно, в отличие от SQLite
        # lower(), поэтому регистронезависимость здесь действительно работает.
        self.setModel(self._proxy)
        self.lineEdit().textEdited.connect(self._on_typed)
        self.activated.connect(self._on_activated)

    # -----------------------------------------------------------------
    def set_reference_items(self, items, *, placeholder: str | None = None) -> None:
        """Заполнить значениями справочника: список пар «подпись, значение».

        Уже выбранное значение сохраняется: перестроение списка не должно
        молча менять реквизит.
        """
        selected = self.current_data()
        if placeholder is not None:
            self._placeholder = placeholder
        self._items = [(str(label), value) for label, value in items]
        self._source.setStringList([self._placeholder] + [i[0] for i in self._items])
        completer = QCompleter(self._proxy, self)
        completer.setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)
        self._proxy.setFilterFixedString("")
        if selected is not None:
            self.set_current_data(selected)

    def _on_typed(self, text: str) -> None:
        """Пока оператор набирает, список сужается до совпадений (ТЗ п.20)."""
        self._proxy.setFilterFixedString(text or "")

    def _on_activated(self, index: int) -> None:
        """После выбора список снова показывается целиком."""
        label = self.itemText(index)
        self._proxy.setFilterFixedString("")
        self.lineEdit().setText(label)

    def _values(self) -> list:
        """Значения по строкам исходной модели; строка 0 — «не выбрано»."""
        return [None] + [value for _, value in self._items]

    def _source_row(self, index: int) -> int:
        source = self._proxy.mapToSource(self._proxy.index(index, 0))
        return source.row() if source.isValid() else -1

    def current_data(self):
        """Выбранное значение справочника или None.

        При наборе текста без совпадений индекс сбрасывается, и значение не
        определяется: чужое значение выбрать нельзя (ТЗ п.20).
        """
        index = self.currentIndex()
        if index < 0:
            return None
        row = self._source_row(index)
        values = self._values()
        return values[row] if 0 <= row < len(values) else None

    def set_current_data(self, value) -> None:
        """Выбрать значение по его идентификатору."""
        self._proxy.setFilterFixedString("")
        for row, item in enumerate(self._values()):
            if row == 0 or item != value:
                continue
            proxy_index = self._proxy.mapFromSource(self._source.index(row, 0))
            self.setCurrentIndex(proxy_index.row())
            return
        self.setCurrentIndex(0)

    def current_text(self) -> str:
        return self.currentText().strip()

    def is_empty(self) -> bool:
        return self.current_data() is None


class ReferenceMultiPicker(QWidget):
    """Отметка нескольких справочных значений (ТЗ п.17, 20, 21).

    Поиск идёт по мере набора, отметки сохраняются: повторный поиск не
    сбрасывает выбранные значения.
    """

    def __init__(self, empty_text: str = "Справочник пуст", parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, object]] = []
        self._empty_text = empty_text

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по справочнику (ТЗ п.20)")
        layout.addWidget(self.search)

        row = QHBoxLayout()
        self.summary = QLabel()
        row.addWidget(self.summary)
        row.addStretch()
        self.select_all = QPushButton("Отметить найденное")
        self.select_all.clicked.connect(self._check_visible)
        row.addWidget(self.select_all)
        layout.addLayout(row)

        self.list = QListWidget()
        self.list.itemChanged.connect(self._update_summary)
        layout.addWidget(self.list)
        self.search.textChanged.connect(self._filter)

    def set_reference_items(self, items) -> None:
        """Заполнить значениями справочника."""
        self._items = [(str(label), value) for label, value in items]
        self._rebuild()

    def item_count(self) -> int:
        """Сколько значений есть в справочнике."""
        return len(self._items)

    def _rebuild(self) -> None:
        selected = set(self.selected_values())
        self.list.clear()
        for label, value in self._items:
            item = QListWidgetItem(label)
            item.setData(_DATA_ROLE, value)
            item.setFlags(item.flags() | _CHECKABLE)
            item.setCheckState(
                Qt.CheckState.Checked if value in selected
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self._update_summary()

    def _filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _check_visible(self) -> None:
        """Отметить только видимые значения — то, что нашёл поиск."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked)
        self._update_summary()

    def _update_summary(self) -> None:
        count = len(self.selected_values())
        self.summary.setText(
            "отмечено: 0" if count == 0 else f"отмечено: {count}"
        )

    def selected_values(self) -> list:
        """Отмеченные значения в порядке справочника."""
        result = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                value = item.data(_DATA_ROLE)
                if value is not None:
                    result.append(value)
        return result

    def set_selected_values(self, values) -> None:
        wanted = set(values or [])
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setCheckState(
                Qt.CheckState.Checked if item.data(_DATA_ROLE) in wanted
                else Qt.CheckState.Unchecked
            )
