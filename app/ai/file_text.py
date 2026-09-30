"""Извлечение текста файла для ИИ — по явному выбору оператора (ТЗ п.103).

ТЗ требует, чтобы ИИ видел только то, что система предоставила через
разрешённые интерфейсы, и не читал диск самостоятельно. Поэтому чтение файла
выполняет система — по идентификатору архивного документа, который оператор
отметил в диалоге, — а ИИ получает уже готовый текст без пути, хеша и прочих
служебных сведений (ТЗ п.103).

Честность важнее удобства:

* поддерживаются только форматы, из которых текст берётся достоверно
  (PDF с текстовым слоем, текстовые файлы); для остальных возвращается причина,
  а не догадка;
* путь на диске наружу не отдаётся никогда;
* лимиты объёма ограничивают контекст: иначе один большой файл вытеснил бы
  из анализа всё остальное. В ТЗ эти лимиты не заданы, поэтому значения
  выбраны так, чтобы обычный документ помещался целиком.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Сколько страниц PDF читается из одного файла. Форма документа обычно
#: укладывается в несколько страниц (ТЗ п.56–58); читать сотни страниц ради
#: одного запроса оператору не нужно.
MAX_PAGES = 20

#: Предел символов на файл. Ограничение контекста, а не документа.
MAX_CHARS = 40_000

#: Форматы, из которых текст извлекается как есть.
TEXT_TYPES = ("txt", "text", "md", "csv", "log", "json", "xml")


@dataclass(frozen=True)
class FileText:
    """Результат чтения файла: текст либо честная причина отказа."""

    text: str = ""
    pages: int = 0
    truncated: bool = False
    reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.text)

    def describe(self) -> str:
        """Строка для интерфейса и истории: что именно увидит ИИ."""
        if not self.available:
            return f"текст не передан: {self.reason}"
        size = f"{len(self.text)} симв."
        if self.pages:
            size += f", страниц: {self.pages}"
        if self.truncated:
            size += ", текст обрезан"
        return size


def extract_file_text(
    path: Path | str, file_type: str = "", *, max_pages: int = MAX_PAGES,
    max_chars: int = MAX_CHARS,
) -> FileText:
    """Прочитать текст файла или вернуть причину, почему это невозможно."""
    path = Path(path)
    kind = (file_type or path.suffix.lstrip(".")).lower()
    if not path.is_file():
        return FileText(reason="файл отсутствует в хранилище архива")
    try:
        if kind == "pdf":
            return _pdf_text(path, max_pages, max_chars)
        if kind in TEXT_TYPES:
            return _plain_text(path, max_chars)
    except Exception as exc:  # noqa: BLE001 — причина показывается оператору
        return FileText(reason=f"не удалось прочитать файл: {exc}")
    return FileText(
        reason=(
            f"формат «{kind or 'без расширения'}» не поддерживается: "
            "текст из него не извлекается"
        )
    )


def _pdf_text(path: Path, max_pages: int, max_chars: int) -> FileText:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    total = len(reader.pages)
    pages = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    # Заголовки страниц не считаются содержимым: иначе пустой «скан» выглядел
    # бы прочитанным.
    if not "".join(pages).strip():
        return FileText(
            pages=min(total, max_pages),
            reason=(
                "в PDF нет текстового слоя (вероятно, скан): "
                "нужен текстовый слой или пересказ оператора"
            ),
        )
    parts = [
        f"— страница {index} —\n{page_text}"
        for index, page_text in enumerate(pages, start=1)
    ]
    text = _limited("\n\n".join(parts), max_chars)
    return FileText(
        text=text, pages=min(total, max_pages),
        truncated=text.endswith(TRUNCATION_MARK) or total > max_pages,
    )


def _plain_text(path: Path, max_chars: int) -> FileText:
    data = path.read_bytes()
    for encoding in ("utf-8", "utf-16", "cp1251", "koi8-r"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return FileText(reason="не удалось определить кодировку текстового файла")
    text = _limited(text, max_chars)
    if not text.strip():
        return FileText(reason="файл пустой")
    return FileText(text=text, pages=0, truncated=text.endswith(TRUNCATION_MARK))


#: Пометка об обрезке: ИИ должен видеть, что текст неполон.
TRUNCATION_MARK = "… (текст обрезан)"


def _limited(text: str, max_chars: int) -> str:
    text = text.replace("\x00", "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + TRUNCATION_MARK
