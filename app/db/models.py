"""Модели данных по разделам ТЗ.

Ключевые решения:

* Архив разделён на логический документ (ArchiveDocument) и физические версии
  файла (ArchiveFileVersion) — ТЗ п.90, 91. Одна физическая копия на версию,
  логических связей сколько угодно (п.47, 49, 84, 92).
* Связь документа с архивным документом закрепляет конкретную версию файла
  (archive_version_id), поэтому историческая выгрузка продолжает ссылаться на
  то состояние, которое было использовано при её формировании (п.91, 93).
* Нормативные формы — данные, а не код: NormativeForm версионируется, документ
  ссылается на ту форму, по которой был сформирован (п.96, п.62).
* Подтип документа получает отдельную таблицу с отдельными полями (п.95).
"""

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, JSON,
    String, Table, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.config import utcnow
from app.db.database import Base
from app.core import domain


# =====================================================================
# СПРАВОЧНИКИ (ТЗ п.14, 18, 19, 20, 22, 44)
# =====================================================================

class Direction(Base):
    """Направление работ. ТЗ п.14."""

    __tablename__ = 'directions'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<Direction {self.name}>"


class Organization(Base):
    """Единый справочник организаций. ТЗ п.18.

    Отдельного обязательного поля «полное наименование организации» здесь нет —
    ТЗ его прямо не предусматривает. Используется краткое наименование.
    """

    __tablename__ = 'organizations'

    id = Column(Integer, primary_key=True)
    short_name = Column(String, nullable=False)
    ogrn = Column(String)
    inn = Column(String)
    address = Column(String)
    phone = Column(String)
    fax = Column(String)
    sro = Column(String)
    nopriz = Column(String)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('inn', 'ogrn', name='uq_organization_inn_ogrn'),
    )

    representatives = relationship(
        "Representative", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Organization {self.short_name}>"


class Representative(Base):
    """Единый справочник представителей. ТЗ п.19.

    Повторный ввод одинаковых реквизитов не требуется: представитель
    принадлежит организации и создаётся один раз.
    """

    __tablename__ = 'representatives'

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey('organizations.id', ondelete="CASCADE"), nullable=False
    )
    position = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    organization = relationship("Organization", back_populates="representatives")

    @property
    def display_name(self) -> str:
        org = self.organization.short_name if self.organization else ""
        return f"{org} — {self.position}: {self.full_name}" if org else self.full_name

    def __repr__(self):
        return f"<Representative {self.full_name}>"


class SectionKind(Base):
    """Справочник разделов проектной документации. ТЗ п.22, расширяемый."""

    __tablename__ = 'section_kinds'

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<SectionKind {self.code}>"


class MaterialType(Base):
    """Справочник типов материалов. ТЗ п.44, расширяемый."""

    __tablename__ = 'material_types'

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)

    def __repr__(self):
        return f"<MaterialType {self.code}>"


# =====================================================================
# ПРОЕКТ (ТЗ п.17, 21)
# =====================================================================

project_organizations = Table(
    'project_organizations',
    Base.metadata,
    Column('project_id', Integer, ForeignKey('projects.id', ondelete="CASCADE"), primary_key=True),
    Column('organization_id', Integer, ForeignKey('organizations.id', ondelete="RESTRICT"), primary_key=True),
    Column('role', String, nullable=False, default=domain.ROLE_CONTRACTOR),
)


