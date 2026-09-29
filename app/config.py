"""Единая точка конфигурации путей и времени.

Все пути хранилища вычисляются ТОЛЬКО здесь (ТЗ п.72: три раздельных
хранилища — рабочее, комплекты, backup). Ни один другой модуль не должен
вычислять путь к хранилищу самостоятельно.

Переопределение для тестов: переменная окружения EXECUTIVE_DOC_DATA_DIR.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "ExecutiveDocSystem"
DATA_DIR_ENV = "EXECUTIVE_DOC_DATA_DIR"

# ТЗ п.59: разрешённые шрифты. По умолчанию Times New Roman 11 pt.
ALLOWED_FONTS = ("Calibri", "Arial", "ISO", "Times New Roman")
DEFAULT_FONT = "Times New Roman"
DEFAULT_FONT_SIZE = 11

# ТЗ п.60: базовые поля A4, мм.
MARGIN_TOP_MM = 20
MARGIN_LEFT_MM = 10
MARGIN_RIGHT_MM = 10
MARGIN_BOTTOM_MM = 10


def is_frozen() -> bool:
    """True, если код запущен из собранного PyInstaller-приложения."""
    return getattr(sys, "frozen", False)


def _default_data_dir() -> Path:
    """Каталог данных по умолчанию.

    В собранном приложении __file__ указывает внутрь бандла (_internal),
    поэтому путь считается от каталога исполняемого файла. Иначе — от
    корня проекта.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent / "storage"
    return Path(__file__).resolve().parent.parent / "storage"


def _resolve_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).resolve()
    return _default_data_dir()


DATA_DIR = _resolve_data_dir()

# 1. Рабочее хранилище (ТЗ п.72) — БД + внутренний архив.
DB_PATH = DATA_DIR / "app.db"
ARCHIVE_DIR = DATA_DIR / "internal_archive"

# 2. Комплекты (ТЗ п.70, 72) — пользовательские выгрузки. Не рабочая база.
PACKAGES_DIR = DATA_DIR / "packages"

# 3. BACKUP (ТЗ п.74) — резервные копии для восстановления/переноса.
BACKUP_DIR = DATA_DIR / "backups"

SETTINGS_PATH = DATA_DIR / "settings.json"

# Журнал работы программы. Приложение собирается как оконное (--windowed,
# console=False), поэтому stderr оператору недоступен: без файла журнала
# предупреждения о состоянии базы (например, оставшаяся WAL) никто бы
# не увидел (ТЗ п.54, 74, 98).
LOG_PATH = DATA_DIR / "app.log"


def setup_logging(level: int = logging.WARNING) -> None:
    """Направить журнал в файл рядом с базой. Идемпотентно.

    Отдельный обработчик добавляется один раз: повторный вызов не создаёт
    дублирующихся строк в файле.
    """
    root = logging.getLogger()
    if any(getattr(h, "_eds_log", False) for h in root.handlers):
        return
    try:
        ensure_dirs()
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    except OSError:
        return  # не смогли открыть файл — не падаем, но и не молчим в журнале
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler._eds_log = True  # метка собственного обработчика
    root.addHandler(handler)
    root.setLevel(level)


def ensure_dirs() -> None:
    """Создать все каталоги хранилища. Идемпотентно."""
    for path in (DATA_DIR, ARCHIVE_DIR, PACKAGES_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def utcnow() -> datetime:
    """Текущее время UTC без зоны (naive).

    SQLite в SQLAlchemy теряет tzinfo даже при DateTime(timezone=True),
    поэтому во всём приложении используется naive-UTC. Это единственное
    место, где создаётся "сейчас" — при переходе на серверную БД
    достаточно вернуть aware datetime.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(value: datetime) -> datetime:
    """Перевести naive-UTC в местное время для отображения оператору."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc).astimezone()
