"""Сквозная проверка работающей части системы.

Проверяет то, что действительно реализовано на текущем этапе:
справочники -> проект -> документы -> архив без дублирования -> версии ->
связи с ролями -> AI Connector -> выгрузка реестра -> миграция схемы.

Проверка идёт в отдельном временном каталоге: рабочее хранилище оператора
не затрагивается. Своё значение EXECUTIVE_DOC_DATA_DIR скрипт уважает.

Не реализовано и потому не проверяется (см. этапы 2-7):
построение нормативных форм, полноценная печать PDF, комплекты по ТЗ,
BACKUP и пользовательский интерфейс справочников.
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

from app.config import DATA_DIR, PACKAGES_DIR, ensure_dirs  # noqa: E402
from app.core import domain  # noqa: E402
from app.core.services.exporter import export_package  # noqa: E402
from app.core.services.project_service import (  # noqa: E402
    ProjectError, can_delete_project, delete_project,
)
from app.core.services.storage_service import (  # noqa: E402
    add_file_to_archive, add_version, can_delete_archive_document, delete_archive_document,
    find_orphan_files,
)
from app.db.database import SessionLocal, check_integrity, get_schema_version, init_db  # noqa: E402
from app.db.migrations import SCHEMA_VERSION  # noqa: E402
from app.db.models import (  # noqa: E402
    ArchiveDocument, Direction, Document, DocumentArchiveLink, DocumentLink,
    DocumentVersion, Package, PackageEntry, Project,
)

FAILURES: list[str] = []


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
    aosr1 = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="1")
    aosr2 = Document(project_id=project.id, doc_type=domain.DOC_TYPE_AOSR, number="2")
    db.add_all([aosr1, aosr2])
    db.commit()
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
    aook = Document(
        project_id=project.id, doc_type=domain.DOC_TYPE_AOOK, number="1",
        doc_date=_date(2024, 5, 15),
    )
    db.add(aook)
    db.commit()

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

    # --- AI Connector ---
    from app.ai.connector import AIConnector

    ai = AIConnector(mode="LOCAL")
    analysis = ai.analyze_package([aosr1.id, aosr2.id])
    print(f"\n[+] AI Connector ({ai.mode_label}) статус: {analysis['status']}")
    check("AI Connector отвечает", "proposals" in analysis)
    for proposal in analysis["proposals"]:
        print(f"    Предложение ИИ: {proposal}")

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

    db.close()

    print("\n=== НЕ РЕАЛИЗОВАНО ===")
    print("  - история по комплектам, реестрам и историческим PDF (п.86)")
    print("  - связь акта испытаний со строкой материала (п.44-48)")
    print("  - BACKUP, восстановление и перенос (п.74, 98)")
    print("  - обновление через миграции (п.97)")
    print("  - ИИ: контекст по запросу, черновики и подтверждение (п.101-106)")

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
