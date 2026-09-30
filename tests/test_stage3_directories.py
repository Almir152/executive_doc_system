"""Тесты справочников. ТЗ п.18–20, 22, 44."""

import pytest

from app.core.services import directory_service as service
from app.db.models import Material, MaterialType, ProjectSection, SectionKind


# =====================================================================
# ОРГАНИЗАЦИИ (ТЗ п.18)
# =====================================================================


def test_organization_is_created_with_all_requisites(db):
    """Реквизиты п.18: краткое наименование, ОГРН, ИНН, адрес, телефон, факс, СРО."""
    organization = service.save_organization(
        db,
        short_name="  ООО «Строй»  ",
        ogrn=" 1027700132195 ",
        inn=" 7707083893 ",
        address=" Москва ",
        phone="+7 495 000-00-00",
        fax="",
        sro="СРО-С-001",
        nopriz="да",
    )
    stored = service.list_organizations(db)[0]
    assert stored.short_name == "ООО «Строй»"
    assert stored.ogrn == "1027700132195"
    assert stored.address == "Москва"
    assert stored.nopriz == "да"
    assert stored.fax is None, "пустая строка не должна храниться как «пустое»"
    assert organization.id is not None


def test_organization_requires_short_name(db):
    """Без краткого наименования организация не опознаётся в комплектах."""
    with pytest.raises(service.DirectoryError, match="Краткое наименование"):
        service.save_organization(db, short_name="   ")


def test_organization_requisites_are_not_duplicated(db):
    """ТЗ п.19: повторный ввод одинаковых реквизитов не требуется."""
    service.save_organization(db, short_name="ООО «Строй»", inn="7707083893")
    with pytest.raises(service.DirectoryError, match="ИНН 7707083893 уже указан"):
        service.save_organization(db, short_name="Другое ООО", inn="7707083893")
    assert len(service.list_organizations(db)) == 1


def test_organization_duplicate_is_found_without_ogrn(db):
    """Ограничение (ИНН, ОГРН) пропускает дубль, если ОГРН не заполнен."""
    service.save_organization(db, short_name="ООО «Строй»", inn="7707083893")
    with pytest.raises(service.DirectoryError, match="ИНН 7707083893 уже указан"):
        service.save_organization(db, short_name="ООО «Строй-дубль»", inn="7707083893")


def test_organization_ogrn_duplicate_is_found(db):
    service.save_organization(db, short_name="ООО «Строй»", ogrn="1027700132195")
    with pytest.raises(service.DirectoryError, match="ОГРН 1027700132195 уже указан"):
        service.save_organization(db, short_name="ООО «Другое»", ogrn="1027700132195")


def test_organization_without_requisites_may_repeat_name(db):
    """Без ИНН и ОГРН повтор краткого наименования допустим."""
    service.save_organization(db, short_name="ООО «Строй»")
    service.save_organization(db, short_name="ООО «Строй»")
    assert len(service.list_organizations(db)) == 2


def test_organization_keeps_its_own_requisites_when_updated(db):
    """Правка организации не должна отвергаться как дубль самой себя."""
    organization = service.save_organization(
        db, short_name="ООО «Строй»", inn="7707083893"
    )
    service.save_organization(
        db, organization.id, short_name="ООО «Строй-М»", inn="7707083893"
    )
    assert len(service.list_organizations(db)) == 1


def test_organization_search_finds_by_inn(db):
    """Поиск по справочнику обязателен (ТЗ п.20), включая кириллицу."""
    service.save_organization(db, short_name="ООО «Строй»", inn="7707083893")
    service.save_organization(db, short_name="ООО «Монтаж»", inn="7802312751")

    assert [o.short_name for o in service.list_organizations(db, "монтаж")] == [
        "ООО «Монтаж»"
    ]
    assert [o.short_name for o in service.list_organizations(db, "7707083893")] == [
        "ООО «Строй»"
    ]
    assert service.list_organizations(db, "нет такого") == []


