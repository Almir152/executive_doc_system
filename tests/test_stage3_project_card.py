"""Тесты Этапа 3: карточка проекта, разделы, история. ТЗ п.17, 21, 22, 86."""

import pytest

from app.core.services import project_service as service
from app.db.models import (
    Document, DocumentVersion, HistoryEvent, Organization, ProjectSection,
    Representative, SectionKind,
)


def _card(db, project, **kwargs):
    payload = {"title": "Строительство корпуса"}
    payload.update(kwargs)
    return service.update_project_card(db, project.id, **payload)


# =====================================================================
# КАРТОЧКА ПРОЕКТА (ТЗ п.17)
# =====================================================================


def test_card_update_changes_static_data(db, project):
    """Статические данные меняются отдельной операцией (ТЗ п.17)."""
    customer = Organization(short_name="Заказчик", inn="7707083893")
    contractor = Organization(short_name="Генподрядчик", inn="7802312751")
    partner = Organization(short_name="Субподрядчик", inn="5024065580")
    db.add_all([customer, contractor, partner])
    db.commit()

    _card(
        db, project,
        title="  Корпус 2  ",
        address="  г. Москва, ул. Ленина 1  ",
        customer_org_id=customer.id,
        general_contractor_org_id=contractor.id,
        organization_ids=[customer.id, partner.id],
    )
    db.expire_all()

    stored = service.get_project(db, project.id)
    assert stored.title == "Корпус 2", "наименование не обрезано"
    assert stored.address == "г. Москва, ул. Ленина 1"
    assert stored.customer_org_id == customer.id
    assert stored.general_contractor_org_id == contractor.id
    assert sorted(o.id for o in stored.organizations) == sorted(
        [customer.id, partner.id]
    ), "необходимые организации не сохранены"


def test_card_rejects_empty_title(db, project):
    """Наименование обязательно: без него проект не опознаётся в комплектах."""
    for bad in ("", "   ", "\t\n"):
        with pytest.raises(service.ProjectError, match="Наименование"):
            _card(db, project, title=bad)


def test_card_rejects_unknown_organizations(db, project):
    """Организация из несуществующего справочника — отказ, а не пустая ссылка.

    Иначе в перечне необходимых организаций появилась бы неразрешимая
    ссылка, и комплект собрался бы с дырой (ТЗ п.17, 20).
    """
    with pytest.raises(service.ProjectError, match="не найдены в справочнике"):
        _card(db, project, organization_ids=[999999])

    db.rollback()
    assert service.get_project(db, project.id) is not None
    assert service.get_project(db, project.id).organizations == []


def test_card_update_is_recorded_in_history(db, project):
    """Правка карточки попадает в историю проекта (ТЗ п.86)."""
    _card(db, project, title="Первое")
    _card(db, project, title="Второе")

    events = service.list_events(db, project.id)
    assert len(events) == 2, "каждая правка должна оставлять след"
    assert all(e.event_type == "project_card_updated" for e in events)
    assert "Второе" in events[0].message, "новейшее событие должно быть первым"


# =====================================================================
# ПРОЕКТНАЯ ДОКУМЕНТАЦИЯ (ТЗ п.21, 22)
# =====================================================================


def test_sections_count_is_not_limited(db, project):
    """ТЗ п.21: количество разделов не ограничено."""
    kind = db.query(SectionKind).first()
    codes = ["ГП", "АР", "КЖ", "КМ", "ВК", "ВВ", "ОТ", "ТС", "НВ", "НК", "ЭС"]
    for code in codes:
        service.add_section(db, project.id, kind_id=kind.id, code=code, name=f"Раздел {code}")

    sections = service.list_sections(db, project.id)
    assert len(sections) == len(codes)


def test_section_code_is_unique_within_project(db, project):
    """Два раздела с одинаковым кодом сделали бы папки комплекта неразличимыми."""
    kind = db.query(SectionKind).first()
    service.add_section(db, project.id, kind_id=kind.id, code="КЖ", name="Первый")

    with pytest.raises(service.ProjectError, match="уже есть в проекте"):
        service.add_section(db, project.id, kind_id=kind.id, code="КЖ", name="Второй")

    assert len(service.list_sections(db, project.id)) == 1