class Project(Base):
    """Карточка проекта. ТЗ п.17: статические данные, редактируются кнопкой."""

    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    direction_id = Column(Integer, ForeignKey('directions.id', ondelete="RESTRICT"), nullable=False)
    title = Column(String, nullable=False)
    address = Column(String)
    customer_org_id = Column(Integer, ForeignKey('organizations.id', ondelete="SET NULL"))
    general_contractor_org_id = Column(Integer, ForeignKey('organizations.id', ondelete="SET NULL"))
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    direction = relationship("Direction")
    customer = relationship("Organization", foreign_keys=[customer_org_id])
    general_contractor = relationship("Organization", foreign_keys=[general_contractor_org_id])

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")
    sections = relationship("ProjectSection", back_populates="project", cascade="all, delete-orphan")
    materials = relationship("Material", back_populates="project", cascade="all, delete-orphan")
    # Каскада намеренно нет: архивные файлы являются доказательством и не
    # должны исчезать вместе с проектом (ТЗ п.54, 109). Удаление проекта с
    # архивом блокируется внешним ключом ON DELETE RESTRICT.
    archive_documents = relationship("ArchiveDocument", back_populates="project")
    organizations = relationship(
        "Organization", secondary=project_organizations, lazy="selectin"
    )
    representatives = relationship(
        "ProjectRepresentative", back_populates="project", cascade="all, delete-orphan"
    )
    packages = relationship("Package", back_populates="project", cascade="all, delete-orphan")
    history_events = relationship(
        "HistoryEvent", back_populates="project", cascade="all, delete-orphan"
    )
    ai_proposals = relationship(
        "AiProposal", back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Project {self.title}>"


class ProjectRepresentative(Base):
    """Представитель, задействованный в проекте, с ролью. ТЗ п.17, 40."""

    __tablename__ = 'project_representatives'

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    representative_id = Column(
        Integer, ForeignKey('representatives.id', ondelete="RESTRICT"), nullable=False
    )
    role = Column(String, nullable=False)

    project = relationship("Project", back_populates="representatives")
    representative = relationship("Representative", lazy="selectin")

    __table_args__ = (
        UniqueConstraint('project_id', 'representative_id', 'role', name='uq_project_rep_role'),
    )


class ProjectSection(Base):
    """Раздел проектной документации. ТЗ п.21: количество разделов не ограничено."""

    __tablename__ = 'project_sections'

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    kind_id = Column(Integer, ForeignKey('section_kinds.id', ondelete="RESTRICT"), nullable=False)
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    organization_id = Column(Integer, ForeignKey('organizations.id', ondelete="SET NULL"))
    designer_rep_id = Column(Integer, ForeignKey('representatives.id', ondelete="SET NULL"))
    sheets = Column(String)
    required_details = Column(Text)

    project = relationship("Project", back_populates="sections")
    kind = relationship("SectionKind", lazy="selectin")
    organization = relationship("Organization", lazy="selectin")
    designer = relationship("Representative", foreign_keys=[designer_rep_id], lazy="selectin")

    __table_args__ = (
        UniqueConstraint('project_id', 'code', name='uq_project_section_code'),
    )


class Material(Base):
    """Материал, задействованный в проекте. ТЗ п.44."""

    __tablename__ = 'materials'
    __table_args__ = (
        Index('ix_materials_project', 'project_id'),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    material_type_id = Column(
        Integer, ForeignKey('material_types.id', ondelete="RESTRICT"), nullable=False
    )
    name = Column(String, nullable=False)
    unit = Column(String)
    quantity = Column(Float)
    note = Column(Text)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="materials")
    material_type = relationship("MaterialType", lazy="selectin")
    # ТЗ п.44, 45: материал участвует в конкретных актах испытаний, и
    # документ качества выбирается для конкретного акта. Связь явная:
    # прикреплять сертификат ко всем актам материала нельзя (ТЗ п.45).
    test_act_links = relationship(
        "MaterialTestActLink", back_populates="material",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Material {self.name}>"


class MaterialTestActLink(Base):
    """Материал — акт испытаний. ТЗ п.44, 45, 49.

    Строка материала и акт испытаний связаны явно: только те акты, которые
    оператор указал. Автоматическое прикрепление документа качества ко всем
    актам материала не допускается (ТЗ п.45).
    """

    __tablename__ = 'material_test_act_links'
    __table_args__ = (
        UniqueConstraint('material_id', 'document_id', name='uq_material_test_act'),
        Index('ix_material_test_act_links_document', 'document_id'),
    )

    # Суррогатный ключ — как у DocumentLink: по нему связь удаляется и
    # отображается в интерфейсе.
    id = Column(Integer, primary_key=True, autoincrement=True)
    material_id = Column(
        Integer, ForeignKey('materials.id', ondelete="CASCADE"), nullable=False
    )
    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime, default=utcnow, nullable=False)

    material = relationship("Material", back_populates="test_act_links")
    document = relationship("Document", lazy="selectin")

    def __repr__(self):
        return f"<MaterialTestActLink {self.material_id}-{self.document_id}>"


# =====================================================================
# АРХИВ: ЛОГИЧЕСКИЙ ДОКУМЕНТ И ФИЗИЧЕСКИЕ ВЕРСИИ (ТЗ п.50, 53, 90, 91)
# =====================================================================

class ArchiveDocument(Base):
    """Логический архивный документ: реквизиты, номер, дата, категория.

    Физического файла у него нет — файлы живут в ArchiveFileVersion.
    ТЗ п.90, 91.
    """

    __tablename__ = 'archive_documents'
    __table_args__ = (
        Index('ix_archive_documents_project', 'project_id', 'category'),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="RESTRICT"), nullable=False
    )
    category = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    number = Column(String)
    doc_date = Column(Date)
    # ТЗ п.46: сроки действия для сертификатов и деклараций.
    validity_from = Column(Date)
    validity_to = Column(Date)
    note = Column(Text)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="archive_documents")
    versions = relationship(
        "ArchiveFileVersion",
        back_populates="archive_document",
        cascade="all, delete-orphan",
        order_by="ArchiveFileVersion.version_no",
    )
    links = relationship(
        "DocumentArchiveLink", back_populates="archive_document", cascade="all, delete-orphan"
    )

    @property
    def current_version(self) -> "ArchiveFileVersion | None":
        for v in self.versions:
            if v.is_actual:
                return v
        return self.versions[-1] if self.versions else None

    @property
    def links_count(self) -> int:
        """ТЗ п.51: количество связей архивного документа."""
        return len(self.links)

    def __repr__(self):
        return f"<ArchiveDocument {self.original_name}>"