def test_organization_is_updated_in_place(db):
    """Правка справочника не создаёт вторую организацию."""
    organization = service.save_organization(db, short_name="ООО «Строй»")
    service.save_organization(
        db, organization.id, short_name="ООО «Строй-М»", phone="+7 495 111-22-33"
    )
    assert len(service.list_organizations(db)) == 1
    stored = service.list_organizations(db)[0]
    assert stored.short_name == "ООО «Строй-М»"
    assert stored.phone == "+7 495 111-22-33"


def test_organization_in_use_cannot_be_deleted(db, project):
    """Удаление задействованной организации оставило бы проект без заказчика."""
    organization = service.save_organization(db, short_name="ООО «Заказчик»")
    service.update_project_card = getattr(service, "update_project_card", None)
    project.customer_org_id = organization.id
    db.commit()

    with pytest.raises(service.DirectoryError, match="карточек проектов: 1"):
        service.delete_organization(db, organization.id)

    db.rollback()
    assert len(service.list_organizations(db)) == 1


def test_organization_with_representatives_is_protected_from_cascade(db):
    """Каскадное удаление снесло бы представителей без предупреждения."""
    organization = service.save_organization(db, short_name="ООО «Строй»")
    service.save_representative(
        db, organization_id=organization.id, position="ГИП", full_name="Иванов И. И."
    )

    with pytest.raises(service.DirectoryError, match="представителей: 1"):
        service.delete_organization(db, organization.id)
    db.rollback()

    assert len(service.list_representatives(db)) == 1


def test_unused_organization_is_deleted(db):
    organization = service.save_organization(db, short_name="ООО «Строй»")
    service.delete_organization(db, organization.id)
    assert service.list_organizations(db) == []


# =====================================================================
# ПРЕДСТАВИТЕЛИ (ТЗ п.19)
# =====================================================================


def test_representative_belongs_to_organization(db):
    """Представитель без организации не к чему привязать."""
    organization = service.save_organization(db, short_name="ООО «Строй»")
    service.save_representative(
        db, organization_id=organization.id,
        position="ГИП", full_name="Иванов И. И.", phone="+7 921 000-00-00",
    )
    representative = service.list_representatives(db)[0]
    assert representative.position == "ГИП"
    assert representative.organization_id == organization.id


def test_representative_requires_known_organization(db):
    with pytest.raises(service.DirectoryError, match="Организация не найдена"):
        service.save_representative(
            db, organization_id=999999, position="ГИП", full_name="Иванов И. И."
        )


@pytest.mark.parametrize(
    "field", ["position", "full_name"]
)
def test_representative_requires_position_and_name(db, field):
    organization = service.save_organization(db, short_name="ООО «Строй»")
    payload = {"position": "ГИП", "full_name": "Иванов И. И."}
    payload[field] = " "
    with pytest.raises(service.DirectoryError):
        service.save_representative(
            db, organization_id=organization.id, **payload
        )


def test_representative_designer_of_section_is_protected(db, project):
    """Проектировщик раздела — часть проектной документации (ТЗ п.21)."""
    from app.core.services import project_service

    organization = service.save_organization(db, short_name="ООО «ПР»")
    representative = service.save_representative(
        db, organization_id=organization.id,
        position="ГИП", full_name="Петров П. П.",
    )
    kind = db.query(SectionKind).first()
    project_service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Конструкции",
        designer_rep_id=representative.id,
    )

    with pytest.raises(service.DirectoryError, match="проектировщиком в 1 разделах"):
        service.delete_representative(db, representative.id)
    db.rollback()
    assert len(service.list_representatives(db)) == 1


def test_representatives_are_filtered_by_organization(db):
    """Справочник с поиском (ТЗ п.20)."""
    first = service.save_organization(db, short_name="ООО «Строй»")
    second = service.save_organization(db, short_name="ООО «Монтаж»")
    service.save_representative(
        db, organization_id=first.id, position="ГИП", full_name="Иванов И. И."
    )
    service.save_representative(
        db, organization_id=second.id, position="ГП", full_name="Сидоров С. С."
    )

    assert len(service.list_representatives(db)) == 2
    assert [
        r.full_name for r in service.list_representatives(db, second.id)
    ] == ["Сидоров С. С."]


