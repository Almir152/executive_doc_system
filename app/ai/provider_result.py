"""Разбор ответа внешней модели в предложения (ТЗ п.102, 106).

Модель возвращает текст. Приложение превращает его в предложения того же
вида, что и локальный режим, и не более: каждое предложение — текст
замечания и, если модель его назвала, основание. Основание без ссылки на
нормативный документ требованием не считается (ТЗ п.106), а изменение
данных модель не предлагает: применение остаётся за оператором через
прикладной API (ТЗ п.104).
"""

from __future__ import annotations

import re

from app.ai import normative

#: Предложения внешней модели всегда замечания без действия.
CODE = "gigachat_review"

_BASIS = re.compile(r"ОСНОВАНИЕ\s*[:—-]\s*(?P<basis>.+?)\s*$", re.IGNORECASE)
_ITEM = re.compile(r"^\s*(?:[-–—*•]|\d+[.)])\s+(?P<text>.+?)\s*$")
_NOT_FOUND = re.compile(r"замечани\w* не (?:найден|выявлен)|не выявлено",
                        re.IGNORECASE)
#: Модель прямо говорит, что основание не нашла: требованием это не является.
_NO_BASIS = re.compile(r"не установлено|нет|не указано|отсутствует",
                       re.IGNORECASE)

STATUS_SUCCESS = "success"


def _split_items(text: str) -> list[tuple[str, str]]:
    """Замечания и их основания из текста модели.

    Основание модель пишет отдельной строкой сразу после замечания, поэтому
    строка «ОСНОВАНИЕ: …» относится к предыдущему пункту, а не к новому.
    """
    items: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        basis_match = _BASIS.match(line)
        if basis_match:
            if items:
                body, _ = items[-1]
                items[-1] = (body, basis_match.group("basis").strip())
            continue
        item_match = _ITEM.match(raw)
        comment = (item_match.group("text") if item_match else line).strip()
        # Заголовок списка («Рекомендации:») замечанием не является.
        if comment.endswith(":"):
            continue
        items.append((comment, ""))
    return items


def parse_proposals(text: str) -> list[dict]:
    """Предложения из ответа модели.

    Ответ без замечаний — честный результат «предложений нет», а не
    предложение по умолчанию.
    """
    text = (text or "").strip()
    if not text or _NOT_FOUND.search(text):
        return []
    proposals: list[dict] = []
    for body, basis in _split_items(text):
        if not body:
            continue
        if basis and _NO_BASIS.fullmatch(basis.strip(" .")):
            # Основание не найдено: это замечание, а не требование нормы.
            basis = ""
        # Основание приходит строкой, а правило требует документ нормы.
        is_requirement = normative.is_normative_claim(
            {"document": basis} if basis else None
        )
        comment = body
        if basis and not is_requirement:
            comment = f"{body} (подтвердите основание)"
        proposals.append({
            "code": CODE,
            "text": comment,
            "document_id": None,
            # Внешняя модель не меняет данные: только оператор (ТЗ п.104).
            "action": None,
            "basis": basis,
            "is_requirement": is_requirement,
        })
    return proposals


def build_result(text: str, *, mode: str) -> dict:
    """Ответ коннектора по тексту модели."""
    proposals = parse_proposals(text)
    message = (
        ""
        if proposals
        else "Модель не сообщила замечаний по переданным сведениям."
    )
    return {
        "status": STATUS_SUCCESS,
        "mode": mode,
        "message": message,
        "proposals": proposals,
    }
