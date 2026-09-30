"""Связь строки материала с актом испытаний. ТЗ п.44, 45, 49.

Материал участвует в конкретных актах испытаний, а документ качества
выбирается для конкретного акта: автоматическое прикрепление ко всем актам
материала не допускается (п.45).
"""

from datetime import date

import pytest

from app.core import domain
from app.core.services import document_service, link_service, project_service
from app.db.models import Material, MaterialTestActLink, Project


@pytest.fixture
def material_type(db):
    from app.db.models import MaterialType

    item = MaterialType(code="BETON", name="Бетон")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def material(db, project, material_type):
    return link_service.save_material(
        db, project.id, name="Бетон Б25", material_type_id=material_type.id,
        unit="м³", quantity=120.0,
    )


@pytest.fixture
def test_act(db, project):
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="1",
        doc_date=date(2024, 5, 20),
    )


# =====================================================================
# СВЯЗЬ МАТЕРИАЛА И АКТА ИСПЫТАНИЙ (ТЗ п.44, 49)
# =====================================================================


def test_material_is_linked_to_test_act(db, material, test_act):
    """Материал относится к конкретному акту испытаний (ТЗ п.44)."""
    link = link_service.link_material_to_test_act(
        db, material.id, test_act.id
    )
    assert link.material_id == material.id
    assert [item.id for item in link_service.list_materials_of_act(db, test_act.id)] == [
        material.id
    ]
    assert [item.id for item in link_service.list_test_acts_of_material(db, material.id)] == [
        test_act.id
    ]


def test_link_survives_restart(db, material, test_act):
    """Связь хранится в БД, а не в состоянии окна (ТЗ п.49)."""
    link_service.link_material_to_test_act(db, material.id, test_act.id)
    db.expire_all()
    assert db.query(MaterialTestActLink).count() == 1


def test_same_material_may_have_several_acts(db, material, project):
    """Один материал проверяется в нескольких актах — поимённо (ТЗ п.44, 49)."""
    first = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="1"
    )
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="2"
    )
    link_service.link_material_to_test_act(db, material.id, first.id)
    link_service.link_material_to_test_act(db, material.id, second.id)
    assert [
        act.number for act in link_service.list_test_acts_of_material(db, material.id)
    ] == ["1", "2"]


def test_quality_document_is_not_attached_to_all_acts(db, material, project):
    """Документ качества не прикрепляется ко всем актам сразу (ТЗ п.45).

    Связь материала с актами задаётся поимённо: акт, который оператор не
    указал, остаётся несвязанным, и сертификат к нему сам не появляется.
    """
    first = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="1"
    )
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="2"
    )
    link_service.link_material_to_test_act(db, material.id, first.id)

    assert link_service.list_materials_of_act(db, second.id) == []
    assert link_service.list_materials_of_act(db, first.id) == [material]


def test_duplicate_link_is_rejected(db, material, test_act):
    """Повторная связь того же материала и акта не создаётся (ТЗ п.45)."""
    link_service.link_material_to_test_act(db, material.id, test_act.id)
    with pytest.raises(link_service.MaterialError) as exc:
        link_service.link_material_to_test_act(db, material.id, test_act.id)
    assert "уже отнесён" in str(exc.value)
    assert db.query(MaterialTestActLink).count() == 1, "дубля связи в БД нет"


def test_only_test_act_can_be_linked(db, material, project):
    """Связать материал с актом можно только с акта испытаний (ТЗ п.36, 44)."""
    aosr = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )
    with pytest.raises(link_service.MaterialError):
        link_service.link_material_to_test_act(db, material.id, aosr.id)


def test_link_to_another_project_is_rejected(db, material, direction):
    """Материал и акт другого проекта не связываются (ТЗ п.52)."""
    other_project = Project(
        direction_id=direction.id, title="Другой", address="М"
    )
    db.add(other_project)
    db.commit()
    foreign_act = document_service.create_document(
        db, other_project.id, doc_type=domain.DOC_TYPE_TEST_ACT
    )
    with pytest.raises(link_service.MaterialError):
        link_service.link_material_to_test_act(db, material.id, foreign_act.id)


