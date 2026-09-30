import os
from pathlib import Path
from sqlalchemy.orm import Session
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from app.core.services.export_checks import CheckResult, ERRORS_FILE_NAME
from app.core.services.package_service import create_package

__all__ = [
    "ERRORS_FILE_NAME",
    "export_package",
    "generate_simple_pdf",
    "write_errors_file",
]

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

def write_errors_file(target_dir: Path | str, result: CheckResult) -> Path:
    """Записать `Ошибки выгрузки.txt` в папку текущей выгрузки (ТЗ п.83).

    Файл создаётся только здесь: ранее сформированные комплекты не
    изменяются (ТЗ п.71, 83).
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / ERRORS_FILE_NAME
    path.write_text(result.report(), encoding="utf-8")
    return path


def export_package(
    db: Session,
    project_id: int,
    target_dir: Path | str,
    **options,
) -> Path:
    """Сформировать комплект в папке, выбранной оператором (ТЗ п.69–83).

    `target_dir` приходит из диалога выбора папки, то есть строкой: путь
    приводится к `Path` здесь, иначе выгрузка падала бы с
    «'str' object has no attribute 'mkdir'». Внутри выбранной папки
    создаётся корневая папка комплектов, а в ней — отдельная папка
    выгрузки (ТЗ п.70, 71).
    """
    package = create_package(db, project_id, base_dir=Path(target_dir), **options)
    return Path(package.absolute_path)
