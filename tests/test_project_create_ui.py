"""Создание проекта в интерфейсе: диалог и его поля (ТЗ п.17, 20)."""

import pytest

from app.db.models import Project


pytestmark = pytest.mark.gui


@pytest.fixture
def window(db, project):
    from app.ui.main_window import MainWindow

    main = MainWindow()
    main.load_projects()
    yield main
    main.close()


def test_create_project_dialog_has_all_card_fields(window, direction):
    """В диалоге есть наименование, направление и адрес (ТЗ п.17)."""
    from app.ui.project_dialog import ProjectCreateDialog

    dialog = ProjectCreateDialog(window.db, window)

    values = dialog.values()
    assert set(values) == {"title", "direction_id", "address"}


def test_create_project_dialog_offers_directions_from_directory(window):
    """Направления берутся из справочника, а не из захардкоженного списка."""
    from app.ui.project_dialog import direction_options

    options = direction_options(window.db)

    assert options
    assert all(name and isinstance(identifier, int) for name, identifier in options)


def test_address_is_asked_not_substituted(window, direction, monkeypatch):
    """Адрес вводится оператором и сохраняется как введено (ТЗ п.17).

    Дефект: адрес не спрашивался, а молча подставлялся «Не указан».
    """
    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

        def values(self):
            return {
                "title": "Жилой дом",
                "direction_id": direction.id,
                "address": "г. Москва, ул. Ленина, 12",
            }

    monkeypatch.setattr(
        "app.ui.main_window.ProjectCreateDialog", FakeDialog
    )
    window.add_project()

    row = window.db.query(Project).filter(
        Project.title == "Жилой дом"
    ).one()
    assert row.address == "г. Москва, ул. Ленина, 12"


def test_project_without_address_saves_empty_address(window, direction,
                                                    monkeypatch):
    """Пустой адрес остаётся пустым, а не превращается в «Не указан»."""
    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

        def values(self):
            return {
                "title": "Жилой дом 2",
                "direction_id": direction.id,
                "address": "",
            }

    monkeypatch.setattr("app.ui.main_window.ProjectCreateDialog", FakeDialog)
    window.add_project()

    row = window.db.query(Project).filter(
        Project.title == "Жилой дом 2"
    ).one()
    assert row.address is None


def test_cancelled_dialog_creates_nothing(window, monkeypatch):
    """Отказ в диалоге не создаёт проект (ТЗ п.17)."""
    before = window.db.query(Project).count()

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr("app.ui.main_window.ProjectCreateDialog", FakeDialog)
    window.add_project()

    assert window.db.query(Project).count() == before


def test_project_without_direction_is_not_created(window, monkeypatch):
    """Без направления проект не создаётся (ТЗ п.20)."""
    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

        def values(self):
            return {"title": "Без направления", "direction_id": None,
                    "address": ""}

    monkeypatch.setattr("app.ui.main_window.ProjectCreateDialog", FakeDialog)
    window.add_project()

    assert window.db.query(Project).filter(
        Project.title == "Без направления"
    ).first() is None


def test_creation_is_reflected_in_the_list(window, direction, monkeypatch):
    """Созданный проект появляется в таблице «Проекты» (ТЗ п.16)."""
    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

        def values(self):
            return {
                "title": "Новый дом",
                "direction_id": direction.id,
                "address": "г. Москва",
            }

    monkeypatch.setattr("app.ui.main_window.ProjectCreateDialog", FakeDialog)
    window.add_project()

    titles = [
        window.projects_table.item(row, 2).text()
        for row in range(window.projects_table.rowCount())
    ]
    assert "Новый дом" in titles