# =====================================================================
# СПРАВОЧНИК РАЗДЕЛОВ (ТЗ п.22)
# =====================================================================


def test_section_kind_code_is_unique(db):
    """Повторный код сделал бы разделы неразличимыми (ТЗ п.22)."""
    with pytest.raises(service.DirectoryError, match="уже есть в справочнике"):
        service.save_section_kind(db, code="КЖ", name="Конструкции железобетонные")

    assert db.query(SectionKind).filter(SectionKind.code == "КЖ").count() == 1


def test_section_kind_is_extended_by_operator(db):
    """Справочник расширяемый (ТЗ п.22)."""
    initial = len(service.list_section_kinds(db))
    service.save_section_kind(db, code="ТЗ", name="Технологические решения")
    stored = service.list_section_kinds(db)
    assert len(stored) == initial + 1
    assert [k.name for k in stored if k.code == "ТЗ"] == ["Технологические решения"]


def test_section_kind_in_use_cannot_be_deleted(db, project):
    """Используемый вид раздела удалять нельзя (ТЗ п.22)."""
    from app.core.services import project_service

    kind = db.query(SectionKind).filter(SectionKind.code == "КЖ").one()
    project_service.add_section(
        db, project.id, kind_id=kind.id, code="КЖ", name="Конструкции"
    )

    with pytest.raises(service.DirectoryError, match="используется в 1 разделах"):
        service.delete_section_kind(db, kind.id)
    db.rollback()
    assert db.query(SectionKind).filter(SectionKind.code == "КЖ").count() == 1


def test_unused_section_kind_is_deleted(db):
    kind = service.save_section_kind(db, code="ТЗ", name="Технологические решения")
    service.delete_section_kind(db, kind.id)
    assert db.query(SectionKind).filter(SectionKind.code == "ТЗ").count() == 0


# =====================================================================
# ТИПЫ МАТЕРИАЛОВ (ТЗ п.44)
# =====================================================================


def test_material_type_code_is_unique(db):
    with pytest.raises(service.DirectoryError, match="уже есть в справочнике"):
        service.save_material_type(db, code="PIPE", name="Труба стальная")


def test_material_type_in_use_cannot_be_deleted(db, project):
    """Тип материала, задействованный в проекте, удалять нельзя (ТЗ п.44)."""
    material_type = db.query(MaterialType).first()
    db.add(
        Material(project_id=project.id, material_type_id=material_type.id, name="Труба")
    )
    db.commit()

    with pytest.raises(service.DirectoryError, match="задействован в 1 материалах"):
        service.delete_material_type(db, material_type.id)
    db.rollback()
    assert db.query(MaterialType).filter(
        MaterialType.id == material_type.id
    ).count() == 1


def test_section_organization_link_is_reported(db, project):
    """Организация раздела тоже защищает организацию от удаления."""
    from app.core.services import project_service

    organization = service.save_organization(db, short_name="ООО «ПР»")
    kind = db.query(SectionKind).first()
    project_service.add_section(
        db, project.id, kind_id=kind.id, code="АР", name="Архитектура",
        organization_id=organization.id,
    )

    assert service.organization_usage(db, organization.id)["sections"] == 1
    with pytest.raises(service.DirectoryError, match="разделов"):
        service.delete_organization(db, organization.id)
    db.rollback()
    assert db.query(ProjectSection).count() == 1


# =====================================================================
# ЭКРАН СПРАВОЧНИКОВ (ТЗ п.18–20, 22, 44)
# =====================================================================


