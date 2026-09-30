"""Интерфейс архива: реквизиты качества и поиск. ТЗ п.44, 45, 46, 51."""

from datetime import date

import pytest

from app.core import domain
from app.core.services import storage_service
from app.db.models import ArchiveDocument, Project
from app.ui.archive_dialog import ArchiveUploadDialog
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.gui


def _pdf(tmp_path, name, content=None):
    source = tmp_path / name
    source.write_bytes(content or f"%PDF-1.4 {name}".encode())
    return source


@pytest.fixture
def window(db, project, qapp):
    main = MainWindow()
    main.load_projects()
    main.projects_table.selectRow(0)
    yield main
    main.close()


def test_quality_fields_disabled_for_schemes(qapp, db, project):
    """Вид качества и сроки действия есть только у материалов (ТЗ п.45, 46)."""
    dialog = ArchiveUploadDialog(
        db, "СХЕМА №12.pdf", domain.ARCHIVE_CATEGORY_SCHEMES
    )

    assert dialog.quality_combo.isEnabled() is False
    assert dialog.validity_from.isEnabled() is False
    assert dialog.quality_type() == ""


def test_certificate_requires_validity_dates(qapp, db, project):
    """Для сертификата срок действия обязателен (ТЗ п.46)."""
    dialog = ArchiveUploadDialog(
        db, "Сертификат.pdf", domain.ARCHIVE_CATEGORY_MATERIALS
    )

    assert dialog.validity_from.isEnabled() is False
    dialog.quality_combo.setCurrentIndex(
        dialog.quality_combo.findData(domain.QUALITY_DOC_CERTIFICATE)
    )
    assert dialog.validity_from.isEnabled() is True
    assert "срок действия обязателен" in dialog.hint.text()


def test_passport_does_not_require_validity(qapp, db, project):
    """У паспорта срок действия не обязателен (ТЗ п.46)."""
    dialog = ArchiveUploadDialog(
        db, "Паспорт.pdf", domain.ARCHIVE_CATEGORY_MATERIALS
    )
    dialog.quality_combo.setCurrentIndex(
        dialog.quality_combo.findData(domain.QUALITY_DOC_PASSPORT)
    )

    assert dialog.validity_from.isEnabled() is False
    start, end = dialog.validity_dates()
    assert start is None and end is None


def test_dialog_returns_chosen_receipts(qapp, db, project):
    dialog = ArchiveUploadDialog(
        db, "Декларация.pdf", domain.ARCHIVE_CATEGORY_MATERIALS
    )
    dialog.quality_combo.setCurrentIndex(
        dialog.quality_combo.findData(domain.QUALITY_DOC_DECLARATION)
    )
    dialog.validity_from.setDate(date(2024, 2, 10))
    dialog.validity_to.setDate(date(2026, 2, 10))
    dialog.number_edit.setText("Д-15")

    options = dialog.options()

    assert options["quality_type"] == domain.QUALITY_DOC_DECLARATION
    assert options["validity_from"] == date(2024, 2, 10)
    assert options["validity_to"] == date(2026, 2, 10)
    assert options["number"] == "Д-15"


def test_dialog_refuses_reversed_validity(qapp, db, project):
    """Обратный срок не принимается: окно остаётся открытым (ТЗ п.46)."""
    dialog = ArchiveUploadDialog(
        db, "Декларация.pdf", domain.ARCHIVE_CATEGORY_MATERIALS
    )
    dialog.quality_combo.setCurrentIndex(
        dialog.quality_combo.findData(domain.QUALITY_DOC_DECLARATION)
    )
    dialog.validity_from.setDate(date(2026, 1, 1))
    dialog.validity_to.setDate(date(2024, 1, 1))

    dialog._on_accept()

    assert dialog.result() != ArchiveUploadDialog.DialogCode.Accepted
    assert "раньше даты начала" in dialog.hint.text()


def test_dialog_explains_act_selection(qapp, db, project):
    """Оператор видит, что акты выбираются отдельно (ТЗ п.45)."""
    dialog = ArchiveUploadDialog(
        db, "Сертификат.pdf", domain.ARCHIVE_CATEGORY_MATERIALS
    )

    assert "к конкретным актам" in dialog.hint.text()


