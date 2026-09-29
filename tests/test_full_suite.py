import os
import pytest
from pathlib import Path
from datetime import datetime
from app.db.database import Base, engine, SessionLocal
from app.db.models import Project, Document, ArchiveFile
from app.core.services.storage_service import add_file_to_archive, link_file_to_document
from app.core.services.exporter import export_package
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

# 1. Стресс-тест создания документов
def test_stress_bulk_documents_creation(db):
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

# 2. Проверка дедупликации
def test_file_deduplication_and_integrity(db, tmp_path):
    f1 = tmp_path / "cert_A.pdf"
    f2 = tmp_path / "cert_B.pdf"
    f1.write_bytes(b"CERTIFICATE_BYTES_999")
    f2.write_bytes(b"CERTIFICATE_BYTES_999")

    arc1 = add_file_to_archive(db, f1, "Сертификат")
    arc2 = add_file_to_archive(db, f2, "Сертификат")

    assert arc1.id == arc2.id
    assert arc1.file_hash == arc2.file_hash

# 3. Режимы безопасности AI Connector
def test_ai_connector_safety_modes():
    ai_off = AIConnector(mode="OFF")
    res_off = ai_off.analyze_package([1, 2])
    assert res_off["status"] == "disabled"

    ai_local = AIConnector(mode="LOCAL")
    res_local = ai_local.analyze_package([1, 2])
    assert res_local["status"] == "success"
    assert len(res_local["proposals"]) > 0

# 4. GUI-тест переключения и кликов в PyQt6
def test_gui_main_window_navigation(qtbot, db):
    p = Project(direction="Наружные сети", title="Тест GUI", address="Москва")
    db.add(p)
    db.commit()

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.projects_table.rowCount() >= 1

    for row in range(window.nav_list.count()):
        window.nav_list.setCurrentRow(row)
        assert "Рабочая область:" in window.title_label.text()

    window.run_ai_check()
    assert "Результат анализа ИИ:" in window.ai_output.toPlainText()

# 5. Тест ошибок экспорта
def test_export_package_missing_project(db, tmp_path):
    with pytest.raises(ValueError, match="Проект не найден"):
        export_package(db, project_id=99999, target_dir=tmp_path / "invalid")
