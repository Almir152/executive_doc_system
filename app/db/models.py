from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Table
from sqlalchemy.orm import relationship
from app.db.database import Base

document_file_links = Table(
    'document_file_links',
    Base.metadata,
    Column('document_id', Integer, ForeignKey('documents.id', ondelete="CASCADE"), primary_key=True),
    Column('file_id', Integer, ForeignKey('archive_files.id', ondelete="RESTRICT"), primary_key=True)
)

class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    direction = Column(String, nullable=False)
    title = Column(String, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    documents = relationship("Document", back_populates="project", cascade="all, delete-orphan")

class Document(Base):
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'))
    doc_type = Column(String, nullable=False)
    number = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    project = relationship("Project", back_populates="documents")
    archive_files = relationship("ArchiveFile", secondary=document_file_links, back_populates="linked_documents")

class ArchiveFile(Base):
    __tablename__ = 'archive_files'

    id = Column(Integer, primary_key=True)
    file_type = Column(String, nullable=False)
    original_name = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    file_hash = Column(String, unique=True, nullable=False)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    linked_documents = relationship("Document", secondary=document_file_links, back_populates="archive_files")

    @property
    def links_count(self) -> int:
        return len(self.linked_documents)
