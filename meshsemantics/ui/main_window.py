from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStatusBar,
    QToolBar,
    QToolButton,
    QWidget,
)

from meshsemantics.core.file_io import FileIO
from meshsemantics.core.interactor import MeshInteractor
from meshsemantics.core.label_engine import LabelEngine
from meshsemantics.core.project_dataset import (
    ProjectDataset,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_UNLABELED,
    build_relative_status_index,
    build_work_path_status_index,
    compute_next_pending_path,
    normalize_path,
    scan_project_dataset,
)
from meshsemantics.core.project_status_store import load_project_statuses, save_project_statuses
from meshsemantics.core.settings import load_colormap, load_settings, save_colormap, save_settings
from meshsemantics.ui.file_panel import FilePanel
from meshsemantics.ui.label_panel import LabelPanel
from meshsemantics.ui.style import APP_QSS
from meshsemantics.ui.vedo_widget import VedoWidget


@dataclass
class HistoryRecord:
    labels_before: np.ndarray
    labels_after: np.ndarray
    ui_before: dict
    ui_after: dict
    dirty_before: bool
    dirty_after: bool


class ProjectScanWorker(QObject):
    finished = pyqtSignal(int, object)
    progress = pyqtSignal(int, int, str)

    def __init__(
        self,
        request_id: int,
        folder: str,
        preferred_path: str | None,
        last_file: str | None,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.folder = folder
        self.preferred_path = preferred_path
        self.last_file = last_file

    @pyqtSlot()
    def run(self) -> None:
        status_by_relative_path = load_project_statuses(self.folder)
        dataset = scan_project_dataset(
            self.folder,
            last_file=self.last_file,
            current_file=self.preferred_path,
            status_by_relative_path=status_by_relative_path,
            progress_callback=self._emit_progress,
        )
        self.finished.emit(
            self.request_id,
            {
                "dataset": dataset,
                "status_by_relative_path": status_by_relative_path,
            },
        )

    def _emit_progress(self, scanned_files: int, latest_path: str) -> None:
        self.progress.emit(self.request_id, scanned_files, latest_path)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        had_legacy_status_map = self.settings.pop("status_by_file", None) is not None
        self.colormap = load_colormap()
        self.current_path: str | None = None
        self.project_dataset: ProjectDataset | None = None
        self.is_dirty = False
        self.last_open_dir = self._resolve_initial_directory()
        self.undo_history: list[HistoryRecord] = []
        self.redo_history: list[HistoryRecord] = []
        self._active_scan_request = 0
        self._active_scan_auto_load = True
        self._scan_thread: QThread | None = None
        self._scan_worker: ProjectScanWorker | None = None
        self._project_status_root: str | None = None
        self._project_status_by_relative_path: dict[str, str] = {}
        self._project_status_by_work_path: dict[str, str] = {}
        self._project_status_save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meshsemantics-status")
        self._project_status_save_future: Future | None = None
        self._project_status_save_lock = threading.Lock()
        self._pending_project_status_save: tuple[str, dict[str, str]] | None = None
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(300)
        self._settings_save_timer.timeout.connect(self._flush_settings)
        self._project_status_save_timer = QTimer(self)
        self._project_status_save_timer.setSingleShot(True)
        self._project_status_save_timer.setInterval(300)
        self._project_status_save_timer.timeout.connect(self._flush_project_statuses)

        self.label_engine = LabelEngine(undo_limit=int(self.settings.get("undo_limit", 50)))
        self.vedo_widget = VedoWidget()
        self.file_panel = FilePanel(cache_limit=int(self.settings.get("cache_limit", 20)))
        self.label_panel = LabelPanel(self.colormap, max_label=int(self.settings.get("max_label", 255)))
        self.interactor = MeshInteractor(self.vedo_widget, self.settings, self)

        self._configure_window()
        self._build_toolbar()
        self._bind_signals()
        self._bind_shortcuts()
        self.file_panel.set_project(None)
        self._refresh_completion_action()
        if had_legacy_status_map:
            self._schedule_settings_save()
        self._restore_last_project()

    def _configure_window(self) -> None:
        self.setWindowTitle("MeshSemantics")
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
        toolbar.setIconSize(QSize(28, 28))
        self.addToolBar(toolbar)

        open_file = QAction("Open File", self)
        open_file.triggered.connect(self.open_file_dialog)
        open_dir = QAction("Open Folder", self)
        open_dir.triggered.connect(self.open_folder_dialog)
        save_action = QAction("Save As", self)
        save_action.triggered.connect(self.save_current)
        undo_action = QAction(QIcon(str(self._asset_path("previous_step.png"))), "", self)
        undo_action.setToolTip("Undo")
        undo_action.triggered.connect(self.undo)
        redo_action = QAction(QIcon(str(self._asset_path("next_step.png"))), "", self)
        redo_action.setToolTip("Redo")
        redo_action.triggered.connect(self.redo)

        for action in [open_file, open_dir, save_action]:
            toolbar.addAction(action)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        for action in [undo_action, redo_action]:
            toolbar.addAction(action)

        self._sync_toolbar_image_button_size(toolbar, save_action, undo_action, self._asset_path("previous_step.png"))
        self._sync_toolbar_image_button_size(toolbar, save_action, redo_action, self._asset_path("next_step.png"))
        self._style_toolbar_icon_button(toolbar, undo_action, "undo-button")
        self._style_toolbar_icon_button(toolbar, redo_action, "redo-button")

    def _bind_signals(self) -> None:
        self.file_panel.open_requested.connect(self.load_mesh)
        self.file_panel.next_todo_requested.connect(self.open_next_pending)

        self.label_panel.colormap_changed.connect(self._on_colormap_changed)
        self.label_panel.remap_requested.connect(self._remap_labels)
        self.label_panel.delete_requested.connect(self._delete_label)
        self.label_panel.completion_toggle_requested.connect(self.toggle_task_completed)
        self.label_panel.quick_save_requested.connect(self.quick_save_current)

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
        QShortcut(QKeySequence("M"), self, activated=self.toggle_task_completed)
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
            self.open_project(Path(file_path).parent, preferred_path=file_path, auto_load=True)

    def open_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", str(self.last_open_dir))
        if folder:
            self.open_project(folder, auto_load=False)

    def open_project(self, folder: str | Path, preferred_path: str | Path | None = None, auto_load: bool = True) -> None:
        normalized_folder = normalize_path(folder)
        if normalized_folder is None:
            return
        self._flush_project_statuses()
        self._set_last_open_dir(normalized_folder)
        last_file = normalize_path(preferred_path) or self._last_file_for_folder(normalized_folder)
        self._active_scan_request += 1
        self._active_scan_auto_load = auto_load
        request_id = self._active_scan_request
        self.file_panel.set_busy(True, "Scanning folder...")

        self._scan_thread = QThread(self)
        self._scan_worker = ProjectScanWorker(
            request_id=request_id,
            folder=normalized_folder,
            preferred_path=normalize_path(preferred_path),
            last_file=last_file,
        )
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.progress.connect(self._handle_project_scan_progress)
        self._scan_worker.finished.connect(self._handle_project_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.start()

    @pyqtSlot(int, object)
    def _handle_project_scan_finished(self, request_id: int, dataset_obj: object) -> None:
        if request_id != self._active_scan_request:
            return
        payload = dataset_obj if isinstance(dataset_obj, dict) else {}
        dataset = payload.get("dataset") if isinstance(payload.get("dataset"), ProjectDataset) else None
        status_by_relative_path = payload.get("status_by_relative_path", {})
        if not isinstance(status_by_relative_path, dict):
            status_by_relative_path = {}

        self.project_dataset = dataset
        self._project_status_root = dataset.root_path if dataset is not None else None
        self._project_status_by_relative_path = dict(status_by_relative_path)
        self._project_status_by_work_path = build_work_path_status_index(dataset, self._project_status_by_relative_path)
        self.file_panel.set_busy(False)
        self.file_panel.set_project(dataset)
        self._refresh_completion_action()

        if dataset is None or not dataset.entries:
            self.statusBar().showMessage("No meshes found in folder")
            return

        self._persist_project_statuses()
        self.statusBar().showMessage(f"Loaded project with {len(dataset.entries)} mesh tasks")

        suggested_path = dataset.suggested_path
        if not self._active_scan_auto_load or not suggested_path:
            return
        if self.current_path and normalize_path(self.current_path) == normalize_path(suggested_path) and self.vedo_widget.mesh is not None:
            return
        self.load_mesh(suggested_path)

    @pyqtSlot(int, int, str)
    def _handle_project_scan_progress(self, request_id: int, scanned_files: int, latest_path: str) -> None:
        if request_id != self._active_scan_request:
            return
        if scanned_files <= 0:
            self.file_panel.set_busy(True, "Scanning folder...")
            return
        message = f"Scanning folder... {scanned_files} meshes"
        if latest_path:
            message = f"{message} | {latest_path}"
        self.file_panel.set_busy(True, message)
        self.statusBar().showMessage(message)

    def open_next_pending(self) -> None:
        if self.project_dataset is None or not self.project_dataset.next_pending_path:
            self.statusBar().showMessage("No pending meshes in the current folder")
            return
        self.load_mesh(self.project_dataset.next_pending_path)

    def toggle_task_completed(self) -> None:
        if self.current_path is None:
            return
        current_status = self._current_entry_status()
        next_status = STATUS_IN_PROGRESS if current_status == STATUS_COMPLETED else STATUS_COMPLETED
        self._set_current_status(next_status)
        if next_status == STATUS_COMPLETED:
            self.statusBar().showMessage("Task marked as completed")
        else:
            self.statusBar().showMessage("Task reopened and set to in progress")

    @pyqtSlot(str)
    def load_mesh(self, file_path: str) -> None:
        normalized_path = normalize_path(file_path)
        if normalized_path is None:
            return
        if not self._prepare_for_model_switch(normalized_path):
            self.file_panel.set_current_path(self.current_path)
            return

        previous_path = self.current_path
        previous_project = self.project_dataset
        self._set_last_open_dir(Path(normalized_path).parent)
        self._remember_last_file(self._project_root_or_parent(normalized_path), normalized_path)

        if self.project_dataset is not None:
            self.project_dataset = ProjectDataset(
                root_path=self.project_dataset.root_path,
                entries=self.project_dataset.entries,
                current_path=normalized_path,
                next_pending_path=self.project_dataset.next_pending_path,
                suggested_path=normalized_path,
            )
            self.file_panel.set_current_path(normalized_path)

        self.file_panel.set_busy(True, "Loading mesh...")
        try:
            mesh, labels = FileIO.load_mesh(normalized_path)
        except Exception as exc:
            self.file_panel.set_busy(False)
            self._record_status(normalized_path, STATUS_FAILED)
            self._project_status_by_work_path[normalized_path] = STATUS_FAILED
            self.project_dataset = previous_project
            self.current_path = previous_path
            self.file_panel.set_project(self.project_dataset)
            QMessageBox.critical(self, "Load Failed", f"Failed to load mesh:\n{normalized_path}\n\n{exc}")
            return

        self.current_path = normalized_path
        self._consume_loaded_mesh(normalized_path, mesh, labels)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in FileIO.SUPPORTED_SUFFIXES:
                self.open_project(Path(path).parent, preferred_path=path, auto_load=True)
                break

    def closeEvent(self, event) -> None:
        if not self._confirm_save_if_dirty():
            event.ignore()
            return
        self.settings["window_size"] = [int(self.width()), int(self.height())]
        self.settings["last_open_dir"] = str(self.last_open_dir)
        self._persist_project_statuses()
        self._flush_project_statuses(block=True)
        self._flush_settings()
        self.file_panel.stop()
        self._project_status_save_executor.shutdown(wait=True)
        super().closeEvent(event)

    def quick_save_current(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        return self._save_vtp_to_path(str(self._default_vtp_target()))

    def save_current(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        default_base = Path(self.current_path or self._default_vtp_target())
        default_path = str(default_base.with_suffix(".vtp"))
        target, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save",
            default_path,
            "VTP (*.vtp);;JSON (*.json);;STL (*.stl)",
        )
        if not target:
            return False

        target_path = Path(target)
        suffix = target_path.suffix.lower()
        if "JSON" in selected_filter and suffix != ".json":
            target_path = target_path.with_suffix(".json")
        elif "STL" in selected_filter and suffix != ".stl":
            target_path = target_path.with_suffix(".stl")
        elif "VTP" in selected_filter and suffix != ".vtp":
            target_path = target_path.with_suffix(".vtp")

        suffix = target_path.suffix.lower()
        if suffix == ".json":
            self._set_last_open_dir(target_path.parent)
            FileIO.save_labels_json(target_path, self.label_engine.label_array)
            self.statusBar().showMessage(f"Saved JSON to {target_path}")
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
        return self._save_vtp_to_path(str(target_path))

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
            self._update_current_status_after_edit()
            self.statusBar().showMessage(f"Assigned label {self.label_panel.current_label()} to {len(cell_ids)} cells")
        self.interactor.clear_preview(emit_preview=False)

    def _remap_labels(self, source: int, target: int) -> None:
        before_labels, before_ui, dirty_before = self._capture_history_state()
        if self.label_engine.remap_label(source, target):
            self.is_dirty = True
            self._push_history(before_labels, before_ui, dirty_before)
            self._update_mesh_view()
            self._update_current_status_after_edit()
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
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.label_engine.remap_label(label, 0)
            self.is_dirty = True
        if not self.label_panel.remove_label(label):
            return
        self._push_history(before_labels, before_ui, dirty_before)
        self._update_mesh_view()
        self._update_current_status_after_edit()
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
        self.file_panel.set_busy(False)
        self.is_dirty = False
        self._clear_history()

        status = self._status_for_loaded_file(file_path)
        self._set_current_status(status, persist_only=True)
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
            "The current task has unsaved changes. Save to the task VTP before switching?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self.quick_save_current()
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
        self._schedule_settings_save()

    def _save_vtp_to_path(self, target: str) -> bool:
        if not target or self.vedo_widget.mesh is None:
            return False
        target_path = Path(target).with_suffix(".vtp")
        self._set_last_open_dir(target_path.parent)
        FileIO.save_vtp(self.vedo_widget.mesh, target_path, self.label_engine.label_array)
        normalized_target = normalize_path(target_path)
        if normalized_target is None:
            return False

        self.current_path = normalized_target
        self.vedo_widget.mesh.filename = normalized_target
        self.is_dirty = False
        status = self._current_entry_status()
        if status == STATUS_FAILED:
            status = self._base_status_for_work(normalized_target)
        self._record_status(normalized_target, status)
        self._project_status_by_work_path[normalized_target] = status
        project_root = self._project_root_or_parent(normalized_target)
        self._remember_last_file(project_root, normalized_target)

        rescan_root = self._rescan_root_for_target(target_path)
        self.open_project(rescan_root, preferred_path=normalized_target, auto_load=False)
        self.statusBar().showMessage(f"Saved VTP to {normalized_target}")
        return True

    def _default_vtp_target(self) -> Path:
        if self.current_path:
            return Path(self.current_path).with_suffix(".vtp")
        return Path(self.last_open_dir) / "mesh.vtp"

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
        self._update_current_status_after_edit()

    def _update_mesh_view(self) -> None:
        self.vedo_widget.update_labels(self.label_engine.label_array)
        self._refresh_stats(self.label_engine.size)

    def _clear_history(self) -> None:
        self.undo_history.clear()
        self.redo_history.clear()

    def _base_status_for_work(self, file_path: str | Path) -> str:
        path = Path(str(file_path))
        values = np.asarray(self.label_engine.label_array, dtype=np.int32).reshape(-1)
        if path.suffix.lower() == ".vtp" or np.count_nonzero(values) > 0:
            return STATUS_IN_PROGRESS
        return STATUS_UNLABELED

    def _status_for_loaded_file(self, file_path: str) -> str:
        current_status = self._current_entry_status(file_path)
        if current_status and current_status != STATUS_FAILED:
            return current_status
        return self._base_status_for_work(file_path)

    def _update_current_status_after_edit(self) -> None:
        if self.current_path is None:
            return
        current_status = self._current_entry_status()
        if current_status == STATUS_COMPLETED:
            self._set_current_status(STATUS_IN_PROGRESS)
            return
        self._set_current_status(self._base_status_for_work(self.current_path))

    def _record_status(self, file_path: str | Path, status: str) -> None:
        normalized = normalize_path(file_path)
        if normalized is None:
            return
        relative_path = self._relative_status_key(normalized)
        if relative_path is None:
            return
        self._project_status_by_relative_path[relative_path] = status
        self._schedule_project_status_save()

    def _remember_last_file(self, folder: str | Path, file_path: str | Path) -> None:
        normalized_folder = normalize_path(folder)
        normalized_file = normalize_path(file_path)
        if normalized_folder is None or normalized_file is None:
            return
        mapping = self.settings.get("last_file_by_folder")
        if not isinstance(mapping, dict):
            mapping = {}
        mapping[normalized_folder] = normalized_file
        self.settings["last_file_by_folder"] = mapping
        self._schedule_settings_save()

    def _last_file_for_folder(self, folder: str | Path) -> str | None:
        normalized_folder = normalize_path(folder)
        if normalized_folder is None:
            return None
        mapping = self.settings.get("last_file_by_folder", {})
        return normalize_path(mapping.get(normalized_folder))

    def _persist_project_statuses(self) -> None:
        if self.project_dataset is None:
            return
        self._project_status_root = self.project_dataset.root_path
        self._project_status_by_relative_path = build_relative_status_index(self.project_dataset)
        self._project_status_by_work_path = build_work_path_status_index(self.project_dataset, self._project_status_by_relative_path)
        self._schedule_project_status_save()

    def _current_entry_status(self, file_path: str | None = None) -> str | None:
        target = normalize_path(file_path or self.current_path)
        if target is None:
            return None
        status = self._project_status_by_work_path.get(target)
        if status:
            return status
        relative_path = self._relative_status_key(target)
        if relative_path is None:
            return None
        return self._project_status_by_relative_path.get(relative_path)

    def _set_current_status(self, status: str, persist_only: bool = False) -> None:
        if self.current_path is None:
            return
        current_status = self._current_entry_status()
        if current_status == status:
            self._refresh_completion_action()
            return
        self._record_status(self.current_path, status)
        self._project_status_by_work_path[self.current_path] = status
        if self.project_dataset is not None:
            next_pending_path = compute_next_pending_path(
                self.project_dataset,
                self._project_status_by_work_path,
                self.current_path,
            )
            self.project_dataset = ProjectDataset(
                root_path=self.project_dataset.root_path,
                entries=self.project_dataset.entries,
                current_path=self.current_path,
                next_pending_path=next_pending_path,
                suggested_path=self.project_dataset.suggested_path,
            )
            self.file_panel.update_status(self.current_path, status, next_pending_path)
            self.file_panel.set_current_path(self.current_path)
        self._refresh_completion_action()
        if persist_only:
            return

    def _refresh_completion_action(self) -> None:
        if not hasattr(self, "label_panel"):
            return
        current_status = self._current_entry_status()
        is_completed = current_status == STATUS_COMPLETED
        self.label_panel.set_completion_state(is_completed)
        enabled = self.current_path is not None
        self.label_panel.complete_checkbox.setEnabled(enabled)
        self.label_panel.quick_save_button.setEnabled(enabled)

    def _restore_last_project(self) -> None:
        saved_dir = str(self.settings.get("last_open_dir", "")).strip()
        if not saved_dir:
            return
        initial_dir = Path(saved_dir).expanduser()
        if initial_dir.exists():
            self.open_project(initial_dir, auto_load=False)

    def _asset_path(self, filename: str) -> Path:
        return Path(__file__).resolve().parents[1] / "assets" / filename

    def _style_toolbar_icon_button(self, toolbar: QToolBar, action: QAction, object_name: str) -> None:
        button = toolbar.widgetForAction(action)
        if not isinstance(button, QToolButton):
            return
        button.setObjectName(object_name)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(False)
        button.setText("")

    def _sync_toolbar_image_button_size(
        self,
        toolbar: QToolBar,
        reference_action: QAction,
        image_action: QAction,
        image_path: Path,
    ) -> None:
        reference_button = toolbar.widgetForAction(reference_action)
        image_button = toolbar.widgetForAction(image_action)
        if not isinstance(reference_button, QToolButton) or not isinstance(image_button, QToolButton):
            return

        button_height = max(reference_button.sizeHint().height(), reference_button.height(), 1)
        width = max(1, round(button_height * 119 / 64))
        image_button.setFixedSize(width, button_height)
        image_button.setIconSize(QSize(width, button_height))
        image_url = image_path.as_posix()
        image_button.setStyleSheet(
            "QToolButton {"
            " padding: 0px;"
            " border: none;"
            " border-radius: 0px;"
            " background: transparent;"
            f" border-image: url({image_url}) 0 0 0 0 stretch stretch;"
            "}"
            "QToolButton:hover {"
            f" border-image: url({image_url}) 0 0 0 0 stretch stretch;"
            "}"
            "QToolButton:pressed {"
            f" border-image: url({image_url}) 0 0 0 0 stretch stretch;"
            "}"
        )

    def _project_root_or_parent(self, file_path: str | Path) -> str:
        normalized = normalize_path(file_path)
        if normalized is None:
            return str(Path(file_path).parent)
        if self.project_dataset is not None:
            root = Path(self.project_dataset.root_path)
            try:
                Path(normalized).relative_to(root)
                return self.project_dataset.root_path
            except Exception:
                pass
        return str(Path(normalized).parent)

    def _rescan_root_for_target(self, target_path: Path) -> str:
        normalized_target = normalize_path(target_path)
        if normalized_target is None or self.project_dataset is None:
            return str(target_path.parent.resolve())
        root = Path(self.project_dataset.root_path)
        try:
            Path(normalized_target).relative_to(root)
            return self.project_dataset.root_path
        except Exception:
            return str(target_path.parent.resolve())

    def _schedule_settings_save(self) -> None:
        self._settings_save_timer.start()

    def _flush_settings(self) -> None:
        self._settings_save_timer.stop()
        save_settings(self.settings)

    def _schedule_project_status_save(self) -> None:
        self._project_status_save_timer.start()

    def _flush_project_statuses(self, *, block: bool = False) -> None:
        self._project_status_save_timer.stop()
        if not self._project_status_root:
            return
        root = self._project_status_root
        snapshot = dict(self._project_status_by_relative_path)
        if block:
            future = self._project_status_save_future
            if future is not None:
                future.result()
            save_project_statuses(root, snapshot)
            return
        self._enqueue_project_status_save(root, snapshot)

    def _enqueue_project_status_save(self, root: str, snapshot: dict[str, str]) -> None:
        with self._project_status_save_lock:
            future = self._project_status_save_future
            if future is not None and not future.done():
                self._pending_project_status_save = (root, snapshot)
                return
            self._project_status_save_future = self._project_status_save_executor.submit(
                save_project_statuses,
                root,
                snapshot,
            )
            self._project_status_save_future.add_done_callback(self._on_project_status_save_done)

    def _on_project_status_save_done(self, _future: Future) -> None:
        pending: tuple[str, dict[str, str]] | None = None
        with self._project_status_save_lock:
            pending = self._pending_project_status_save
            self._pending_project_status_save = None
            self._project_status_save_future = None
        if pending is not None:
            root, snapshot = pending
            self._enqueue_project_status_save(root, snapshot)

    def _relative_status_key(self, file_path: str | Path) -> str | None:
        root = self._project_status_root or (self.project_dataset.root_path if self.project_dataset is not None else None)
        if not root:
            return None
        normalized = normalize_path(file_path)
        if normalized is None:
            return None
        try:
            return str(Path(normalized).relative_to(Path(root)).with_suffix("")).replace("/", "\\")
        except Exception:
            return None
