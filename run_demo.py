"""Сквозная проверка работающей части системы.

Проверяет то, что действительно реализовано на текущем этапе:
справочники -> проект -> документы -> архив без дублирования -> версии ->
связи с ролями -> ИИ-агент по запросу -> выгрузка реестра -> миграция схемы.

Проверка идёт в отдельном временном каталоге: рабочее хранилище оператора
не затрагивается. Своё значение EXECUTIVE_DOC_DATA_DIR скрипт уважает.

Проверяются также обновление базы через миграции и работа ИИ-агента:
запрос оператора, черновики, применение по подтверждению и нормативные
основания (ТЗ п.74, 97, 101-106).
"""

import os
import sys
import tempfile
from pathlib import Path

# Каталог данных задаётся ДО импорта app.config: пути вычисляются при импорте.
_TMP_DIR = tempfile.TemporaryDirectory(prefix="eds-demo-")
os.environ.setdefault(
    "EXECUTIVE_DOC_DATA_DIR", str(Path(_TMP_DIR.name) / "storage")
)

from app import config as app_config  # noqa: E402
from app.ai import context as ai_context, normative  # noqa: E402
from app.config import BACKUP_DIR, DATA_DIR, PACKAGES_DIR, ensure_dirs  # noqa: E402
from app.core import domain  # noqa: E402
from app.core.services import backup_service  # noqa: E402
from app.core.services.exporter import export_package  # noqa: E402
from app.core.services.project_service import (  # noqa: E402
    ProjectError, can_delete_project, delete_project, list_events,
)
from app.core.services.storage_service import (  # noqa: E402
    add_file_to_archive, add_version, can_delete_archive_document, delete_archive_document,
    find_orphan_files,
)
from app.db.database import SessionLocal, check_integrity, get_schema_version, init_db  # noqa: E402
from app.db.migrations import SCHEMA_VERSION  # noqa: E402
from app.db.models import (  # noqa: E402
    ArchiveDocument, ArchiveFileVersion, Direction, Document, DocumentArchiveLink,
    DocumentLink, DocumentVersion, MaterialType, Package, PackageEntry, Project,
)

FAILURES: list[str] = []


