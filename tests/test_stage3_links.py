"""Тесты материалов и связей документов с архивом. ТЗ п.44–49, 51, 52."""

import pytest

from app.core import domain
from app.core.services import document_service, link_service
from app.db.models import (
    ArchiveDocument, ArchiveFileVersion, DocumentArchiveLink, Material, MaterialType,
    Project,
)


@pytest.fixture
def aosr(db, project):
    """Документ-акт в проекте."""
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _write_pdf(name: str, tmp_path=None):
    """Временный PDF для загрузки в архив."""
    import tempfile
    from pathlib import Path

    directory = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    path = directory / name
    path.write_bytes(b"%PDF-1.4 " + name.encode())
    return path


@pytest.fixture
def scheme(db, project, tmp_path):
    """Архивный документ с загруженным файлом: схема из ТЗ п.47."""
    from app.core.services.storage_service import add_file_to_archive

    source = tmp_path / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 scheme 12")
    return add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )


# =====================================================================
# МАТЕРИАЛЫ (ТЗ п.44)
# =====================================================================


def test_material_is_added_to_project(db, project):
    """Материал заводится в проекте с типом из справочника (ТЗ п.44)."""
    material_type = db.query(MaterialType).first()
    material = link_service.save_material(
        db, project.id, name="Труба стальная 57х3х5",
        material_type_id=material_type.id, unit="м", quantity=120.5,
    )
    assert material.name == "Труба стальная 57х3х5"
    assert material.quantity == 120.5
    assert len(link_service.list_materials(db, project.id)) == 1


def test_material_requires_name_and_known_type(db, project):
    material_type = db.query(MaterialType).first()
    with pytest.raises(link_service.MaterialError, match="Наименование"):
        link_service.save_material(
            db, project.id, name="  ", material_type_id=material_type.id
        )
    with pytest.raises(link_service.MaterialError, match="Тип материала не найден"):
        link_service.save_material(db, project.id, name="Труба", material_type_id=999999)


def test_material_search_by_name_and_type(db, project):
    """Поиск по материалам обязателен (ТЗ п.44)."""
    pipe = db.query(MaterialType).filter(MaterialType.code == "PIPE").one()
    cable = db.query(MaterialType).filter(MaterialType.code == "CABLE").one()
    link_service.save_material(
        db, project.id, name="Труба 57", material_type_id=pipe.id
    )
    link_service.save_material(
        db, project.id, name="Кабель ВВГ", material_type_id=cable.id
    )

    assert [m.name for m in link_service.list_materials(db, project.id, "кабел")] == [
        "Кабель ВВГ"
    ], "поиск по русским буквам должен работать"
    assert [m.name for m in link_service.list_materials(db, project.id, "труб")] == [
        "Труба 57"
    ]


def test_material_of_other_project_is_not_editable(db, project, direction):
    from app.db.models import Project

    other = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other)
    db.commit()
    material_type = db.query(MaterialType).first()
    material = link_service.save_material(
        db, other.id, name="Труба", material_type_id=material_type.id
    )

    with pytest.raises(link_service.MaterialError, match="другому проекту"):
        link_service.save_material(
            db, project.id, name="Взлом", material_type_id=material_type.id,
            material_id=material.id,
        )


def test_material_is_deleted(db, project):
    material_type = db.query(MaterialType).first()
    material = link_service.save_material(
        db, project.id, name="Труба", material_type_id=material_type.id
    )
    link_service.delete_material(db, material.id)
    assert db.query(Material).count() == 0


# =====================================================================
# СВЯЗИ ДОКУМЕНТОВ С АРХИВОМ (ТЗ п.49)
# =====================================================================


def test_one_scheme_is_linked_to_many_acts(db, project, scheme):
    """ТЗ п.47: один PDF схемы связан с несколькими актами, файл не копируется."""
    first = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15"
    )
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="18"
    )
    third = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="4"
    )

    for document in (first, second, third):
        link_service.link_document_to_archive(
            db, document_id=document.id,
            archive_document_id=scheme.id, link_role=domain.LINK_ROLE_SCHEME,
        )

    versions = (
        db.query(ArchiveFileVersion)
        .filter(ArchiveFileVersion.archive_document_id == scheme.id)
        .all()
    )
    assert len(versions) == 1, "файл схемы должен храниться один раз (ТЗ п.49)"
    assert link_service.archive_links_count(db, scheme.id) == 3, "счётчик ТЗ п.51"


