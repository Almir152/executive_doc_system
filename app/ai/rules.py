"""Проверки ИИ-агента над контекстом проекта (ТЗ п.102, 104, 106).

Правила работают только с контекстом, который предоставила система
(ТЗ п.103), и возвращают предложения двух видов:

* замечание без изменения данных (``action`` = ``None``);
* предложение изменить данные через прикладной API (``action`` задан).

Каждое предложение несёт основание, если оно опирается на норму, и не
выдаёт предположение за установленное требование (ТЗ п.106).
"""

from __future__ import annotations

from app.ai import normative
from app.core import domain

# Коды предложений: по ним интерфейс и история узнают, что именно найдено.
CODE_DRAFT_NOT_ISSUED = "document_not_issued"
CODE_NO_SCHEME = "no_scheme_linked"
CODE_FINAL_ACT_WITHOUT_LINKS = "final_act_without_links"
CODE_MATERIAL_WITHOUT_ACT = "material_without_test_act"
CODE_NO_NORMS = "no_normative_basis"
CODE_ACT_WITHOUT_MATERIALS = "test_act_without_materials"
CODE_FILE_TEXT_UNAVAILABLE = "file_text_unavailable"
CODE_FILE_TEXT_TRUNCATED = "file_text_truncated"


def analyze_context(context: dict) -> list[dict]:
    """Предложения по проекту из контекста (ТЗ п.102)."""
    proposals: list[dict] = []
    documents = context.get("documents", [])
    by_id = {document["id"]: document for document in documents}
    links = context.get("links", [])
    archive = context.get("archive", [])

    proposals.extend(_drafts_not_issued(context, documents))
    proposals.extend(_final_act_links(context, documents, by_id, links))
    proposals.extend(_scheme_links(context, documents, archive))
    proposals.extend(_test_act_evidence(context, documents))
    proposals.extend(_materials(context))
    proposals.extend(_normative_caveat(context, documents))
    proposals.extend(_file_text_limits(context))
    return proposals


def _file_text_limits(context: dict) -> list[dict]:
    """Честно сказать, что именно из текста файлов увидел ИИ (ТЗ п.103, 106).

    Проверки по содержимому файла строятся на нормативных основаниях, а не на
    догадках. Если текст получить не удалось или он обрезан, ИИ обязан сказать
    об этом прямо: иначе оператор решит, что файл проверен, а это не так.
    """
    proposals: list[dict] = []
    for row in context.get("file_texts") or []:
        name = row.get("name") or "файл архива"
        if not row.get("available"):
            reason = row.get("reason") or "причина не определена"
            proposals.append(_remark(
                CODE_FILE_TEXT_UNAVAILABLE,
                f"Текст файла «{name}» не передан ИИ: {reason}. Содержимое "
                "не проверялось.",
            ))
        elif row.get("truncated"):
            proposals.append(_remark(
                CODE_FILE_TEXT_TRUNCATED,
                f"Текст файла «{name}» передан частично: проверялось начало, "
                "остальная часть не анализировалась.",
            ))
    return proposals


def _remark(code: str, text: str, *, document_id: int | None = None,
            basis: dict | None = None, action: dict | None = None) -> dict:
    """Собрать предложение: текст, основание, признак требования."""
    is_requirement = normative.is_normative_claim(basis)
    if basis and not is_requirement:
        # Основание без документа нормы требованием не является.
        text = f"{text} (подтвердите основание)"
    return {
        "code": code,
        "text": text,
        "document_id": document_id,
        "action": action,
        "basis": basis,
        "is_requirement": is_requirement,
    }


def _drafts_not_issued(context: dict, documents: list[dict]) -> list[dict]:
    """Документы без выпущенной версии (ТЗ п.54, 85)."""
    proposals = []
    for document in documents:
        if document.get("issued"):
            continue
        proposals.append(_remark(
            CODE_DRAFT_NOT_ISSUED,
            f"{document['type_label']} № {document['number'] or '—'} не выпущен: "
            "черновик в комплект не попадает.",
            document_id=document["id"],
        ))
    return proposals


