"""Нормативные основания для ответов ИИ (ТЗ п.106).

ИИ может опираться на нормативную базу системы, и при нормативном ответе
следует по возможности указать основание: документ, раздел, пункт. Если
базы не хватает, предположение нельзя выдавать за установленное
требование — поэтому каждое предложение помечается основанием и признаком
«это требование нормы», а не мнение.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

#: Документ, раздел, пункт — то, что оператор должен увидеть у нормы.
BASIS_KEYS = ("document", "section", "clause")


def basis_for_doc_type(db: Session, doc_type: str) -> dict | None:
    """Основание нормативной формы по виду документа (ТЗ п.24, 96, 106).

    Форма ведёт свою нормативную основу: основание хранится в справочнике
    форм, а не в коде (ТЗ п.96). Если формы нет — основания нет, и ИИ не
    имеет права ссылаться на норму.
    """
    from app.db.models import NormativeForm

    form = db.query(NormativeForm).filter(
        NormativeForm.doc_type == doc_type,
        NormativeForm.is_current.is_(True),
    ).order_by(NormativeForm.version.desc()).first()
    if form is None:
        return None
    basis = {
        "document": form.basis or "",
        "section": f"{form.title}, версия {form.version}",
        "clause": "",
        "form_version": form.version,
    }
    return basis if basis["document"] else None


def form_basis_from_context(context: dict, doc_type: str) -> dict | None:
    """Основание из уже собранного контекста (без обращения к базе)."""
    for row in context.get("normative", []):
        if row.get("doc_type") == doc_type and row.get("basis"):
            return {
                "document": row["basis"],
                "section": f"{row.get('title', '')}, версия {row.get('version')}",
                "clause": "",
                "form_version": row.get("version"),
            }
    return None


def format_basis(basis: dict | None) -> str:
    """Основание строкой для интерфейса и отчёта (ТЗ п.106)."""
    if not basis:
        return "основание не указано"
    parts = [basis.get(key) for key in BASIS_KEYS if basis.get(key)]
    return ", ".join(parts) if parts else "основание не указано"


def is_normative_claim(basis: dict | None) -> bool:
    """Можно ли считать утверждение требованием нормы (ТЗ п.106).

    Требованием нормативной базы считается только утверждение с основанием.
    Без него текст остаётся замечанием ИИ, а не установленным требованием.
    """
    if not basis:
        return False
    return bool(basis.get("document"))
