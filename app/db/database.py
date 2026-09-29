import logging
import sqlite3
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DB_PATH, ensure_dirs

logger = logging.getLogger(__name__)

# Гарантируем наличие папки storage до подключения к БД.
ensure_dirs()

DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Выставить обязательные параметры SQLite на каждом подключении.

    SQLite по умолчанию НЕ проверяет внешние ключи. Без этой прагмы объявленные
    в моделях ondelete="CASCADE"/"RESTRICT" не действуют, и целостность связей
    (ТЗ п.49, 50, 52) не гарантируется.

    Прагмы выполняются в режиме автокоммита: внутри неявной транзакции
    ``PRAGMA foreign_keys`` не действует, и целостность отключается молча.
    """
    # Автокоммит на время настройки: иначе первая же прагма может попасть
    # в неявную транзакцию и остаться без эффекта.
    previous_isolation = dbapi_connection.isolation_level
    dbapi_connection.isolation_level = None
    try:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            # Проверка обязательна: если прагма не применилась, программа
            # продолжила бы работать без контроля связей, ничего не сообщив
            # оператору. Для исполнительной документации это недопустимо
            # (ТЗ п.54, 86).
            #
            # ВАЖНО: занятость файла другим процессом — НЕ причина сюда
            # попасть. Проверено: под BEGIN EXCLUSIVE чужого соединения
            # PRAGMA foreign_keys=ON применяется и даёт 1, потому что это
            # настройка соединения, а не файла. Прагма не действует только
            # внутри уже начатой транзакции, поэтому сообщение говорит
            # именно об этом, а не выдаёт неверную причину.
            enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
            if enabled != 1:
                raise RuntimeError(
                    "не удалось включить проверку внешних ключей SQLite "
                    f"(база {DB_PATH}). Прагма не действует внутри начатой "
                    "транзакции — сообщите разработчику, это не связано с "
                    "занятостью файла."
                )

            # Журнал намеренно НЕ используется в режиме WAL:
            #   - WAL не поддерживается на сетевых дисках, а каталог данных
            #     оператор может разместить на общем ресурсе или USB-носителе;
            #   - WAL требует держать рядом app.db-wal и app.db-shm, из-за чего
            #     копирование одного app.db перестаёт быть полноценным BACKUP
            #     (ТЗ п.74, 98);
            #   - synchronous=NORMAL в WAL допускает потерю последних коммитов
            #     при сбое питания, что недопустимо (ТЗ п.54, 86).
            #
            # Режим хранится В САМОМ файле базы и переживает перезапуск
            # программы, поэтому его нужно не «не переключать», а возвращать
            # принудительно: иначе база, однажды открытая в режиме WAL
            # (например, прежней версией программы), остаётся в нём навсегда,
            # и незакрытые коммиты лежат в app.db-wal, которого нет в копии
            # app.db. Смена режима требует эксклюзивного доступа, поэтому при
            # занятом файле она не удаётся — это не повод не запускаться.
            try:
                cursor.execute("PRAGMA journal_mode=DELETE")
            except sqlite3.OperationalError as exc:
                logger.warning("не удалось вернуть обычный режим журнала: %s", exc)
            try:
                cursor.execute("PRAGMA synchronous=FULL")
            except sqlite3.OperationalError as exc:
                logger.warning("не удалось выставить synchronous=FULL: %s", exc)
        finally:
            cursor.close()
    finally:
        dbapi_connection.isolation_level = previous_isolation



def journal_mode() -> str:
    """Фактический режим журнала SQLite для рабочей базы."""
    with engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA journal_mode").fetchone()[0]


def _restore_delete_journal(attempts: int = 3, pause: float = 0.2) -> bool:
    """Вернуть базе обычный режим журнала. True, если режим DELETE достигнут.

    Смена режима требует эксклюзивного доступа к файлу, поэтому может не
    удаться: база занята вторым экземпляром программы или просмотрщиком.
    Перед повторами освобождаем собственный пул соединений — нередко файл
    держит именно наше собственное незакрытое соединение.
    """
    for attempt in range(attempts):
        try:
            if journal_mode().lower() != "wal":
                return True
        except Exception as exc:  # noqa: BLE001 - причина уйдёт в отчёт
            logger.warning("не удалось прочитать режим журнала: %s", exc)
            return False
        if attempt:
            # Наша доля в блокировке снимается закрытием соединений пула.
            engine.dispose()
            time.sleep(pause)
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
        except SQLAlchemyError as exc:
            # SQLAlchemy оборачивает sqlite3.OperationalError в свой тип,
            # поэтому ловим оба уровня.
            logger.warning(
                "не удалось вернуть обычный режим журнала (%s): %s", DB_PATH, exc
            )
    try:
        return journal_mode().lower() != "wal"
    except Exception:  # noqa: BLE001
        return False


def init_db() -> dict:
    """Привести БД к актуальной схеме. Возвращает отчёт о применённых миграциях.

    Порядок обязателен иначе миграция не увидит унаследованные таблицы:

    1. apply_migrations — переносит данные из прежней схемы в новую;
    2. create_all — создаёт таблицы, которых ещё нет (новая БД);
    3. seed — заполняет справочники, идемпотентно.

    create_all умеет только создавать отсутствующие таблицы: он не меняет
    существующие. Поэтому правки существующих таблиц оформляются миграциями.

    В отчёте есть ``journal_ok``: False означает, что база осталась в WAL.
    Это не повод не работать, но копия одного app.db тогда неполна
    (ТЗ п.74, 98), поэтому вызывающий обязан предупредить оператора.
    """
    from app.db import migrations
    from app.db.seed import seed_reference_data

    applied = migrations.apply_migrations(engine)
    Base.metadata.create_all(bind=engine)
    journal_ok = _restore_delete_journal()
    if not journal_ok:
        logger.error(
            "БАЗА ОСТАЛАСЬ В РЕЖИМЕ WAL (%s): копия одного app.db может оказаться "
            "неполной. Закройте второй экземпляр программы и запустите снова.",
            DB_PATH,
        )
    with SessionLocal() as session:
        seeded = seed_reference_data(session)
    return {
        "migrations": applied,
        "seeded": seeded,
        "journal_ok": journal_ok,
        "journal_mode": journal_mode(),
    }


def check_integrity() -> list:
    """Проверить целостность БД. Пустой список — нарушений нет."""
    from app.db.migrations import foreign_key_check

    with engine.connect() as connection:
        return foreign_key_check(connection.connection.driver_connection)


def get_schema_version() -> int:
    """Версия схемы БД (PRAGMA user_version)."""
    from app.db.migrations import get_user_version

    with engine.connect() as connection:
        return get_user_version(connection.connection.driver_connection)