class ArchiveFileVersion(Base):
    """Физическая версия файла архивного документа.

    Новая редакция создаёт новую строку, а не перезаписывает старую
    (ТЗ п.53, 91, 93). Старые комплекты продолжают ссылаться на то состояние,
    которое было использовано при их формировании.
    """

    __tablename__ = 'archive_file_versions'

    id = Column(Integer, primary_key=True)
    archive_document_id = Column(
        Integer, ForeignKey('archive_documents.id', ondelete="CASCADE"), nullable=False
    )
    version_no = Column(Integer, nullable=False)
    stored_path = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    file_size = Column(Integer)
    is_actual = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    archive_document = relationship("ArchiveDocument", back_populates="versions")

    __table_args__ = (
        UniqueConstraint('archive_document_id', 'version_no', name='uq_archive_version_no'),
    )

    def __repr__(self):
        return f"<ArchiveFileVersion v{self.version_no} {self.file_hash[:8]}>"


# =====================================================================
# НОРМАТИВНЫЕ ФОРМЫ (ТЗ п.96)
# =====================================================================

class NormativeForm(Base):
    """Версия нормативной формы документа.

    Форма отделена от программного кода: изменение формы порождает новую
    версию, а уже выпущенные документы сохраняют ту форму, по которой были
    сформированы (ТЗ п.96).
    """

    __tablename__ = 'normative_forms'

    id = Column(Integer, primary_key=True)
    doc_type = Column(String, nullable=False)
    version = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    basis = Column(String)
    is_current = Column(Boolean, nullable=False, default=True)
    # Описание формы: поля, порядок, статические формулировки. Этап 2.
    definition = Column(JSON)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('doc_type', 'version', name='uq_form_doc_type_version'),
    )

    def __repr__(self):
        return f"<NormativeForm {self.doc_type} v{self.version}>"


# =====================================================================
# ДОКУМЕНТЫ (ТЗ п.42, 43, 85, 89, 95)
# =====================================================================