def _final_act_links(context: dict, documents: list[dict], by_id: dict,
                     links: list[dict]) -> list[dict]:
    """Итоговый акт без связей с актами скрытых работ (ТЗ п.87)."""
    linked = {
        (link["from"], link["to"], link["role"]) for link in links
        if link.get("role") == domain.LINK_ROLE_FINALIZES
    }
    proposals = []
    for document in documents:
        if document["type"] not in domain.FINAL_ACT_TYPES:
            continue
        missing = [
            other for other in documents
            if other["type"] == domain.DOC_TYPE_AOSR
            and (document["id"], other["id"], domain.LINK_ROLE_FINALIZES)
            not in linked
        ]
        if not missing:
            continue
        for other in missing:
            proposals.append(_remark(
                CODE_FINAL_ACT_WITHOUT_LINKS,
                f"{document['type_label']} № {document['number'] or '—'} не связан "
                f"с {other['type_label']} № {other['number'] or '—'}: "
                "срок окончания работ сравнить не с чем.",
                document_id=document["id"],
                basis=normative.form_basis_from_context(context, document["type"]),
                action={
                    "kind": domain.AI_ACTION_LINK_DOCUMENTS,
                    "document_id": document["id"],
                    "related_document_id": other["id"],
                    "link_role": domain.LINK_ROLE_FINALIZES,
                },
            ))
    return proposals


def _scheme_links(context: dict, documents: list[dict], archive: list[dict]) -> list[dict]:
    """Акты без исполнительной схемы (ТЗ п.49, 79)."""
    with_scheme = {
        row["document_id"] for row in archive
        if row.get("role") == domain.LINK_ROLE_SCHEME
    }
    proposals = []
    for document in documents:
        if document["type"] != domain.DOC_TYPE_AOSR:
            continue
        if document["id"] in with_scheme:
            continue
        proposals.append(_remark(
            CODE_NO_SCHEME,
            f"К {document['type_label']} № {document['number'] or '—'} не приложена "
            "исполнительная схема.",
            document_id=document["id"],
        ))
    return proposals


def _test_act_evidence(context: dict, documents: list[dict]) -> list[dict]:
    """Акт испытаний без материалов и протокола (ТЗ п.44, 45, 36).

    Акт испытаний создаётся только по запросу оператора (ТЗ п.36), поэтому
    система не достраивает его содержимое, а лишь отмечает, что
    подтверждать в нём пока нечего.
    """
    materials = context.get("materials", [])
    with_materials = {
        act_id for material in materials for act_id in material["test_act_ids"]
    }
    protocols = {
        row["document_id"] for row in context.get("archive", [])
        if row.get("role") in (domain.LINK_ROLE_PROTOCOL, domain.LINK_ROLE_TEST_PROTOCOL)
    }
    proposals = []
    for document in documents:
        if document["type"] != domain.DOC_TYPE_TEST_ACT:
            continue
        if document["id"] in with_materials or document["id"] in protocols:
            continue
        proposals.append(_remark(
            CODE_ACT_WITHOUT_MATERIALS,
            f"Акт испытаний № {document['number'] or '—'} не содержит ни "
            "материалов, ни протокола: результат испытаний не подтверждён.",
            document_id=document["id"],
        ))
    return proposals


def _materials(context: dict) -> list[dict]:
    """Материалы без акта испытаний (ТЗ п.44)."""
    proposals = []
    for material in context.get("materials", []):
        if material.get("test_act_ids"):
            continue
        proposals.append(_remark(
            CODE_MATERIAL_WITHOUT_ACT,
            f"Материал «{material['name']}» не отнесён ни к одному акту "
            "испытаний.",
        ))
    return proposals


def _normative_caveat(context: dict, documents: list[dict]) -> list[dict]:
    """Нет нормативной базы — ИИ не делает требований (ТЗ п.106)."""
    if context.get("normative"):
        return []
    if not documents:
        return []
    return [_remark(
        CODE_NO_NORMS,
        "Нормативная база системы пуста: проверка соответствия требованиям "
        "невозможна, замечания ниже — наблюдения, а не установленные "
        "требования.",
    )]
