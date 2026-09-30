"""Проверки комплекта перед выгрузкой и файл ошибок. ТЗ п.82, 83."""

from datetime import date, timedelta

from pathlib import Path

import pytest

from app.config import utcnow
from app.core import domain
from app.core.services import (
    document_service, export_checks, exporter, form_service, issue_service,
    storage_service,
)
from app.db.models import (
    DocumentArchiveLink, DocumentVersion, ProjectSection, SectionKind,
)


@pytest.fixture
def document(db, project):
    """Документ проекта без номера и даты."""
    return document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR
    )


def _issue(db, document, payload=None):
    """Выпустить документ: заполнить форму и зафиксировать версию (ТЗ п.85).

    Черновик и выпущенная версия — одна строка `DocumentVersion`: выпуск
    проставляет ей время. Форма заполняется до выпуска: выпущенную версию
    изменить нельзя (ТЗ п.54, 85), поэтому порядок здесь и в приложении
    одинаков.
    """
    if payload:
        form_service.save_draft(db, document.id, payload)
    version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document.id
    ).order_by(DocumentVersion.version_no.desc()).first()
    if version is not None:
        version.issued_at = utcnow()
    document.status = domain.DOC_STATUS_ISSUED
    db.commit()
    return document


RELEASED_PAYLOAD = {
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
def released_with_form(db, project):
    """Документ с заполненной формой и зафиксированной версией (ТЗ п.85)."""
    db.add(ProjectSection(
        project_id=project.id, kind_id=db.query(SectionKind).first().id,
        code="КЖ", name="Конструкции",
    ))
    db.commit()
    document = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15",
        doc_date=date(2026, 3, 1),
    )
    form_service.save_draft(db, document.id, dict(RELEASED_PAYLOAD))
    issue_service.issue_document(db, document.id, doc_date=date(2026, 3, 1))
    return document


@pytest.fixture
def released(db, project):
    """Выпущенный документ с номером, датой и выпущенной версией."""
    return _issue(db, document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15",
        doc_date=date(2026, 3, 1),
    ))


@pytest.fixture
def scheme_file(db, project, tmp_path):
    """Архивная исполнительная схема с настоящим файлом на диске."""
    source = tmp_path / "схема.txt"
    source.write_text("схема", encoding="utf-8")
    archive = storage_service.add_file_to_archive(
        db, source, project.id, category=domain.ARCHIVE_CATEGORY_SCHEMES
    )
    return archive


def _link(db, document, archive, role):
    version = archive.current_version
    db.add(DocumentArchiveLink(
        document_id=document.id, archive_document_id=archive.id,
        archive_version_id=version.id, link_role=role,
    ))
    db.commit()


# =====================================================================
# ПРОВЕРКИ КОМПЛЕКТА (ТЗ п.82)
# =====================================================================


def test_complete_package_has_no_problems(db, project, scheme_file):
    """Заполненный комплект проходит проверку без замечаний (ТЗ п.82)."""
    issued = _issue(db, document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15",
        doc_date=date(2026, 3, 1),
    ), payload={"section_refs": ["КЖ"]})
    # Комплект с приложением и схемой: и то и другое входит в выгрузку
    # вместе с актом (ТЗ п.75, 79).
    _link(db, issued, scheme_file, domain.LINK_ROLE_SCHEME)
    _link(db, issued, scheme_file, domain.LINK_ROLE_ATTACHMENT)
    db.add(ProjectSection(
        project_id=project.id, kind_id=db.query(SectionKind).first().id,
        code="КЖ", name="Конструкции",
    ))
    db.commit()

    result = export_checks.check_package(db, project.id)

    assert not result.has_errors, result.report()


def test_empty_package_is_reported(db, project):
    """Пустой комплект — ошибка проверки (ТЗ п.82)."""
    result = export_checks.check_package(db, project.id)

    assert result.has_errors
    assert result.by_code(export_checks.CHECK_DOCUMENTS)
    assert "не попал ни один документ" in result.report()


def test_check_covers_only_package_documents(db, project, released):
    """Во второй выгрузке проверяются только её документы (ТЗ п.75, 82)."""
    broken = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="7",
    )

    result = export_checks.check_package(db, project.id, document_ids=[released.id])

    subjects = {problem.subject for problem in result.problems}
    assert not result.by_code(export_checks.CHECK_NUMBERS), (
        "у документа вне выгрузки нет номера — это не замечание к комплекту"
    )
    assert not any("АООК" in subject for subject in subjects), (
        f"замечания относятся к постороннему документу: {subjects}"
    )
    assert broken.number == "7"


