from __future__ import annotations

import importlib
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QSurfaceFormat

from meshsemantics.ui.main_window import MainWindow


def _configure_vtk_surface_format() -> None:
    if not hasattr(QSurfaceFormat, "setDefaultFormat"):
        return
    try:
        module = importlib.import_module("vtkmodules.qt.QVTKOpenGLNativeWidget")
        widget_cls = getattr(module, "QVTKOpenGLNativeWidget", None)
        default_format = getattr(widget_cls, "defaultFormat", None)
        if callable(default_format):
            QSurfaceFormat.setDefaultFormat(default_format())
    except Exception:
        # Older or minimal VTK builds may omit the Qt OpenGL native widget.
        pass


def build_app() -> QApplication:
    for attr_name in ["AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"]:
        attr = getattr(Qt.ApplicationAttribute, attr_name, None)
        if attr is not None:
            QApplication.setAttribute(attr, True)
    _configure_vtk_surface_format()
    app = QApplication(sys.argv)
    app.setApplicationName("MeshSemantics")
    app.setOrganizationName("MeshSemantics")
    assets_dir = Path(__file__).resolve().parent / "assets"
    for icon_name in ("app.png", "app.ico"):
        app_icon_path = assets_dir / icon_name
        if app_icon_path.exists():
            app.setWindowIcon(QIcon(str(app_icon_path)))
            break
    app.setStyle("Fusion")
    return app


def main() -> int:
    app = build_app()
    window = MainWindow()
    window.show()
    return app.exec()
