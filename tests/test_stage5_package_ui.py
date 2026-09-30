"""Интерфейс комплектов: диалог выгрузки и панель «Комплекты». ТЗ п.16, 69–73."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog

from app.core import domain
from app.core.services import (
    document_service, form_service, issue_service, package_service,
)
from app.db.models import ProjectSection, SectionKind
from PyQt6.QtWidgets import QMessageBox

from app.ui.package_dialog import PackageDialog
from app.ui.project_window import ProjectWindow

pytestmark = pytest.mark.gui

REQUIRED = {
    "object_name": "ЖК Северный, корпус 2",
    "address": "г. Москва, ул. Северная, 1",
    "work_description": "Армирование стен и перекрытий",
    "section_refs": "КЖ",
    "work_period": "с 01.04.2024 по 30.04.2024",
    "work_volume": "120 м² бетона Б25",
    "has_defects": "Нет",
    "conclusion": "Работы выполнены в полном объёме",
    "work_performer": "ООО «Строй»",
}


@pytest.fixture
def section(db, project):
    db.add(ProjectSection(
        project_id=project.id, kind_id=db.query(SectionKind).first().id,
        code="КЖ", name="Конструкции",
    ))
    db.commit()


def _released(db, project, number: str):
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number=number,
        doc_date=date(2024, 5, int(number)),
    )
    form_service.save_draft(db, document.id, dict(REQUIRED))
    issue_service.issue_document(db, document.id, doc_date=date(2024, 5, int(number)))
    return document


@pytest.fixture
def shown(monkeypatch):
    """Все сообщения собираются в список вместо показа окон."""
    messages: list[tuple[str, str, str]] = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, kind,
            classmethod(
                lambda cls, parent, title, text, *a, **k:
                messages.append((title, text))
            ),
        )
    return messages


@pytest.fixture
def aosr(qapp, db, project, section):
    return _released(db, project, "1")


# =====================================================================
# ДИАЛОГ ФОРМИРОВАНИЯ (ТЗ п.69, 70, 75, 81)
# =====================================================================


def test_dialog_lists_documents_with_state(qapp, db, project, section):
    draft = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1",
        doc_date=date(2024, 5, 2),
    )
    del draft
    released = _released(db, project, "1")

    dialog = PackageDialog(db, project.id)

    texts = [
        dialog.documents_list.item(index).text()
        for index in range(dialog.documents_list.count())
    ]
    assert any("АОСР № 1" in text and "выпущен" in text for text in texts)
    assert any("АООК № 1" in text and "рабочая редакция" in text for text in texts)
    assert dialog.selected_document_ids()
    assert released.id in dialog.selected_document_ids()


def test_dialog_offers_both_variants(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)

    variants = [
        dialog.variant_combo.itemData(index)
        for index in range(dialog.variant_combo.count())
    ]

    assert variants == list(domain.EXPORT_VARIANTS)
    assert domain.EXPORT_VARIANT_LABELS[domain.EXPORT_VARIANT_BY_ACT] in [
        dialog.variant_combo.itemText(index)
        for index in range(dialog.variant_combo.count())
    ]


def test_dialog_defaults(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)

    assert dialog.root_name() == package_service.PACKAGE_ROOT_NAME
    assert dialog.variant() == domain.EXPORT_VARIANT_ALL
    assert dialog.page_numbering() is False
    assert dialog.base_dir is None


def test_dialog_choose_place(qapp, db, project, section, aosr, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *a, **k: str(tmp_path)),
    )
    dialog = PackageDialog(db, project.id)

    dialog.choose_place()

    assert dialog.base_dir == tmp_path
    assert str(tmp_path) in dialog.place_label.text()


def test_dialog_cancelled_place_keeps_previous(qapp, db, project, section, aosr, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "")
    )
    dialog = PackageDialog(db, project.id)

    dialog.choose_place()

    assert dialog.base_dir is None


def test_dialog_marks_and_unmarks_documents(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)
    count = dialog.documents_list.count()

    dialog._set_all(False)
    assert dialog.selected_document_ids() == []

    dialog._set_all(True)
    assert len(dialog.selected_document_ids()) == count


def test_dialog_unmarks_single_document(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)

    dialog.documents_list.item(0).setCheckState(Qt.CheckState.Unchecked)

    assert dialog.selected_document_ids() == []


def test_dialog_refuses_to_accept_without_documents(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)
    dialog.base_dir = Path("/tmp")
    dialog._set_all(False)

    dialog._on_accept()

    assert dialog.result() != PackageDialog.DialogCode.Accepted
    assert "Отметьте хотя бы один документ" in dialog.hint_label.text()


def test_dialog_refuses_to_accept_without_place(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)

    dialog._on_accept()

    assert dialog.result() != PackageDialog.DialogCode.Accepted
    assert "Выберите папку" in dialog.hint_label.text()


def test_dialog_accepts_complete_choice(qapp, db, project, section, aosr, tmp_path):
    dialog = PackageDialog(db, project.id)
    dialog.base_dir = tmp_path

    dialog._on_accept()

    assert dialog.result() == PackageDialog.DialogCode.Accepted


def test_dialog_root_name_is_operator_choice(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)
    dialog.root_name_edit.setText("Выгрузки заказчика")

    assert dialog.root_name() == "Выгрузки заказчика"


def test_dialog_page_numbering_is_choice(qapp, db, project, section, aosr):
    dialog = PackageDialog(db, project.id)
    dialog.page_numbering_check.setChecked(True)

    assert dialog.page_numbering() is True


# =====================================================================
# ПАНЕЛЬ «КОМПЛЕКТЫ» (ТЗ п.16, 73)
# =====================================================================


def test_packages_panel_is_empty_at_start(qapp, db, project, section, aosr):
    window = ProjectWindow(db, project.id)

    assert window.packages_table.rowCount() == 0
    assert "Реестры" in window.packages_hint.text()
    window.close()


def test_packages_panel_lists_package(qapp, db, project, section, aosr, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    window = ProjectWindow(db, project.id)

    assert window.packages_table.rowCount() == 1
    assert window.packages_table.item(0, 0).text() == package.folder_name
    assert "папка на месте" in window.packages_table.item(0, 4).text()
    window.close()


def test_packages_panel_reports_missing_folder(qapp, db, project, section, aosr, tmp_path):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    shutil.rmtree(package.absolute_path)
    window = ProjectWindow(db, project.id)

    assert "недоступна" in window.packages_table.item(0, 4).text()
    assert "заново" in window.packages_table.item(0, 4).text()
    window.close()


def test_packages_panel_shows_variant_and_numbering(
    qapp, db, project, section, aosr, tmp_path
):
    package_service.create_package(
        db, project.id, base_dir=tmp_path,
        variant=domain.EXPORT_VARIANT_BY_ACT, page_numbering=True,
    )
    window = ProjectWindow(db, project.id)

    assert "Вариант 2" in window.packages_table.item(0, 2).text()
    assert window.packages_table.item(0, 3).text() == "сквозная"
    window.close()


def test_opening_folder_without_selection_informs(qapp, db, project, section, aosr, shown):
    window = ProjectWindow(db, project.id)

    window.open_package_folder()

    assert any("Выберите комплект" in text for _, text in shown)
    window.close()


def test_missing_folder_is_reported_to_operator(
    qapp, db, project, section, aosr, tmp_path, shown, monkeypatch
):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    shutil.rmtree(package.absolute_path)
    window = ProjectWindow(db, project.id)
    window.packages_table.selectRow(0)

    window.open_package_folder()

    assert any("ТЗ п.73" in title for title, _ in shown)
    window.close()


def test_open_folder_calls_file_manager(
    qapp, db, project, section, aosr, tmp_path, monkeypatch
):
    package = package_service.create_package(db, project.id, base_dir=tmp_path)
    opened = []
    monkeypatch.setattr(
        "app.ui.project_window._open_folder",
        lambda path, parent: opened.append(path),
    )
    window = ProjectWindow(db, project.id)
    window.packages_table.selectRow(0)

    window.open_package_folder()

    assert opened == [package.absolute_path]
    window.close()