def test_unreleased_document_is_reported(db, project, document):
    """Невыпущенный документ в комплекте — ошибка (ТЗ п.54, 82)."""
    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_VERSIONS)

    assert problems and "не выпущен" in problems[0].message


def test_issued_document_without_version_is_reported(db, project):
    """Выпущенный документ без выпущенной версии — ошибка (ТЗ п.82)."""
    doc = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="1",
        doc_date=date(2026, 1, 5), status=domain.DOC_STATUS_ISSUED,
    )

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_VERSIONS)

    assert any("нет выпущенной версии" in problem.message for problem in problems)
    assert doc.id is not None


def test_missing_number_and_date_are_reported(db, project, document):
    """Номер и дата документа обязательны для выгрузки (ТЗ п.42, 43, 82)."""
    # Номер в базе обязателен (NOT NULL), поэтому «нет номера» достижимо
    # только как пустая строка: так выглядит проект с неудачным импортом.
    document.number = ""
    db.commit()

    result = export_checks.check_package(db, project.id)

    assert result.by_code(export_checks.CHECK_NUMBERS)
    assert result.by_code(export_checks.CHECK_DATES)


def test_future_date_is_reported(db, project, document):
    """Дата позже сегодняшней — ошибка проверки (ТЗ п.87)."""
    document.doc_date = date.today() + timedelta(days=1)
    db.commit()

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_DATES)

    assert any("позже сегодняшней" in problem.message for problem in problems)


def test_duplicate_number_is_reported(db, project, released):
    """Один и тот же номер дважды в комплекте — ошибка (ТЗ п.42, 82)."""
    second = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR,
        doc_date=date(2026, 4, 1),
    )
    # Номер присваивается базой данных при обходе сервиса: так выглядит
    # проект, где номера правились вручную или при импорте.
    second.number = "15"
    db.commit()
    _issue(db, second)

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_NUMBERS)

    assert any("повторяется" in problem.message for problem in problems)


def test_link_without_pinned_version_is_reported(db, project, released, scheme_file):
    """Связь без закреплённой версии файла — ошибка (ТЗ п.91, 82)."""
    db.add(DocumentArchiveLink(
        document_id=released.id, archive_document_id=scheme_file.id,
        link_role=domain.LINK_ROLE_SCHEME,
    ))
    db.commit()

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_LINKS)

    assert any("закрепляет версию" in problem.message for problem in problems)


def test_deleted_file_is_reported(db, project, released, scheme_file):
    """Пропавший на диске файл связи — ошибка состава комплекта (ТЗ п.82)."""
    from pathlib import Path

    _link(db, released, scheme_file, domain.LINK_ROLE_SCHEME)
    Path(scheme_file.current_version.stored_path).unlink()

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_FILES)

    assert any("не найден на диске" in problem.message for problem in problems)


def test_missing_section_reference_is_reported(db, project):
    """Ссылка на несуществующий раздел — ошибка состава (ТЗ п.21, 82)."""
    _issue(db, document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15",
        doc_date=date(2026, 3, 1),
    ), payload={"section_refs": ["НЕТ-ТАКОГО"]})

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_COMPOSITION)

    assert problems and "НЕТ-ТАКОГО" in problems[0].message


def test_existing_section_reference_is_accepted(db, project):
    """Ссылка на существующий раздел замечанием не считается (ТЗ п.21)."""
    db.add(ProjectSection(
        project_id=project.id, kind_id=db.query(SectionKind).first().id,
        code="КЖ", name="Конструкции",
    ))
    db.commit()
    _issue(db, document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="15",
        doc_date=date(2026, 3, 1),
    ), payload={"section_refs": ["КЖ"]})

    assert not export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_COMPOSITION)


def test_attachments_absent_is_reported(db, project, released, scheme_file):
    """Комплект без приложений — замечание проверки (ТЗ п.79, 82)."""
    _link(db, released, scheme_file, domain.LINK_ROLE_SCHEME)

    problems = export_checks.check_package(
        db, project.id
    ).by_code(export_checks.CHECK_ATTACHMENTS)

    assert problems and "приложени" in problems[0].message


def test_unknown_project_is_reported(db):
    """Проверка несуществующего проекта возвращает замечание, а не исключение."""
    result = export_checks.check_package(db, 999999)

    assert result.has_errors
    assert "не найден" in result.report()


