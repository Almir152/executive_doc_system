import sys
from PyQt6.QtWidgets import QApplication
from app.db.database import init_db
from app.ui.main_window import MainWindow

if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
