"""Тесты формы документа: черновик, подписанты, решение по п.64.

ТЗ п.63, 64, 66, 96.
"""

from datetime import date

import pytest

from app.core import domain
from app.core.services import document_service, form_service
from app.db.models import DocumentVersion, NormativeForm, SignatureBlock


@pytest.fixture
def aosr(db, project):
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _complete_payload(**extra) -> dict:
    """Набор данных, проходящий проверку обязательных полей АОСР."""
    payload = {
        "object_name": "Корпус 2",
        "address": "г. Москва",
        "work_description": "Армирование стен, 120 м²",
        "section_refs": "КЖ",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "work_volume": "120 м² бетона Б25",
        "has_defects": "Нет",
        "conclusion": "Работы выполнены в полном объёме",
        "work_performer": "ООО «Строй»",
    }
    payload.update(extra)
    return payload


# =====================================================================
# ЧЕРНОВИК (ТЗ п.66)
# =====================================================================


def test_draft_is_saved_even_when_incomplete(db, aosr):
    """Черновик по определению неполон: его сохранение не должно падать."""
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2"})
    assert form_service.load_draft(db, aosr.id) == {"object_name": "Корпус 2"}


def test_draft_is_updated_in_place(db, aosr):
    """Повторное сохранение не плодит версии: черновик один."""
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2"})
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2", "address": "Москва"})

    assert db.query(DocumentVersion).count() == 1
    assert form_service.load_draft(db, aosr.id)["address"] == "Москва"


def test_draft_can_be_saved_with_validation(db, aosr):
    """Явно запрошенная проверка сообщает, чего не хватает."""
    with pytest.raises(form_service.FormError, match="обязательное поле"):
        form_service.save_draft(db, aosr.id, {}, validate=True)


def test_draft_with_validation_passes_when_complete(db, aosr):
    form_service.save_draft(db, aosr.id, _complete_payload(), validate=True)
    assert len(form_service.load_draft(db, aosr.id)) == 9


def test_unsaved_work_is_detected(db, aosr):
    """Закрытие незавершённой формы должно предлагать сохранение (ТЗ п.66)."""
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2"})

    assert not form_service.has_unsaved_work(db, aosr.id, {"object_name": "Корпус 2"})
    assert form_service.has_unsaved_work(db, aosr.id, {"object_name": "Корпус 3"})
    assert form_service.has_unsaved_work(db, aosr.id, {})


def test_empty_document_has_no_unsaved_work(db, aosr):
    """Новый документ без правок не должен просить сохранить пустоту."""
    assert not form_service.has_unsaved_work(db, aosr.id, {})


def test_issued_document_is_not_editable(db, aosr):
    """Выпущенная версия зафиксирована (ТЗ п.54, 85)."""
    aosr.status = domain.DOC_STATUS_ISSUED
    db.commit()

    with pytest.raises(form_service.FormError, match="выпущен"):
        form_service.save_draft(db, aosr.id, {"object_name": "Правка"})


# =====================================================================
# ПРОВЕРКА ОБЯЗАТЕЛЬНЫХ ПОЛЕЙ (ТЗ п.96)
# =====================================================================


def test_empty_form_reports_required_fields(db, aosr):
    """Пустая форма перечисляет незаполненные обязательные поля."""
    problems = form_service.check_payload(db, aosr.id, {})
    assert problems, "пустая форма обязана давать список проблем"
    assert any("Наименование объекта" in problem for problem in problems)


def test_whitespace_does_not_count_as_filled(db, aosr):
    """Пробелы не считаются заполнением: иначе форма уйдёт пустой."""
    problems = form_service.check_payload(db, aosr.id, {"object_name": "   "})
    assert any("Наименование объекта" in problem for problem in problems)


def test_form_without_description_cannot_be_issued(db, project):
    """Без описания формы неизвестно, что заполнено (ТЗ п.96)."""
    form = db.query(NormativeForm).filter(
        NormativeForm.doc_type == domain.DOC_TYPE_AOSR
    ).one()
    form.definition = None
    db.commit()

    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    problems = form_service.check_payload(db, document.id, _complete_payload())
    assert any("нет загруженной нормативной формы" in p for p in problems)


def test_pinned_form_version_is_used(db, aosr):
    """Документ проверяется по той форме, по которой он заполняется (ТЗ п.96)."""
    form = db.query(NormativeForm).filter(
        NormativeForm.doc_type == domain.DOC_TYPE_AOSR
    ).one()
    definition = dict(form.definition)
    definition["sections"] = [
        {
            "number": 1, "title": "Проверка",
            "blocks": [{"key": "my_field", "label": "Моё поле", "required": True}],
        }
    ]
    form.definition = definition
    db.commit()
    aosr.form_version_id = form.id
    db.commit()

    problems = form_service.check_payload(db, aosr.id, _complete_payload())
    assert problems == ["Моё поле — обязательное поле (ТЗ п.96)."]


