"""Комплекты: состав, реестр, файлы выгрузки. ТЗ п.69–83, 72, 76–81.

Комплект — отдельная папка выгрузки, а не раздел рабочей базы (ТЗ п.72).
Каждая выгрузка создаётся заново и не трогает предыдущие (ТЗ п.71), а её
состав закрепляется в строках реестра вместе с версией документа: прежняя
выгрузка продолжает ссылаться на то состояние, в котором она была сделана
(ТЗ п.91), даже после новых редакций.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services import form_service, printing
from app.db.models import (
    ArchiveDocument,
    ArchiveFileVersion,
    Document,
    DocumentArchiveLink,
    Package,
    PackageEntry,
    Project,
)

# --- ТЗ п.70: корневая папка комплектов и её содержимое -------------------
PACKAGE_ROOT_NAME = "Комплекты"
PACKAGE_FOLDER_PREFIX = "Комплект"
DOCUMENTS_DIR = "Документы"
SCHEMES_DIR = "Схемы"
ATTACHMENTS_DIR = "Приложения"
REGISTRY_FILE_NAME = "Реестр_выгрузки.pdf"
COMBINED_PDF_NAME = "Все документы комплекта.pdf"
# Псевдотип строки реестра приложений (ТЗ п.79).
REGISTER_OF_ATTACHMENTS = "Реестр приложений"


class PackageError(ValueError):
    """Ошибка формирования комплекта с текстом для оператора.

    Наследует ValueError: состав выгрузки и место хранения задаются
    вызывающим, и вызывающий ждёт именно ошибку значения, а не системный
    сбой (ТЗ п.69, 73).
    """


@dataclass
class PlannedEntry:
    """Строка будущего реестра выгрузки (ТЗ п.76–78)."""

    row_no: int
    doc_type: str
    document_number: str
    document: Document | None = None
    archive: ArchiveDocument | None = None
    is_attachment: bool = False
    # Строка акта, к которому относится приложение (ТЗ п.80).
    parent: "PlannedEntry | None" = None
    note: str = ""
    # Файл уже включён в выгрузку по другой строке (ТЗ п.92).
    duplicated_in: str = ""


@dataclass
class FileItem:
    """Файл, копируемый в папку выгрузки, вместе с версией (ТЗ п.91)."""

    archive: ArchiveDocument
    version: ArchiveFileVersion | None
    folder: str


@dataclass
class PackagePlan:
    """План выгрузки: строки реестра, документы и копируемые файлы."""

    variant: str
    entries: list[PlannedEntry] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    files: list[FileItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Папка выгрузки (ТЗ п.70, 71, 73)
# ---------------------------------------------------------------------------


def resolve_root(base_dir: Path | str, root_name: str = PACKAGE_ROOT_NAME) -> Path:
    """Корневая папка комплектов внутри выбранной оператором папки (ТЗ п.70).

    Оператор задаёт и родительскую папку, и название корневой, поэтому
    «D:\\Работа» + «Комплекты» даёт `D:\\Работа\\Комплекты`, а комплекты
    внутри — «Комплект 01»… Рабочее хранилище при этом не затрагивается
    (ТЗ п.72).
    """
    name = (root_name or "").strip()
    if not name:
        raise PackageError("Название корневой папки комплектов обязательно (ТЗ п.70).")
    if Path(name).name != name or name in {".", ".."}:
        raise PackageError(
            f"Название корневой папки «{name}» недопустимо: это не имя папки "
            "(ТЗ п.70)."
        )
    return Path(base_dir) / name


def ensure_root(root: Path | str) -> Path:
    """Создать корневую папку комплектов, если её нет (ТЗ п.70)."""
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise PackageError(
            f"На месте папки комплектов «{root}» находится файл. Выберите или "
            "создайте место хранения заново (ТЗ п.70, 73)."
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def check_root_available(root: Path | str) -> str | None:
    """Причина недоступности папки комплектов или None (ТЗ п.73).

    Проверяется именно запись: папка может остаться на месте после
    подключения сетевого диска, и недоступность обнаружилась бы только в
    момент записи файлов.
    """
    root = Path(root)
    if not root.exists():
        return "папка комплектов не создана"
    if not root.is_dir():
        return "на месте папки комплектов находится файл"
    if not os.access(root, os.W_OK | os.X_OK):
        return "нет прав на запись в папку комплектов"
    return None


def next_folder_name(root: Path | str, prefix: str = PACKAGE_FOLDER_PREFIX) -> str:
    """Имя следующей папки выгрузки: «Комплект 01», «Комплект 02» (ТЗ п.70).

    Нумерация ищется по существующим папкам, а не по записям в базе: папки
    могли быть удалены или скопированы с другого компьютера (ТЗ п.98), и
    повторное имя перезаписало бы прежнюю выгрузку (ТЗ п.71).
    """
    root = Path(root)
    taken = {item.name for item in root.iterdir() if item.is_dir()} if root.is_dir() else set()
    for number in range(1, 10000):
        name = f"{prefix} {number:02d}"
        if name not in taken:
            return name
    raise PackageError(  # pragma: no cover — 9999 папок одного комплекта
        "Не удалось подобрать имя папки выгрузки: слишком много комплектов."
    )


# ---------------------------------------------------------------------------
# Состав комплекта (ТЗ п.69, 75, 76, 79, 80, 92)
# ---------------------------------------------------------------------------


def project_documents(db: Session, project_id: int) -> list[Document]:
    """Документы проекта в порядке видов документов и номеров."""
    documents = list(
        db.scalars(select(Document).where(Document.project_id == project_id)).all()
    )
    order = {doc_type: index for index, doc_type in enumerate(domain.NUMBERED_DOC_TYPES)}
    return sorted(
        documents,
        key=lambda d: (order.get(d.doc_type, len(order)), _number_key(d.number)),
    )


def selected_documents(
    db: Session, project_id: int, document_ids: list[int] | None = None
) -> list[Document]:
    """Документы, выбранные оператором для комплекта (ТЗ п.69).

    Без явного списка берутся все документы проекта: выбор делает оператор,
    а молчаливое ограничение выгрузки только выпущенными документами
    привело бы к тому, что часть выбранного молча исчезла бы из комплекта.
    """
    documents = project_documents(db, project_id)
    if document_ids is None:
        return documents
    wanted = set(document_ids)
    known = {d.id for d in documents}
    missing = wanted - known
    if missing:
        raise PackageError(
            "Выбраны документы, которых нет в проекте: "
            f"{', '.join(str(item) for item in sorted(missing))}."
        )
    return [d for d in documents if d.id in wanted]


def _number_key(number: str | None) -> tuple[int, int, str]:
    number = (number or "").strip()
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def _links_with_roles(document: Document, roles: tuple[str, ...]) -> list[DocumentArchiveLink]:
    return sorted(
        (link for link in document.archive_links if link.link_role in roles),
        key=lambda link: (link.order_no, link.id),
    )


def _files_of(document: Document, role: str) -> list[FileItem]:
    """Файлы документа по роли связи, с версией файла (ТЗ п.91)."""
    items: list[FileItem] = []
    for link in _links_with_roles(document, (role,)):
        archive = link.archive_document
        if archive is None:
            continue
        version = link.archive_version or archive.current_version
        items.append(FileItem(archive=archive, version=version, folder=""))
    return items


def _append_files(
    plan: PackagePlan,
    items: list[FileItem],
    *,
    row_no: int,
    is_attachment: bool,
    parent: PlannedEntry | None,
    folder: str,
    copied: dict[int, str],
    row_kind: str,
    keep_repeated: bool = False,
) -> int:
    """Добавить перечень файлов в план (ТЗ п.28, 76, 79, 80, 92).

    Файл попадает в выгрузку один раз, сколько бы актов на него ни
    ссылалось (ТЗ п.92): вторая строка лишь фиксирует принадлежность
    (ТЗ п.80). При количестве сверх порога перед перечнем появляется
    строка реестра приложений (ТЗ п.28, 79).
    """
    unique: list[FileItem] = []
    repeated: list[tuple[FileItem, str]] = []
    for item in items:
        if item.archive.id in copied:
            repeated.append((item, copied[item.archive.id]))
            continue
        copied[item.archive.id] = folder
        item.folder = folder
        unique.append(item)
        plan.files.append(item)

    threshold = (
        domain.ATTACHMENT_REGISTER_THRESHOLD if row_kind == "attachment"
        else domain.REGISTER_THRESHOLD
    )
    if len(unique) >= threshold:
        plan.entries.append(PlannedEntry(
            row_no=row_no,
            doc_type=REGISTER_OF_ATTACHMENTS,
            document_number=str(len(unique) + len(repeated)),
            is_attachment=is_attachment,
            parent=parent,
            note=f"Реестр приложений: {len(unique) + len(repeated)} (ТЗ п.79)",
        ))
        row_no += 1

    for item in unique:
        plan.entries.append(PlannedEntry(
            row_no=row_no,
            doc_type=item.archive.category,
            document_number=item.archive.number or item.archive.original_name,
            archive=item.archive,
            is_attachment=is_attachment,
            parent=parent,
        ))
        row_no += 1

    for item, where in repeated:
        if not keep_repeated:
            # Вариант 1: файл один раз в выгрузке (ТЗ п.76, 92), а строка
            # реестра не должна повторять уже включённый документ.
            continue
        plan.entries.append(PlannedEntry(
            row_no=row_no,
            doc_type=item.archive.category,
            document_number=item.archive.number or item.archive.original_name,
            archive=item.archive,
            is_attachment=is_attachment,
            parent=parent,
            note="файл уже включён в выгрузку",
            duplicated_in=where,
        ))
        row_no += 1
    return row_no


def build_plan(
    db: Session,
    project_id: int,
    documents: list[Document],
    variant: str = domain.EXPORT_VARIANT_ALL,
) -> PackagePlan:
    """Составить план выгрузки: строки реестра и раскладка по папкам.

    Вариант 1 (ТЗ п.75): все акты, затем все исполнительные схемы, затем
    приложения. Вариант 2: каждый акт со своими схемами и приложениями,
    принадлежность приложения сохраняется (ТЗ п.80).
    """
    if variant not in domain.EXPORT_VARIANTS:
        raise PackageError(
            f"Неизвестный вариант выгрузки: {variant}. Допустимы: "
            f"{', '.join(domain.EXPORT_VARIANTS)} (ТЗ п.75)."
        )
    plan = PackagePlan(variant=variant)
    row_no = 1
    copied: dict[int, str] = {}

    def add_document(document: Document) -> PlannedEntry:
        nonlocal row_no
        entry = PlannedEntry(
            row_no=row_no, document=document, doc_type=document.doc_type,
            document_number=document.number or "",
        )
        plan.entries.append(entry)
        plan.documents.append(document)
        row_no += 1
        return entry

    if variant == domain.EXPORT_VARIANT_BY_ACT:
        acts = [d for d in documents if d.doc_type == domain.DOC_TYPE_AOSR]
        others = [d for d in documents if d.doc_type != domain.DOC_TYPE_AOSR]
        for act in acts:
            entry = add_document(act)
            folder = f"АОСР № {act.number}"
            row_no = _append_files(
                plan, _files_of(act, domain.LINK_ROLE_SCHEME), row_no=row_no,
                is_attachment=False, parent=entry,
                folder=f"{folder}/{SCHEMES_DIR}", copied=copied, row_kind="scheme",
            )
            row_no = _append_files(
                plan, _files_of(act, domain.LINK_ROLE_ATTACHMENT), row_no=row_no,
                is_attachment=True, parent=entry,
                folder=f"{folder}/{ATTACHMENTS_DIR}", copied=copied,
                row_kind="attachment", keep_repeated=True,
            )
        for document in others:
            # Второй вариант группирует по актам, но не теряет остальные
            # документы: они идут после актов (ТЗ п.75, 76).
            add_document(document)
        return plan

    for document in documents:
        add_document(document)

    schemes: list[FileItem] = []
    attachments: list[FileItem] = []
    for document in documents:
        schemes.extend(_files_of(document, domain.LINK_ROLE_SCHEME))
        attachments.extend(_files_of(document, domain.LINK_ROLE_ATTACHMENT))
    row_no = _append_files(
        plan, schemes, row_no=row_no, is_attachment=False, parent=None,
        folder=SCHEMES_DIR, copied=copied, row_kind="scheme",
    )
    _append_files(
        plan, attachments, row_no=row_no, is_attachment=True, parent=None,
        folder=ATTACHMENTS_DIR, copied=copied, row_kind="attachment",
    )
    return plan


# ---------------------------------------------------------------------------
# Файлы выгрузки
# ---------------------------------------------------------------------------


def _safe_name(text: str) -> str:
    """Имя файла без запрещённых символов (ТЗ п.98 — перенос на другой ПК)."""
    cleaned = "".join(
        char if char.isalnum() or char in " ._-№()" else "_" for char in str(text)
    ).strip().strip(".")
    return cleaned or "документ"


def _document_file_name(document: Document) -> str:
    label = domain.DOC_TYPE_LABELS.get(document.doc_type, document.doc_type)
    return _safe_name(f"{label} № {document.number or 'б_номера'}.pdf")


def _copy_file(item: FileItem, folder: Path, problems: list[str]) -> bool:
    """Скопировать файл в папку выгрузки.

    Отсутствующий или нечитаемый файл не пропускается молча: строка реестра
    остаётся, а проблема попадает в отчёт комплекта (ТЗ п.82, 83).
    """
    version = item.version
    source = Path(version.stored_path) if version is not None else None
    if source is None or not source.exists():
        problems.append(
            f"Файл «{item.archive.original_name}» не найден в архиве "
            f"({domain.LINK_ROLE_ATTACHMENT if item.folder.endswith(ATTACHMENTS_DIR) else domain.LINK_ROLE_SCHEME}) "
            "(ТЗ п.82)."
        )
        return False
    target = folder / item.folder / _safe_name(item.archive.original_name)
    if target.exists():
        target = folder / item.folder / (
            f"{target.stem} ({item.archive.number or item.archive.category}){target.suffix}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def _write_registry(plan: PackagePlan, target: Path, project: Project) -> Path:
    """Реестр выгрузки. Формируется заново для каждой выгрузки (ТЗ п.76, 78).

    Номер строки и номер документа — разные идентификаторы (ТЗ п.77):
    документ «АОСР № 15» может стоять в строке 27.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer

    styles = printing.styles()
    story = [
        Paragraph("РЕЕСТР ВЫГРУЗКИ", styles["title"]),
        Paragraph(printing.escape(project.title or ""), styles["center"]),
        Paragraph(
            f"{target.parent.name}: {domain.EXPORT_VARIANT_LABELS[plan.variant]} "
            f"(ТЗ п.68, 75)",
            styles["center"],
        ),
        Spacer(1, 6),
    ]
    for entry in plan.entries:
        story.append(Paragraph(
            f"{entry.row_no}. {_entry_title(entry)}", styles["fill"]
        ))

    target.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(target), pagesize=A4,
        leftMargin=printing.MARGIN_LEFT_MM * mm,
        rightMargin=printing.MARGIN_RIGHT_MM * mm,
        topMargin=printing.MARGIN_TOP_MM * mm,
        bottomMargin=printing.MARGIN_BOTTOM_MM * mm,
        title="Реестр выгрузки",
    )
    frame = Frame(
        printing.MARGIN_LEFT_MM * mm, printing.MARGIN_BOTTOM_MM * mm,
        A4[0] - (printing.MARGIN_LEFT_MM + printing.MARGIN_RIGHT_MM) * mm,
        A4[1] - (printing.MARGIN_TOP_MM + printing.MARGIN_BOTTOM_MM) * mm,
        id="registry",
    )
    doc.addPageTemplates([PageTemplate(id="registry", frames=[frame])])
    doc.build(story)
    return target


