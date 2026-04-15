from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QDir, QObject, pyqtSignal
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (
    QDockWidget,
    QFrame,
    QLineEdit,
    QProgressBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from meshlabeler.core.file_io import FileIO


class PreloadManager(QObject):
    preload_done = pyqtSignal(str, object, object)
    preload_failed = pyqtSignal(str, str)

    def __init__(self, cache_limit: int = 20) -> None:
        super().__init__()
        self.cache_limit = max(1, int(cache_limit))
        self.cache: OrderedDict[str, tuple[object, object]] = OrderedDict()

    def cached(self, path: str):
        item = self.cache.get(path)
        if item is None:
            return None
        self.cache.move_to_end(path)
        return item

    def enqueue(self, path: str) -> None:
        if path in self.cache:
            self.preload_done.emit(path, *self.cache[path])
            return
        try:
            mesh, labels = FileIO.load_mesh(path)
            self.cache[path] = (mesh, labels)
            self.cache.move_to_end(path)
            while len(self.cache) > self.cache_limit:
                self.cache.popitem(last=False)
            self.preload_done.emit(path, mesh, labels)
        except Exception as exc:
            self.preload_failed.emit(path, str(exc))


class FilePanel(QDockWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, cache_limit: int = 20, parent=None) -> None:
        super().__init__("Files", parent)
        self.setObjectName("file-panel")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        panel = QFrame()
        panel.setProperty("panel", True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter .stl / .vtp")

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Files)
        self.model.setNameFilters(["*.stl", "*.vtp"])
        self.model.setNameFilterDisables(False)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._emit_selected)
        self.tree.clicked.connect(self._emit_selected)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        panel_layout.addWidget(self.search_edit)
        panel_layout.addWidget(self.tree, 1)
        panel_layout.addWidget(self.progress)
        layout.addWidget(panel)
        self.setWidget(content)

        self.search_edit.textChanged.connect(self._expand_matches)

        self.preloader = PreloadManager(cache_limit=cache_limit)

    def set_root_path(self, path: str) -> None:
        root = str(Path(path))
        index = self.model.setRootPath(root)
        self.tree.setRootIndex(index)

    def preload(self, path: str) -> None:
        self.progress.setVisible(True)
        self.preloader.enqueue(path)

    def stop(self) -> None:
        return

    def _emit_selected(self, index) -> None:
        path = self.model.filePath(index)
        if Path(path).suffix.lower() in FileIO.SUPPORTED_SUFFIXES:
            self.file_selected.emit(path)

    def _expand_matches(self, text: str) -> None:
        if not text:
            self.tree.collapseAll()
            return
        for row in range(self.model.rowCount(self.tree.rootIndex())):
            idx = self.model.index(row, 0, self.tree.rootIndex())
            name = self.model.fileName(idx).lower()
            self.tree.setRowHidden(row, self.tree.rootIndex(), text.lower() not in name)