def test_unlink_keeps_material_and_act(db, material, test_act):
    """Удаление связи ничего не удаляет, кроме самой связи (ТЗ п.52)."""
    link = link_service.link_material_to_test_act(db, material.id, test_act.id)
    link_service.unlink_material_from_test_act(db, link.id)
    assert link_service.list_test_acts_of_material(db, material.id) == []
    assert db.get(Material, material.id) is not None
    assert db.get(type(test_act), test_act.id) is not None


def test_deleted_material_drops_its_links(db, material, test_act):
    """Материал удаляется вместе со своими связями (ТЗ п.52)."""
    link_service.link_material_to_test_act(db, material.id, test_act.id)
    db.delete(material)
    db.commit()
    assert db.query(MaterialTestActLink).count() == 0
    assert db.get(type(test_act), test_act.id) is not None


# =====================================================================
# ИСТОРИЯ (ТЗ п.86)
# =====================================================================


def test_material_links_are_recorded(db, material, test_act):
    """Добавление и удаление связи материала попадают в историю (ТЗ п.86)."""
    link = link_service.link_material_to_test_act(db, material.id, test_act.id)
    event = project_service.list_events(db, material.project_id)[0]
    assert event.event_type == domain.HISTORY_MATERIAL_LINK_ADDED
    assert event.payload["document_id"] == test_act.id

    link_service.unlink_material_from_test_act(db, link.id)
    event = project_service.list_events(db, material.project_id)[0]
    assert event.event_type == domain.HISTORY_MATERIAL_LINK_REMOVED


# =====================================================================
# ИНТЕРФЕЙС ОПЕРАТОРА (ТЗ п.44, 45)
# =====================================================================


@pytest.mark.gui
def test_dialog_marks_linked_acts(db, material, test_act, qapp):
    """Уже связанный акт отмечен и не предлагается повторно (ТЗ п.45)."""
    from PyQt6.QtCore import Qt

    from app.ui.material_dialog import MaterialActsDialog

    link_service.link_material_to_test_act(db, material.id, test_act.id)
    dialog = MaterialActsDialog(db, material.project_id, material)
    assert dialog.acts_list.count() == 1
    item = dialog.acts_list.item(0)
    assert item.checkState() == Qt.CheckState.Checked
    assert "уже указан" in item.text()
    assert dialog.selected_acts() == [], "повторно предлагать нечего"


@pytest.mark.gui
def test_dialog_returns_only_new_acts(db, material, test_act, qapp, project):
    """В выбранные попадают только новые акты (ТЗ п.45)."""
    from PyQt6.QtCore import Qt

    from app.core.services import document_service
    from app.ui.material_dialog import MaterialActsDialog

    link_service.link_material_to_test_act(db, material.id, test_act.id)
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="2"
    )
    dialog = MaterialActsDialog(db, material.project_id, material)
    for row in range(dialog.acts_list.count()):
        item = dialog.acts_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == second.id:
            item.setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_acts() == [second.id]


@pytest.mark.gui
def test_window_shows_act_count(db, project, material, test_act, qapp):
    """В таблице материалов видно число связанных актов испытаний (ТЗ п.44)."""
    from app.ui.project_window import ProjectWindow

    link_service.link_material_to_test_act(db, material.id, test_act.id)
    window = ProjectWindow(db, project.id)
    window.show()
    window.materials_table.selectRow(0)
    window._reload_materials()
    window._reload_material_acts()

    assert window.materials_table.item(0, 5).text() == "1"
    assert window.material_acts_table.item(0, 0).text() == "Акт испытаний № 1"
    assert "Актов испытаний" in window.material_acts_hint.text()
    window.close()