def _entry_title(entry: PlannedEntry) -> str:
    if entry.document is not None:
        label = domain.DOC_TYPE_LABELS.get(entry.doc_type, entry.doc_type)
        state = (
            "выпущен" if entry.document.status == domain.DOC_STATUS_ISSUED
            else "рабочая редакция"
        )
        text = f"{label} № {entry.document_number} ({state})"
    elif entry.doc_type == REGISTER_OF_ATTACHMENTS:
        text = f"{REGISTER_OF_ATTACHMENTS}: {entry.note}"
    else:
        text = f"{entry.doc_type} {entry.document_number}"
    if entry.duplicated_in:
        text += f" — копия: {entry.duplicated_in}"
    elif entry.parent is not None and entry.archive is not None:
        text += f" — к {entry.parent.document_number}"
    return text


# ---------------------------------------------------------------------------
# Формирование комплекта
# ---------------------------------------------------------------------------


def create_package(
    db: Session,
    project_id: int,
    *,
    base_dir: Path | str,
    root_name: str = PACKAGE_ROOT_NAME,
    document_ids: list[int] | None = None,
    variant: str = domain.EXPORT_VARIANT_ALL,
    page_numbering: bool = False,
    allow_errors: bool = False,
) -> Package:
    """Сформировать комплект: отдельная папка, реестр, документы (ТЗ п.69–83).

    Прежние выгрузки не изменяются (ТЗ п.71): новая папка создаётся рядом,
    а её имя подбирается так, чтобы не совпасть с существующей. Недоступная
    папка комплектов приводит к предложению выбрать место заново, рабочая
    база при этом не страдает (ТЗ п.73).
    """
    from app.core.services import export_checks

    project = db.get(Project, project_id)
    if project is None:
        raise PackageError(f"Проект не найден: {project_id}")

    if document_ids is not None and not document_ids:
        raise PackageError(
            "Не выбрано ни одного документа. Отметьте документы в комплекте "
            "(ТЗ п.69)."
        )
    documents = selected_documents(db, project_id, document_ids)
    if not documents:
        raise PackageError(
            "В проекте нет документов для комплекта. Создайте документ "
            "(ТЗ п.69)."
        )

    checks = export_checks.check_package(
        db, project_id, [d.id for d in documents] if document_ids is not None else None
    )
    if checks.has_errors and not allow_errors:
        raise PackageError(
            "Комплект не прошёл проверку (ТЗ п.82):\n"
            + "\n".join(f"• {problem}" for problem in checks.problems)
            + "\n\nИсправьте проблемы или выгрузите с пометкой об ошибках "
            "(ТЗ п.83)."
        )

    root = resolve_root(base_dir, root_name)
    if root.exists() and not root.is_dir():
        raise PackageError(
            f"На месте папки комплектов «{root}» находится файл. Выберите или "
            "создайте место хранения заново (ТЗ п.70, 73)."
        )
    if root.exists() and not os.access(root, os.W_OK | os.X_OK):
        raise PackageError(
            f"Папка комплектов «{root}» недоступна для записи. Выберите или "
            "создайте место хранения заново (ТЗ п.73)."
        )
    ensure_root(root)
    name = next_folder_name(root)
    folder = root / name

    plan = build_plan(db, project_id, documents, variant)

    # Содержимое собирается во временной папке рядом: прерванная выгрузка
    # не должна остаться в папке комплектов как будто она готова.
    staging = root / f".{name}.сборка"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    problems: list[str] = []
    try:
        for document in plan.documents:
            printing.render_document_pdf(
                db, document, staging / DOCUMENTS_DIR / _document_file_name(document)
            )
        if page_numbering and len(plan.documents) > 1:
            # Единая сквозная нумерация по всему комплекту (ТЗ п.81).
            printing.render_documents_pdf(
                db, plan.documents,
                staging / DOCUMENTS_DIR / COMBINED_PDF_NAME,
                page_numbers=True,
            )
        for item in plan.files:
            _copy_file(item, staging, problems)
        _write_registry(plan, staging / REGISTRY_FILE_NAME, project)
        if checks.has_errors:
            # Файл ошибок относится только к текущей выгрузке и появляется
            # при выборе «всё равно завершить» (ТЗ п.83). Не блокирующие
            # замечания остаются в диалоге оператора.
            (staging / export_checks.ERRORS_FILE_NAME).write_text(
                checks.report(), encoding="utf-8"
            )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # exist_ok=False: если папка появилась между проверкой и сбором,
    # содержимое прежней выгрузки не перезаписывается (ТЗ п.71).
    try:
        folder.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        shutil.rmtree(staging, ignore_errors=True)
        raise PackageError(
            f"Папка «{name}» уже существует: прежняя выгрузка не "
            "перезаписывается (ТЗ п.71)."
        ) from None
    for item in sorted(staging.iterdir()):
        shutil.move(str(item), str(folder / item.name))
    shutil.rmtree(staging, ignore_errors=True)

    package = Package(
        project_id=project_id,
        folder_name=name,
        absolute_path=str(folder),
        export_variant=variant,
        page_numbering=page_numbering,
        has_errors_file=checks.has_errors,
    )
    db.add(package)
    db.flush()

    rows: dict[int, int] = {}
    for entry in plan.entries:
        version = (
            form_service.actual_version(db, entry.document.id)
            if entry.document is not None else None
        )
        record = PackageEntry(
            package_id=package.id,
            document_id=entry.document.id if entry.document is not None else None,
            document_version_id=version.id if version is not None else None,
            register_row_no=entry.row_no,
            document_number=entry.document_number,
            doc_type=entry.doc_type,
            is_attachment=entry.is_attachment,
            parent_entry_id=rows.get(entry.parent.row_no) if entry.parent else None,
        )
        db.add(record)
        db.flush()
        rows[entry.row_no] = record.id

    from app.core.services.project_service import record_event

    record_event(
        db, project_id, domain.HISTORY_PACKAGE_EXPORTED,
        f"Сформирован комплект «{name}»: вариант "
        f"{domain.EXPORT_VARIANT_LABELS[plan.variant]}, документов "
        f"{len(plan.documents)}, строк реестра {len(plan.entries)} "
        f"(ТЗ п.69, 75, 86)",
        entity_type="package",
        entity_id=package.id,
        payload={
            "folder_name": name,
            "absolute_path": str(folder),
            "variant": variant,
            "page_numbering": page_numbering,
            "entries": len(plan.entries),
            "documents": len(plan.documents),
            "files": len(plan.files),
            "has_errors": checks.has_errors,
        },
    )
    db.commit()
    if problems:
        # Файлы, которых нет в архиве, уже записаны в отчёт проверки при
        # формировании реестра; здесь оператор узнаёт о них сразу.
        raise PackageError(
            f"Комплект «{name}» создан, но часть файлов не выгружена:\n"
            + "\n".join(f"• {problem}" for problem in problems)
        )
    return package