class Document(Base):
    """Исполнительный документ. Базовые поля общие для всех типов."""

    __tablename__ = 'documents'
    __table_args__ = (
        # Список документов проекта — самая частая выборка (ТЗ п.30, 47).
        Index('ix_documents_project', 'project_id', 'doc_type'),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    doc_type = Column(String, nullable=False)
    number = Column(String, nullable=False)
    doc_date = Column(Date)
    status = Column(String, nullable=False, default=domain.DOC_STATUS_DRAFT)
    form_version_id = Column(Integer, ForeignKey('normative_forms.id', ondelete="RESTRICT"))
    # ТЗ п.64: решение по незаполненному представителю эксплуатации.
    exploitation_missing_choice = Column(String)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    project = relationship("Project", back_populates="documents")
    form_version = relationship("NormativeForm", lazy="selectin")

    # cascade намеренно без delete: ORM удалил бы выпущенные версии раньше
    # родителя, и внешний ключ RESTRICT (ТЗ п.85) не успел бы сработать.
    # passive_deletes: без него ORM шлёт UPDATE document_id=NULL, и удаление
    # отклонялось бы только потому, что колонка NOT NULL. Это «испортить и
    # упасть», а не «отклонить»: с nullable-колонкой версия осиротела бы.
    # С удалением разбирается БД — она и должна решать (ТЗ п.85).
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="DocumentVersion.version_no",
    )
    archive_links = relationship(
        "DocumentArchiveLink", back_populates="document", cascade="all, delete-orphan"
    )
    # Связи с другими документами (ТЗ п.87).
    document_links = relationship(
        "DocumentLink", back_populates="document",
        foreign_keys="DocumentLink.document_id",
        cascade="all, delete-orphan",
        order_by="DocumentLink.order_no",
    )
    # ТЗ п.44, 45: акты испытаний, в которых проверялся этот материал.
    material_links = relationship(
        "MaterialTestActLink", back_populates="document",
        cascade="all, delete-orphan",
    )
    signature_blocks = relationship(
        "SignatureBlock", back_populates="document", cascade="all, delete-orphan"
    )
    aosr = relationship("AosrDetails", back_populates="document", uselist=False, cascade="all, delete-orphan")
    aook = relationship("AookDetails", back_populates="document", uselist=False, cascade="all, delete-orphan")
    aou_sito = relationship("AouSitoDetails", back_populates="document", uselist=False, cascade="all, delete-orphan")
    test_act = relationship("TestActDetails", back_populates="document", uselist=False, cascade="all, delete-orphan")

    @property
    def current_version(self) -> "DocumentVersion | None":
        for v in self.versions:
            if v.is_actual:
                return v
        return self.versions[-1] if self.versions else None

    @property
    def type_label(self) -> str:
        return domain.DOC_TYPE_LABELS.get(self.doc_type, self.doc_type)

    def links_with_role(self, role: str) -> list:
        return [link for link in self.archive_links if link.link_role == role]

    def __repr__(self):
        return f"<Document {self.doc_type} №{self.number}>"


class DocumentVersion(Base):
    """Зафиксированная версия содержимого документа.

    После выпуска версия не меняется вследствие последующего редактирования
    (ТЗ п.85, 93).
    """

    __tablename__ = 'document_versions'

    id = Column(Integer, primary_key=True)
    document_id = Column(
        # RESTRICT, а не CASCADE: выпущенная версия — историческое
        # доказательство (ТЗ п.85, 54). При CASCADE удаление черновика стирало
        # бы зафиксированную версию без следа, а удаление проекта каскадом
        # стирало бы выпуски всех его документов разом.
        Integer, ForeignKey('documents.id', ondelete="RESTRICT"), nullable=False
    )
    version_no = Column(Integer, nullable=False)
    form_version_id = Column(Integer, ForeignKey('normative_forms.id', ondelete="RESTRICT"))
    # Замороженное содержимое на момент фиксации версии.
    payload = Column(JSON, nullable=False)
    issued_at = Column(DateTime)
    is_actual = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    document = relationship("Document", back_populates="versions")
    form_version = relationship("NormativeForm", lazy="selectin")

    __table_args__ = (
        UniqueConstraint('document_id', 'version_no', name='uq_document_version_no'),
    )


