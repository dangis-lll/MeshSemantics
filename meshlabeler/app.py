from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from meshlabeler.ui.main_window import MainWindow


def build_app() -> QApplication:
    for attr_name in ["AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"]:
        attr = getattr(Qt.ApplicationAttribute, attr_name, None)
        if attr is not None:
            QApplication.setAttribute(attr, True)
    app = QApplication(sys.argv)
    app.setApplicationName("MeshLabeler")
    app.setOrganizationName("MeshLabeler")
    app.setStyle("Fusion")
    return app


def main() -> int:
    app = build_app()
    window = MainWindow()
    window.show()
    return app.exec()
