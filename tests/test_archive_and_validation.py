from datetime import datetime
import pytest
from app.db.database import Base, engine, SessionLocal
from app.db.models import Project, Document
from app.core.services.storage_service import add_file_to_archive, link_file_to_document
from app.core.validators import validate_aook_dates, ValidationError

@pytest.fixture
def db():
    # Полностью пересоздаем таблицы для изоляции тестов
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()

def test_no_file_duplication_and_link_counter(db, tmp_path):
    dummy_scheme = tmp_path / "scheme_1.pdf"
    dummy_scheme.write_bytes(b"PDF-dummy-content-12345")

    project = Project(direction="Общестроительные", title="Тестовый объект")
    db.add(project)
    db.commit()

    aosr1 = Document(project_id=project.id, doc_type="AOSR", number="1")
    aosr2 = Document(project_id=project.id, doc_type="AOSR", number="2")
    db.add_all([aosr1, aosr2])
    db.commit()

    file1 = add_file_to_archive(db, dummy_scheme, "SCHEME")
    file2 = add_file_to_archive(db, dummy_scheme, "SCHEME")

    assert file1.id == file2.id

    link_file_to_document(db, aosr1.id, file1.id)
    link_file_to_document(db, aosr2.id, file1.id)

    # Обновляем состояние объекта из базы
    db.refresh(file1)
    assert file1.links_count == 2

def test_aook_date_validation():
    aosr_start = datetime(2026, 1, 10)
    aosr_end = datetime(2026, 1, 20)

    validate_aook_dates(
        aook_start=datetime(2026, 1, 5),
        aook_end=datetime(2026, 1, 25),
        aosr_dates=[(aosr_start, aosr_end)]
    )

    with pytest.raises(ValidationError):
        validate_aook_dates(
            aook_start=datetime(2026, 1, 5),
            aook_end=datetime(2026, 1, 15),
            aosr_dates=[(aosr_start, aosr_end)]
        )