class AosrDetails(Base):
    """Поля акта освидетельствования скрытых работ. ТЗ п.24-28."""

    __tablename__ = 'aosr_details'

    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), primary_key=True
    )
    # ТЗ п.25: поле обязательное.
    performer_org_id = Column(Integer, ForeignKey('organizations.id', ondelete="RESTRICT"))
    # ТЗ п.26: включается по умолчанию, может остаться незаполненным.
    nrs_number = Column(String)
    # ТЗ п.27: пункт 7 заполняется оператором вручную.
    clause_7_text = Column(Text)

    document = relationship("Document", back_populates="aosr")
    performer_org = relationship("Organization", lazy="selectin")


class AookDetails(Base):
    """Поля акта освидетельствования качества. ТЗ п.30-33.

    Разделы 5, 8 и 9 имеют фиксированную структуру, произвольный текст
    оператора их не заменяет (ТЗ п.31, 32, 62).
    """

    __tablename__ = 'aook_details'

    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), primary_key=True
    )
    # Раздел 8 — фиксированная формулировка, не редактируется оператором.
    clause_8_fixed = Column(Text)
    # Решения по пункту 9, а-г. Заполняются флагами/текстом по структуре.
    clause_9_decisions = Column(JSON)
    compliance_text = Column(Text)

    document = relationship("Document", back_populates="aook")


class AouSitoDetails(Base):
    """Поля акта освидетельствования и приёмки инженерных систем. ТЗ п.34, 40."""

    __tablename__ = 'aou_sito_details'

    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), primary_key=True
    )
    # ТЗ п.40: представитель эксплуатирующей организации допускается в АОУСИТО.
    exploitation_rep_id = Column(Integer, ForeignKey('representatives.id', ondelete="SET NULL"))
    system_name = Column(String)

    document = relationship("Document", back_populates="aou_sito")
    exploitation_rep = relationship("Representative", lazy="selectin")


class TestActDetails(Base):
    """Поля акта испытаний. ТЗ п.35-39, 41.

    Акт испытания создаётся только по запросу оператора (ТЗ п.36), дата
    вводится оператором (ТЗ п.38), состав участников задаётся отдельно для
    каждого акта (ТЗ п.39).
    """

    __tablename__ = 'test_act_details'

    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), primary_key=True
    )
    test_type = Column(String, nullable=False, default=domain.TEST_TYPE_INSPECTION)
    system_name = Column(String, nullable=False)
    # ТЗ п.40: представитель эксплуатирующей организации — во всех актах испытаний.
    exploitation_rep_id = Column(Integer, ForeignKey('representatives.id', ondelete="SET NULL"))
    norm_basis = Column(String, default=domain.TEST_ACT_BASIS)

    document = relationship("Document", back_populates="test_act")
    exploitation_rep = relationship("Representative", lazy="selectin")
    participants = relationship(
        "TestActParticipant", back_populates="test_act", cascade="all, delete-orphan"
    )


class TestActParticipant(Base):
    """Участник конкретного акта испытаний. ТЗ п.39.

    Фиксированного универсального состава участников не существует.
    """

    __tablename__ = 'test_act_participants'

    id = Column(Integer, primary_key=True)
    test_act_id = Column(
        Integer, ForeignKey('test_act_details.document_id', ondelete="CASCADE"), nullable=False
    )
    representative_id = Column(
        Integer, ForeignKey('representatives.id', ondelete="RESTRICT"), nullable=False
    )
    role = Column(String, nullable=False)

    test_act = relationship("TestActDetails", back_populates="participants")
    representative = relationship("Representative", lazy="selectin")

    __table_args__ = (
        UniqueConstraint('test_act_id', 'representative_id', 'role', name='uq_test_act_participant'),
    )