def test_section_code_may_repeat_in_another_project(db, project, direction):
    """Один и тот же код в разных проектах — обычное дело, не ошибка."""
    from app.db.models import Project

    other = Project(direction_id=direction.id, title="Другой объект", address="М")
    db.add(other)
    db.commit()
    kind = db.query(SectionKind).first()

    service.add_section(db, project.id, kind_id=kind.id, code="КЖ", name="Первый")
    service.add_section(db, other.id, kind_id=kind.id, code="КЖ", name="Второй")

    assert len(service.list_sections(db, project.id)) == 1
    assert len(service.list_sections(db, other.id)) == 1


@pytest.mark.parametrize("field", ["code", "name"])
def test_section_requires_code_and_name(db, project, field):
    """Пустой код или наименование раздела недопустимы (ТЗ п.21)."""
    kind = db.query(SectionKind).first()
    payload = {"kind_id": kind.id, "code": "КЖ", "name": "Конструкции"}
    payload[field] = "   "
    with pytest.raises(service.ProjectError):
        service.add_section(db, project.id, **payload)


def test_section_is_not_deleted_while_project_has_issued_versions(db, project):
    """Раздел относится к историческому результату (ТЗ п.54, 85)."""
    from app.config import utcnow

    kind = db.query(SectionKind).first()
    section = service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Конструкции"
    )
    document = Document(project_id=project.id, doc_type="АОСР", number="12")
    db.add(document)
    db.commit()
    db.add(
        DocumentVersion(
            document_id=document.id, version_no=1,
            payload={"номер": "12"}, issued_at=utcnow(), is_actual=True,
        )
    )
    db.commit()

    with pytest.raises(service.ProjectError, match="выпущенных версий"):
        service.delete_section(db, section.id)

    db.rollback()
    assert db.query(ProjectSection).count() == 1, "раздел потерян"


def test_section_is_deleted_when_project_has_no_issued_versions(db, project):
    """Без выпусков раздел — рабочий черновик и удаляется."""
    kind = db.query(SectionKind).first()
    section = service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Конструкции"
    )
    service.delete_section(db, section.id)
    assert db.query(ProjectSection).count() == 0


# =====================================================================
# ИСТОРИЯ (ТЗ п.86)
# =====================================================================


def test_history_requires_message(db, project):
    """Событие без описания бесполезно для оператора."""
    with pytest.raises(service.ProjectError, match="Описание"):
        service.record_event(db, project.id, "some_event", "   ")


def test_history_events_are_listed_newest_first(db, project):
    service.record_event(db, project.id, "created", "Проект создан")
    service.record_event(db, project.id, "section_added", "Добавлен раздел КЖ")
    db.commit()

    events = service.list_events(db, project.id)
    assert [e.event_type for e in events] == ["section_added", "created"]
    assert db.query(HistoryEvent).count() == 2


def test_history_is_removed_with_project(db, project):
    """История — часть проекта, а не самостоятельное накопление."""
    service.record_event(db, project.id, "created", "Проект создан")
    db.commit()
    service.delete_project(db, project.id)
    assert db.query(HistoryEvent).count() == 0


# =====================================================================
# РАБОЧЕЕ ОКНО ПРОЕКТА (ТЗ п.16, 17)
# =====================================================================


@pytest.mark.gui
def test_project_window_shows_card_sections_and_history(db, project, qapp):
    """Окно проекта показывает карточку, разделы и историю (ТЗ п.16, 17)."""
    from app.ui.project_window import ProjectWindow

    kind = db.query(SectionKind).first()
    service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ",
        name="Конструкции железобетонные", sheets="12",
    )
    service.record_event(db, project.id, "project_created", "Проект создан")
    db.commit()

    window = ProjectWindow(db, project.id)
    window.show()

    assert "Тестовый объект" in window.title_label.text()
    assert "Общестроительные" in window.card_label.text(), "направление не видно"
    assert window.sections_table.rowCount() == 1
    assert window.sections_table.item(0, 0).text() == "КЖ"
    assert window.sections_table.item(0, 4).text() == "12"
    assert window.history_table.rowCount() == 1
    assert "Проект создан" in window.history_table.item(0, 2).text()
    window.close()