def test_report_lists_every_problem(db, project, document):
    """Отчёт перечисляет все найденные проблемы (ТЗ п.82, 83)."""
    result = export_checks.check_package(db, project.id)

    report = result.report()
    assert f"Обнаружено замечаний: {len(result.problems)}" in report
    for problem in result.problems:
        assert problem.code in report
        assert problem.message in report


def test_clean_report_says_no_problems():
    """Отчёт без замечаний сообщает, что ошибок нет."""
    assert export_checks.CheckResult(()).report() == "Ошибок не обнаружено."


# =====================================================================
# ФАЙЛ ОШИБОК ВЫГРУЗКИ (ТЗ п.83)
# =====================================================================


def test_errors_file_is_written_to_current_export(tmp_path):
    """Файл ошибок появляется в папке текущей выгрузки (ТЗ п.83)."""
    result = export_checks.CheckResult((
        export_checks.CheckProblem(export_checks.CHECK_NUMBERS, "АОСР", "Не задан номер"),
    ))

    path = exporter.write_errors_file(tmp_path, result)

    assert path == tmp_path / "Ошибки выгрузки.txt"
    assert "Не задан номер" in path.read_text(encoding="utf-8")


def test_errors_file_name_is_exact():
    """Имя файла ошибок — как в ТЗ п.83."""
    assert exporter.ERRORS_FILE_NAME == "Ошибки выгрузки.txt"


def test_errors_file_does_not_touch_previous_exports(tmp_path):
    """Ранее созданные комплекты не изменяются (ТЗ п.71, 83)."""
    previous = tmp_path / "Комплект 01"
    previous.mkdir()
    (previous / "Реестр_выгрузки.pdf").write_text("старый", encoding="utf-8")

    exporter.write_errors_file(
        tmp_path / "Комплект 02",
        export_checks.CheckResult((
            export_checks.CheckProblem(export_checks.CHECK_FILES, "a.pdf", "Нет файла"),
        )),
    )

    assert [item.name for item in previous.iterdir()] == ["Реестр_выгрузки.pdf"]
    assert not (previous / "Ошибки выгрузки.txt").exists()


def test_repeated_export_replaces_only_its_own_file(tmp_path):
    """Повторная выгрузка пишет свой файл заново (ТЗ п.83)."""
    target = tmp_path / "Комплект 03"
    exporter.write_errors_file(target, export_checks.CheckResult((
        export_checks.CheckProblem(export_checks.CHECK_FILES, "a.pdf", "нет файла"),
    )))

    exporter.write_errors_file(target, export_checks.CheckResult((
        export_checks.CheckProblem(export_checks.CHECK_LINKS, "b.pdf", "нет связи"),
    )))

    text = (target / "Ошибки выгрузки.txt").read_text(encoding="utf-8")
    assert "нет связи" in text
    assert "нет файла" not in text


# =====================================================================
# ВЫГРУЗКА С ЗАМЕЧАНИЯМИ (ТЗ п.82, 83)
# =====================================================================


@pytest.fixture
def dialogs(monkeypatch):
    """Подмена QMessageBox: окно не показывается, ответ задаётся заранее.

    Настоящее модальное окно в тесте hang-ило бы прогон, а `QMessageBox`
    подменяется классом, а не функцией: код окна обращается к его
    перечислениям `Icon` и `ButtonRole`.
    """
    import app.ui.main_window as main_module
    from PyQt6.QtWidgets import QMessageBox

    recorded = {"chosen": None, "messages": [], "texts": []}

    class FakeBox:
        Icon = QMessageBox.Icon
        ButtonRole = QMessageBox.ButtonRole
        StandardButton = QMessageBox.StandardButton

        def __init__(self, *args, **kwargs):
            self.text = ""
            self.title = ""
            self.buttons: dict[str, str] = {}

        def setIcon(self, *args):
            pass

        def setWindowTitle(self, title):
            self.title = title

        def setText(self, text):
            self.text = text
            recorded["texts"].append(text)

        def addButton(self, text, role):
            button = f"button::{text}"
            self.buttons[text] = button
            return button

        def exec(self):
            return 0

        def clickedButton(self):
            return self.buttons.get(recorded["chosen"])

        @staticmethod
        def information(*args):
            recorded["messages"].append(args[2:])

        @staticmethod
        def critical(*args):
            recorded["messages"].append(args[2:])

        @staticmethod
        def warning(*args):
            recorded["messages"].append(args[2:])

    monkeypatch.setattr(main_module, "QMessageBox", FakeBox)
    return recorded