def test_link_is_rejected_without_uploaded_file(db, aosr, project):
    """Нельзя привязать архивный документ без файла (ТЗ п.84)."""
    archive_document = ArchiveDocument(
        project_id=project.id, category=domain.ARCHIVE_CATEGORY_SCHEMES,
        file_type="pdf", original_name="СХЕМА №99.pdf",
    )
    db.add(archive_document)
    db.commit()

    with pytest.raises(link_service.MaterialError, match="нет загруженного файла"):
        link_service.link_document_to_archive(
            db, document_id=aosr.id, archive_document_id=archive_document.id,
            link_role=domain.LINK_ROLE_SCHEME,
        )
    assert db.query(DocumentArchiveLink).count() == 0


def test_duplicate_link_is_rejected(db, aosr, scheme):
    """Одна и та же связь дважды не создаётся (ТЗ п.49)."""
    for _ in range(2):
        try:
            link_service.link_document_to_archive(
                db, document_id=aosr.id, archive_document_id=scheme.id,
                link_role=domain.LINK_ROLE_SCHEME,
            )
        except link_service.MaterialError as exc:
            assert "уже есть" in str(exc)
    assert link_service.archive_links_count(db, scheme.id) == 1


def test_same_file_may_hold_different_roles(db, aosr, scheme):
    """Роль входит в уникальность связи: схема и протокол — разные роли."""
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_PROTOCOL,
    )
    assert link_service.archive_links_count(db, scheme.id) == 2


def test_link_pins_current_version(db, aosr, project, tmp_path):
    """Закреплённая версия не меняется при появлении новой редакции (ТЗ п.91)."""
    from app.core.services.storage_service import add_version

    from app.core.services.storage_service import add_file_to_archive

    source = tmp_path / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 v1")
    archive_document = add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link = link_service.link_document_to_archive(
        db, document_id=aosr.id,
        archive_document_id=archive_document.id, link_role=domain.LINK_ROLE_SCHEME,
    )
    first_version_id = link.archive_version_id

    second = tmp_path / "СХЕМА №12-2.pdf"
    second.write_bytes(b"%PDF-1.4 v2")
    add_version(db, archive_document.id, second)

    db.expire_all()
    stored = db.get(DocumentArchiveLink, link.id)
    assert stored.archive_version_id == first_version_id, (
        "выданная связь должна продолжать указывать на использованную версию"
    )


def test_document_link_rejects_archive_of_another_project(db, aosr, direction):
    """Файл другого проекта не попадёт в комплект (ТЗ п.49)."""
    from app.core.services.storage_service import add_file_to_archive

    other_project = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other_project)
    db.commit()

    foreign = add_file_to_archive(
        db, src_path=_write_pdf("СХЕМА чужая.pdf"),
        project_id=other_project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )

    with pytest.raises(link_service.MaterialError, match="другому проекту"):
        link_service.link_document_to_archive(
            db, document_id=aosr.id, archive_document_id=foreign.id,
            link_role=domain.LINK_ROLE_SCHEME,
        )
    assert db.query(DocumentArchiveLink).count() == 0


def test_unlink_keeps_archive_document(db, aosr, scheme):
    """Связь удаляется независимо от архивного документа (ТЗ п.52)."""
    link = link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )
    link_service.unlink_document_from_archive(db, link.id)

    assert db.query(DocumentArchiveLink).count() == 0
    assert db.get(ArchiveDocument, scheme.id) is not None, "архивный документ удалён"


def test_deleted_document_drops_its_links_but_keeps_archive(db, aosr, scheme):
    """Связь удаляется с документом, архивный файл остаётся (ТЗ п.52, 50)."""
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )
    db.delete(aosr)
    db.commit()

    assert db.query(DocumentArchiveLink).count() == 0
    assert db.get(ArchiveDocument, scheme.id) is not None
    assert link_service.archive_links_count(db, scheme.id) == 0


def test_project_with_archive_cannot_be_deleted(db, project, aosr, scheme):
    """Файл не должен молча исчезнуть вместе с проектом (ТЗ п.50)."""
    from app.core.services.project_service import ProjectError, delete_project

    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    with pytest.raises(ProjectError, match="архивных документов"):
        delete_project(db, project.id)

    db.rollback()
    assert link_service.archive_links_count(db, scheme.id) == 1


