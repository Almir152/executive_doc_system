"""Регрессионные тесты Этапа 0: БД, экспорт, AI Connector и GUI.

Тесты, помеченные в имени «регрессия», закрывают ошибки, из-за которых
приложение не запускалось или неверно работало.
"""

from pathlib import Path

import pytest

from app.core.services.exporter import export_package
from app.ai.connector import (
    MODE_INTERNET, MODE_LOCAL, MODE_OFF, MODES, AIConnector,
)


# -------------------------------------------------------------------
# БАЗА ДАННЫХ
# -------------------------------------------------------------------
def test_bulk_documents_creation(db, project):
    """Стресс: массовое создание документов без падений."""
    from app.core import domain
    from app.db.models import Document

    db.add_all([
        Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number=str(i))
        for i in range(1, 101)
    ])
    db.commit()
    assert db.query(Document).filter(Document.project_id == project.id).count() == 100


def test_project_with_archive_cannot_be_deleted(db, project, tmp_path):
    """ТЗ п.54, 109: архивные файлы не теряются вместе с проектом.

    Раньше проект удалялся каскадом вместе с архивом, что необратимо уничтожало
    файлы исполнительной документации.
    """
    from app.core import domain
    from app.core.services.project_service import ProjectError, delete_project
    from app.core.services.storage_service import add_file_to_archive
    from app.db.models import ArchiveDocument, Document, Project

    db.add(Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1"))
    db.commit()

    src = tmp_path / "fixture.pdf"
    src.write_bytes(b"archive-file-survives-project-delete")
    add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_PROJECT)

    assert db.query(ArchiveDocument).count() == 1
    with pytest.raises(ProjectError, match="архивн"):
        delete_project(db, project.id)

    assert db.query(Project).count() == 1
    assert db.query(Document).count() == 1
    assert db.query(ArchiveDocument).count() == 1, "Архивный файл нельзя терять вместе с проектом"


def test_project_without_archive_deletes_cascade(db, project):
    """Без архивных документов проект удаляется вместе со своими документами."""
    from app.core import domain
    from app.core.services.project_service import delete_project
    from app.db.models import Document, Project

    db.add(Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1"))
    db.commit()

    delete_project(db, project.id)
    assert db.query(Project).count() == 0
    assert db.query(Document).count() == 0


# -------------------------------------------------------------------
# AI CONNECTOR (ТЗ п.5-11)
# -------------------------------------------------------------------
def test_ai_connector_three_modes_per_tz_9():
    """ТЗ п.9: ровно три режима — нет ИИ / локальный / интернет."""
    assert MODES == (MODE_OFF, MODE_LOCAL, MODE_INTERNET)
    assert AIConnector(mode=MODE_OFF).enabled is False
    assert AIConnector(mode=MODE_LOCAL).enabled is True
    assert AIConnector(mode=MODE_INTERNET).enabled is True


def test_ai_connector_off_returns_disabled():
    res = AIConnector(mode=MODE_OFF).analyze_package([1, 2])
    assert res["status"] == "disabled"
    assert res["proposals"] == []


def test_ai_connector_local_returns_proposals():
    res = AIConnector(mode=MODE_LOCAL).analyze_package([1, 2])
    assert res["status"] == "success"
    assert res["mode"] == MODE_LOCAL
    assert len(res["proposals"]) > 0


def test_ai_connector_unknown_mode_falls_back_to_off():
    """Неизвестный режим не должен молча включать ИИ."""
    assert AIConnector(mode="ЧТО-ТО").mode == MODE_OFF


def test_ai_connector_indicator_colors_differ():
    """ТЗ п.9: зелёный — локальный, красный — интернет-ИИ."""
    assert AIConnector(mode=MODE_LOCAL).mode_color() != AIConnector(mode=MODE_INTERNET).mode_color()
    assert AIConnector(mode=MODE_OFF).mode_color() == "#666666"


# -------------------------------------------------------------------
# ЭКСПОРТ (ТЗ п.69-81)
# -------------------------------------------------------------------
def test_export_creates_registry_pdf(db, project, tmp_path):
    from app.db.models import Document

    from app.core import domain

    db.add(Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="100-П"))
    db.commit()

    out = export_package(db, project.id, tmp_path / "export_test", allow_errors=True)
    pdf = out / "Реестр_выгрузки.pdf"
    assert pdf.exists()
    assert pdf.stat().st_size > 0