@pytest.mark.gui
def test_project_window_survives_project_with_no_content(db, project, qapp):
    """Пустой проект должен открываться, а не падать (ТЗ п.16)."""
    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()
    assert window.sections_table.rowCount() == 0
    assert window.documents_hint.isVisible(), "подсказка о пустых разделах не показана"
    window.close()


@pytest.mark.gui
def test_project_window_reports_deleted_project(db, qapp):
    """Окно удалённого проекта показывает это, а не падает."""
    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, 999999)
    assert "не найден" in window.title_label.text()
    window.close()


@pytest.mark.gui
def test_project_window_is_scrollable_and_resizable(db, project, qapp):
    """Рабочее окно прокручивается и свободно меняет размер.

    Замечание приёмки: окно не влезало в экран, не уменьшалось и не
    прокручивалось, поэтому часть содержимого была недоступна.
    """
    from PyQt6.QtWidgets import QScrollArea

    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()
    assert window.findChild(QScrollArea) is not None, "нужен ползунок прокрутки"
    assert window.minimumWidth() <= 700, "окно должно уменьшаться"
    window.resize(700, 500)
    assert window.width() == 700
    window.close()


@pytest.mark.gui
def test_project_window_reload_shows_new_section(db, project, qapp):
    """Добавленный раздел появляется в окне после перезагрузки."""
    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()
    kind = db.query(SectionKind).first()
    service.add_section(db, project.id, kind_id=kind.id, code="КМ", name="Металл")
    window.reload()
    assert window.sections_table.rowCount() == 1
    assert window.sections_table.item(0, 0).text() == "КМ"
    window.close()


@pytest.mark.gui
def test_main_window_opens_project_window(db, project, qapp):
    """Двойной щелчок по проекту открывает рабочее окно (ТЗ п.16)."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)

    assert window.project_window is None, "окно не должно открываться само"
    window.open_project_window()

    assert window.project_window is not None
    assert "Тестовый объект" in window.project_window.title_label.text()
    # Окно должно быть самостоятельным, а не дочерним виджетом главного:
    # иначе show() прячет его за центральным виджетом и оператор ничего не
    # видит (ТЗ п.16).
    assert window.project_window.isWindow() is True
    assert window.project_window.isVisible() is True
    window.close()


@pytest.mark.gui
def test_project_window_can_be_reopened_after_close(db, project, qapp):
    """Повторное открытие не падает на уже удалённом окне (ТЗ п.16)."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)

    window.open_project_window()
    first = window.project_window
    first.close()
    qapp.processEvents()

    window.open_project_window()

    assert window.project_window is not None
    assert window.project_window is not first
    assert window.project_window.isWindow() is True
    window.close()


@pytest.mark.gui
def test_card_dialog_marks_organizations_with_search(db, project, qapp):
    """Необходимые организации отмечаются в списке с поиском (ТЗ п.17, 20)."""
    from app.ui.project_window import ProjectCardDialog

    customer = Organization(short_name="ЗАО «Заказчик»", inn="7701111111")
    partner = Organization(short_name="ООО «Подрядчик»", inn="7702222222")
    db.add_all([customer, partner])
    db.commit()
    service.update_project_card(
        db, project.id, title=project.title,
        organization_ids=[customer.id, partner.id],
    )

    dialog = ProjectCardDialog(db, service.get_project(db, project.id))
    picker = dialog.organizations_list

    assert picker.item_count() == 2
    assert sorted(picker.selected_values()) == sorted([customer.id, partner.id])
    assert dialog.values()["organization_ids"] == [customer.id, partner.id]
    picker.search.setText("подряд")
    assert dialog.values()["organization_ids"] == [customer.id, partner.id], (
        "поиск не должен сбрасывать отметки"
    )
    picker.set_selected_values([customer.id])
    assert dialog.values()["organization_ids"] == [customer.id]


