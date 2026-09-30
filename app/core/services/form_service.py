"""Форма документа: данные полей, блоки подписантов, черновик. ТЗ п.63, 64, 66.

Форма открывается в рабочей области (ТЗ п.65) и хранится как версия
документа: незавершённая форма не теряется при закрытии (ТЗ п.66), а
выпущенная версия остаётся неизменной (ТЗ п.54, 85).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services import issue_service
from app.db.models import Document, DocumentVersion, NormativeForm, SignatureBlock

BLOCK_HANDED_OVER = "Сдал"
BLOCK_ACCEPTED = "Принял"
SIGNATURE_BLOCKS = (BLOCK_HANDED_OVER, BLOCK_ACCEPTED)

# ТЗ п.64: решение по незаполненному представителю эксплуатации.
MISSING_KEEP_PLACE = "keep_place"
MISSING_OMIT_BLOCK = "omit_block"
MISSING_CHOICES = (MISSING_KEEP_PLACE, MISSING_OMIT_BLOCK)

# Поле представителя эксплуатирующей организации (ТЗ п.40, 64).
EXPLOITATION_FIELD = "exploitation_rep"


class FormError(Exception):
    """Ошибка работы с формой с текстом для оператора."""


def get_signature_block(db: Session, document_id: int, block: str) -> SignatureBlock | None:
    if block not in SIGNATURE_BLOCKS:
        raise FormError(f"Неизвестный блок подписантов: {block}")
    return db.scalar(
        select(SignatureBlock).where(
            SignatureBlock.document_id == document_id,
            SignatureBlock.block == block,
        )
    )


def save_signature_block(
    db: Session,
    document_id: int,
    block: str,
    *,
    position: str | None,
    full_name: str | None,
    sign_place: str | None,
) -> SignatureBlock:
    """Сохранить один из двух независимых блоков (ТЗ п.63).

    Блоки независимы: заполненный «Сдал» не требует заполненного «Принял».
    """
    if block not in SIGNATURE_BLOCKS:
        raise FormError(f"Неизвестный блок подписантов: {block}")
    if db.get(Document, document_id) is None:
        raise FormError(f"Документ не найден: {document_id}")

    values = {
        "position": (position or "").strip() or None,
        "full_name": (full_name or "").strip() or None,
        "sign_place": (sign_place or "").strip() or None,
    }
    existing = get_signature_block(db, document_id, block)
    if existing is None:
        existing = SignatureBlock(document_id=document_id, block=block, **values)
        db.add(existing)
    else:
        for key, value in values.items():
            setattr(existing, key, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise FormError(f"Не удалось сохранить блок «{block}».") from exc
    return existing


def set_exploitation_missing_choice(db: Session, document_id: int, choice: str) -> str:
    """Запомнить решение по незаполненному представителю (ТЗ п.64).

    Решение относится к конкретному документу, а не к форме или проекту.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    if choice not in MISSING_CHOICES:
        raise FormError(
            "Решение по представителю эксплуатации должно быть одним из двух: "
            "оставить место для ручного заполнения или убрать блок из печати (ТЗ п.64)."
        )
    document.exploitation_missing_choice = choice
    db.commit()
    return choice


def draft_version(db: Session, document_id: int) -> DocumentVersion | None:
    """Текущая рабочая версия документа (невыпущенная).

    Выпущенная версия рабочей не является: после выпуска правится новая
    (ТЗ п.85, 93).
    """
    return db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.issued_at.is_(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )


def actual_version(db: Session, document_id: int) -> DocumentVersion | None:
    """Версия документа, которая попадёт в комплект (ТЗ п.93).

    У выпущенного документа — зафиксированная версия, у рабочего —
    черновик. Проверки перед выгрузкой (ТЗ п.82) должны смотреть именно на
    неё: выгружается зафиксированное содержимое, а не то, что оператор
    правит сейчас.
    """
    issued = db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.issued_at.is_not(None),
        )
        .order_by(DocumentVersion.version_no.desc())
    )
    return issued if issued is not None else draft_version(db, document_id)


