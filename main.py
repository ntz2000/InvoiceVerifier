import sys

from PyQt5.QtWidgets import QApplication

from app.main_window import MainWindow


def main() -> int:
    """启动桌面应用。"""
    app = QApplication(sys.argv)
    app.setApplicationName("InvoiceVerifier")

    window = MainWindow()
    window.show()

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
