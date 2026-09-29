import os
from pathlib import Path
from sqlalchemy.orm import Session
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from app.db.models import Project, Document

# Регистрируем шрифт с поддержкой кириллицы
FONT_NAME = "Helvetica"
possible_fonts = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/FreeSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf"
]

for font_path in possible_fonts:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont("CustomCyrillic", font_path))
            FONT_NAME = "CustomCyrillic"
            break
        except Exception:
            pass

def generate_simple_pdf(output_path: Path, title: str, content: list[str]):
    """Генерация PDF по стандарту A4 с поддержкой кириллицы (Разделы 55-61 ТЗ)"""
    c = canvas.Canvas(str(output_path), pagesize=A4)
    width, height = A4
    
    # Заголовок
    c.setFont(FONT_NAME, 14)
    c.drawString(50, height - 50, title)
    
    # Содержимое
    c.setFont(FONT_NAME, 10)
    y = height - 80
    for line in content:
        c.drawString(50, y, line)
        y -= 15
        if y < 50:
            c.showPage()
            c.setFont(FONT_NAME, 10)
            y = height - 50
    c.save()

def export_package(db: Session, project_id: int, target_dir: Path) -> Path:
    """Формирование выгрузки комплекта (Разделы 69-81 ТЗ)"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("Проект не найден")

    target_dir.mkdir(parents=True, exist_ok=True)
    
    registry_lines = [f"РЕЕСТР ВЫГРУЗКИ: {project.title}", "="*50]
    docs = db.query(Document).filter(Document.project_id == project_id).all()
    
    for idx, doc in enumerate(docs, start=1):
        registry_lines.append(f"№ {idx} | Тип: {doc.doc_type} | Номер документа: {doc.number}")

    registry_pdf = target_dir / "Реестр_выгрузки.pdf"
    generate_simple_pdf(registry_pdf, "РЕЕСТР ИСПОЛНИТЕЛЬНОЙ ДОКУМЕНТАЦИИ", registry_lines)

    return target_dir
