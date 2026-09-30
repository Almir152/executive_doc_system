"""Тесты нумерации и дат документов. ТЗ п.42, 43, 49."""

from datetime import date, datetime, timedelta

import pytest

from app.core import domain
from app.core.services import document_service as service
from app.db.models import Document


# =====================================================================
# НУМЕРАЦИЯ (ТЗ п.42)
# =====================================================================


def test_number_is_proposed_for_each_numbered_type(db, project):
    """Нумеруются АОСР, АООК, АОУСИТО и акты испытаний (ТЗ п.42)."""
    assert set(domain.NUMBERED_DOC_TYPES) == {
        domain.DOC_TYPE_AOSR, domain.DOC_TYPE_AOOK,
        domain.DOC_TYPE_AOU_SITO, domain.DOC_TYPE_TEST_ACT,
    }
    for doc_type in domain.NUMBERED_DOC_TYPES:
        assert service.next_document_number(db, project.id, doc_type) == "1"


def test_numbering_is_sequential_within_project(db, project, direction):
    """Номера идут подряд внутри проекта."""
    from app.db.models import Project

    other = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other)
    db.commit()

    first = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    second = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    assert (first.number, second.number) == ("1", "2")
    assert service.next_document_number(db, project.id, domain.DOC_TYPE_AOSR) == "3"
    assert service.next_document_number(db, other.id, domain.DOC_TYPE_AOSR) == "1", (
        "нумерация ведётся отдельно по проектам"
    )


def test_numbering_is_independent_per_document_type(db, project):
    """АОСР №1 и АООК №1 не конфликтуют: вид входит в уникальность."""
    aosr = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    aook = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOOK)
    assert (aosr.number, aook.number) == ("1", "1")
    assert service.next_document_number(db, project.id, domain.DOC_TYPE_AOOK) == "2"


def test_deleted_draft_number_is_reused(db, project):
    """Пропуск в номерах выглядел бы как утраченный документ."""
    document = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    db.delete(document)
    db.commit()

    assert service.next_document_number(db, project.id, domain.DOC_TYPE_AOSR) == "1"


def test_operator_may_change_the_proposed_number(db, project):
    """Оператор вправе изменить предложенный номер (ТЗ п.42)."""
    document = service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15"
    )
    assert document.number == "15"
    assert service.next_document_number(db, project.id, domain.DOC_TYPE_AOSR) == "1"


def test_duplicate_number_in_project_is_rejected(db, project):
    """Два акта с одинаковым номером сделали бы комплект неоднозначным."""
    service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="7")
    with pytest.raises(service.DocumentNumberError, match="уже есть в проекте"):
        service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="7"
        )
    assert db.query(Document).count() == 1


@pytest.mark.parametrize("number", ["", "   ", "двенадцать", "1а", "1.2"])
def test_numbered_documents_require_numeric_number(db, project, number):
    """Последовательная нумерация предполагает число (ТЗ п.42)."""
    with pytest.raises(service.DocumentNumberError):
        service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, number=number
        )


def test_unnumbered_type_is_reported(db, project):
    """Нельзя нумеровать документ, который ТЗ не нумерует."""
    with pytest.raises(service.DocumentNumberError, match="не нумеруется"):
        service.next_document_number(db, project.id, "ПРОЧЕЕ")


def test_unknown_document_type_is_rejected(db, project):
    with pytest.raises(service.DocumentNumberError, match="Неизвестный вид"):
        service.create_document(db, project.id, doc_type="АКТ_ЧТО_ТО")


def test_document_of_unknown_project_is_rejected(db):
    with pytest.raises(service.DocumentNumberError, match="Проект не найден"):
        service.create_document(db, 999999, doc_type=domain.DOC_TYPE_AOSR)


# =====================================================================
# ДАТЫ (ТЗ п.43)
# =====================================================================


def test_document_date_is_stored_as_entered(db, project):
    """Дату вводит оператор, система её не меняет (ТЗ п.43)."""
    entered = date(2024, 3, 15)
    document = service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=entered
    )
    assert document.doc_date == entered


def test_document_without_date_is_allowed(db, project):
    """Дата не обязательна при создании черновика."""
    document = service.create_document(db, project.id, doc_type=domain.DOC_TYPE_AOSR)
    assert document.doc_date is None


def test_future_date_is_rejected(db, project):
    """Дата в будущем — почти наверняка опечатка (ТЗ п.43)."""
    future = date.today() + timedelta(days=1)
    with pytest.raises(service.DocumentNumberError, match="позже сегодняшней"):
        service.create_document(
            db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=future
        )
    assert db.query(Document).count() == 0


def test_date_is_not_replaced_by_today(db, project):
    """Система не подставляет текущую дату вместо введённой (ТЗ п.43)."""
    old = date(2019, 1, 31)
    document = service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=old
    )
    db.expire_all()
    stored = db.query(Document).filter(Document.id == document.id).one()
    assert stored.doc_date == old
    assert stored.doc_date != date.today()


def test_datetime_is_accepted_and_trimmed_to_date(db, project):
    """Поле в интерфейсе может прийти как datetime — берётся дата."""
    value = datetime(2024, 5, 20, 15, 30)
    document = service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=value
    )
    assert document.doc_date == date(2024, 5, 20)