@pytest.mark.gui
def test_card_dialog_returns_chosen_customer_and_contractor(db, project, qapp):
    """Заказчик и генподрядчик читаются из ReferencePicker (ТЗ п.17).

    Дефект: значения брались через Qt-метод ``currentData()``, который у
    ReferencePicker всегда None, поэтому выбранные организации терялись при
    сохранении карточки.
    """
    from app.ui.project_window import ProjectCardDialog

    customer = Organization(short_name="ЗАО «Заказчик»", inn="7703333333")
    contractor = Organization(short_name="ООО «Генподрядчик»", inn="7704444444")
    db.add_all([customer, contractor])
    db.commit()

    dialog = ProjectCardDialog(db, service.get_project(db, project.id))
    assert dialog.values()["customer_org_id"] is None

    dialog.customer_combo.set_current_data(customer.id)
    dialog.contractor_combo.set_current_data(contractor.id)

    values = dialog.values()
    assert values["customer_org_id"] == customer.id
    assert values["general_contractor_org_id"] == contractor.id


def test_duplicate_section_code_is_blocked_by_database(db, project):
    """Уникальность кода держит сама БД, а не только проверка в сервисе."""
    from sqlalchemy.exc import IntegrityError

    kind = db.query(SectionKind).first()
    db.add(ProjectSection(project_id=project.id, kind_id=kind.id, code="КЖ", name="А"))
    db.commit()
    db.add(ProjectSection(project_id=project.id, kind_id=kind.id, code="КЖ", name="Б"))

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_section_race_is_reported_as_readable_error(db, project, monkeypatch):
    """Гонка по коду раздела даёт оператору текст, а не исключение SQLAlchemy.

    Предварительная проверка в add_section не может защитить от двух
    одновременных добавлений; ограничение БД — единственная надёжная
    защита, значит её нарушение обязано превращаться в ProjectError.
    """
    from sqlalchemy.exc import IntegrityError

    kind = db.query(SectionKind).first()

    def racing_commit():
        raise IntegrityError("INSERT", {}, Exception("UNIQUE failed"))

    monkeypatch.setattr(db, "commit", racing_commit)
    with pytest.raises(service.ProjectError, match="уже есть в проекте"):
        service.add_section(db, project.id, kind_id=kind.id, code="КЖ", name="А")
    monkeypatch.undo()

    assert db.query(ProjectSection).count() == 0, "сессия осталась в сломанном состоянии"


@pytest.mark.gui
def test_project_window_add_section_through_dialogs(db, project, qapp, monkeypatch):
    """Добавление раздела из окна проекта доходит до БД.

    Справочник разделов не имеет поля sort_order: обращение к нему роняло
    окно при первом же добавлении, а сервисные тесты это не ловили.
    """
    import app.ui.project_window as project_window
    from app.ui.project_window import ProjectWindow

    kind = db.query(SectionKind).first()
    real_class = project_window.SectionDialog

    def factory(dbase, section=None, parent=None):
        dialog = real_class(dbase, section, parent)
        dialog.code.setText("КЖ")
        dialog.name.setText("Конструкции железобетонные")
        dialog.kind.set_current_data(kind.id)
        return dialog

    monkeypatch.setattr(project_window, "SectionDialog", factory)
    from PyQt6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window = ProjectWindow(db, project.id)
    window.show()
    window.add_section()

    assert db.query(ProjectSection).count() == 1
    assert window.sections_table.item(0, 0).text() == "КЖ"
    assert window.sections_table.item(0, 2).text() == "—", "организация не выбрана"
    window.close()


