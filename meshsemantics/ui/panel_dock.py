from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QTabWidget


class PanelDockWidget(QDockWidget):
    current_panel_changed = pyqtSignal(str)

    def __init__(self, label_panel, landmark_panel, parent=None) -> None:
        super().__init__("Workbench", parent)
        self.setObjectName("panel-dock")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._floating_size = QSize(420, 760)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("panel-tabs")
        self.tab_widget.addTab(label_panel, "Labels")
        self.tab_widget.addTab(landmark_panel, "Landmarks")
        self.tab_widget.currentChanged.connect(self._emit_current_panel_changed)
        self.setWidget(self.tab_widget)

        self.topLevelChanged.connect(self._on_top_level_changed)

    def show_panel(self, panel: str) -> None:
        index = 0 if panel == "label" else 1
        self.show()
        self.raise_()
        self.tab_widget.setCurrentIndex(index)

    def current_panel(self) -> str:
        return "label" if self.tab_widget.currentIndex() == 0 else "landmark"

    def _emit_current_panel_changed(self, index: int) -> None:
        self.current_panel_changed.emit("label" if index == 0 else "landmark")

    def _on_top_level_changed(self, floating: bool) -> None:
        if floating:
            QTimer.singleShot(0, self._apply_floating_size)

    def _apply_floating_size(self) -> None:
        if self.isFloating():
            self.resize(self._floating_size)
