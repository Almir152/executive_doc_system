import hashlib
import shutil
from pathlib import Path
from sqlalchemy.orm import Session
from app.config import BASE_DIR
from app.db.models import ArchiveFile, Document

ARCHIVE_DIR = BASE_DIR / "storage" / "internal_archive"

def calculate_hash(file_path: Path) -> str:
    """Вычисление SHA-256 хэша файла для дедупликации"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def add_file_to_archive(db: Session, src_path: Path, file_type: str) -> ArchiveFile:
    """Добавление файла в единый архив без дублирования (Раздел 84 ТЗ)"""
    file_hash = calculate_hash(src_path)
    
    # Ищем, есть ли уже такой файл в архиве
    existing_file = db.query(ArchiveFile).filter(ArchiveFile.file_hash == file_hash).first()
    if existing_file:
        return existing_file

    # Если файла нет, копируем его во внутренний архив под уникальным именем
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest_filename = f"{file_hash}_{src_path.name}"
    dest_path = ARCHIVE_DIR / dest_filename
    shutil.copy2(src_path, dest_path)

    new_file = ArchiveFile(
        file_type=file_type,
        original_name=src_path.name,
        stored_path=str(dest_path),
        file_hash=file_hash
    )
    db.add(new_file)
    db.commit()
    db.refresh(new_file)
    return new_file

def link_file_to_document(db: Session, document_id: int, archive_file_id: int):
    """Связывание файла с актом"""
    doc = db.query(Document).filter(Document.id == document_id).first()
    archive_file = db.query(ArchiveFile).filter(ArchiveFile.id == archive_file_id).first()
    
    if doc and archive_file and archive_file not in doc.archive_files:
        doc.archive_files.append(archive_file)
        db.commit()