def actual_payload(db: Session, document_id: int) -> dict:
    """Содержимое документа, подлежащее выгрузке (ТЗ п.82, 93)."""
    version = actual_version(db, document_id)
    return dict(version.payload) if version is not None else {}


def available_versions(db: Session, doc_type: str) -> list[NormativeForm]:
    """Все версии формы типа документа, свежая первой (ТЗ п.96).

    Нужен панели формы документа: версия выбирается явно, потому что
    структура формы не изменяется под печать (ТЗ п.62), а различается
    только версией.
    """
    return list(
        db.scalars(
            select(NormativeForm)
            .where(NormativeForm.doc_type == doc_type)
            .order_by(NormativeForm.version.desc())
        )
    )


def pin_form_version(db: Session, document_id: int, version_id: int | None) -> str:
    """Закрепить за документом версию формы (ТЗ п.96).

    ``None`` возвращает документ к актуальной версии справочника.
    Выпущенный документ закреплённую версию не меняет: иначе уже
    зафиксированное содержимое перестало бы соответствовать форме, по
    которой оно составлено.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise ValueError("документ не найден")
    if document.status == domain.DOC_STATUS_ISSUED:
        return "документ выпущен: версия формы зафиксирована (ТЗ п.96)"
    if version_id is not None:
        form = db.get(NormativeForm, version_id)
        if form is None or form.doc_type != document.doc_type:
            return "выбранная версия относится к другому типу документа"
        document.form_version_id = version_id
    else:
        document.form_version_id = None
    db.commit()
    return ""


def current_definition(db: Session, document: Document) -> dict | None:
    """Описание формы документа: закреплённое или текущее (ТЗ п.96).

    У документа может быть закреплена конкретная версия формы: она
    сохраняется, даже когда в справочнике появилась новая.
    """
    if document.form_version is not None:
        return document.form_version.definition
    return db.scalar(
        select(NormativeForm)
        .where(
            NormativeForm.doc_type == document.doc_type,
            NormativeForm.is_current.is_(True),
        )
        .order_by(NormativeForm.version.desc())
    ).definition if _has_current_form(db, document.doc_type) else None


def _has_current_form(db: Session, doc_type: str) -> bool:
    return db.scalar(
        select(NormativeForm.id).where(
            NormativeForm.doc_type == doc_type,
            NormativeForm.is_current.is_(True),
        ).limit(1)
    ) is not None


def check_payload(
    db: Session, document_id: int, payload: dict
) -> list[str]:
    """Незаполненные обязательные поля формы.

    Проверяются по описанию нормативной формы (ТЗ п.96): обязательность
    задана формой, а не кодом. Отсутствие описания — тоже проблема: без него
    выпустить документ нельзя, ведь неизвестно, что заполнено.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")

    definition = current_definition(db, document)
    if not definition:
        return [
            f"Для вида «{domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)}» "
            "нет загруженной нормативной формы. Выпуск невозможен (ТЗ п.96)."
        ]

    problems: list[str] = []
    values = payload or {}
    seen = 0
    for section in definition.get("sections", []):
        # Поля описания лежат в blocks; fields — синоним, встречающийся в
        # сторонних описаниях форм.
        fields = list(section.get("blocks") or []) + list(section.get("fields") or [])
        for field in fields:
            key = field.get("key")
            if not key:
                continue
            seen += 1
            if not field.get("required"):
                continue
            if _is_empty(values.get(key)):
                problems.append(
                    f"{field.get('label') or key} — обязательное поле (ТЗ п.96)."
                )
    if not seen:
        return [
            f"Описание формы «{definition.get('title') or document.doc_type}» "
            "не содержит полей. Выпуск невозможен: нечего проверять (ТЗ п.96)."
        ]
    return problems


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def form_has_exploitation_block(db: Session, document_id: int) -> bool:
    """Есть ли в форме представитель эксплуатирующей организации (ТЗ п.40)."""
    return _exploitation_capability(db, document_id)["present"]


def form_allows_exploitation_omission(db: Session, document_id: int) -> bool:
    """Разрешено ли убрать незаполненный блок из печатной формы (ТЗ п.64).

    Разрешение задаёт форма: убрать блок можно не везде.
    """
    return _exploitation_capability(db, document_id)["omittable"]


