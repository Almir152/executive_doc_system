"""Изоляция тестов от рабочего хранилища.

Тесты не должны трогать боевую базу и боевой архив (ТЗ п.72: рабочее
хранилище, комплекты и BACKUP — разные сущности). Поэтому каталог данных
подменяется на временный ДО импорта app.config: переменная окружения
читается в момент первого импорта.
"""

import os
import shutil
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="executive_doc_test_")
os.environ["EXECUTIVE_DOC_DATA_DIR"] = _TEST_DATA_DIR
# Qt запускается без дисплея: без этого GUI-тесты падают не тестом, а
# целиком (Qt не может открыть настоящий экран и вызывает qFatal).
# QApplication из pytest-qt создаётся на уровне сессии, раньше фикстур,
# поэтому переменная выставляется здесь, при импорте.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from app.config import ensure_dirs  # noqa: E402

ensure_dirs()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture
def db():
    """Чистая схема и чистое хранилище на каждый тест.

    Схема поднимается тем же путём, что и в приложении: миграции, создание
    таблиц, наполнение справочников. Так тесты проверяют реальный путь
    инициализации, а не только create_all.

    Очищается и каталог архива: проверки целостности (осиротевшие и
    пропавшие файлы) иначе видели бы файлы, оставленные предыдущими тестами.
    """
    from app.config import ARCHIVE_DIR, BACKUP_DIR, PACKAGES_DIR
    from app.db.database import Base, SessionLocal, engine, init_db
    from app.db.migrations import set_user_version

    Base.metadata.drop_all(bind=engine)
    raw = engine.raw_connection()
    try:
        set_user_version(raw, 0)
    finally:
        raw.close()

    for directory in (ARCHIVE_DIR, PACKAGES_DIR, BACKUP_DIR):
        if directory.is_dir():
            for item in directory.iterdir():
                if item.is_file() and item.name != ".gitkeep":
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

    init_db()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def direction(db):
    """Первое направление из справочника ТЗ п.14."""
    from app.db.models import Direction

    row = db.query(Direction).order_by(Direction.sort_order).first()
    assert row is not None, "справочник направлений должен быть заполнен"
    return row


@pytest.fixture
def project(db, direction):
    from app.db.models import Project

    p = Project(
        direction_id=direction.id,
        title="Тестовый объект",
        address="г. Москва",
    )
    db.add(p)
    db.commit()
    return p


@pytest.fixture(autouse=True)
def qapp_env():
    """Заглушка на случай, если фикстуру попросят явно.

    Основная установка переменной — на уровне модуля: QApplication из
    pytest-qt имеет область сессии и создаётся раньше любой фикстуры
    области функции, поэтому переменная из фикстуры приходит слишком
    поздно, и Qt валит процесс целиком.
    """
    return None


@pytest.fixture(autouse=True)
def gui_support(request, monkeypatch):
    """Поддержка GUI-тестов: QApplication + заглушка модальных диалогов.

    Модальные диалоги глушатся и одновременно записываются, чтобы тест мог
    их проверить. Без заглушки GUI-тест молча зависает вместо падения.
    """
    if "gui" not in request.node.keywords:
        return

    request.getfixturevalue("qapp")

    from PyQt6.QtWidgets import QMessageBox

    calls = {"information": [], "warning": [], "critical": [], "question": []}

    def recorder(kind, default=None):
        def handler(*args, **kwargs):
            calls[kind].append(args)
            return default
        return handler

    monkeypatch.setattr(QMessageBox, "information", recorder("information"))
    monkeypatch.setattr(QMessageBox, "warning", recorder("warning"))
    monkeypatch.setattr(QMessageBox, "critical", recorder("critical"))
    monkeypatch.setattr(
        QMessageBox, "question",
        recorder("question", QMessageBox.StandardButton.No),
    )
    return calls
