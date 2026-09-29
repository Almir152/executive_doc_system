"""Точка входа приложения.

Здесь и только здесь решается, что оператор увидит при сбое на старте.
Приложение собирается как оконное (--windowed, console=False), поэтому
необработанное исключение не приводит ни к какому сообщению на экране:
пользователь видит, что окно не открылось, и не знает причины.
"""

import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.config import setup_logging
from app.db.database import init_db
from app.ui.main_window import MainWindow

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