@pytest.mark.gui
def test_project_window_edit_section_changes_row(db, project, qapp, monkeypatch):
    """Правка раздела из окна проекта меняет реквизиты (ТЗ п.21).

    Раньше `update_section()` был доступен только из кода: оператор не мог
    исправить раздел, не редактируя базу.
    """
    import app.ui.project_window as project_window
    from PyQt6.QtWidgets import QDialog
    from app.ui.project_window import ProjectWindow

    kind = db.query(SectionKind).first()
    section = service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Старое наименование",
    )
    real_class = project_window.SectionDialog

    def factory(dbase, target=None, parent=None):
        dialog = real_class(dbase, target, parent)
        assert dialog.code.isReadOnly(), "код раздела при правке не меняется"
        dialog.name.setText("Новое наименование")
        dialog.sheets.setText("24")
        return dialog

    monkeypatch.setattr(project_window, "SectionDialog", factory)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window = ProjectWindow(db, project.id)
    window.show()
    window.sections_table.setCurrentCell(0, 0)
    window.edit_section()

    db.expire_all()
    assert db.get(ProjectSection, section.id).name == "Новое наименование"
    assert window.sections_table.item(0, 1).text() == "Новое наименование"
    assert window.sections_table.item(0, 4).text() == "24", "листы не обновились"
    window.close()


@pytest.mark.gui
def test_section_dialog_prefills_current_values(db, project, qapp):
    """Диалог правки открывается с текущими реквизитами раздела (ТЗ п.21)."""
    from app.ui.section_dialog import SectionDialog

    kind = db.query(SectionKind).first()
    # Организации и представители — справочники оператора, они не засеваются.
    org = Organization(short_name="ЗАО «Строй»", inn="7701234567")
    db.add(org)
    db.commit()
    designer = Representative(
        organization_id=org.id, position="ГИП", full_name="Иванов Иван Иванович",
    )
    db.add(designer)
    db.commit()
    section = service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Конструкции",
        organization_id=org.id, designer_rep_id=designer.id, sheets="12",
    )

    dialog = SectionDialog(db, section)

    assert dialog.code.text() == "КЖ"
    assert dialog.name.text() == "Конструкции"
    assert dialog.values() == {
        "code": "КЖ",
        "name": "Конструкции",
        "kind_id": kind.id,
        "organization_id": org.id,
        "designer_rep_id": designer.id,
        "sheets": "12",
        "required_details": "",
    }


# =====================================================================
# СОЗДАНИЕ ПРОЕКТА (ТЗ п.17, 20)
# =====================================================================


def test_create_project_keeps_address_as_entered(db, direction):
    """Введённый адрес сохраняется как есть (ТЗ п.17)."""
    created = service.create_project(
        db, title="  Корпус 2  ", direction_id=direction.id,
        address="  г. Москва, ул. Ленина 1  ",
    )

    assert created.title == "Корпус 2"
    assert created.address == "г. Москва, ул. Ленина 1"


def test_create_project_without_address_stores_nothing(db, direction):
    """Пустой адрес остаётся пустым, а не превращается в «Не указан».

    Дефект: программа подставляла «Не указан» молча, и оператор искал
    потерянный адрес, которого никто не вводил.
    """
    created = service.create_project(
        db, title="Корпус 3", direction_id=direction.id, address="   "
    )

    db.expire_all()
    stored = service.get_project(db, created.id)
    assert stored.address is None
    assert "Не указан" not in (stored.address or "")


def test_create_project_requires_title(db, direction):
    """Без наименования проект не создаётся (ТЗ п.17)."""
    with pytest.raises(service.ProjectError, match="Наименование"):
        service.create_project(db, title="  ", direction_id=direction.id)


def test_create_project_requires_known_direction(db):
    """Направление берётся из справочника, а не из произвольного числа."""
    with pytest.raises(service.ProjectError, match="Направление не найдено"):
        service.create_project(db, title="Корпус", direction_id=9999)


def test_create_project_is_recorded_in_history(db, direction):
    """Создание проекта попадает в историю (ТЗ п.86)."""
    created = service.create_project(
        db, title="Корпус 4", direction_id=direction.id
    )

    events = service.list_events(db, created.id)
    assert any(event.event_type == "project_created" for event in events)