def test_export_creates_own_folder_for_package(db, project, tmp_path):
    """Каждая выгрузка — отдельная папка внутри корневой (ТЗ п.70, 71)."""
    from app.db.models import Document

    from app.core import domain

    db.add(Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="100-П"))
    db.commit()

    first = export_package(db, project.id, tmp_path, allow_errors=True)
    second = export_package(db, project.id, tmp_path, allow_errors=True)

    assert first.name == "Комплект 01"
    assert second.name == "Комплект 02"
    assert (first / "Реестр_выгрузки.pdf").exists()


def test_export_missing_project_raises(db, tmp_path):
    with pytest.raises(ValueError, match="Проект не найден"):
        export_package(db, project_id=99999, target_dir=tmp_path / "invalid")


# -------------------------------------------------------------------
# GUI: регрессии запуска (ТЗ п.13, 51, 71)
# -------------------------------------------------------------------
@pytest.mark.gui
def test_gui_regression_window_starts_with_populated_archive(db, project, tmp_path):
    """РЕГРЕССИЯ: приложение падало на AttributeError: 'doc_type'.

    load_archive_files() обращался к несуществующим полям модели архива,
    поэтому окно не открывалось, если в архиве есть хоть один файл.
    """
    from app.core import domain
    from app.core.services.storage_service import add_file_to_archive
    from app.db.models import ArchiveDocument
    from app.ui.main_window import MainWindow

    src = tmp_path / "_gui_fixture.pdf"
    src.write_bytes(b"gui-archive-regression")
    add_file_to_archive(db, src, project.id, domain.ARCHIVE_CATEGORY_SCHEMES)
    assert db.query(ArchiveDocument).count() == 1, "Архив непустой — именно этот случай падал"

    window = MainWindow()
    window.show()
    # ТЗ п.90: архив показывается по выбранному проекту, без проекта он пуст.
    assert window.archive_table.rowCount() == 0
    window.load_projects()
    window.projects_table.selectRow(0)
    window.load_archive_files()
    assert window.archive_table.rowCount() == 1
    assert window.archive_table.item(0, 1).text() == domain.ARCHIVE_CATEGORY_SCHEMES
    assert window.archive_table.item(0, 2).text() == "_gui_fixture.pdf", "Имя файла должно быть видно оператору"
    assert window.archive_table.item(0, 5).text() == "0", "ТЗ п.51: колонка счётчика связей"
    window.close()


@pytest.mark.gui
def test_gui_regression_upload_file_to_archive(db, project, monkeypatch, gui_support, tmp_path):
    """РЕГРЕССИЯ: загрузка файла всегда падала с ошибкой полей модели."""
    from PyQt6.QtWidgets import QFileDialog

    from app.core import domain
    from app.db.models import ArchiveDocument
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)

    src = tmp_path / "_upload_fixture.pdf"
    src.write_bytes(b"upload-regression-content")

    picked = iter([str(src), str(src)])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (next(picked), ""))

    import app.ui.main_window as main_module

    # ТЗ п.45, 46: реквизиты качества вводятся в отдельном окне; в тесте
    # оператора играет заглушка с нужными значениями.
    asked = []

    def fake_dialog(db_arg, file_name, category, parent=None):
        asked.append((file_name, category))
        return {"category": category, "quality_type": None,
                "validity_from": None, "validity_to": None, "number": None}

    monkeypatch.setattr(main_module, "show_upload_dialog", fake_dialog)

    assert db.query(ArchiveDocument).count() == 0
    window.archive_category_combo.setCurrentText(domain.ARCHIVE_CATEGORY_SCHEMES)
    window.upload_to_archive()

    assert db.query(ArchiveDocument).count() == 1, "Файл должен попасть в архив"
    archive_doc = db.query(ArchiveDocument).one()
    assert archive_doc.category == domain.ARCHIVE_CATEGORY_SCHEMES
    assert archive_doc.project_id == project.id, "Архивный документ принадлежит проекту"
    stored = Path(archive_doc.current_version.stored_path)
    assert stored.exists()
    assert str(stored).startswith(str(window.archive_dir))
    assert not gui_support["critical"], "Загрузка не должна давать ошибку"
    assert asked == [("_upload_fixture.pdf", domain.ARCHIVE_CATEGORY_SCHEMES)], (
        "оператору показывается окно реквизитов документа качества (ТЗ п.45)"
    )

    # Повторная загрузка того же файла — дедупликация (ТЗ п.92).
    window.upload_to_archive()
    assert db.query(ArchiveDocument).count() == 1, "Дубликат не должен создавать вторую запись"
    assert gui_support["information"], "Оператор должен получить уведомление о дубликате"

    window.close()


