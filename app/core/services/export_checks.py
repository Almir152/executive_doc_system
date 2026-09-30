"""Проверки комплекта перед выгрузкой. ТЗ п.82, 83.

Проверки выполняются до выгрузки, а не после: оператор должен увидеть
неполноту комплекта до того, как файлы появятся в пользовательской папке
(ТЗ п.82). Проверка ничего не исправляет и не меняет данные.

Перечень проверок соответствует ТЗ п.82: наличие обязательных документов,
связи, файлы, версии, приложения, номера, даты, состав комплекта.

ТЗ не перечисляет, какие именно виды документов обязательны в комплекте,
поэтому выдумывать такой список нельзя. Проверяется объективный факт —
комплект не пуст, а его документы выпущены; остальное оператор решает сам.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import domain
from app.core.services import document_service, form_service
from app.db.models import (
    ArchiveFileVersion, Document, DocumentArchiveLink, DocumentVersion,
    Project, ProjectSection,
)

# Файл ошибок выгрузки относится только к текущей выгрузке (ТЗ п.83).
ERRORS_FILE_NAME = "Ошибки выгрузки.txt"

# Категории проверок из ТЗ п.82.
CHECK_DOCUMENTS = "documents"
CHECK_LINKS = "links"
CHECK_FILES = "files"
CHECK_VERSIONS = "versions"
CHECK_ATTACHMENTS = "attachments"
CHECK_NUMBERS = "numbers"
CHECK_DATES = "dates"
CHECK_COMPOSITION = "composition"

# Роли связей, которые означают приложение к документу (ТЗ п.79, 80).
ATTACHMENT_ROLES = (domain.LINK_ROLE_ATTACHMENT,)

# Серьёзность замечания. Ошибка останавливает выгрузку: оператор должен
# исправить данные или явно выбрать выгрузку с пометкой (ТЗ п.83).
# Замечание показывается, но выгрузку не блокирует.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class CheckProblem:
    """Одна найденная проблема комплекта."""

    code: str
    subject: str
    message: str
    severity: str = SEVERITY_ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def __str__(self) -> str:
        return f"{self.subject}: {self.message}"


@dataclass(frozen=True)
class CheckResult:
    """Итог проверки комплекта (ТЗ п.82)."""

    problems: tuple[CheckProblem, ...]

    @property
    def has_errors(self) -> bool:
        """Есть ли ошибки, останавливающие выгрузку (ТЗ п.82)."""
        return any(problem.is_error for problem in self.problems)

    @property
    def has_problems(self) -> bool:
        """Есть ли замечания вовсе, включая не блокирующие (ТЗ п.82)."""
        return bool(self.problems)

    @property
    def warnings(self) -> tuple[CheckProblem, ...]:
        return tuple(p for p in self.problems if not p.is_error)

    def by_code(self, code: str) -> list[CheckProblem]:
        return [problem for problem in self.problems if problem.code == code]

    def report(self) -> str:
        """Текст отчёта для оператора (ТЗ п.82, 83)."""
        if not self.problems:
            return "Ошибок не обнаружено."
        lines = [f"Обнаружено замечаний: {len(self.problems)}", ""]
        lines.extend(
            f"- {'ошибка' if problem.is_error else 'внимание'}: "
            f"{problem.code}: {problem}"
            for problem in self.problems
        )
        return "\n".join(lines)


def _label(document: Document) -> str:
    number = (document.number or "").strip()
    return f"{document.doc_type}{' №' + number if number else ' (без номера)'}"


def check_package(
    db: Session,
    project_id: int,
    document_ids: list[int] | None = None,
) -> CheckResult:
    """Проверить комплект перед выгрузкой (ТЗ п.82).

    `document_ids` ограничивает проверку конкретной выгрузкой: во втором
    варианте комплекта проверяются только документы, попавшие в него
    (ТЗ п.75).
    """
    project = db.get(Project, project_id)
    if project is None:
        return CheckResult((CheckProblem(
            CHECK_DOCUMENTS, "Проект",
            f"Проект {project_id} не найден.",
        ),))

    documents = _package_documents(db, project_id, document_ids)
    problems: list[CheckProblem] = []
    problems += _check_documents(project, documents)
    problems += _check_versions(db, documents)
    problems += _check_numbers(db, project_id, documents)
    problems += _check_dates(documents)
    problems += _check_links(db, documents)
    problems += _check_files(db, documents)
    problems += _check_attachments(db, documents)
    problems += _check_sections(db, project_id, documents)
    return CheckResult(tuple(problems))


def _package_documents(
    db: Session, project_id: int, document_ids: list[int] | None
) -> list[Document]:
    """Документы, входящие в комплект (ТЗ п.75, 76)."""
    statement = select(Document).where(Document.project_id == project_id)
    if document_ids is not None:
        if not document_ids:
            return []
        statement = statement.where(Document.id.in_(document_ids))
    return list(db.scalars(statement.order_by(Document.doc_type, Document.id)))


def _check_documents(project: Project, documents: list[Document]) -> list[CheckProblem]:
    """Наличие обязательных документов и состав комплекта (ТЗ п.82)."""
    if not documents:
        return [CheckProblem(
            CHECK_DOCUMENTS, project.title,
            "В комплект не попал ни один документ: выгружать нечего.",
        )]
    return []


def _check_versions(db: Session, documents: list[Document]) -> list[CheckProblem]:
    """Выпущенные версии документов (ТЗ п.54, 82).

    Черновик хранится той же строкой `DocumentVersion`, но без `issued_at`:
    выпущенной считается версия с зафиксированным временем выпуска.
    """
    document_ids = [d.id for d in documents]
    released = set(
        db.scalars(
            select(DocumentVersion.document_id).where(
                DocumentVersion.document_id.in_(document_ids),
                DocumentVersion.issued_at.is_not(None),
            )
        )
    )
    problems = []
    for document in documents:
        if document.id not in released:
            problems.append(CheckProblem(
                CHECK_VERSIONS, _label(document),
                "У документа нет выпущенной версии: документ не выпущен "
                "(ТЗ п.54, 85).",
            ))
        elif document.status != domain.DOC_STATUS_ISSUED:
            # Документ отредактирован после выпуска: в комплект идёт
            # выпущенная версия (ТЗ п.91), поэтому это не ошибка.
            problems.append(CheckProblem(
                CHECK_VERSIONS, _label(document),
                "У документа новая невыпущенная редакция: в комплект попадёт "
                "выпущенная версия (ТЗ п.91).",
                SEVERITY_WARNING,
            ))
    return problems


def _check_numbers(
    db: Session, project_id: int, documents: list[Document]
) -> list[CheckProblem]:
    """Номера документов (ТЗ п.42, 77, 82)."""
    problems = []
    seen: dict[tuple[str, str], str] = {}
    for document in documents:
        number = (document.number or "").strip()
        if not number:
            problems.append(CheckProblem(
                CHECK_NUMBERS, _label(document),
                "Не задан номер документа.",
            ))
            continue
        key = (document.doc_type, number)
        if key in seen:
            problems.append(CheckProblem(
                CHECK_NUMBERS, _label(document),
                f"Номер {number} повторяется у документа «{seen[key]}».",
            ))
        else:
            seen[key] = _label(document)
    return problems


def _check_dates(documents: list[Document]) -> list[CheckProblem]:
    """Даты документов (ТЗ п.43, 87)."""
    problems = []
    for document in documents:
        if document.doc_date is None:
            problems.append(CheckProblem(
                CHECK_DATES, _label(document), "Не задана дата документа.",
            ))
            continue
        try:
            document_service.validate_document_date(document.doc_date)
        except document_service.DocumentNumberError as exc:
            problems.append(CheckProblem(
                CHECK_DATES, _label(document), str(exc),
            ))
    return problems


def _check_links(db: Session, documents: list[Document]) -> list[CheckProblem]:
    """Связи документов с архивными документами (ТЗ п.45, 52, 82)."""
    if not documents:
        return []
    links = list(db.scalars(
        select(DocumentArchiveLink).where(
            DocumentArchiveLink.document_id.in_([d.id for d in documents])
        )
    ))
    known_documents = {d.id for d in documents}
    problems = []
    for link in links:
        if link.document_id not in known_documents:
            continue
        if link.archive_document_id is None:
            problems.append(CheckProblem(
                CHECK_LINKS, _label(link.document),
                f"Связь «{link.link_role}» не указывает на архивный документ.",
            ))
            continue
        if link.archive_version_id is None:
            problems.append(CheckProblem(
                CHECK_LINKS, _label(link.document),
                f"Связь «{link.link_role}» не закрепляет версию файла "
                "(ТЗ п.91): при выгрузке файл может измениться.",
            ))
    return problems


def _check_files(db: Session, documents: list[Document]) -> list[CheckProblem]:
    """Файлы, на которые опираются связи комплекта (ТЗ п.82)."""
    if not documents:
        return []
    versions = list(db.scalars(
        select(ArchiveFileVersion).where(
            ArchiveFileVersion.id.in_(
                select(DocumentArchiveLink.archive_version_id).where(
                    DocumentArchiveLink.document_id.in_([d.id for d in documents])
                )
            )
        )
    ))
    problems = []
    for version in versions:
        if not version.stored_path or not Path(version.stored_path).is_file():
            problems.append(CheckProblem(
                CHECK_FILES,
                Path(version.stored_path).name if version.stored_path
                else f"Файл №{version.id}",
                "Файл не найден на диске: комплект будет неполным.",
            ))
    return problems


def _check_attachments(
    db: Session, documents: list[Document]
) -> list[CheckProblem]:
    """Приложения (ТЗ п.79, 80, 82)."""
    if not documents:
        return []
    links = list(db.scalars(
        select(DocumentArchiveLink).where(
            DocumentArchiveLink.document_id.in_([d.id for d in documents]),
            DocumentArchiveLink.link_role.in_(ATTACHMENT_ROLES),
        )
    ))
    if not links:
        # ТЗ п.79 задаёт правило отображения приложений, но не требует их
        # наличия у каждого документа: запрещать выгрузку акта без
        # приложений было бы неверно.
        return [CheckProblem(
            CHECK_ATTACHMENTS, "Комплект",
            "Ни у одного документа нет приложений (ТЗ п.79).",
            SEVERITY_WARNING,
        )]
    problems = []
    for link in links:
        if link.archive_version_id is None:
            problems.append(CheckProblem(
                CHECK_ATTACHMENTS, _label(link.document),
                "Приложение не закрепляет версию файла (ТЗ п.91).",
                SEVERITY_WARNING,
            ))
    return problems


def _check_sections(
    db: Session, project_id: int, documents: list[Document]
) -> list[CheckProblem]:
    """Разделы, на которые ссылаются документы комплекта (ТЗ п.21, 82)."""
    sections = {
        section.code: section
        for section in db.scalars(
            select(ProjectSection).where(ProjectSection.project_id == project_id)
        )
    }
    problems = []
    for document in documents:
        for code in _referenced_sections(db, document):
            if code not in sections:
                problems.append(CheckProblem(
                    CHECK_COMPOSITION, _label(document),
                    f"Раздел {code} не найден в проекте (ТЗ п.21).",
                ))
    return problems


def _referenced_sections(db: Session, document: Document) -> list[str]:
    """Коды разделов, на которые ссылается форма документа (ТЗ п.21, 93)."""
    payload = form_service.actual_payload(db, document.id)
    raw = payload.get("section_refs")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]
