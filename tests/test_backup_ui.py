"""Экран резервного копирования в настройках (ТЗ п.74, 98)."""

from datetime import date

import pytest

from app.config import BACKUP_DIR
from app.core import domain
from app.core.services import (
    backup_service, document_service, form_service, issue_service, link_service,
)
from app.db.database import SessionLocal, release_database
from app.db.models import ArchiveFileVersion, Document, Project
from app.ui.main_window import MainWindow

pytestmark = pytest.mark.gui


@pytest.fixture
def window(db, project, qapp):
    main = MainWindow()
    main.load_projects()
    main.load_backups()
    yield main
    main.close()


@pytest.fixture
def issued_project(db, project):
    """Проект с выпущенным документом и файлом архива (ТЗ п.49, 98)."""
    aosr = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, doc_date=date(2024, 5, 15)
    )
    form_service.save_draft(db, aosr.id, {
        "object_name": "Корпус 2", "address": "г. Москва",
        "work_description": "Армирование стен", "section_refs": "КЖ",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "period_start": "01.04.2024", "period_end": "30.04.2024",
        "work_volume": "120 м²", "has_defects": "Нет",
        "conclusion": "Работы выполнены", "work_performer": "ООО «Строй»",
    })
    db.refresh(aosr)
    issue_service.issue_document(db, aosr.id)

    from app.core.services import storage_service
    from app.config import ARCHIVE_DIR

    source = ARCHIVE_DIR / "СХЕМА №12.pdf"
    source.write_bytes(b"%PDF-1.4 scheme 12")
    scheme = storage_service.add_file_to_archive(
        db, src_path=source, project_id=project.id,
        category=domain.ARCHIVE_CATEGORY_SCHEMES,
    )
    link_service.link_document_to_archive(
        db, document_id=aosr.id, archive_document_id=scheme.id,
        link_role=domain.LINK_ROLE_SCHEME,
    )
    return project


def test_backup_section_is_on_settings_page(window):
    """Копирование доступно оператору из настроек (ТЗ п.74)."""
    assert window.btn_create_backup.text() == "Создать резервную копию"
    assert window.btn_restore_backup.text() == "Восстановить из копии…"


def test_create_backup_button_reports_result(qapp, window, issued_project,
                                              monkeypatch):
    """Кнопка создаёт копию и показывает оператору её состав (ТЗ п.74, 98)."""
    shown = {}

    def fake_info(parent, title, text):
        shown["title"] = title
        shown["text"] = text

    monkeypatch.setattr("app.ui.main_window.QMessageBox.information", fake_info)
    window.create_backup_action()

    backups = backup_service.list_backups(BACKUP_DIR)
    assert len(backups) == 1
    assert shown["title"] == "Резервная копия"
    assert "проверена" in shown["text"]
    assert "Проектов: 1" in shown["text"]


def test_backup_list_shows_created_copy(qapp, window, issued_project, monkeypatch):
    """Созданная копия появляется в списке (ТЗ п.74)."""
    monkeypatch.setattr("app.ui.main_window.QMessageBox.information",
                        lambda *args, **kwargs: None)
    window.create_backup_action()
    window.load_backups()

    assert window.backup_list.rowCount() == 1
    assert window.backup_list.item(0, 0).text() == "Резервная копия 01"
    assert window.backup_list.item(0, 2).text() == "1 / 1"


def test_create_backup_refuses_unsaved_changes(qapp, window, project, monkeypatch):
    """Несохранённая правка не даёт снять копию (ТЗ п.86, 98)."""
    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda parent, title, text: shown.update(title=title, text=text),
    )
    window.db.query(Project).filter(Project.id == project.id).one().title = (
        "Правка без сохранения"
    )
    window.create_backup_action()

    assert shown["title"] == "Резервная копия"
    assert "несохранённые изменения" in shown["text"].lower()
    assert backup_service.list_backups(BACKUP_DIR) == []