def test_archive_table_shows_quality_columns(window, db, project, tmp_path):
    storage_service.add_file_to_archive(
        db, _pdf(tmp_path, "Сертификат арматуры.pdf"), project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
        quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 15), validity_to=date(2025, 3, 20),
        number="С-7",
    )

    window.load_archive_files()

    assert window.archive_table.item(0, 3).text() == domain.QUALITY_DOC_CERTIFICATE
    assert window.archive_table.item(0, 4).text() == "действует 15.01.2024 — 20.03.2025"


def test_archive_search_filters_rows(window, db, project, tmp_path):
    """ТЗ п.44: поиск по имени, номеру и виду документа качества."""
    storage_service.add_file_to_archive(
        db, _pdf(tmp_path, "Сертификат арматуры.pdf", b"%PDF-1.4 a"), project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
        quality_type=domain.QUALITY_DOC_CERTIFICATE,
        validity_from=date(2024, 1, 1), validity_to=date(2025, 1, 1),
    )
    storage_service.add_file_to_archive(
        db, _pdf(tmp_path, "Паспорт бетона.pdf", b"%PDF-1.4 b"), project.id,
        category=domain.ARCHIVE_CATEGORY_MATERIALS,
        quality_type=domain.QUALITY_DOC_PASSPORT,
    )

    window.archive_search_input.setText("армат")
    assert window.archive_table.rowCount() == 1
    assert window.archive_table.item(0, 2).text() == "Сертификат арматуры.pdf"

    window.archive_search_input.setText(domain.QUALITY_DOC_PASSPORT)
    assert window.archive_table.rowCount() == 1
    assert window.archive_table.item(0, 2).text() == "Паспорт бетона.pdf"

    window.archive_search_input.setText("")
    assert window.archive_table.rowCount() == 2


def test_archive_shows_only_selected_project(window, db, project, direction, tmp_path):
    """ТЗ п.90: чужой архив в списке не показывается."""
    other = Project(direction_id=direction.id, title="Другой", address="М")
    db.add(other)
    db.commit()
    storage_service.add_file_to_archive(
        db, _pdf(tmp_path, "Наш файл.pdf", b"%PDF-1.4 our"), project.id
    )
    storage_service.add_file_to_archive(
        db, _pdf(tmp_path, "Чужой файл.pdf", b"%PDF-1.4 foreign"), other.id
    )

    window.load_archive_files()

    names = [
        window.archive_table.item(row, 2).text()
        for row in range(window.archive_table.rowCount())
    ]
    assert names == ["Наш файл.pdf"]


def test_upload_keeps_quality_details(
    window, db, project, tmp_path, monkeypatch, gui_support
):
    """Файл попадает в архив вместе с видом и сроком действия (ТЗ п.45, 46)."""
    from PyQt6.QtWidgets import QFileDialog

    source = _pdf(tmp_path, "Декларация о соответствии.pdf")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "")
    )
    import app.ui.main_window as main_module

    monkeypatch.setattr(
        main_module, "show_upload_dialog",
        lambda db_arg, name, category, parent=None: {
            "category": category,
            "quality_type": domain.QUALITY_DOC_DECLARATION,
            "validity_from": date(2024, 5, 1),
            "validity_to": date(2027, 5, 1),
            "number": "Д-1",
        },
    )
    window.archive_category_combo.setCurrentText(domain.ARCHIVE_CATEGORY_MATERIALS)

    window.upload_to_archive()

    stored = db.query(ArchiveDocument).one()
    assert stored.number == "Д-1"
    assert stored.validity_from == date(2024, 5, 1)
    assert stored.validity_to == date(2027, 5, 1)
    assert domain.QUALITY_DOC_DECLARATION in stored.note
    assert not gui_support["critical"]


def test_upload_reports_quality_error(
    window, db, project, tmp_path, monkeypatch, gui_support
):
    """Ошибка реквизитов объясняется оператору, файл не попадает в архив."""
    from PyQt6.QtWidgets import QFileDialog

    source = _pdf(tmp_path, "Сертификат арматуры.pdf")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "")
    )
    import app.ui.main_window as main_module

    def failing(db_arg, name, category, parent=None):
        raise storage_service.StorageError(
            "Для документа качества «Сертификат» обязательны дата начала "
            "и дата окончания действия (ТЗ п.46)."
        )

    monkeypatch.setattr(main_module, "show_upload_dialog", failing)
    window.archive_category_combo.setCurrentText(domain.ARCHIVE_CATEGORY_MATERIALS)

    window.upload_to_archive()

    assert db.query(ArchiveDocument).count() == 0
    assert any("ТЗ п.46" in str(args[2]) for args in gui_support["critical"])