def test_today_is_accepted(db, project):
    """Сегодняшняя дата не считается опечаткой."""
    today = date.today()
    document = service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=today
    )
    assert document.doc_date == today


# =====================================================================
# ПЕРЕЧЕНЬ ДОКУМЕНТОВ (ТЗ п.49)
# =====================================================================


def test_documents_are_listed_by_type_and_number(db, project):
    """Порядок документов стабилен (ТЗ п.49)."""
    for doc_type, number in (
        (domain.DOC_TYPE_AOOK, "2"),
        (domain.DOC_TYPE_AOSR, "10"),
        (domain.DOC_TYPE_AOSR, "2"),
    ):
        service.create_document(
            db, project.id, doc_type=doc_type, number=number
        )

    listed = [
        (d.doc_type, d.number) for d in service.list_documents(db, project.id)
    ]
    assert listed == [
        (domain.DOC_TYPE_AOSR, "2"),
        (domain.DOC_TYPE_AOSR, "10"),
        (domain.DOC_TYPE_AOOK, "2"),
    ], "виды идут в порядке ТЗ, числа сортируются как числа, а не как строки"


# =====================================================================
# ДОКУМЕНТЫ В ОКНЕ ПРОЕКТА (ТЗ п.16, 42, 43)
# =====================================================================


@pytest.mark.gui
def test_project_window_creates_document_with_proposed_number(
    db, project, qapp, monkeypatch
):
    """Окно предлагает номер и создаёт документ (ТЗ п.42)."""
    from PyQt6.QtWidgets import QDialog, QInputDialog

    from app.db.models import Document
    from app.ui.project_window import ProjectWindow

    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("АОСР", True)),
    )
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window = ProjectWindow(db, project.id)
    window.show()
    window.add_document()

    documents = db.query(Document).all()
    assert len(documents) == 1
    assert documents[0].doc_type == domain.DOC_TYPE_AOSR
    assert documents[0].number == "1"
    assert window.documents_table.rowCount() == 1
    assert window.documents_table.item(0, 1).text() == "1"
    assert window.documents_table.item(0, 2).text() == "—", "дата не подставлена сама"
    window.close()


@pytest.mark.gui
def test_project_window_document_creation_is_recorded(db, project, qapp, monkeypatch):
    """Создание документа попадает в историю проекта (ТЗ п.86)."""
    from PyQt6.QtWidgets import QDialog, QInputDialog

    from app.core.services import project_service
    from app.ui.project_window import ProjectWindow

    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("АООК", True))
    )
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window = ProjectWindow(db, project.id)
    window.show()
    window.add_document()

    events = project_service.list_events(db, project.id)
    assert [e.event_type for e in events] == ["document_created"]
    assert "АООК № 1" in events[0].message
    window.close()


@pytest.mark.gui
def test_project_window_rejects_future_date(db, project, qapp, monkeypatch):
    """Дата в будущем не сохраняется, оператор получает объяснение."""
    from datetime import timedelta

    from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox

    from app.db.models import Document
    from app.ui.project_window import ProjectWindow

    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("АОСР", True))
    )
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text: shown.append(text),
    )

    future = (date.today() + timedelta(days=3)).strftime("%d.%m.%Y")
    window = ProjectWindow(db, project.id)
    window.show()

    original_values = ProjectWindow._document_values

    def values_with_future_date(window, dialog):
        values = original_values(window, dialog)
        if values is not None:
            values["doc_date"] = date.today() + timedelta(days=3)
        return values

    monkeypatch.setattr(ProjectWindow, "_document_values", values_with_future_date)
    window.add_document()

    assert db.query(Document).count() == 0
    assert shown, "оператор не получил сообщение о недопустимой дате"
    assert "позже сегодняшней" in shown[0]
    assert future  # дата будущего действительно выглядит корректно
    window.close()


@pytest.mark.gui
def test_project_window_saves_operator_date(db, project, qapp, monkeypatch):
    """Введённая оператором дата сохраняется в перечне (ТЗ п.43)."""
    from PyQt6.QtWidgets import QDialog, QInputDialog

    from app.db.models import Document
    from app.ui.project_window import ProjectWindow

    monkeypatch.setattr(
        QInputDialog, "getItem", staticmethod(lambda *a, **k: ("АОСР", True))
    )
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    entered = date(2023, 11, 7)
    window = ProjectWindow(db, project.id)
    window.show()

    original_values = ProjectWindow._document_values

    def values_with_date(window, dialog):
        values = original_values(window, dialog)
        if values is not None:
            values["doc_date"] = entered
        return values

    monkeypatch.setattr(ProjectWindow, "_document_values", values_with_date)
    window.add_document()

    assert db.query(Document).one().doc_date == entered
    assert window.documents_table.item(0, 2).text() == "07.11.2023"
    window.close()


def test_parse_ru_date_accepts_common_formats():
    from app.ui.project_window import parse_ru_date

    assert parse_ru_date("07.11.2023") == date(2023, 11, 7)
    assert parse_ru_date("7.11.23") == date(2023, 11, 7)
    assert parse_ru_date("2023-11-07") == date(2023, 11, 7)
    assert parse_ru_date("  ") is None


def test_parse_ru_date_rejects_garbage():
    from app.ui.project_window import parse_ru_date

    with pytest.raises(ValueError, match="не распознана"):
        parse_ru_date("вчера")