def test_restore_button_restores_project(qapp, window, issued_project, monkeypatch,
                                         tmp_path):
    """Восстановление возвращает проект и предлагает перезапуск (ТЗ п.98)."""
    monkeypatch.setattr("app.ui.main_window.QMessageBox.information",
                        lambda *args, **kwargs: None)
    window.create_backup_action()
    folder = backup_service.list_backups(BACKUP_DIR)[0]["path"]

    # Данные меняются после копии — их не должно остаться после восстановления.
    window.db.query(Project).filter(Project.id == issued_project.id).one().title = "Правка"
    window.db.commit()

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(folder),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox_Yes(),
    )
    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda parent, title, text: shown.update(title=title, text=text),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda parent, title, text: shown.setdefault("error", text),
    )
    window.restore_backup_action()
    assert "error" not in shown, shown.get("error")

    session = SessionLocal()
    try:
        project_row = session.query(Project).filter(
            Project.id == issued_project.id
        ).one()
        assert project_row.title == "Тестовый объект", "вернулась копия"
        assert session.query(Document).count() == 1
        assert session.query(ArchiveFileVersion).count() == 1
    finally:
        session.close()
        release_database()
    assert "Перезапустите программу" in shown["text"]


def QMessageBox_Yes():
    from PyQt6.QtWidgets import QMessageBox

    return QMessageBox.StandardButton.Yes


def test_restore_of_broken_copy_changes_nothing(qapp, window, issued_project,
                                                monkeypatch):
    """Испорченная копия не трогает рабочие данные (ТЗ п.98)."""
    monkeypatch.setattr("app.ui.main_window.QMessageBox.information",
                        lambda *args, **kwargs: None)
    window.create_backup_action()
    folder = backup_service.list_backups(BACKUP_DIR)[0]["path"]
    (folder / "app.db").write_bytes("не база".encode())

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(folder),
    )
    executed = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.exec",
        lambda self: executed.setdefault("shown", self.text()),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox_No(),
    )
    window.restore_backup_action()

    assert "невозможно" in executed["shown"].lower()
    session = SessionLocal()
    try:
        assert session.query(Project).count() == 1
    finally:
        session.close()


def QMessageBox_No():
    from PyQt6.QtWidgets import QMessageBox

    return QMessageBox.StandardButton.No


def test_restore_declined_leaves_data(qapp, window, issued_project, monkeypatch):
    """Отказ оператора ничего не меняет (ТЗ п.98)."""
    monkeypatch.setattr("app.ui.main_window.QMessageBox.information",
                        lambda *args, **kwargs: None)
    window.create_backup_action()
    folder = backup_service.list_backups(BACKUP_DIR)[0]["path"]
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(folder),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox_No(),
    )
    window.restore_backup_action()

    session = SessionLocal()
    try:
        assert session.query(Project).count() == 1
    finally:
        session.close()


def test_restore_without_choice_does_nothing(qapp, window, issued_project,
                                             monkeypatch):
    """Отказ в выборе папки не запускает восстановление (ТЗ п.98)."""
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: "",
    )
    window.restore_backup_action()
    session = SessionLocal()
    try:
        assert session.query(Project).count() == 1
    finally:
        session.close()


# =====================================================================
# Программа не закрывается молча (ТЗ п.106)
# =====================================================================


def test_file_error_does_not_close_the_program(qapp, window, project,
                                                monkeypatch):
    """Файловая ошибка показывается окном, а не закрывает приложение.

    Отказ при создании копии выглядел как исчезновение окна программы:
    необработанное исключение в слоте Qt завершает процесс без сообщения.
    """
    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda parent, title, text: shown.update(title=title, text=text),
    )

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(backup_service, "create_backup", refuse)
    window.create_backup_action()

    assert shown["title"] == "Резервная копия"
    assert "Permission denied" in shown["text"]
    assert "app.log" in shown["text"]


def test_storage_problem_reaches_the_operator(qapp, window, project,
                                              monkeypatch):
    """Файловая ошибка хранилища описана языком оператора (ТЗ п.98)."""
    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda parent, title, text: shown.update(title=title, text=text),
    )

    def refuse():
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(backup_service, "ensure_dirs", refuse)
    window.create_backup_action()

    assert shown["title"] == "Резервная копия"
    assert "Permission denied" in shown["text"]
    assert "права на запись" in shown["text"]
    assert backup_service.list_backups(BACKUP_DIR) == []


def test_restore_failure_does_not_close_the_program(qapp, window, issued_project,
                                                    monkeypatch):
    """Неожиданная ошибка восстановления не закрывает программу (ТЗ п.98)."""
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    window.create_backup_action()
    folder = backup_service.list_backups(BACKUP_DIR)[0]["path"]
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(folder),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox_Yes(),
    )
    shown = {}
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda parent, title, text: shown.update(title=title, text=text),
    )

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_service, "restore_backup", refuse)
    window.restore_backup_action()

    assert "Восстановление не выполнено" == shown["title"]
    assert "No space left on device" in shown["text"]
    assert window.db is not None