class SignatureBlock(Base):
    """Блок подписантов. ТЗ п.63: два независимых блока «Сдал» и «Принял»."""

    __tablename__ = 'signature_blocks'

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), nullable=False
    )
    block = Column(String, nullable=False)   # Сдал / Принял
    position = Column(String)
    full_name = Column(String)
    sign_place = Column(String)

    document = relationship("Document", back_populates="signature_blocks")

    __table_args__ = (
        UniqueConstraint('document_id', 'block', name='uq_signature_block'),
    )


# =====================================================================
# СВЯЗИ ДОКУМЕНТОВ И АРХИВА (ТЗ п.30, 47, 48, 49, 52, 79, 91)
# =====================================================================

class DocumentArchiveLink(Base):
    """Логическая связь документа с архивным документом.

    Связь удаляется независимо от самого документа (ТЗ п.52). Закреплённая
    версия файла не меняется при появлении новой редакции (ТЗ п.91).
    """

    __tablename__ = 'document_archive_links'

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), nullable=False
    )
    archive_document_id = Column(
        Integer, ForeignKey('archive_documents.id', ondelete="CASCADE"), nullable=False
    )
    # Версия файла, зафиксированная в момент создания связи.
    archive_version_id = Column(Integer, ForeignKey('archive_file_versions.id', ondelete="RESTRICT"))
    link_role = Column(String, nullable=False)
    order_no = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    document = relationship("Document", back_populates="archive_links")
    archive_document = relationship("ArchiveDocument", back_populates="links", lazy="selectin")
    archive_version = relationship("ArchiveFileVersion", lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            'document_id', 'archive_document_id', 'link_role', name='uq_doc_archive_role'
        ),
        Index('ix_doc_archive_links_document', 'document_id', 'link_role'),
        # Обратный поиск: какие документы и какая редакция файла используют
        # архивный документ (ТЗ п.49, 91).
        Index('ix_doc_archive_links_archive', 'archive_document_id'),
        Index('ix_doc_archive_links_version', 'archive_version_id'),
    )

    def __repr__(self):
        return f"<Link {self.link_role} -> {self.archive_document_id}>"


class DocumentLink(Base):
    """Логическая связь между документами проекта. ТЗ п.43, 87, 88, 89.

    Связь документов с архивом хранится в DocumentArchiveLink, а здесь
    хранится связь документов друг с другом: итоговый акт ссылается на те
    акты, которые он завершает. Без неё нельзя проверить логику дат
    (ТЗ п.87): дата окончания АООК не может быть раньше окончания связанного
    АОСР, а начало АООК не может быть позже начала связанного АОСР.
    """

    __tablename__ = 'document_links'
    __table_args__ = (
        UniqueConstraint(
            'document_id', 'related_document_id', 'link_role',
            name='uq_doc_doc_role',
        ),
        Index('ix_document_links_document', 'document_id', 'link_role'),
        # Обратный поиск: какие итоговые акты закрывают данный акт.
        Index('ix_document_links_related', 'related_document_id', 'link_role'),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), nullable=False
    )
    related_document_id = Column(
        Integer, ForeignKey('documents.id', ondelete="CASCADE"), nullable=False
    )
    link_role = Column(String, nullable=False)
    order_no = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    document = relationship(
        "Document", back_populates="document_links",
        foreign_keys=[document_id],
    )
    related_document = relationship("Document", foreign_keys=[related_document_id])

    def __repr__(self):
        return f"<DocumentLink {self.link_role} {self.document_id}->{self.related_document_id}>"


# =====================================================================
# КОМПЛЕКТЫ, РЕЕСТРЫ, ИСТОРИЯ (ТЗ п.68, 70, 76-78, 86)
# =====================================================================

