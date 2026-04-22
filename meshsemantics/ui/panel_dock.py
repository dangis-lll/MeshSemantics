from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QTabWidget


class PanelDockWidget(QDockWidget):
    current_panel_changed = pyqtSignal(str)

    def __init__(self, label_panel, landmark_panel, mesh_doctor_panel, parent=None) -> None:
        super().__init__("Workbench", parent)
        self.setObjectName("panel-dock")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._floating_size = QSize(420, 760)
        self._panel_order = ("label", "landmark", "meshdoctor")

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("panel-tabs")
        self.tab_widget.addTab(label_panel, "Labels")
        self.tab_widget.addTab(landmark_panel, "Landmarks")
        self.tab_widget.addTab(mesh_doctor_panel, "Mesh Doctor")
        self.tab_widget.currentChanged.connect(self._emit_current_panel_changed)
        self.setWidget(self.tab_widget)

        self.topLevelChanged.connect(self._on_top_level_changed)

    def show_panel(self, panel: str) -> None:
        if panel not in self._panel_order:
            return
        index = self._panel_order.index(panel)
        self.show()
        self.raise_()
        self.tab_widget.setCurrentIndex(index)

    def current_panel(self) -> str:
        index = self.tab_widget.currentIndex()
        if 0 <= index < len(self._panel_order):
            return self._panel_order[index]
        return "label"

    def _emit_current_panel_changed(self, index: int) -> None:
        if 0 <= index < len(self._panel_order):
            self.current_panel_changed.emit(self._panel_order[index])

    def _on_top_level_changed(self, floating: bool) -> None:
        if floating:
            QTimer.singleShot(0, self._apply_floating_size)

    def _apply_floating_size(self) -> None:
        if self.isFloating():
            self.resize(self._floating_size)
