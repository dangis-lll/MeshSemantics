from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QSurfaceFormat

from meshsemantics.ui.main_window import MainWindow


def build_app() -> QApplication:
    for attr_name in ["AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"]:
        attr = getattr(Qt.ApplicationAttribute, attr_name, None)
        if attr is not None:
            QApplication.setAttribute(attr, True)
    if hasattr(QSurfaceFormat, "setDefaultFormat"):
        try:
            from vtkmodules.qt.QVTKOpenGLNativeWidget import QVTKOpenGLNativeWidget

            QSurfaceFormat.setDefaultFormat(QVTKOpenGLNativeWidget.defaultFormat())
        except ImportError:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("MeshSemantics")
    app.setOrganizationName("MeshSemantics")
    app_icon_path = Path(__file__).resolve().parent / "assets" / "app.ico"
    if app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_icon_path)))
    app.setStyle("Fusion")
    return app


def main() -> int:
    app = build_app()
    window = MainWindow()
    window.show()
    return app.exec()