class Package(Base):
    """Сформированный комплект. ТЗ п.70, 71.

    Каждая выгрузка — отдельная папка; предыдущие комплекты не изменяются
    (ТЗ п.71, 109). Реестр не является отдельным разделом системы, он часть
    конкретной выгрузки (ТЗ п.68).
    """

    __tablename__ = 'packages'
    __table_args__ = (
        Index('ix_packages_project', 'project_id'),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    folder_name = Column(String, nullable=False)
    absolute_path = Column(String, nullable=False)
    export_variant = Column(String, nullable=False, default=domain.EXPORT_VARIANT_ALL)
    page_numbering = Column(Boolean, nullable=False, default=False)
    has_errors_file = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="packages")
    entries = relationship(
        "PackageEntry", back_populates="package", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Package {self.folder_name}>"


class PackageEntry(Base):
    """Строка реестра конкретной выгрузки.

    Номер строки реестра и номер документа — разные идентификаторы
    (ТЗ п.77). Нумерация строк пересоздаётся заново для каждой выгрузки
    (ТЗ п.78), а номер документа не меняется.
    """

    __tablename__ = 'package_entries'

    id = Column(Integer, primary_key=True)
    package_id = Column(
        Integer, ForeignKey('packages.id', ondelete="CASCADE"), nullable=False
    )
    document_id = Column(Integer, ForeignKey('documents.id', ondelete="CASCADE"))
    # Версия документа, попавшая в этот комплект.
    document_version_id = Column(Integer, ForeignKey('document_versions.id', ondelete="RESTRICT"))
    register_row_no = Column(Integer, nullable=False)
    document_number = Column(String, nullable=False)
    doc_type = Column(String, nullable=False)
    is_attachment = Column(Boolean, nullable=False, default=False)
    parent_entry_id = Column(Integer, ForeignKey('package_entries.id', ondelete="CASCADE"))
    created_at = Column(DateTime, default=utcnow, nullable=False)

    package = relationship("Package", back_populates="entries")
    document = relationship("Document", lazy="selectin")
    document_version = relationship("DocumentVersion", lazy="selectin")
    parent_entry = relationship("PackageEntry", remote_side=[id], lazy="selectin")

    __table_args__ = (
        UniqueConstraint('package_id', 'register_row_no', name='uq_package_register_row'),
    )


class HistoryEvent(Base):
    """Событие истории проекта. ТЗ п.86.

    История сохраняет версии документов, изменения архивных документов,
    сформированные комплекты, реестры и исторические PDF.
    """

    __tablename__ = 'history_events'

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    event_type = Column(String, nullable=False)
    entity_type = Column(String)
    entity_id = Column(Integer)
    message = Column(Text)
    payload = Column(JSON)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="history_events")

    __table_args__ = (
        Index('ix_history_project_time', 'project_id', 'created_at'),
    )


class AiProposal(Base):
    """Предложение ИИ-агента: черновик, а не исполнительный документ.

    ТЗ п.104, 105: по умолчанию ИИ ничего критического не меняет. Его
    результат сохраняется здесь как черновик, и лишь после подтверждения
    оператора изменение выполняется через прикладной API сервисов.

    Поле ``action`` описывает, что именно предлагается изменить (например,
    связать два документа). ``None`` означает замечание без изменения
    данных. ``basis`` хранит основание нормы: документ, раздел, пункт
    (ТЗ п.106); без основания предложение не выдаётся за требование.
    """

    __tablename__ = 'ai_proposals'

    id = Column(Integer, primary_key=True)
    project_id = Column(
        Integer, ForeignKey('projects.id', ondelete="CASCADE"), nullable=False
    )
    document_id = Column(Integer, ForeignKey('documents.id', ondelete="SET NULL"))
    code = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    action = Column(JSON)
    basis = Column(JSON)
    is_requirement = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default=domain.AI_PROPOSAL_DRAFT)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    decided_at = Column(DateTime)
    decision_note = Column(Text)

    project = relationship("Project", back_populates="ai_proposals")
    document = relationship("Document", lazy="selectin")

    __table_args__ = (
        Index('ix_ai_proposals_project_status', 'project_id', 'status'),
    )
