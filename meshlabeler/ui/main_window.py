from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QStatusBar, QToolBar

from meshlabeler.core.file_io import FileIO
from meshlabeler.core.interactor import MeshInteractor
from meshlabeler.core.label_engine import LabelEngine
from meshlabeler.core.settings import load_colormap, load_settings, save_colormap
from meshlabeler.ui.file_panel import FilePanel
from meshlabeler.ui.label_panel import LabelPanel
from meshlabeler.ui.style import APP_QSS
from meshlabeler.ui.vedo_widget import VedoWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.colormap = load_colormap()
        self.current_path: str | None = None

        self.label_engine = LabelEngine(undo_limit=int(self.settings.get("undo_limit", 50)))
        self.vedo_widget = VedoWidget()
        self.file_panel = FilePanel(cache_limit=int(self.settings.get("cache_limit", 20)))
        self.label_panel = LabelPanel(self.colormap, max_label=int(self.settings.get("max_label", 255)))
        self.interactor = MeshInteractor(self.vedo_widget, self.settings, self)

        self._configure_window()
        self._build_toolbar()
        self._bind_signals()
        self._bind_shortcuts()

    def _configure_window(self) -> None:
        self.setWindowTitle("MeshLabeler")
        width, height = self.settings.get("window_size", [1560, 980])
        self.resize(int(width), int(height))
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_QSS)
        self.setCentralWidget(self.vedo_widget)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.file_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.label_panel)

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_file = QAction("Open File", self)
        open_file.triggered.connect(self.open_file_dialog)
        open_dir = QAction("Open Folder", self)
        open_dir.triggered.connect(self.open_folder_dialog)
        save_vtp = QAction("Save VTP", self)
        save_vtp.triggered.connect(self.save_current_vtp)
        save_json = QAction("Save JSON", self)
        save_json.triggered.connect(self.save_current_json)
        save_stl = QAction("Export STL", self)
        save_stl.triggered.connect(self.export_stl_per_label)
        undo_action = QAction("Undo", self)
        undo_action.triggered.connect(self.undo)

        for action in [open_file, open_dir, save_vtp, save_json, save_stl, undo_action]:
            toolbar.addAction(action)

    def _bind_signals(self) -> None:
        self.file_panel.file_selected.connect(self.load_mesh)
        self.file_panel.preloader.preload_done.connect(self._on_preload_done)
        self.file_panel.preloader.preload_failed.connect(self._on_preload_failed)

        self.label_panel.colormap_changed.connect(self._on_colormap_changed)
        self.label_panel.remap_requested.connect(self._remap_labels)

        self.vedo_widget.mesh_loaded.connect(self._refresh_stats)

        self.interactor.mode_changed.connect(self._on_mode_changed)
        self.interactor.preview_changed.connect(self.vedo_widget.preview_cells)
        self.interactor.control_points_changed.connect(self.vedo_widget.set_control_points)
        self.interactor.apply_requested.connect(self._apply_cells)
        self.interactor.message.connect(self.statusBar().showMessage)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("S"), self, activated=self.interactor.begin_spline)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self.interactor.confirm_preview)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, activated=self.interactor.confirm_preview)
        QShortcut(QKeySequence("E"), self, activated=self.interactor.apply_preview)
        QShortcut(QKeySequence("C"), self, activated=self.interactor.clear_preview)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)

    def open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Mesh", "", "Meshes (*.stl *.vtp)")
        if file_path:
            self.load_mesh(file_path)

    def open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", "")
        if folder:
            self.file_panel.set_root_path(folder)
            files = sorted(str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in FileIO.SUPPORTED_SUFFIXES)
            if files:
                self.load_mesh(files[0])

    @pyqtSlot(str)
    def load_mesh(self, file_path: str) -> None:
        self.current_path = file_path
        self.file_panel.progress.setVisible(True)
        cached = self.file_panel.preloader.cached(file_path)
        if cached is not None:
            mesh, labels = cached
            self._consume_loaded_mesh(file_path, mesh, labels)
            return
        self.file_panel.preload(file_path)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in FileIO.SUPPORTED_SUFFIXES:
                self.load_mesh(path)
                break

    def closeEvent(self, event) -> None:
        self.file_panel.stop()
        super().closeEvent(event)

    def save_current_vtp(self) -> None:
        if self.vedo_widget.mesh is None:
            return
        default_path = Path(self.current_path or "mesh.vtp").with_suffix(".vtp")
        target, _ = QFileDialog.getSaveFileName(self, "Save VTP", str(default_path), "VTP (*.vtp)")
        if not target:
            return
        FileIO.save_vtp(self.vedo_widget.mesh, target, self.label_engine.label_array)
        self.statusBar().showMessage(f"Saved VTP to {target}")

    def export_stl_per_label(self) -> None:
        if self.vedo_widget.mesh is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "Export STL Files", "")
        if not directory:
            return
        files = FileIO.save_stl_per_label(
            self.vedo_widget.mesh,
            self.label_engine,
            directory,
            save_unlabeled=bool(self.settings.get("save_unlabeled_stl", False)),
        )
        self.statusBar().showMessage(f"Exported {len(files)} STL files")

    def save_current_json(self) -> None:
        if self.vedo_widget.mesh is None:
            return
        default_path = Path(self.current_path or "mesh.json").with_suffix(".json")
        target, _ = QFileDialog.getSaveFileName(self, "Save JSON", str(default_path), "JSON (*.json)")
        if not target:
            return
        FileIO.save_labels_json(target, self.label_engine.label_array)
        self.statusBar().showMessage(f"Saved JSON to {target}")

    def undo(self) -> None:
        if self.label_engine.undo():
            self.vedo_widget.update_labels(self.label_engine.label_array)
            self._refresh_stats(self.label_engine.size)

    def redo(self) -> None:
        if self.label_engine.redo():
            self.vedo_widget.update_labels(self.label_engine.label_array)
            self._refresh_stats(self.label_engine.size)

    def _apply_cells(self, cell_ids) -> None:
        if self.label_engine.assign(cell_ids, self.label_panel.current_label()):
            self.vedo_widget.update_labels(self.label_engine.label_array)
            self._refresh_stats(self.label_engine.size)
            self.statusBar().showMessage(f"Assigned label {self.label_panel.current_label()} to {len(cell_ids)} cells")
        self.interactor.clear_preview()

    def _remap_labels(self, source: int, target: int) -> None:
        if self.label_engine.remap_label(source, target):
            self.vedo_widget.update_labels(self.label_engine.label_array)
            self._refresh_stats(self.label_engine.size)
            self.statusBar().showMessage(f"Remapped label {source} to {target}")

    def _on_colormap_changed(self, colormap: dict) -> None:
        self.colormap = colormap
        save_colormap(colormap)
        self.vedo_widget.set_colormap(colormap)

    def _on_mode_changed(self, mode: str) -> None:
        self.statusBar().showMessage(f"Mode: {mode}")

    def _refresh_stats(self, total_cells: int) -> None:
        labeled = int((self.label_engine.label_array != 0).sum()) if self.label_engine.size else 0
        self.label_panel.refresh_stats(total_cells, labeled)

    def _consume_loaded_mesh(self, file_path: str, mesh, labels) -> None:
        mesh.filename = file_path
        self.label_engine.reset(labels)
        self.vedo_widget.set_mesh(mesh, self.label_engine.label_array, self.colormap)
        self.file_panel.progress.setVisible(False)
        self.statusBar().showMessage(f"Loaded {Path(file_path).name}")

    def _on_preload_done(self, path: str, mesh, labels) -> None:
        if path == self.current_path:
            self._consume_loaded_mesh(path, mesh, labels)

    def _on_preload_failed(self, path: str, error: str) -> None:
        self.file_panel.progress.setVisible(False)
        if path != self.current_path:
            return
        QMessageBox.critical(self, "Load Failed", f"Failed to load mesh:\n{path}\n\n{error}")