# =====================================================================
# ПОДПИСАНТЫ (ТЗ п.63)
# =====================================================================


def test_two_signature_blocks_are_independent(db, aosr):
    """«Сдал» и «Принял» заполняются независимо (ТЗ п.63)."""
    form_service.save_signature_block(
        db, aosr.id, form_service.BLOCK_HANDED_OVER,
        position="ГИП", full_name="Иванов И. И.", sign_place="Москва",
    )
    assert form_service.get_signature_block(
        db, aosr.id, form_service.BLOCK_ACCEPTED
    ) is None, "блок «Принял» не должен появляться сам"

    form_service.save_signature_block(
        db, aosr.id, form_service.BLOCK_ACCEPTED,
        position="Заказчик", full_name="Петров П. П.", sign_place="Москва",
    )
    assert db.query(SignatureBlock).count() == 2


def test_signature_block_is_updated_not_duplicated(db, aosr):
    for _ in range(2):
        form_service.save_signature_block(
            db, aosr.id, form_service.BLOCK_HANDED_OVER,
            position="ГИП", full_name="Иванов И. И.", sign_place="Москва",
        )
    stored = form_service.get_signature_block(
        db, aosr.id, form_service.BLOCK_HANDED_OVER
    )
    assert db.query(SignatureBlock).count() == 1
    assert stored.full_name == "Иванов И. И."


def test_signature_block_requires_no_partial_names(db, aosr):
    """Пустые значения сохраняются как отсутствие, а не как пробелы."""
    form_service.save_signature_block(
        db, aosr.id, form_service.BLOCK_HANDED_OVER,
        position="  ", full_name="Иванов И. И.", sign_place="",
    )
    stored = form_service.get_signature_block(
        db, aosr.id, form_service.BLOCK_HANDED_OVER
    )
    assert stored.position is None
    assert stored.sign_place is None


def test_unknown_signature_block_is_rejected(db, aosr):
    with pytest.raises(form_service.FormError, match="Неизвестный блок"):
        form_service.save_signature_block(
            db, aosr.id, "Подписал", position="ГИП", full_name="Иванов",
            sign_place=None,
        )


def test_signature_block_of_other_document_is_not_reachable(db, project, aosr):
    """Блок привязан к своему документу (ТЗ п.63)."""
    other = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK
    )
    form_service.save_signature_block(
        db, other.id, form_service.BLOCK_HANDED_OVER,
        position="ГИП", full_name="Сидоров", sign_place="Москва",
    )
    assert form_service.get_signature_block(
        db, aosr.id, form_service.BLOCK_HANDED_OVER
    ) is None


# =====================================================================
# НЕЗАПОЛНЕННЫЙ ПРЕДСТАВИТЕЛЬ ЭКСПЛУАТАЦИИ (ТЗ п.64)
# =====================================================================


def test_exploitation_choice_belongs_to_document(db, project, aosr):
    """Решение относится к конкретному документу (ТЗ п.64)."""
    other = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK
    )
    form_service.set_exploitation_missing_choice(
        db, aosr.id, form_service.MISSING_KEEP_PLACE
    )

    db.expire_all()
    assert aosr.exploitation_missing_choice == form_service.MISSING_KEEP_PLACE
    assert other.exploitation_missing_choice is None


def test_exploitation_choice_must_be_one_of_two(db, aosr):
    """Иных вариантов по ТЗ п.64 нет: иначе печать станет непредсказуемой."""
    form_service.set_exploitation_missing_choice(
        db, aosr.id, form_service.MISSING_OMIT_BLOCK
    )
    with pytest.raises(form_service.FormError, match="одним из двух"):
        form_service.set_exploitation_missing_choice(db, aosr.id, "скрыть")


def test_exploitation_choice_of_unknown_document_is_rejected(db):
    with pytest.raises(form_service.FormError, match="Документ не найден"):
        form_service.set_exploitation_missing_choice(db, 999999, "keep_place")


# =====================================================================
# СВЯЗЬ С ДОКУМЕНТОМ
# =====================================================================


def test_draft_and_blocks_survive_document_reload(db, aosr):
    """Черновик и подписанты относятся к документу и перечитываются."""
    form_service.save_draft(db, aosr.id, {"object_name": "Корпус 2"})
    form_service.save_signature_block(
        db, aosr.id, form_service.BLOCK_ACCEPTED,
        position="Заказчик", full_name="Петров П. П.", sign_place="Москва",
    )
    db.expire_all()

    assert form_service.load_draft(db, aosr.id) == {"object_name": "Корпус 2"}
    stored = form_service.get_signature_block(
        db, aosr.id, form_service.BLOCK_ACCEPTED
    )
    assert stored.full_name == "Петров П. П."
    assert stored.sign_place == "Москва"
    assert date is not None