def session_at(db_path: Path):
    """Сессия к чужой базе — как на другом компьютере (ТЗ п.98)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    session = sessionmaker(bind=engine)()
    return session


def integrity_problems(db_path: Path) -> list[str]:
    """Нарушения целостности чужой базы (ТЗ п.54)."""
    import sqlite3

    raw = sqlite3.connect(str(db_path))
    try:
        problems = []
        if raw.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("база повреждена")
        if raw.execute("PRAGMA foreign_key_check").fetchall():
            problems.append("нарушены внешние ключи")
        return problems
    finally:
        raw.close()


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "СБОЙ"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def main() -> int:
    print("=== СКВОЗНАЯ ПРОВЕРКА РАБОТАЮЩЕЙ ЧАСТЕМ СИСТЕМЫ ===")
    print(f"Каталог данных: {DATA_DIR}")
    ensure_dirs()

    report = init_db()
    print(f"[+] Миграции применены: {report['migrations'] or 'не требуются'}")
    print(f"[+] Справочники заполнены: {report['seeded']}")
    # Схема сверяется с актуальной версией, а не с зашитым числом: иначе
    # каждая следующая миграция ломала бы сквозную проверку.
    check("версия схемы БД", get_schema_version() == SCHEMA_VERSION,
          f"user_version={get_schema_version()}, ожидается {SCHEMA_VERSION}")

    db = SessionLocal()

    # --- Справочники направлений (ТЗ п.14) ---
    directions = db.query(Direction).order_by(Direction.sort_order).all()
    check(
        "справочник направлений заполнен (ТЗ п.14)",
        [d.name for d in directions] == list(domain.DIRECTIONS),
        f"{[d.name for d in directions]}",
    )

    # --- Проект (ТЗ п.17) ---
    project = Project(
        direction_id=directions[0].id,
        title="Строительство корпуса (демо)",
        address="г. Долгопрудный, ул. Первмайская",
    )
    db.add(project)
    db.commit()
    print(f"\n[+] Проект создан: '{project.title}' (ID: {project.id})")

    # --- Документы (ТЗ п.42, 43) ---
    # Создание идёт через сервис: он же пишет событие истории (ТЗ п.86).
    from app.core.services import document_service

    aosr1 = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="1"
    )
    aosr2 = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOSR, number="2"
    )
    check(
        "документы в статусе 'черновик' (ТЗ п.85)",
        all(d.status == domain.DOC_STATUS_DRAFT for d in (aosr1, aosr2)),
    )
    print(f"[+] Добавлены акты: {aosr1.type_label} №1 и {aosr1.type_label} №2")

    # --- Сроки работ и связь итогового акта с АОСР (ТЗ п.43, 87) ---
    from datetime import date as _date

    from app.core.services import form_service, link_service
    from app.core.services.issue_service import IssueError, issue_document

    form_service.save_draft(db, aosr1.id, {
        "object_name": "Корпус 2", "address": project.address,
        "work_description": "Армирование стен, 120 м²", "section_refs": "",
        "work_period": "с 01.04.2024 по 30.04.2024",
        "period_start": "01.04.2024", "period_end": "30.04.2024",
        "work_volume": "120 м² бетона Б25", "has_defects": "Нет",
        "conclusion": "Работы выполнены в полном объёме",
        "work_performer": "ООО «Строй»",
    })
    aook = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOOK, number="1",
        doc_date=_date(2024, 5, 15),
    )

    def _aook_payload(start: str, end: str) -> dict:
        return {
            "object_name": "Корпус 2", "address": project.address,
            "work_description": "Приёмка завершённых работ",
            "base_documents": "Договор №12 от 01.03.2024",
            "period_start": start, "period_end": end,
            "work_volume": "120 м² бетона Б25",
            "decisions": "Принято без замечаний",
        }

    # Итоговый акт завершает АОСР №1: связь выбирает оператор.
    link_service.link_documents(
        db, document_id=aook.id, related_document_id=aosr1.id,
        link_role=domain.LINK_ROLE_FINALIZES,
    )
    check("связь «завершает акт» создана (ТЗ п.87)",
          [item.number for item in link_service.finalized_acts(db, aook.id)] == ["1"])

    # Даты противоречат друг другу: система обязана отказать, но не править их.
    form_service.save_draft(db, aook.id, _aook_payload("01.03.2024", "20.03.2024"))
    check("система не подставляет даты сама (ТЗ п.43)",
          form_service.load_draft(db, aook.id)["period_end"] == "20.03.2024")
    try:
        issue_document(db, aook.id)
        check("выпуск с неверным порядком дат отклонён (ТЗ п.87, 96)", False,
              "выпуск состоялся")
    except IssueError as exc:
        check("выпуск с неверным порядком дат отклонён (ТЗ п.87, 96)",
              any("раньше окончания" in text for text in exc.problems),
              f"причина: {exc.problems[0][:70]}")

    form_service.save_draft(db, aook.id, _aook_payload("01.04.2024", "31.05.2024"))
    version = issue_document(db, aook.id)
    check("после исправления дат итоговый акт выпущен (ТЗ п.87)",
          version.is_actual is True)

    # Выпуск — исторический результат: проект с ним удалить нельзя (ТЗ п.54, 85).
    try:
        delete_project(db, project.id)
        check("проект с выпуском не удаляется (ТЗ п.54, 85)", False, "удалён")
    except ProjectError as exc:
        check("проект с выпуском не удаляется (ТЗ п.54, 85)",
              "выпущенных версий" in str(exc), str(exc)[:60])
    # --- Архив: дедупликация (ТЗ п.84, 92) ---
    with tempfile.TemporaryDirectory() as tmp:
        scheme = Path(tmp) / "Схема №12.pdf"
        scheme.write_bytes(b"%PDF-1.4 demo executive scheme")

        archive_doc = add_file_to_archive(
            db, scheme, project.id, domain.ARCHIVE_CATEGORY_SCHEMES
        )
        again = add_file_to_archive(
            db, scheme, project.id, domain.ARCHIVE_CATEGORY_SCHEMES
        )
        check("дедупликация: один логический документ (ТЗ п.92)",
              archive_doc.id == again.id, f"ID {archive_doc.id}")
        check("одна физическая копия на диске",
              len(list((DATA_DIR / "internal_archive").glob("*.pdf"))) == 1)
        check("категория архива определена", archive_doc.category == domain.ARCHIVE_CATEGORY_SCHEMES)
        check("тип файла восстановлен по расширению", archive_doc.file_type == "pdf",
              f"file_type={archive_doc.file_type!r}")

        # --- Новая версия того же документа (ТЗ п.53, 91) ---
        scheme_v2 = Path(tmp) / "Схема №12.pdf"
        scheme_v2.write_bytes(b"%PDF-1.4 demo executive scheme REVISED")
        v2 = add_version(db, archive_doc.id, scheme_v2)
        db.refresh(archive_doc)
        check("новая редакция создаёт версию 2 (ТЗ п.53)", v2.version_no == 2,
              f"версий: {len(archive_doc.versions)}")
        check("прежняя версия сохранена, а не перезаписана (ТЗ п.93)",
              len(archive_doc.versions) == 2)
        check("актуальна ровно одна версия (ТЗ п.91)",
              sum(1 for v in archive_doc.versions if v.is_actual) == 1)
        check("текущая версия — последняя", archive_doc.current_version.version_no == 2)

        # --- Связи с ролями (ТЗ п.47, 49, 91) ---
        v1 = archive_doc.versions[0]
        for doc in (aosr1, aosr2):
            db.add(DocumentArchiveLink(
                document_id=doc.id,
                archive_document_id=archive_doc.id,
                archive_version_id=v1.id,   # закреплена версия 1 (ТЗ п.91)
                link_role=domain.LINK_ROLE_SCHEME,
            ))
        db.commit()
        db.refresh(archive_doc)
        check("одна схема связана с двумя актами (ТЗ п.47, 84)",
              archive_doc.links_count == 2, f"связей: {archive_doc.links_count}")
        check("физическая копия не удвоилась",
              len(list((DATA_DIR / "internal_archive").glob("*.pdf"))) == 2,
              "2 версии = 2 файла")

        allowed, links = can_delete_archive_document(db, archive_doc.id)
        check("используемый архивный документ удалять нельзя (ТЗ п.109)",
              not allowed and links == 2, f"связей: {links}")

        links_pinned = db.query(DocumentArchiveLink).all()
        check("связь закрепляет версию файла (ТЗ п.91)",
              all(link.archive_version_id == v1.id for link in links_pinned))

    # --- Целостность хранилища ---
    check("нет нарушений внешних ключей", check_integrity() == [])
    check("нет осиротевших файлов архива", find_orphan_files(db) == [])

    # --- ИИ-агент: запрос, черновики, подтверждение (ТЗ п.101-106) ---
    from app.ai.connector import AIConnector
    from app.core.services import ai_service

    # Итоговый акт без связи — типичная ситуация: система предлагает
    # связать его с актом скрытых работ, но только после подтверждения.
    document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_AOU_SITO, number="1"
    )
    db.commit()

    ai = AIConnector(mode="LOCAL")
    result = ai_service.analyze(
        db, project.id, "Проверь комплект АОСР №1", ai
    )
    analysis = result["answer"]
    print(f"\n[+] AI Connector ({ai.mode_label}) статус: {analysis['status']}")
    print(f"    Контекст запроса: {result['context']['request']}")
    check("ИИ работает по контексту проекта (ТЗ п.103)",
          set(result["context"]) == set(ai_context.CONTEXT_KEYS))
    check("пути файлов в контекст не попали (ТЗ п.103)",
          str(app_config.ARCHIVE_DIR) not in repr(result["context"]))
    drafts = result["proposals"]
    check("предложения сохранены черновиками (ТЗ п.105)",
          bool(drafts) and all(
              item.status == domain.AI_PROPOSAL_DRAFT for item in drafts
          ))
    for proposal in drafts:
        print(f"    Черновик ИИ: [{proposal.code}] {proposal.text}")
        if proposal.basis:
            print(f"        основание: {normative.format_basis(proposal.basis)}")

    links_before = db.query(DocumentLink).count()
    check("анализ ничего не изменил (ТЗ п.104)",
          db.query(DocumentLink).count() == links_before)

    applicable = [item for item in drafts if item.action]
    if applicable:
        proposal = applicable[0]
        ai_service.accept(db, proposal.id)
        check("подтверждённое предложение применено через сервис (ТЗ п.104)",
              proposal.status == domain.AI_PROPOSAL_ACCEPTED)
        try:
            ai_service.accept(db, proposal.id)
            check("повторное применение запрещено (ТЗ п.104)", False)
        except ai_service.AiError:
            check("повторное применение запрещено (ТЗ п.104)", True)
    else:
        check("предложений к применению нет: все наблюдения", True)

    remaining = [
        item for item in ai_service.list_proposals(db, project.id)
        if item.status == domain.AI_PROPOSAL_DRAFT
    ]
    if remaining:
        ai_service.reject(db, remaining[0].id, "требует проверки инженером")
        check("отклонённое предложение не применено (ТЗ п.104)",
              db.query(DocumentLink).count() >= links_before)


    # --- Выгрузка комплекта ---
    # Демонстрационные документы не выпущены: комплект выгружается с
    # пометкой об ошибках (ТЗ п.83), как это сделал бы оператор.
    folder = export_package(
        db, project.id, PACKAGES_DIR, allow_errors=True
    )
    print(f"\n[+] Комплект выгружен в: {folder}")
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            print(f"    - {f.relative_to(folder)}")
    check(
        "выгрузка создала реестр (ТЗ п.76)",
        (folder / "Реестр_выгрузки.pdf").is_file(),
    )
    check(
        "проблемный комплект помечен файлом ошибок (ТЗ п.83)",
        (folder / "Ошибки выгрузки.txt").is_file(),
    )
    check(
        "прежняя выгрузка не изменена (ТЗ п.71)",
        export_package(db, project.id, PACKAGES_DIR, allow_errors=True).name
        == "Комплект 02",
    )

    # --- Материал и акт испытаний (ТЗ п.44, 45, 49) ---
    from app.core.services import link_service

    material_type = db.query(MaterialType).filter(MaterialType.code == "BETON").first()
    if material_type is None:
        material_type = MaterialType(code="BETON", name="Бетон и растворы")
        db.add(material_type)
        db.commit()
    material = link_service.save_material(
        db, project.id, name="Бетон Б25", material_type_id=material_type.id,
        unit="м³", quantity=120.0,
    )
    test_act = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="1",
        doc_date=_date(2024, 5, 20),
    )
    other_test_act = document_service.create_document(
        db, project.id, doc_type=domain.DOC_TYPE_TEST_ACT, number="2",
        doc_date=_date(2024, 6, 3),
    )
    link_service.link_material_to_test_act(db, material.id, test_act.id)
    check("материал отнесён к конкретному акту испытаний (ТЗ п.44)",
          [act.number for act in
           link_service.list_test_acts_of_material(db, material.id)] == ["1"])
    check("к акту без явной связи материал не прикрепляется (ТЗ п.45)",
          link_service.list_materials_of_act(db, other_test_act.id) == [])
    try:
        link_service.link_material_to_test_act(db, material.id, other_test_act.id)
        link_service.link_material_to_test_act(db, material.id, other_test_act.id)
        check("повторная связь материала отклонена (ТЗ п.45)", False, "связи нет")
    except link_service.MaterialError as exc:
        check("повторная связь материала отклонена (ТЗ п.45)", True, str(exc)[:60])
    try:
        link_service.link_material_to_test_act(db, material.id, aosr1.id)
        check("материал не связывается с актом не-испытаний (ТЗ п.36, 44)",
              False, "связь создана")
    except link_service.MaterialError as exc:
        check("материал не связывается с актом не-испытаний (ТЗ п.36, 44)",
              True, str(exc)[:60])

    # --- История проекта сохраняет результаты работы (ТЗ п.86) ---
    history_types = [event.event_type for event in list_events(db, project.id)]
    for expected, label in (
        (domain.HISTORY_DOCUMENT_CREATED, "создание документов"),
        (domain.HISTORY_DOCUMENT_ISSUED, "выпуск версии документа"),
        (domain.HISTORY_LINK_ADDED, "связь акта с файлом архива"),
        (domain.HISTORY_PACKAGE_EXPORTED, "сформированный комплект"),
        (domain.HISTORY_REGISTER_WRITTEN, "реестр комплекта"),
        (domain.HISTORY_ARCHIVE_FILE_ADDED, "изменение архивных документов"),
        (domain.HISTORY_MATERIAL_LINK_ADDED, "связь материала с актом испытаний"),
    ):
        check(f"история сохраняет: {label} (ТЗ п.86)", expected in history_types)
    register_event = next(
        event for event in list_events(db, project.id)
        if event.event_type == domain.HISTORY_REGISTER_WRITTEN
    )
    check("в записи о реестре есть число строк (ТЗ п.86)",
          register_event.payload["rows"] >= 1,
          f"строк: {register_event.payload['rows']}")

    # --- Резервная копия, проверка, перенос на другой компьютер (ТЗ п.74, 98) ---
    backup_root = BACKUP_DIR
    backup = backup_service.create_backup(db)
    check("резервная копия создана отдельной папкой (ТЗ п.74, 98)",
          backup.name.startswith(backup_service.BACKUP_PREFIX)
          and backup.parent == backup_root,
          backup.name)
    manifest = backup_service.read_manifest(backup)
    counts = manifest["counts"]
    check("в копии связи, версии и история (ТЗ п.98)",
          counts["archive_links"] >= 1 and counts["versions"] >= 1
          and counts["events"] >= 1,
          f"связей: {counts['archive_links']}, версий: {counts['versions']}, "
          f"событий: {counts['events']}")
    check("пользовательские комплекты в копию не попали (ТЗ п.72, 74)",
          manifest["packages_included"] is False
          and not list(backup.rglob("packages")))
    check("свежая копия проходит проверку (ТЗ п.98)",
          backup_service.verify_backup(backup) == [],
          backup_service.describe_backup(backup).splitlines()[0])

    # Перенос на другой компьютер: своя база, свой архив, свои настройки.
    other = Path(_TMP_DIR.name) / "other"
    other_archive = other / "internal_archive"
    other_archive.mkdir(parents=True)
    backup_service.restore_backup(
        backup, db_path=other / "app.db", archive_dir=other_archive,
        settings_path=other / "settings.json", safety_dir=backup_root,
    )
    check("восстановленная база целостна (ТЗ п.54, 98)",
          integrity_problems(other / "app.db") == [])
    other_db = session_at(other / "app.db")
    try:
        check("проект вернулся на другой компьютер (ТЗ п.98)",
              other_db.query(Project).count() == db.query(Project).count() == 1)
        check("документы и связи вернулись (ТЗ п.98)",
              other_db.query(Document).count() == db.query(Document).count()
              and other_db.query(DocumentArchiveLink).count() >= 1)
        moved = other_db.query(ArchiveFileVersion).all()
        check("пути файлов переписаны на новый каталог архива (ТЗ п.49, 98)",
              bool(moved)
              and all(other_archive.resolve() in Path(v.stored_path).resolve().parents
                      and Path(v.stored_path).is_file() for v in moved),
              moved[0].stored_path if moved else "нет файлов")
    finally:
        other_db.close()

    # Текущие данные целевой машины откладываются перед восстановлением.
    safety = backup_service.restore_backup(
        backup, db_path=other / "app.db", archive_dir=other_archive,
        settings_path=other / "settings.json", safety_dir=backup_root,
    )
    check("прежние данные отложены перед восстановлением (ТЗ п.98)",
          (safety / "app.db").is_file(), safety.name)

    tampered = next(path for path in (backup / "files").rglob("*") if path.is_file())
    tampered.write_bytes(b"%PDF-1.4 changed")
    check("изменённый файл архива обнаруживается проверкой (ТЗ п.98)",
          any("изменён" in problem
              for problem in backup_service.verify_backup(backup)))

    # --- Удаление проекта не должно унести архив (ТЗ п.54, 109) ---
    allowed, stats = can_delete_project(db, project.id)
    check("проект с архивом удалить нельзя (ТЗ п.109)", not allowed,
          f"архивных документов: {stats['archive_documents']}")
    try:
        delete_project(db, project.id)
        check("удаление отклонено с понятной ошибкой", False, "ошибки не было")
    except ProjectError as e:
        check("удаление отклонено с понятной ошибкой", True, str(e)[:60])
    check("архивный документ уцелел после попытки удаления",
          db.query(ArchiveDocument).count() == 1)

    # После очистки архива проект удаляется, а его документы — каскадом.
    for link in db.query(DocumentArchiveLink).all():
        db.delete(link)
    db.commit()
    delete_archive_document(db, archive_doc.id)
    check("архивный документ удалён после снятия связей",
          db.query(ArchiveDocument).count() == 0)

    # Выпуск итогового акта — исторический результат (ТЗ п.54, 85): ради
    # чистого финала демонстрации он снимается явно, вместе со связями актов.
    for link in db.query(DocumentLink).all():
        db.delete(link)
    # Реестр комплекта ссылается на выпущенную версию: сначала комплект.
    db.query(PackageEntry).delete()
    db.query(Package).delete()
    db.delete(version)
    db.commit()
    check("демонстрационные связи и выпуск сняты перед удалением проекта",
          db.query(DocumentLink).count() == 0
          and db.query(DocumentVersion).filter(
              DocumentVersion.issued_at.isnot(None)
          ).count() == 0)

    # Черновики — рабочие данные (ТЗ п.66) и удаляются вместе с проектом.
    delete_project(db, project.id)
    check("проект без архива удаляется", db.query(Project).count() == 0)
    check("документы проекта удалены каскадом",
          db.query(Document).count() == 0)

    # --- Интернет-ИИ: контракт и честная неготовность (ТЗ п.101) ---
    from app.ai.connector import MODE_INTERNET, build_internet_provider
    from app.ai.secrets import GIGACHAT_KEY, STORE

    provider = build_internet_provider()
    internet = AIConnector(mode=MODE_INTERNET, provider=provider)
    internet_result = internet.analyze({"request": "Проверь комплект", "documents": []})
    if provider is None:
        check("интернет-ИИ без ключа сообщает not_configured (ТЗ п.101)",
              internet_result["status"] == "not_configured"
              and internet_result["proposals"] == [])
        check("ключ GigaChat не задан", STORE.get(GIGACHAT_KEY) is None)
    else:
        check("интернет-ИИ настроен на GigaChat", provider.configured())

    db.close()

    print("\n=== ЧТО ОСТАЁТСЯ ЗА ПРЕДЕЛАМИ ПРИЛОЖЕНИЯ ===")
    print("  - фактический вызов GigaChat: нужен ключ авторизации, выданный "
          "Сбером; запросы выполняются только из интернет-режима")

    if FAILURES:
        print(f"\n=== ПРОВЕРКА НЕ ПРОЙДЕНА: {len(FAILURES)} ===")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\n=== ПРОВЕРКА ЗАВЕРШЕНА УСПЕШНО ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _TMP_DIR.cleanup()