@pytest.mark.gui
def test_gui_upload_requires_selected_project(db, monkeypatch, gui_support, tmp_path):
    """Архивный документ принадлежит проекту: без выбора загрузка не идёт."""
    from PyQt6.QtWidgets import QFileDialog

    from app.db.models import ArchiveDocument
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()

    src = tmp_path / "_no_project.pdf"
    src.write_bytes(b"must-not-be-stored")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))

    window.upload_to_archive()
    assert gui_support["warning"], "Оператор должен получить предупреждение"
    assert db.query(ArchiveDocument).count() == 0
    window.close()


@pytest.mark.gui
def test_gui_regression_no_destructive_cache_button(db):
    """РЕГРЕССИЯ: кнопка «Очистить кэш выгрузок» удаляла комплекты без спроса."""
    from PyQt6.QtWidgets import QPushButton
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    assert not hasattr(window, "clear_cache"), "Разрушительная функция должна быть удалена (ТЗ п.71, 109)"
    labels = [b.text() for b in window.findChildren(QPushButton)]
    assert not any("кэш" in t.lower() for t in labels), f"Кнопки очистки кэша быть не должно: {labels}"
    window.close()


@pytest.mark.gui
def test_gui_regression_delete_project_asks_confirmation(db, project, monkeypatch, gui_support):
    """ТЗ п.109: необратимое удаление требует подтверждения оператора."""
    from PyQt6.QtWidgets import QMessageBox
    from app.db.models import Project
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()

    window.projects_table.selectRow(0)

    # Оператор отвечает «Нет» — проект должен сохраниться.
    window.delete_project()
    assert db.query(Project).count() == 1, "Отказ от подтверждения не должен удалять проект"
    assert gui_support["question"], "Обязателен диалог подтверждения"

    # Оператор отвечает «Да» — проект удаляется.
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    window.delete_project()
    assert db.query(Project).count() == 0
    window.close()


@pytest.mark.gui
def test_gui_ai_mode_switch_hides_chat_when_off(db, monkeypatch, gui_support):
    """ТЗ п.9: при «Нет ИИ» чат ИИ скрывается."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    ai_row = next(
        (i for i in range(window.nav_list.count())
         if window.nav_list.item(i).text() == "ИИ-Агент"),
        -1,
    )
    assert ai_row >= 0
    window.nav_list.setCurrentRow(ai_row)
    window.show()

    assert window.ai.enabled is True
    assert window.ai_output.isVisible() is True

    off_index = window.ai_mode_combo.findText("Нет ИИ")
    assert off_index >= 0, "Режим «Нет ИИ» должен быть доступен"
    window.ai_mode_combo.setCurrentIndex(off_index)
    assert window.ai.mode == MODE_OFF
    assert window.ai_output.isVisible() is False

    window.ai_mode_combo.setCurrentText("Интернет-ИИ")
    assert window.ai.mode == MODE_INTERNET
    assert window.ai_output.isVisible() is True
    window.close()


@pytest.mark.gui
def test_gui_ai_analysis_requires_selected_project(db, project, monkeypatch, gui_support):
    """ИИ-анализ должен идти по выбранному проекту, а не по жёсткому ID."""
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.load_projects()

    window.run_ai_check()
    assert gui_support["warning"], "Без выбранного проекта нужно предупредить оператора"
    assert window.ai_output.toPlainText() == ""

    window.projects_table.selectRow(0)
    window.run_ai_check()
    assert f"ID {project.id}" in window.ai_output.toPlainText()
    window.close()


@pytest.mark.gui
def test_gui_navigation_switches_all_pages(db):
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    for row in range(window.nav_list.count()):
        window.nav_list.setCurrentRow(row)
        assert "Рабочая область:" in window.title_label.text()
    window.close()


@pytest.mark.gui
def test_gui_delete_project_reports_issued_versions_instead_of_crashing(
    db, project, gui_support
):
    """Удаление проекта с выпусками должно объяснять отказ, а не падать.

    Защита от потери выпусков живёт в сервисе, а окно удаляет проект само,
    минуя сервис. Такое расхождение означало, что оператор вместо внятного
    сообщения получал необработанную ошибку Qt — по ТЗ п.97 сбой должен быть
    виден и объяснён.
    """
    from app.config import utcnow
    from app.db.models import Document, DocumentVersion
    from app.ui.main_window import MainWindow

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

    window = MainWindow()
    window.show()
    window.load_projects()
    window.projects_table.selectRow(0)
    window.delete_project()

    assert gui_support["warning"], "оператор не получил объяснение отказа"
    assert gui_support["critical"] == [], "ошибка упала необработанной"
    text = gui_support["warning"][0][2]
    assert "выпущенных версий" in text, f"в сообщении нет сути: {text!r}"
    assert db.query(DocumentVersion).count() == 1, "выпуск потерян"
    window.close()