@pytest.fixture
def main_window(db, project, qapp):
    from app.ui.main_window import MainWindow

    window = MainWindow()
    window.load_projects()
    window.projects_table.selectRow(0)
    yield window
    window.close()


@pytest.fixture
def target_dir(tmp_path, monkeypatch):
    """Папка выгрузки выбирается без диалога."""
    from PyQt6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(tmp_path)),
    )
    return tmp_path


def stub_package_dialog(monkeypatch, base_dir, *, document_ids=None,
                         variant=domain.EXPORT_VARIANT_ALL,
                         page_numbering=False, accepted=True):
    """Диалог комплекта (ТЗ п.69) без реального окна.

    Возвращает состояние, чтобы тест мог проверить решение оператора.
    """
    import app.ui.main_window as main_module
    from app.ui.package_dialog import PackageDialog

    state = {
        "document_ids": document_ids,
        "variant": variant,
        "page_numbering": page_numbering,
        "root_name": "Комплекты",
        "exec_calls": 0,
    }

    class FakeDialog:
        DialogCode = PackageDialog.DialogCode

        def __init__(self, db, project_id, parent=None):
            self.db = db
            self.project_id = project_id
            self.base_dir = Path(base_dir)

        def exec(self):
            state["exec_calls"] += 1
            return (
                PackageDialog.DialogCode.Accepted if accepted
                else PackageDialog.DialogCode.Rejected
            )

        def selected_document_ids(self):
            if state["document_ids"] is not None:
                return list(state["document_ids"])
            from app.core.services import package_service

            return [
                d.id for d in package_service.project_documents(
                    self.db, self.project_id
                )
            ]

        def variant(self):
            return state["variant"]

        def root_name(self):
            return state["root_name"]

        def page_numbering(self):
            return state["page_numbering"]

    monkeypatch.setattr(main_module, "PackageDialog", FakeDialog)
    return state


def package_folder(base_dir, name="Комплект 01"):
    return Path(base_dir) / "Комплекты" / name


def _problem() -> export_checks.CheckResult:
    return export_checks.CheckResult((
        export_checks.CheckProblem(
            export_checks.CHECK_NUMBERS, "АОСР", "Не задан номер"
        ),
    ))


def test_operator_can_finish_export_with_errors(main_window, dialogs):
    """Выбор «всё равно завершить» продолжает выгрузку (ТЗ п.83)."""
    dialogs["chosen"] = "Всё равно завершить"

    assert main_window._confirm_export_with_errors(_problem())


def test_operator_sees_problems_before_choosing(main_window, dialogs):
    """Проблемы комплекта показаны оператору до выбора (ТЗ п.82, 83)."""
    dialogs["chosen"] = "Всё равно завершить"

    main_window._confirm_export_with_errors(_problem())

    assert dialogs["texts"], "проблемы должны быть показаны оператору"
    assert "Не задан номер" in dialogs["texts"][0]


def test_operator_can_cancel_export_on_errors(main_window, dialogs):
    """Отказ оператора отменяет выгрузку целиком (ТЗ п.83)."""
    dialogs["chosen"] = "Отменить выгрузку"

    assert not main_window._confirm_export_with_errors(_problem())


def test_export_creates_errors_file_after_confirmation(
    main_window, dialogs, target_dir, monkeypatch, document
):
    """После подтверждения выгрузка завершается и пишет файл ошибок (ТЗ п.83)."""
    monkeypatch.setattr(
        main_window, "_confirm_export_with_errors", lambda result: True
    )
    stub_package_dialog(monkeypatch, target_dir)

    main_window.export_project_pdf()

    folder = package_folder(target_dir)
    assert (folder / "Ошибки выгрузки.txt").is_file()
    assert (folder / "Реестр_выгрузки.pdf").is_file()
    assert dialogs["messages"], "оператору сообщают о результате выгрузки"
    assert any("Ошибки выгрузки.txt" in message[0] for message in dialogs["messages"])


def test_export_is_not_started_when_operator_declines(
    main_window, dialogs, target_dir, monkeypatch
):
    """Без согласия оператора выгрузка не начинается (ТЗ п.83)."""
    monkeypatch.setattr(
        main_window, "_confirm_export_with_errors", lambda result: False
    )
    stub_package_dialog(monkeypatch, target_dir)

    main_window.export_project_pdf()

    assert not package_folder(target_dir).exists()
    assert not (target_dir / "Реестр_выгрузки.pdf").exists()
    assert not (target_dir / "Ошибки выгрузки.txt").exists()