def test_roles_for_category_follow_archive_structure(db, project):
    """Категория архива задаёт роль связи (ТЗ п.50, 47, 48)."""
    assert link_service.link_roles_for_category(
        domain.ARCHIVE_CATEGORY_SCHEMES
    ) == (domain.LINK_ROLE_SCHEME,)
    assert link_service.link_roles_for_category(
        domain.ARCHIVE_CATEGORY_PROTOCOLS
    ) == (domain.LINK_ROLE_PROTOCOL,)
    assert link_service.link_roles_for_category(
        domain.ARCHIVE_CATEGORY_MATERIALS
    ) == (domain.LINK_ROLE_QUALITY,)


# =====================================================================
# ЭКРАН МАТЕРИАЛОВ И СВЯЗЕЙ (ТЗ п.44, 49)
# =====================================================================


@pytest.mark.gui
def test_project_window_adds_material(db, project, qapp, monkeypatch):
    """Материал добавляется из окна проекта (ТЗ п.44)."""
    from PyQt6.QtWidgets import QDialog

    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()

    material_type = db.query(MaterialType).first()
    _fill_material_dialog(monkeypatch, window, material_type, "Труба стальная", "м", 120.0)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.add_material()

    assert db.query(Material).count() == 1
    assert window.materials_table.rowCount() == 1
    assert window.materials_table.item(0, 1).text() == "Труба стальная"
    assert window.materials_table.item(0, 3).text() == "120", (
        "целое количество не должно показываться как 120.000"
    )
    window.close()


@pytest.mark.gui
def test_project_window_material_search(db, project, qapp, monkeypatch):
    """Поиск по материалам работает на экране (ТЗ п.44)."""
    from PyQt6.QtWidgets import QDialog

    from app.ui.project_window import ProjectWindow

    pipe = db.query(MaterialType).filter(MaterialType.code == "PIPE").one()
    window = ProjectWindow(db, project.id)
    window.show()

    for name in ("Труба 57", "Кабель ВВГ"):
        _fill_material_dialog(monkeypatch, window, pipe, name, "м", 1.0)
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        window.add_material()

    assert window.materials_table.rowCount() == 2
    window.material_search.setText("кабел")
    assert window.materials_table.rowCount() == 1
    assert window.materials_table.item(0, 1).text() == "Кабель ВВГ"
    window.close()


@pytest.mark.gui
def test_project_window_shows_links_of_selected_document(
    db, project, aosr, scheme, qapp
):
    """Связи показываются для выбранного документа (ТЗ п.49)."""
    from app.ui.project_window import ProjectWindow

    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )

    window = ProjectWindow(db, project.id)
    window.show()
    window.documents_table.selectRow(0)
    window._reload_links()

    assert window.links_table.rowCount() == 1
    assert window.links_table.item(0, 0).text() == scheme.original_name
    assert window.links_table.item(0, 2).text() == domain.LINK_ROLE_SCHEME
    assert "один раз" in window.links_hint.text()
    window.close()


@pytest.mark.gui
def test_project_window_links_hint_without_document(db, project, qapp):
    """Без выбранного документа подсказка объясняет, что делать дальше."""
    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()
    window._reload_links()

    assert window.links_table.rowCount() == 0
    assert "выбранного" in window.links_hint.text()
    window.close()


@pytest.mark.gui
def test_project_window_link_dialog_offers_only_loaded_files(
    db, project, aosr, qapp
):
    """В выборе связи только файлы с загруженной версией (ТЗ п.84)."""
    from app.ui.material_dialog import LinkDialog
    from app.ui.project_window import ProjectWindow

    window = ProjectWindow(db, project.id)
    window.show()
    dialog = LinkDialog(db, project.id, parent=window)
    assert dialog.archive_combo.count() == 1, "только заглушка, файлов нет"
    assert dialog.archive_combo.currentData() is None


def _fill_material_dialog(monkeypatch, window, material_type, name, unit, quantity):
    """Подменяет создание диалога материала заполненным экземпляром.

    Иначе проверялся бы диалог, собранный тестом, а не тот, который создаёт
    окно проекта: расхождение осталось бы незамеченным.
    """
    import app.ui.material_dialog as material_dialog

    real_class = material_dialog.MaterialDialog

    def factory(db, project_id, parent=None):
        dialog = real_class(db, project_id, parent)
        index = dialog.type_combo.findData(material_type.id)
        assert index >= 0, "тип материала должен быть в выпадающем списке"
        dialog.type_combo.setCurrentIndex(index)
        dialog.name_edit.setText(name)
        dialog.unit_edit.setText(unit)
        dialog.quantity_spin.setValue(quantity)
        return dialog

    monkeypatch.setattr(material_dialog, "MaterialDialog", factory)
