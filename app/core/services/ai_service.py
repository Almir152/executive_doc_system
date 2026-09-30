"""ИИ-агент: предложения как черновики (ТЗ п.101–106).

Правила работы агента:

* ИИ получает только контекст, предоставленный системой (ТЗ п.103);
* результат сохраняется как черновик и не является выпущенным документом
  (ТЗ п.105);
* данные меняются только после подтверждения оператора и только через
  прикладной API сервисов (ТЗ п.104);
* нормативное утверждение сопровождается основанием, а без основания
  остаётся предположением (ТЗ п.106).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import domain
from app.core.services.project_service import record_event


class AiError(Exception):
    """Ошибка работы ИИ-агента с текстом для оператора."""


def analyze(
    db: Session,
    project_id: int,
    request: str,
    connector,
) -> dict:
    """Спросить ИИ по проекту и сохранить предложения-черновики (ТЗ п.102, 105).

    Возвращает ответ коннектора; найденные предложения уже записаны как
    черновики, но ничего не изменено.
    """
    from app.ai import context as ai_context

    context = ai_context.build_context(db, project_id, request)
    ai_context.assert_context_is_allowed(context)
    answer = connector.analyze(context)
    proposals = [
        proposal for proposal in (answer.get("proposals") or [])
        if proposal.get("text")
    ]
    stored = [_store(db, project_id, proposal) for proposal in proposals]
    if stored:
        record_event(
            db, project_id, domain.HISTORY_AI_PROPOSED,
            f"ИИ ({answer.get('mode', '')}) предложил изменений: {len(stored)} "
            f"черновиков по запросу «{request.strip()}» (ТЗ п.102, 105)",
            entity_type="ai_proposal_batch",
            payload={
                "request": request.strip(),
                "status": answer.get("status"),
                "mode": answer.get("mode"),
                "proposal_ids": [proposal.id for proposal in stored],
                "context": ai_context.describe_context(context),
            },
        )
        db.commit()
    return {
        "answer": answer,
        "context": context,
        "proposals": stored,
    }


def _store(db: Session, project_id: int, proposal: dict):
    """Сохранить предложение как черновик (ТЗ п.105)."""
    from app.db.models import AiProposal

    basis = proposal.get("basis") or None
    record = AiProposal(
        project_id=project_id,
        document_id=proposal.get("document_id"),
        code=proposal.get("code", "proposal"),
        text=proposal["text"],
        action=proposal.get("action"),
        basis=basis,
        # Требованием нормативной базы считается только утверждение с
        # основанием (ТЗ п.106).
        is_requirement=bool(proposal.get("is_requirement")) and bool(basis),
        status=domain.AI_PROPOSAL_DRAFT,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_proposals(
    db: Session, project_id: int, status: str | None = None
) -> list:
    """Предложения ИИ по проекту, новые сверху (ТЗ п.105)."""
    from app.db.models import AiProposal

    query = db.query(AiProposal).filter(AiProposal.project_id == project_id)
    if status:
        if status not in domain.AI_PROPOSAL_STATUSES:
            raise AiError(f"Неизвестный статус предложения: {status}")
        query = query.filter(AiProposal.status == status)
    return query.order_by(AiProposal.id.desc()).all()


def accept(db: Session, proposal_id: int) -> object:
    """Подтвердить предложение и применить его через прикладной API (ТЗ п.104)."""
    from app.db.models import AiProposal

    proposal = db.get(AiProposal, proposal_id)
    if proposal is None:
        raise AiError(f"Предложение не найдено: {proposal_id}")
    if proposal.status != domain.AI_PROPOSAL_DRAFT:
        raise AiError(
            f"Предложение уже рассмотрено ({proposal.status}): повторное "
            "применение невозможно (ТЗ п.104)."
        )
    note = ""
    if proposal.action:
        note = _apply_action(db, proposal)
    proposal.status = domain.AI_PROPOSAL_ACCEPTED
    proposal.decided_at = _now()
    proposal.decision_note = note
    record_event(
        db, proposal.project_id, domain.HISTORY_AI_ACTION_APPLIED,
        f"Оператор принял предложение ИИ «{proposal.code}»: {note or 'без изменения данных'} "
        "(ТЗ п.104, 105)",
        entity_type="ai_proposal", entity_id=proposal.id,
        payload={"code": proposal.code, "action": proposal.action, "note": note},
    )
    db.commit()
    db.refresh(proposal)
    return proposal


def reject(db: Session, proposal_id: int, reason: str = "") -> object:
    """Отклонить предложение (ТЗ п.104)."""
    from app.db.models import AiProposal

    proposal = db.get(AiProposal, proposal_id)
    if proposal is None:
        raise AiError(f"Предложение не найдено: {proposal_id}")
    if proposal.status != domain.AI_PROPOSAL_DRAFT:
        raise AiError(
            f"Предложение уже рассмотрено ({proposal.status}) (ТЗ п.104)."
        )
    proposal.status = domain.AI_PROPOSAL_REJECTED
    proposal.decided_at = _now()
    proposal.decision_note = (reason or "").strip() or "отклонено оператором"
    record_event(
        db, proposal.project_id, domain.HISTORY_AI_ACTION_APPLIED,
        f"Оператор отклонил предложение ИИ «{proposal.code}»: "
        f"{proposal.decision_note} (ТЗ п.104)",
        entity_type="ai_proposal", entity_id=proposal.id,
        payload={"code": proposal.code, "reason": proposal.decision_note},
    )
    db.commit()
    db.refresh(proposal)
    return proposal


def _apply_action(db: Session, proposal) -> str:
    """Применить предложенное изменение через сервисы (ТЗ п.104).

    ИИ не обращается к базе напрямую: изменение выполняет сервис, который
    проверяет данные и пишет историю проекта.
    """
    from app.core.services import link_service

    action = proposal.action or {}
    kind = action.get("kind")
    if kind == domain.AI_ACTION_LINK_DOCUMENTS:
        try:
            link_service.link_documents(
                db,
                document_id=action["document_id"],
                related_document_id=action["related_document_id"],
                link_role=action.get("link_role", domain.LINK_ROLE_FINALIZES),
            )
        except link_service.MaterialError as exc:
            # Проверки сервиса — последнее слово: предложение остаётся
            # черновиком, данные не меняются (ТЗ п.104).
            db.rollback()
            raise AiError(str(exc)) from exc
        return "документы связаны"
    raise AiError(
        f"Предложенное изменение «{kind}» система применить не умеет: "
        "подтверждённые данные меняются только через известные операции "
        "(ТЗ п.104)."
    )


def _now():
    from app.config import utcnow

    return utcnow()