def _exploitation_capability(db: Session, document_id: int) -> dict:
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    for _, field in _iter_fields(db, document):
        if field["key"] == EXPLOITATION_FIELD:
            return {
                "present": True,
                "omittable": bool(field.get("omittable_if_empty")),
            }
    # Поля представителя в форме нет: выводить нечего (ТЗ п.40).
    return {"present": False, "omittable": False}


def needs_exploitation_decision(db: Session, document_id: int, payload: dict) -> bool:
    """Нужно ли спросить оператора о выводе поля перед сохранением (ТЗ п.64).

    Спрашивают один раз: если решение уже принято для этого документа,
    повторный вопрос при каждом сохранении неуместен.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    if document.exploitation_missing_choice is not None:
        return False
    if not form_allows_exploitation_omission(db, document_id):
        return False
    return _is_empty((payload or {}).get(EXPLOITATION_FIELD))



def _iter_fields(db: Session, document: Document):
    """Поля формы документа вместе с разделом, к которому они относятся."""
    definition = current_definition(db, document) or {}
    for section in definition.get("sections", []):
        blocks = list(section.get("blocks") or []) + list(section.get("fields") or [])
        for block in blocks:
            if block.get("key"):
                yield section, block


def save_draft(
    db: Session,
    document_id: int,
    payload: dict,
    *,
    validate: bool = False,
    exploitation_choice: str | None = None,
) -> DocumentVersion:
    """Сохранить незавершённую форму (ТЗ п.66).

    Черновик по определению заполнен частично, поэтому по умолчанию
    обязательные поля не проверяются. Явно запрошенная проверка выполняется
    всегда — по ней оператор узнаёт, что ещё нужно доделать.

    Решение по незаполненному представителю эксплуатации (ТЗ п.64)
    передаётся сюда же и сохраняется в одной транзакции с черновиком:
    форма не может сохраниться, если оператор не определил, как выводить
    этот блок.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise FormError(f"Документ не найден: {document_id}")
    if document.status == domain.DOC_STATUS_ISSUED:
        raise FormError(
            "Документ выпущен: его версия зафиксирована и не изменяется "
            "(ТЗ п.54, 85). Создайте новый документ для новой редакции."
        )

    if exploitation_choice is not None and exploitation_choice not in MISSING_CHOICES:
        raise FormError(
            "Решение по представителю эксплуатации должно быть одним из двух: "
            "оставить место для ручного заполнения или убрать блок из печати "
            "(ТЗ п.64)."
        )
    if exploitation_choice is None and needs_exploitation_decision(
        db, document_id, payload
    ):
        raise FormError(
            "Не принято решение, как выводить незаполненный блок представителя "
            "эксплуатации (ТЗ п.64): оставить пустую строку или убрать блок."
        )

    if validate:
        problems = check_payload(db, document_id, payload)
        if problems:
            raise FormError(
                "Форма заполнена не полностью:\n• " + "\n• ".join(problems)
            )

    version = draft_version(db, document_id)
    if version is None:
        # Номер продолжает последовательность версий, а не начинается с
        # единицы: после выпуска черновика уже нет, и version_no=1 конфликтовал
        # бы с зафиксированной версией (ТЗ п.91).
        next_no = issue_service.next_version_no(db, document_id)
        version = DocumentVersion(
            document_id=document_id, version_no=next_no, payload={}
        )
        db.add(version)
    version.payload = dict(payload)
    version.is_actual = True
    if document.form_version_id is not None:
        version.form_version_id = document.form_version_id
    if exploitation_choice is not None:
        document.exploitation_missing_choice = exploitation_choice

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise FormError("Не удалось сохранить форму.") from exc
    return version


def load_draft(db: Session, document_id: int) -> dict:
    """Данные незавершённой формы или пустой набор (ТЗ п.66)."""
    version = draft_version(db, document_id)
    return dict(version.payload) if version is not None else {}


def has_unsaved_work(db: Session, document_id: int, payload: dict) -> bool:
    """Есть ли несохранённые изменения формы (ТЗ п.66)."""
    return dict(payload or {}) != load_draft(db, document_id)