# ---------------------------------------------------------------------------
# Просмотр комплектов (ТЗ п.16, 70, 73)
# ---------------------------------------------------------------------------


def list_packages(db: Session, project_id: int) -> list[Package]:
    """Комплекты проекта: новые сверху (ТЗ п.16, 70)."""
    return list(
        db.scalars(
            select(Package).where(Package.project_id == project_id)
            .order_by(Package.created_at.desc(), Package.id.desc())
        ).all()
    )


def package_entries(db: Session, package_id: int) -> list[PackageEntry]:
    """Строки реестра комплекта по номерам строк (ТЗ п.78)."""
    return list(
        db.scalars(
            select(PackageEntry).where(PackageEntry.package_id == package_id)
            .order_by(PackageEntry.register_row_no)
        ).all()
    )


def package_exists_on_disk(package: Package) -> bool:
    """На месте ли папка выгрузки (ТЗ п.73)."""
    return Path(package.absolute_path).is_dir()


def missing_package_paths(db: Session, project_id: int) -> list[Package]:
    """Комплекты, папка которых удалена или перемещена (ТЗ п.73)."""
    return [p for p in list_packages(db, project_id) if not package_exists_on_disk(p)]


def package_count(db: Session, project_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Package).where(Package.project_id == project_id)
    ) or 0
