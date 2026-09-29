import os
import pytest
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QInputDialog

from app.db.database import Base, engine, SessionLocal
from app.db.models import Project, Document, ArchiveFile
from app.core.services.storage_service import add_file_to_archive, link_file_to_document
from app.core.services.exporter import export_package, generate_simple_pdf
from app.core.validators import validate_aook_dates, ValidationError
from app.ai.connector import AIConnector
from app.ui.main_window import MainWindow

@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

# -------------------------------------------------------------------
# 1. ТЕСТЫ БАЗЫ ДАННЫХ И СТРЕСС-НАГРУЗКИ
# -------------------------------------------------------------------
def test_stress_bulk_documents_creation(db):
    """Стресс-тест: массовое создание 100 документов без падений"""
    project = Project(direction="Общестроительные", title="Стресс-объект")
    db.add(project)
    db.commit()

    docs = [
        Document(project_id=project.id, doc_type="АОСР", number=str(i))
        for i in range(1, 101)
    ]
    db.add_all(docs)
    db.commit()

    count = db.query(Document).filter(Document.project_id == project.id).count()
    assert count == 100

def test_file_deduplication_and_integrity(db, tmp_path):
    """Проверка сохранности и дедупликации файлов при одинаковом SHA-256 хэше"""
    f1 = tmp_path / "cert_A.pdf"
    f2 = tmp_path / "cert_B.pdf"
    f1.write_bytes(b"CERTIFICATE_BYTES_999")
    f2.write_bytes(b"CERTIFICATE_BYTES_999")

    arc1 = add_file_to_archive(db, f1, "Сертификат")
    arc2 = add_file_to_archive(db, f2, "Сертификат")

    assert arc1.id == arc2.id
    assert arc1.file_hash == arc2.file_hash

# -------------------------------------------------------------------
# 2. ТЕСТЫ БЕЗОПАСНОСТИ AI CONNECTOR (Разделы 5-11 ТЗ)
# -------------------------------------------------------------------
def test_ai_connector_safety_modes():
    """Проверка безопасных режимов ИИ"""
    ai_off = AIConnector(mode="OFF")
    res_off = ai_off.analyze_package([1, 2])
    assert res_off["status"] == "disabled"

    ai_local = AIConnector(mode="LOCAL")
    res_local = ai_local.analyze_package([1, 2])
    assert res_local["status"] == "success"
    assert len(res_local["proposals"]) > 0

# -------------------------------------------------------------------
# 3. GUI ТЕСТЫ (PyQt6): Проверка интерфейса и диалогов
# -------------------------------------------------------------------
def test_gui_main_window_navigation_and_seeding(qtbot, db):
    """Проверка автозаполнения (seeding) при пустой БД и навигации по меню"""
    window = MainWindow()
    qtbot.addWidget(window)

    # База должна была автоматически наполниться демо-проектом
    assert window.projects_table.rowCount() == 1
    assert window.projects_table.item(0, 2).text() == "Строительство корпуса МФТИ"

    # Переключение всех пунктов меню
    for row in range(window.nav_list.count()):
        window.nav_list.setCurrentRow(row)
        assert "Рабочая область:" in window.title_label.text()

    # Проверка работы кнопки ИИ
    window.run_ai_check()
    assert "Результат анализа ИИ:" in window.ai_output.toPlainText()

def test_gui_create_project_dialog(qtbot, db, monkeypatch):
    """Тестирование диалога создания нового проекта в GUI"""
    window = MainWindow()
    qtbot.addWidget(window)

    # Эмулируем пользовательский ввод в QInputDialog и QMessageBox
    inputs = iter(["Новый Лабораторный Корпус", "Внутренние инженерные сети"])
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: (next(inputs), True))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: (next(inputs), True))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    # Нажимаем кнопку создания проекта
    window.btn_add_project.click()

    # Проверяем, что в таблице появился новый проект
    assert window.projects_table.rowCount() == 2
    assert window.projects_table.item(1, 2).text() == "Новый Лабораторный Корпус"

# -------------------------------------------------------------------
# 4. ТЕСТЫ ГЕНЕРАЦИИ PDF И ЭКСПОРТА (Разделы 55-81 ТЗ)
# -------------------------------------------------------------------
def test_pdf_export_cyrillic_generation(db, tmp_path):
    """Проверка генерации PDF-реестра с кириллицей"""
    project = Project(direction="Общестроительные", title="Тестовый объект PDF")
    db.add(project)
    db.commit()

    doc = Document(project_id=project.id, doc_type="АОСР", number="100-П")
    db.add(doc)
    db.commit()

    export_dir = tmp_path / "export_test"
    export_package(db, project.id, export_dir)

    pdf_file = export_dir / "Реестр_выгрузки.pdf"
    assert pdf_file.exists()
    assert pdf_file.stat().st_size > 0

def test_export_package_missing_project(db, tmp_path):
    """Обработка ошибок при экспорте несуществующего проекта"""
    with pytest.raises(ValueError, match="Проект не найден"):
        export_package(db, project_id=99999, target_dir=tmp_path / "invalid")