def test_export_is_not_started_when_dialog_is_cancelled(
    main_window, dialogs, target_dir, monkeypatch
):
    """Отмена диалога комплекта ничего не выгружает (ТЗ п.69)."""
    state = stub_package_dialog(monkeypatch, target_dir, accepted=False)

    main_window.export_project_pdf()

    assert state["exec_calls"] == 1
    assert not package_folder(target_dir).exists()


def test_clean_package_exports_without_errors_file(
    main_window, dialogs, target_dir, monkeypatch, released_with_form
):
    """Чистый комплект выгружается без файла ошибок и без вопроса (ТЗ п.83)."""
    import app.ui.main_window as main_module

    def no_dialog(result):
        raise AssertionError("чистый комплект не должен спрашивать оператора")

    monkeypatch.setattr(
        main_module, "check_package", lambda dbase, pid, ids=None: export_checks.CheckResult(())
    )
    monkeypatch.setattr(main_window, "_confirm_export_with_errors", no_dialog)
    stub_package_dialog(monkeypatch, target_dir)

    main_window.export_project_pdf()

    folder = package_folder(target_dir)
    assert (folder / "Реестр_выгрузки.pdf").is_file()
    assert not (folder / "Ошибки выгрузки.txt").exists()


def test_export_reports_folder_problem_instead_of_crash(
    main_window, dialogs, tmp_path, monkeypatch, released_with_form
):
    """Недоступное место хранения: понятное сообщение, рабочая база цела."""
    import app.ui.main_window as main_module

    (tmp_path / "Комплекты").write_text("не папка", encoding="utf-8")
    stub_package_dialog(monkeypatch, tmp_path)

    main_window.export_project_pdf()

    texts = [text for message in dialogs["messages"] for text in message]
    assert any("заново" in text for text in texts), (
        "оператору предлагают выбрать место хранения заново (ТЗ п.73): "
        f"{texts}"
    )
    assert main_module.MainWindow is not None


def test_warnings_do_not_block_export_but_are_shown(
    main_window, dialogs, target_dir, monkeypatch, released_with_form
):
    """Замечание не останавливает выгрузку, но видно оператору (ТЗ п.83)."""
    import app.ui.main_window as main_module

    monkeypatch.setattr(
        main_module, "check_package",
        lambda dbase, pid, ids=None: export_checks.CheckResult((
            export_checks.CheckProblem(
                export_checks.CHECK_ATTACHMENTS,
                "АОСР №1",
                "У документа нет приложений",
                export_checks.SEVERITY_WARNING,
            ),
        )),
    )

    def no_dialog(result):
        raise AssertionError("замечание не должно требовать решения оператора")

    monkeypatch.setattr(main_window, "_confirm_export_with_errors", no_dialog)
    stub_package_dialog(monkeypatch, target_dir)

    main_window.export_project_pdf()

    folder = package_folder(target_dir)
    assert (folder / "Реестр_выгрузки.pdf").is_file()
    assert not (folder / export_checks.ERRORS_FILE_NAME).exists()
    texts = [text for message in dialogs["messages"] for text in message]
    assert any("У документа нет приложений" in text for text in texts), (
        f"замечание должно быть показано оператору: {texts}"
    )


def test_warnings_are_capped_with_remainder(
    main_window, dialogs, target_dir, monkeypatch, released_with_form
):
    """Длинный список замечаний обрезается с указанием остатка (ТЗ п.83)."""
    import app.ui.main_window as main_module

    monkeypatch.setattr(
        main_module, "check_package",
        lambda dbase, pid, ids=None: export_checks.CheckResult(tuple(
            export_checks.CheckProblem(
                export_checks.CHECK_ATTACHMENTS,
                f"АОСР №{index}",
                "нет приложений",
                export_checks.SEVERITY_WARNING,
            )
            for index in range(1, 14)
        )),
    )
    monkeypatch.setattr(main_window, "_confirm_export_with_errors", lambda result: True)
    stub_package_dialog(monkeypatch, target_dir)

    main_window.export_project_pdf()

    texts = [text for message in dialogs["messages"] for text in message]
    summary = [text for text in texts if "ещё" in text]
    assert summary, f"оператор должен видеть, что список неполный: {texts}"
    assert "3 замечаний" in summary[0]
