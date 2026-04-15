from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QStatusBar, QToolBar

from meshlabeler.core.file_io import FileIO
from meshlabeler.core.interactor import MeshInteractor
from meshlabeler.core.label_engine import LabelEngine
from meshlabeler.core.settings import load_colormap, load_settings, save_colormap, save_settings
from meshlabeler.ui.file_panel import FilePanel
from meshlabeler.ui.label_panel import LabelPanel
from meshlabeler.ui.style import APP_QSS
from meshlabeler.ui.vedo_widget import VedoWidget


@dataclass
class HistoryRecord:
    labels_before: np.ndarray
    labels_after: np.ndarray
    ui_before: dict
    ui_after: dict
    dirty_before: bool
    dirty_after: bool


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.colormap = load_colormap()
        self.current_path: str | None = None
        self.is_dirty = False
        self.last_open_dir = self._resolve_initial_directory()
        self.undo_history: list[HistoryRecord] = []
        self.redo_history: list[HistoryRecord] = []

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
        self.file_panel.set_root_path(str(self.last_open_dir))

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
        save_action = QAction("Save", self)
        save_action.triggered.connect(self.save_current)
        undo_action = QAction("←", self)
        undo_action.setToolTip("Undo")
        undo_action.triggered.connect(self.undo)
        redo_action = QAction("→", self)
        redo_action.setToolTip("Redo")
        redo_action.triggered.connect(self.redo)

        for action in [open_file, open_dir, save_action, undo_action, redo_action]:
            toolbar.addAction(action)

        for action in (undo_action, redo_action):
            button = toolbar.widgetForAction(action)
            if button is not None:
                font = QFont(button.font())
                font.setPointSize(max(font.pointSize() + 4, 16))
                font.setBold(True)
                button.setFont(font)

    def _bind_signals(self) -> None:
        self.file_panel.file_selected.connect(self.load_mesh)

        self.label_panel.colormap_changed.connect(self._on_colormap_changed)
        self.label_panel.remap_requested.connect(self._remap_labels)
        self.label_panel.delete_requested.connect(self._delete_label)

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
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Mesh",
            str(self.last_open_dir),
            "Meshes (*.stl *.vtp)",
        )
        if file_path:
            self.load_mesh(file_path)

    def open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", str(self.last_open_dir))
        if folder:
            self._set_last_open_dir(folder)
            self.file_panel.set_root_path(folder)
            files = sorted(str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in FileIO.SUPPORTED_SUFFIXES)
            if files:
                self.load_mesh(files[0])

    @pyqtSlot(str)
    def load_mesh(self, file_path: str) -> None:
        if not self._prepare_for_model_switch(file_path):
            return
        self.current_path = file_path
        self._set_last_open_dir(Path(file_path).parent)
        self.file_panel.set_root_path(str(Path(file_path).parent), file_path)
        self.file_panel.progress.setVisible(True)
        try:
            mesh, labels = FileIO.load_mesh(file_path)
        except Exception as exc:
            self.file_panel.progress.setVisible(False)
            if file_path == self.current_path:
                QMessageBox.critical(self, "Load Failed", f"Failed to load mesh:\n{file_path}\n\n{exc}")
            return
        self._consume_loaded_mesh(file_path, mesh, labels)

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
        if not self._confirm_save_if_dirty():
            event.ignore()
            return
        self.settings["window_size"] = [int(self.width()), int(self.height())]
        self.settings["last_open_dir"] = str(self.last_open_dir)
        save_settings(self.settings)
        self.file_panel.stop()
        super().closeEvent(event)

    def save_current_vtp(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        default_path = Path(self.current_path or "mesh.vtp").with_suffix(".vtp")
        target, _ = QFileDialog.getSaveFileName(self, "Save VTP", str(default_path), "VTP (*.vtp)")
        return self._save_vtp_to_path(target)

    def save_current(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        default_base = Path(self.current_path or "mesh")
        default_path = str(default_base.with_suffix(".vtp"))
        target, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save",
            default_path,
            "VTP (*.vtp);;JSON (*.json);;STL (*.stl)",
        )
        if not target:
            return False
        suffix = Path(target).suffix.lower()
        if "JSON" in selected_filter and suffix != ".json":
            target = str(Path(target).with_suffix(".json"))
        elif "STL" in selected_filter and suffix != ".stl":
            target = str(Path(target).with_suffix(".stl"))
        elif "VTP" in selected_filter and suffix != ".vtp":
            target = str(Path(target).with_suffix(".vtp"))

        target_path = Path(target)
        suffix = target_path.suffix.lower()
        if suffix == ".json":
            self._set_last_open_dir(target_path.parent)
            FileIO.save_labels_json(target, self.label_engine.label_array)
            self.file_panel.refresh_entry_states()
            self.statusBar().showMessage(f"Saved JSON to {target}")
            return True
        if suffix == ".stl":
            self._set_last_open_dir(target_path.parent)
            files = FileIO.save_stl_per_label(
                self.vedo_widget.mesh,
                self.label_engine,
                target_path.parent,
                save_unlabeled=bool(self.settings.get("save_unlabeled_stl", False)),
            )
            self.statusBar().showMessage(f"Exported {len(files)} STL files")
            return True
        return self._save_vtp_to_path(str(target_path.with_suffix(".vtp")))

    def export_stl_per_label(self) -> None:
        if self.vedo_widget.mesh is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "Export STL Files", str(self.last_open_dir))
        if not directory:
            return
        self._set_last_open_dir(directory)
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
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save JSON",
            str(default_path),
            "JSON (*.json)",
        )
        if not target:
            return
        self._set_last_open_dir(Path(target).parent)
        FileIO.save_labels_json(target, self.label_engine.label_array)
        self.file_panel.refresh_entry_states()
        self.statusBar().showMessage(f"Saved JSON to {target}")

    def undo(self) -> None:
        if not self.undo_history:
            return
        record = self.undo_history.pop()
        self.redo_history.append(record)
        self._restore_history_state(record.labels_before, record.ui_before, record.dirty_before)

    def redo(self) -> None:
        if not self.redo_history:
            return
        record = self.redo_history.pop()
        self.undo_history.append(record)
        self._restore_history_state(record.labels_after, record.ui_after, record.dirty_after)

    def _apply_cells(self, cell_ids) -> None:
        before_labels, before_ui, dirty_before = self._capture_history_state()
        if self.label_engine.assign(cell_ids, self.label_panel.current_label()):
            self.is_dirty = True
            self._push_history(before_labels, before_ui, dirty_before)
            self._update_mesh_view()
            self.statusBar().showMessage(f"Assigned label {self.label_panel.current_label()} to {len(cell_ids)} cells")
        self.interactor.clear_preview()

    def _remap_labels(self, source: int, target: int) -> None:
        before_labels, before_ui, dirty_before = self._capture_history_state()
        if self.label_engine.remap_label(source, target):
            self.is_dirty = True
            self._push_history(before_labels, before_ui, dirty_before)
            self._update_mesh_view()
            self.statusBar().showMessage(f"Remapped label {source} to {target}")

    def _delete_label(self, label: int) -> None:
        if label <= 0:
            return
        before_labels, before_ui, dirty_before = self._capture_history_state()
        has_cells = self.vedo_widget.mesh is not None and label in set(self.label_engine.unique_labels())
        if has_cells:
            reply = QMessageBox.question(
                self,
                "Delete Label",
                f"Delete label {label} and remap its cells to 0?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.label_engine.remap_label(label, 0)
            self.is_dirty = True
        if not self.label_panel.remove_label(label):
            return
        self._push_history(before_labels, before_ui, dirty_before)
        self._update_mesh_view()
        self.statusBar().showMessage(f"Deleted label {label}")

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
        self.label_panel.ensure_labels(self.label_engine.unique_labels())
        self.vedo_widget.set_mesh(mesh, self.label_engine.label_array, self.colormap)
        self.file_panel.progress.setVisible(False)
        self.is_dirty = False
        self._clear_history()
        self.statusBar().showMessage(f"Loaded {Path(file_path).name}")

    def _prepare_for_model_switch(self, next_path: str) -> bool:
        if self.current_path is None:
            return True
        try:
            current = Path(self.current_path).resolve()
            upcoming = Path(next_path).resolve()
        except Exception:
            current = Path(self.current_path)
            upcoming = Path(next_path)
        if current == upcoming:
            return True
        return self._confirm_save_if_dirty()

    def _confirm_save_if_dirty(self) -> bool:
        if not self.is_dirty or self.vedo_widget.mesh is None:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The current model has unsaved changes. Save as VTP before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self.save_current_vtp()
        if reply == QMessageBox.StandardButton.Discard:
            return True
        return False

    def _resolve_initial_directory(self) -> Path:
        saved_dir = self.settings.get("last_open_dir", "")
        if saved_dir:
            candidate = Path(saved_dir).expanduser()
            if candidate.exists():
                return candidate
        return self._application_directory()

    def _application_directory(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]

    def _set_last_open_dir(self, directory: str | Path) -> None:
        path = Path(directory).expanduser()
        if not path.exists():
            return
        resolved = path.resolve()
        self.last_open_dir = resolved
        self.settings["last_open_dir"] = str(resolved)
        save_settings(self.settings)

    def _save_vtp_to_path(self, target: str) -> bool:
        if not target or self.vedo_widget.mesh is None:
            return False
        self._set_last_open_dir(Path(target).parent)
        FileIO.save_vtp(self.vedo_widget.mesh, target, self.label_engine.label_array)
        self.file_panel.refresh_entry_states()
        self.is_dirty = False
        self.statusBar().showMessage(f"Saved VTP to {target}")
        return True

    def _capture_history_state(self) -> tuple[np.ndarray, dict, bool]:
        return (
            self.label_engine.label_array.copy(),
            self.label_panel.snapshot_state(),
            bool(self.is_dirty),
        )

    def _push_history(self, before_labels: np.ndarray, before_ui: dict, dirty_before: bool) -> None:
        record = HistoryRecord(
            labels_before=np.asarray(before_labels, dtype=np.int32).copy(),
            labels_after=self.label_engine.label_array.copy(),
            ui_before=before_ui,
            ui_after=self.label_panel.snapshot_state(),
            dirty_before=dirty_before,
            dirty_after=bool(self.is_dirty),
        )
        self.undo_history.append(record)
        undo_limit = max(1, int(self.settings.get("undo_limit", 50)))
        if len(self.undo_history) > undo_limit:
            self.undo_history.pop(0)
        self.redo_history.clear()

    def _restore_history_state(self, labels: np.ndarray, ui_state: dict, dirty: bool) -> None:
        self.label_engine.label_array = np.asarray(labels, dtype=np.int32).copy()
        self.label_panel.restore_state(ui_state)
        self.colormap = self.label_panel.colormap()
        save_colormap(self.colormap)
        self.vedo_widget.set_colormap(self.colormap)
        self._update_mesh_view()
        self.is_dirty = bool(dirty)

    def _update_mesh_view(self) -> None:
        self.vedo_widget.update_labels(self.label_engine.label_array)
        self._refresh_stats(self.label_engine.size)

    def _clear_history(self) -> None:
        self.undo_history.clear()
        self.redo_history.clear()