@pytest.mark.gui
def test_directories_page_lists_all_four_books(db, qapp):
    """Четыре справочника доступны на одной странице."""
    from app.ui.directories_page import DirectoryPage

    page = DirectoryPage(db)
    page.show()

    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == [
        "Организации", "Представители", "Разделы", "Типы материалов",
    ]
    assert page.org_table.rowCount() == 0, "справочник организаций пуст"
    assert page.kind_table.rowCount() >= 11, "разделы из ТЗ п.22 должны быть"
    assert page.material_type_table.rowCount() >= 6
    page.close()


@pytest.mark.gui
def test_directories_page_search_filters_organizations(db, qapp):
    """Поиск работает на экране, а не только в сервисе (ТЗ п.20)."""
    from app.ui.directories_page import DirectoryPage

    service.save_organization(db, short_name="ООО «Монтаж»", inn="7802312751")
    service.save_organization(db, short_name="ООО «Строй»", inn="7707083893")

    page = DirectoryPage(db)
    page.show()
    assert page.org_table.rowCount() == 2

    page.org_search.setText("монтаж")
    assert page.org_table.rowCount() == 1
    assert page.org_table.item(0, 0).text() == "ООО «Монтаж»"
    page.close()


@pytest.mark.gui
def test_directories_page_reports_duplicate_inn(db, qapp, monkeypatch):
    """Отказ на дубль ИНН должен быть виден оператору, а не ронять окно."""
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from app.ui.directories_page import DirectoryPage

    service.save_organization(db, short_name="ООО «Строй»", inn="7707083893")

    page = DirectoryPage(db)
    page.show()
    dialog = _organization_dialog(page, "ООО «Дубль»", inn="7707083893")
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text: shown.append(text),
    )
    page._edit_organization(dialog)

    assert shown, "оператор не получил сообщение о дубле"
    assert "ИНН" in shown[0]
    assert len(service.list_organizations(db)) == 1
    page.close()


@pytest.mark.gui
def test_directories_page_add_organization(db, qapp, monkeypatch):
    """Добавление организации с экрана попадает в справочник."""
    from PyQt6.QtWidgets import QDialog

    from app.ui.directories_page import DirectoryPage

    page = DirectoryPage(db)
    page.show()
    dialog = _organization_dialog(
        page, "ООО «Новое»", inn="7707083893", address="Москва"
    )
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    page._edit_organization(dialog)

    stored = service.list_organizations(db)
    assert len(stored) == 1
    assert stored[0].short_name == "ООО «Новое»"
    assert page.org_table.rowCount() == 1, "экран не обновился"
    page.close()


@pytest.mark.gui
def test_directories_page_delete_uses_service(db, qapp, project, monkeypatch):
    """Удаление с экрана идёт через сервис и уважает отказ (ТЗ п.18)."""
    from PyQt6.QtWidgets import QMessageBox

    from app.ui.directories_page import DirectoryPage

    organization = service.save_organization(db, short_name="ООО «Заказчик»")
    project.customer_org_id = organization.id
    db.commit()

    page = DirectoryPage(db)
    page.show()
    page.org_table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda parent, title, text: shown.append(text),
    )

    page.delete_organization()

    assert shown, "отказ в удалении должен быть показан"
    assert "карточек проектов" in shown[0]
    assert len(service.list_organizations(db)) == 1
    page.close()


def _organization_dialog(page, short_name, **extra):
    """Диалог организации с заполненными полями без запуска event loop."""
    from app.ui.directories_page import OrganizationDialog

    dialog = OrganizationDialog(page.db, None, page)
    dialog.edits["short_name"].setText(short_name)
    for field, value in extra.items():
        dialog.edits[field].setText(value)
    return dialog


@pytest.mark.gui
def test_main_window_navigates_to_directories(db, qapp):
    """Страница справочников открывается из навигации главного окна."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    rows = [
        window.nav_list.item(i).text() for i in range(window.nav_list.count())
    ]
    assert "Справочники" in rows

    index = rows.index("Справочники")
    window.nav_list.setCurrentRow(index)

    assert window.stack.currentWidget() is window.directories_page
    assert window.title_label.text() == "Рабочая область: Справочники"
    window.close()
