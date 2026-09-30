"""Точка входа приложения.

Здесь и только здесь решается, что оператор увидит при сбое на старте.
Приложение собирается как оконное (--windowed, console=False), поэтому
необработанное исключение не приводит ни к какому сообщению на экране:
пользователь видит, что окно не открылось, и не знает причины.
"""

import logging
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.config import setup_logging
from app.db.database import init_db
from app.ui.main_window import MainWindow

CRASH_HINT = (
    "Работа программы продолжена. Если действие не получилось, повторите его "
    "и сообщите разработчику текст ошибки: подробности записаны в файл "
    "app.log рядом с базой."
)


def install_crash_handler() -> None:
    """Показывать необработанные ошибки вместо молчаливого закрытия окна.

    Приложение собрано как оконное (--windowed, console=False), поэтому
    необработанное исключение в слоте Qt закрывает программу без единого
    слова на экране: оператор видит только исчезновение окна и не может
    ни понять причину, ни переслать её разработчику. Именно так выглядел
    отказ при создании резервной копии (ТЗ п.106: ошибка видна оператору).
    """
    def report(exc_type, exc_value, traceback_object) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback_object)
            return
        logging.getLogger(__name__).error(
            "Необработанная ошибка",
            exc_info=(exc_type, exc_value, traceback_object),
        )
        box = QMessageBox(QMessageBox.Icon.Critical, "Ошибка программы", "")
        box.setText(
            f"{exc_type.__name__}: {exc_value}\n\n{CRASH_HINT}"
        )
        box.setInformativeText(
            "Это ошибка в самой программе, а не в ваших данных. "
            "Последнее действие не завершено."
        )
        box.exec()

    sys.excepthook = report


WAL_WARNING = (
    "Не удалось вернуть базе обычный режим работы.\n\n"
    "Сейчас она осталась в режиме WAL: незакрытые изменения лежат во "
    "вспомогательном файле рядом с базой, поэтому копия одного app.db "
    "получится неполной.\n\n"
    "Закройте второй экземпляр программы, внешний просмотрщик базы или "
    "антивирус, сканирующий каталог, и запустите программу снова.\n\n"
    "Подробности записаны в файл app.log рядом с базой."
)


def main() -> int:
    setup_logging()
    install_crash_handler()
    app = QApplication(sys.argv)

    try:
        report = init_db()
    except Exception as exc:  # noqa: BLE001 - оператор должен увидеть причину
        QMessageBox.critical(
            None,
            "Программа не может запуститься",
            "Не удалось подготовить базу данных.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Работа остановлена, чтобы не повредить данные. Подробности "
            "записаны в файл app.log рядом с базой.",
        )
        return 1

    if not report.get("journal_ok", True):
        # Не отказ: работать можно. Но молчать нельзя — копия базы рискована.
        QMessageBox.warning(None, "Предупреждение о базе данных", WAL_WARNING)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
