from __future__ import annotations

import threading
from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
import sys
from typing import Callable

import numpy as np

from PyQt6.QtCore import QObject, QPointF, QSize, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QRegion, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QDialogButtonBox,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QCheckBox,
    QAbstractSpinBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
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
    compute_next_open_path,
    normalize_path,
    scan_project_dataset,
)
from meshsemantics.core.project_status_store import load_project_statuses, save_project_statuses
from meshsemantics.core.settings import load_colormap, load_settings, save_colormap, save_settings
from meshsemantics.ui.file_panel import FilePanel
from meshsemantics.ui.landmark_panel import LandmarkPanel
from meshsemantics.ui.label_panel import LabelPanel
from meshsemantics.ui.panel_dock import PanelDockWidget
from meshsemantics.ui.style import build_app_qss
from meshsemantics.ui.vedo_widget import VedoWidget


@dataclass
class HistoryRecord:
    state_before: dict
    state_after: dict


@dataclass
class ShortcutBinding:
    shortcut: QShortcut
    contexts: frozenset[str]
    enabled_when: Callable[[], bool] | None = None


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
        self.landmark_dirty = False
        self.landmarks: list[dict] = []
        self.active_landmark_index = -1
        self.currentPanel = "label"
        self._shortcut_bindings: list[ShortcutBinding] = []
        self._syncing_current_panel = False
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

        self.label_engine = LabelEngine()
        self.vedo_widget = VedoWidget()
        self.file_panel = FilePanel(cache_limit=int(self.settings.get("cache_limit", 20)))
        self.label_panel = LabelPanel(self.colormap, max_label=int(self.settings.get("max_label", 255)))
        self.landmark_panel = LandmarkPanel()
        self.panel_dock = PanelDockWidget(self.label_panel, self.landmark_panel)
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
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        self.setAcceptDrops(True)
        self.setStyleSheet(build_app_qss())
        self.setCentralWidget(self.vedo_widget)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.file_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.panel_dock)
        self.setcurrentpanel("label")
        self._build_floating_action_bar()
        self.label_panel.set_overwrite_existing_labels(bool(self.settings.get("overwrite_existing_labels", False)))

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    def _build_floating_action_bar(self) -> None:
        self.file_panel.next_model_button.hide()
        self.label_panel.quick_save_button.hide()
        self.label_panel.complete_checkbox.hide()

        self.floating_previous_model_button = QPushButton(self.vedo_widget)
        self.floating_previous_model_button.setIcon(self._create_arrow_icon(direction="left"))
        self.floating_previous_model_button.setToolTip("Previous Model")
        self.floating_previous_model_button.clicked.connect(self.file_panel.open_previous_model)

        self.floating_next_model_button = QPushButton(self.vedo_widget)
        self.floating_next_model_button.setIcon(self._create_arrow_icon(direction="right"))
        self.floating_next_model_button.setToolTip("Next Model")
        self.floating_next_model_button.clicked.connect(self.file_panel._open_next_model)

        self.floating_quick_save_button = QPushButton("Quick Save", self.vedo_widget)
        self.floating_quick_save_button.clicked.connect(self.quick_save_current)

        self.floating_complete_checkbox = QCheckBox("Completed", self.vedo_widget)
        self.floating_complete_checkbox.setObjectName("floating-completion-toggle")
        self.floating_complete_checkbox.setStyleSheet(
            self.label_panel._completion_checkbox_qss().replace("completion-toggle", "floating-completion-toggle")
        )
        self.floating_complete_checkbox.clicked.connect(self.toggle_task_completed)
        self.vedo_widget.installEventFilter(self)
        for control in (
            self.floating_previous_model_button,
            self.floating_next_model_button,
            self.floating_quick_save_button,
            self.floating_complete_checkbox,
        ):
            control.installEventFilter(self)
        self._position_floating_action_bar()

    def _update_floating_control_mask(self, control: QWidget) -> None:
        rect = control.rect()
        if rect.isEmpty():
            return
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 10.0, 10.0)
        control.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _position_floating_action_bar(self) -> None:
        if not hasattr(self, "floating_previous_model_button"):
            return
        margin = 20
        spacing = 8

        controls = [
            self.floating_previous_model_button,
            self.floating_next_model_button,
            self.floating_quick_save_button,
            self.floating_complete_checkbox,
        ]
        visible_controls = [control for control in controls if control.isVisible()]
        if not visible_controls:
            return
        widths = []
        height = 0
        for control in visible_controls:
            hint = control.sizeHint()
            control.resize(hint)
            widths.append(hint.width())
            height = max(height, hint.height())

        total_width = sum(widths) + spacing * (len(visible_controls) - 1)
        x = max(margin, self.vedo_widget.width() - total_width - margin)
        y = max(margin, self.vedo_widget.height() - height - margin)

        cursor_x = x
        for control, width in zip(visible_controls, widths):
            control.move(cursor_x, y)
            self._update_floating_control_mask(control)
            control.raise_()
            cursor_x += width + spacing

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_floating_action_bar()

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.vedo_widget and event.type() == event.Type.Resize:
            self._position_floating_action_bar()
        elif watched in {
            getattr(self, "floating_previous_model_button", None),
            getattr(self, "floating_next_model_button", None),
            getattr(self, "floating_quick_save_button", None),
            getattr(self, "floating_complete_checkbox", None),
        } and event.type() in {event.Type.Resize, event.Type.Show}:
            self._update_floating_control_mask(watched)
        return super().eventFilter(watched, event)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(28, 28))
        self.addToolBar(toolbar)

        open_file = QAction("Open File", self)
        open_file.triggered.connect(self.open_file_dialog)
        open_dir = QAction("Open Folder", self)
        open_dir.triggered.connect(self.open_folder_dialog)
        import_json = QAction("Import Segment", self)
        import_json.triggered.connect(self.import_labels_json_dialog)
        save_action = QAction("Save As", self)
        save_action.triggered.connect(self.save_current)
        clear_selection_action = QAction("Clear", self)
        clear_selection_action.triggered.connect(self.clear_current_model_selection)

        self.import_json_action = import_json
        self.clear_selection_action = clear_selection_action
        self.import_json_action.setEnabled(False)
        self.clear_selection_action.setEnabled(False)

        for action in [open_file, open_dir, import_json, save_action, clear_selection_action]:
            toolbar.addAction(action)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

    def _bind_signals(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(lambda *_: self._refresh_shortcut_bindings())

        self.file_panel.open_requested.connect(self.load_mesh)
        self.file_panel.remove_requested.connect(self._remove_file_from_list)
        self.file_panel.delete_local_requested.connect(self._delete_local_file_from_list)
        self.file_panel.previous_model_requested.connect(self.load_mesh)
        self.file_panel.next_model_requested.connect(self.load_mesh)

        self.label_panel.colormap_changed.connect(self._on_colormap_changed)
        self.label_panel.remap_requested.connect(self._remap_labels)
        self.label_panel.delete_requested.connect(self._delete_label)
        self.label_panel.overwrite_mode_changed.connect(self._on_overwrite_mode_changed)
        self.label_panel.completion_toggle_requested.connect(self.toggle_task_completed)
        self.label_panel.quick_save_requested.connect(self.quick_save_current)
        self.label_panel.panel_activated.connect(lambda: self.setcurrentpanel("label"))
        self.landmark_panel.add_requested.connect(self._add_landmark)
        self.landmark_panel.rename_requested.connect(self._rename_landmark)
        self.landmark_panel.delete_requested.connect(self._delete_landmark)
        self.landmark_panel.select_requested.connect(self._select_landmark)
        self.landmark_panel.pick_requested.connect(self._begin_landmark_pick)
        self.landmark_panel.save_requested.connect(self.quick_save_landmarks)
        self.landmark_panel.import_requested.connect(self.import_landmarks_json_dialog)
        self.landmark_panel.panel_activated.connect(lambda: self.setcurrentpanel("landmark"))
        self.panel_dock.current_panel_changed.connect(self.setcurrentpanel)

        self.vedo_widget.mesh_loaded.connect(self._refresh_stats)

        self.interactor.mode_changed.connect(self._on_mode_changed)
        self.interactor.preview_changed.connect(self.vedo_widget.preview_cells)
        self.interactor.control_points_changed.connect(self.vedo_widget.set_control_points)
        self.interactor.apply_requested.connect(self._apply_cells)
        self.interactor.surface_double_clicked.connect(self._handle_surface_double_click)
        self.interactor.landmark_picked.connect(self._apply_landmark_pick)
        self.interactor.message.connect(self.statusBar().showMessage)

    def _bind_shortcuts(self) -> None:
        self._shortcut_bindings.clear()
        self._register_shortcut("B", self, {"label", "landmark"}, self.file_panel.open_previous_model, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut("N", self, {"label", "landmark"}, self.file_panel._open_next_model, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut("Ctrl+S", self, {"label"}, self.quick_save_current)
        self._register_shortcut("Ctrl+Shift+S", self, {"label"}, self.save_current)
        self._register_shortcut("S", self, {"label"}, self.interactor.begin_spline, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut(
            Qt.Key.Key_Return,
            self,
            {"label"},
            self.interactor.confirm_preview,
            enabled_when=self._shortcut_can_use_enter,
        )
        self._register_shortcut(
            Qt.Key.Key_Enter,
            self,
            {"label"},
            self.interactor.confirm_preview,
            enabled_when=self._shortcut_can_use_enter,
        )
        self._register_shortcut("E", self, {"label"}, self.interactor.apply_preview, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut("C", self, {"label"}, self.interactor.clear_preview, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut("M", self, {"label"}, self.toggle_task_completed, enabled_when=self._shortcut_can_use_plain_action)
        self._register_shortcut(QKeySequence.StandardKey.Undo, self, {"label"}, self.undo)
        self._register_shortcut(QKeySequence.StandardKey.Redo, self, {"label"}, self.redo)

        self._register_shortcut(
            Qt.Key.Key_Return,
            self,
            {"landmark"},
            self._handle_landmark_add_shortcut,
            enabled_when=self._shortcut_can_use_enter,
        )
        self._register_shortcut(
            Qt.Key.Key_Enter,
            self,
            {"landmark"},
            self._handle_landmark_add_shortcut,
            enabled_when=self._shortcut_can_use_enter,
        )
        self._register_shortcut("Ctrl+S", self, {"landmark"}, self.quick_save_landmarks)
        self._register_shortcut("Ctrl+Shift+S", self, {"landmark"}, self.save_current)
        self._register_shortcut(
            Qt.Key.Key_Delete,
            self,
            {"landmark"},
            self._handle_landmark_delete_shortcut,
            enabled_when=self._shortcut_can_delete_selection,
        )
        self._register_shortcut(
            Qt.Key.Key_Backspace,
            self,
            {"landmark"},
            self._handle_landmark_delete_shortcut,
            enabled_when=self._shortcut_can_delete_selection,
        )
        self._register_shortcut(QKeySequence.StandardKey.Undo, self, {"landmark"}, self.undo)
        self._register_shortcut(QKeySequence.StandardKey.Redo, self, {"landmark"}, self.redo)
        self._refresh_shortcut_bindings()

    def _register_shortcut(
        self,
        sequence,
        parent,
        contexts: set[str],
        handler,
        enabled_when: Callable[[], bool] | None = None,
    ) -> None:
        key_sequence = sequence if isinstance(sequence, QKeySequence) else QKeySequence(int(sequence) if isinstance(sequence, Qt.Key) else sequence)
        shortcut = QShortcut(key_sequence, parent)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(handler)
        self._shortcut_bindings.append(
            ShortcutBinding(shortcut=shortcut, contexts=frozenset(contexts), enabled_when=enabled_when)
        )

    def setcurrentpanel(self, panel: str) -> None:
        if panel not in {"label", "landmark"}:
            return
        previous_panel = self.currentPanel
        if previous_panel == "label" and panel == "landmark":
            self.interactor.clear_preview()
        self.currentPanel = panel
        self.interactor.set_interaction_context(panel)
        self._refresh_shortcut_bindings()
        self._sync_floating_action_buttons()
        if self._syncing_current_panel:
            return
        self._syncing_current_panel = True
        try:
            if self.panel_dock.current_panel() != panel:
                self.panel_dock.show_panel(panel)
        finally:
            self._syncing_current_panel = False

    def _refresh_shortcut_bindings(self) -> None:
        for binding in self._shortcut_bindings:
            enabled = self.currentPanel in binding.contexts
            if enabled and binding.enabled_when is not None:
                enabled = bool(binding.enabled_when())
            binding.shortcut.setEnabled(enabled)

    def _focused_widget_is_editable(self) -> bool:
        focus_widget = QApplication.focusWidget()
        while focus_widget is not None:
            if isinstance(focus_widget, (QLineEdit, QAbstractSpinBox)):
                return True
            focus_widget = focus_widget.parentWidget()
        return False

    def _shortcut_can_use_plain_action(self) -> bool:
        return not self._focused_widget_is_editable()

    def _shortcut_can_use_enter(self) -> bool:
        return not self._focused_widget_is_editable()

    def _shortcut_can_delete_selection(self) -> bool:
        return not self._focused_widget_is_editable()

    def _show_label_panel(self) -> None:
        self.setcurrentpanel("label")

    def _show_landmark_panel(self) -> None:
        self.setcurrentpanel("landmark")

    def _handle_landmark_add_shortcut(self) -> None:
        self.landmark_panel.add_requested.emit(self.landmark_panel.name_edit.text().strip())

    def _handle_landmark_delete_shortcut(self) -> None:
        index = self.active_landmark_index if self.active_landmark_index >= 0 else self.landmark_panel.selected_row()
        if index >= 0:
            self._delete_landmark(index)

    def _handle_surface_double_click(self, position, cell_id: int) -> None:
        if self.currentPanel == "landmark":
            self._prompt_landmark_name_for_position(position)
            return
        self._select_label_for_cell(cell_id)

    def _remove_file_from_list(self, file_path: str) -> None:
        entry = self._project_entry_for_path(file_path)
        if entry is None:
            return
        target_name = Path(file_path).name
        reply = QMessageBox.question(
            self,
            "Remove From List",
            f"Remove {target_name} from the task list only?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_remove_project_entry(entry):
            return
        self._remove_project_entry(entry)
        self.statusBar().showMessage(f"Removed {target_name} from the task list")

    def _delete_local_file_from_list(self, file_path: str) -> None:
        entry = self._project_entry_for_path(file_path)
        if entry is None:
            return
        target_path = Path(file_path)
        target_name = target_path.name
        reply = QMessageBox.question(
            self,
            "Delete Local File",
            f"Delete {target_name} from disk and remove it from the task list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_remove_project_entry(entry):
            return
        try:
            if target_path.exists():
                target_path.unlink()
        except Exception as exc:
            QMessageBox.critical(self, "Delete Failed", f"Failed to delete file:\n{target_path}\n\n{exc}")
            return
        self._remove_project_entry(entry)
        self.statusBar().showMessage(f"Deleted {target_name} and removed it from the task list")

    def _confirm_remove_project_entry(self, entry) -> bool:
        current_path = normalize_path(self.current_path)
        entry_work = normalize_path(entry.work_path)
        entry_source = normalize_path(entry.source_path)
        if current_path is not None and current_path in {entry_work, entry_source}:
            return self._confirm_save_if_dirty()
        return True

    def _prompt_landmark_name_for_position(self, position) -> None:
        if self.vedo_widget.mesh is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Landmark")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        prompt = QLabel("Enter a landmark name:")
        layout.addWidget(prompt)

        name_edit = QLineEdit(dialog)
        name_edit.setPlaceholderText(f"Landmark {len(self.landmarks) + 1}")
        layout.addWidget(name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        name_edit.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        landmark_name = name_edit.text().strip()
        existing_index = self._find_landmark_index_by_name(landmark_name)
        if existing_index >= 0:
            reply = QMessageBox.question(
                self,
                "Landmark Exists",
                f"Landmark \"{self.landmarks[existing_index].get('name', landmark_name)}\" already exists.\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.No:
                landmark_name = self._build_landmark_copy_name(landmark_name)
        self._add_landmark_at_position(landmark_name, position)

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
            if self.vedo_widget.mesh is not None:
                self._clear_loaded_mesh()
            self.open_project(folder, auto_load=False)

    def import_labels_json_dialog(self) -> None:
        if not self._can_import_json():
            return
        default_path = Path(self.current_path).with_suffix(".json")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Label JSON",
            str(default_path if default_path.exists() else self.last_open_dir),
            "JSON (*.json)",
        )
        if not file_path:
            return
        self._set_last_open_dir(Path(file_path).parent)
        self._import_labels_json(file_path)

    def import_landmarks_json_dialog(self) -> None:
        if self.vedo_widget.mesh is None:
            return
        default_path = self._default_landmark_target()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Landmark JSON",
            str(default_path if default_path.exists() else self.last_open_dir),
            "JSON (*.json)",
        )
        if not file_path:
            return
        self._set_last_open_dir(Path(file_path).parent)
        self._import_landmarks_json(file_path)

    def export_landmarks_json_dialog(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Landmark JSON",
            str(self._default_landmark_target()),
            "JSON (*.json)",
        )
        if not target:
            return False
        return self._save_landmarks_to_path(target)

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
        self._sync_floating_action_buttons()

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
            self._sync_floating_action_buttons()
            return
        message = f"Scanning folder... {scanned_files} meshes"
        if latest_path:
            message = f"{message} | {latest_path}"
        self.file_panel.set_busy(True, message)
        self._sync_floating_action_buttons()
        self.statusBar().showMessage(message)

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

    def clear_current_model_selection(self) -> None:
        if self.current_path is None and self.vedo_widget.mesh is None:
            self.statusBar().showMessage("No model is currently selected")
            return
        if not self._confirm_save_if_dirty():
            return
        self._clear_loaded_mesh()
        if self.project_dataset is not None:
            self.project_dataset = ProjectDataset(
                root_path=self.project_dataset.root_path,
                entries=self.project_dataset.entries,
                current_path=None,
                next_open_path=self.project_dataset.next_open_path,
                suggested_path=None,
            )
        self.file_panel.set_current_path(None)
        self._sync_floating_action_buttons()
        self.statusBar().showMessage("Cleared current model selection")

    @pyqtSlot(str)
    def load_mesh(self, file_path: str) -> None:
        normalized_path = normalize_path(file_path)
        if normalized_path is None:
            return
        resolved_path = self._resolve_open_target_path(normalized_path)
        if resolved_path is None:
            self.file_panel.set_current_path(self.current_path)
            return
        normalized_path = resolved_path
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
                next_open_path=self.project_dataset.next_open_path,
                suggested_path=normalized_path,
            )
            self.file_panel.set_current_path(normalized_path)

        self.file_panel.set_busy(True, "Loading mesh...")
        self._sync_floating_action_buttons()
        try:
            mesh, labels = FileIO.load_mesh(normalized_path)
        except Exception as exc:
            self.file_panel.set_busy(False)
            self._sync_floating_action_buttons()
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
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            suffix = Path(path).suffix.lower()
            if suffix in FileIO.SUPPORTED_SUFFIXES:
                event.acceptProposedAction()
                return
            if suffix == ".json" and self._can_import_json():
                event.acceptProposedAction()
                return

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            suffix = Path(path).suffix.lower()
            if suffix in FileIO.SUPPORTED_SUFFIXES:
                self.open_project(Path(path).parent, preferred_path=path, auto_load=True)
                break
            if suffix == ".json":
                if not self._can_import_json():
                    QMessageBox.information(self, "Import JSON", "请先打开一个模型，再导入 JSON 标注文件。")
                    break
                self._set_last_open_dir(Path(path).parent)
                self._import_labels_json(path)
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

    def quick_save_landmarks(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        return self._save_landmarks_to_path(self._default_landmark_target())

    def save_current(self) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        if self.currentPanel == "landmark":
            return self.export_landmarks_json_dialog()
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

    def _import_labels_json(self, file_path: str | Path) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        before_state = self._capture_history_state()
        try:
            labels = FileIO.load_labels_json(file_path, expected_cell_count=self.label_engine.size)
        except Exception as exc:
            reason = self._friendly_json_import_error(exc)
            QMessageBox.critical(
                self,
                "Import Failed",
                reason,
            )
            return False

        self.interactor.clear_preview(emit_preview=False)
        self.label_engine.reset(labels)
        self.label_panel.ensure_labels(self.label_engine.unique_labels())
        self.is_dirty = True
        self._push_history(before_state)
        self._update_mesh_view()
        self._update_current_status_after_edit()
        self.statusBar().showMessage(f"Imported labels from {Path(file_path).name}")
        return True

    def _import_landmarks_json(self, file_path: str | Path) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        before_state = self._capture_history_state()
        try:
            landmarks = FileIO.load_landmarks_json(file_path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Import Failed",
                self._friendly_landmark_import_error(exc),
            )
            return False

        self.landmarks = landmarks
        self.active_landmark_index = 0 if self.landmarks else -1
        self.landmark_dirty = False
        self.landmark_panel.set_pick_mode(False)
        self._push_history(before_state)
        self._update_landmark_view()
        self.statusBar().showMessage(f"Imported landmarks from {Path(file_path).name}")
        return True

    def _save_landmarks_to_path(self, target: str | Path, mark_clean: bool = True) -> bool:
        if self.vedo_widget.mesh is None:
            return False
        target_path = Path(target)
        if target_path.suffix.lower() != ".json":
            target_path = target_path.with_suffix(".json")
        self._set_last_open_dir(target_path.parent)
        FileIO.save_landmarks_json(target_path, self.landmarks)
        if mark_clean:
            self.landmark_dirty = False
        self.statusBar().showMessage(f"Saved landmarks to {target_path}")
        return True

    def _can_import_json(self) -> bool:
        return self.vedo_widget.mesh is not None and self.current_path is not None

    def _friendly_json_import_error(self, error: Exception) -> str:
        code = str(error).strip()
        if code == "cell_count_mismatch":
            return "导入的标注文件和当前打开的模型不匹配，请确认它们是否为同一个模型。"
        if code in {"missing_labels", "invalid_count", "invalid_json"}:
            return "这个标注文件格式不正确，无法导入。"
        return "导入失败，请检查标注文件的格式。"

    def _friendly_landmark_import_error(self, error: Exception) -> str:
        code = str(error).strip()
        if code in {"missing_landmarks", "invalid_count", "invalid_json", "invalid_landmark"}:
            return "这个特征点文件格式不正确，无法导入。"
        return "导入特征点失败，请检查文件内容和格式。"

    def undo(self) -> None:
        if not self.undo_history:
            return
        record = self.undo_history.pop()
        self.redo_history.append(record)
        self._restore_history_state(record.state_before)

    def redo(self) -> None:
        if not self.redo_history:
            return
        record = self.redo_history.pop()
        self.undo_history.append(record)
        self._restore_history_state(record.state_after)

    def _apply_cells(self, cell_ids) -> None:
        before_state = self._capture_history_state()
        overwrite_existing = self.label_panel.overwrite_existing_labels()
        raw_ids = np.asarray(cell_ids, dtype=np.int32).reshape(-1)
        assignable_ids = self.label_engine.assignable_cells(
            raw_ids,
            overwrite_existing=overwrite_existing,
        )
        if self.label_engine.assign(assignable_ids, self.label_panel.current_label(), overwrite_existing=True):
            self.is_dirty = True
            self._push_history(before_state)
            self._update_mesh_view()
            self._update_current_status_after_edit()
            skipped_count = max(0, int(raw_ids.size) - int(assignable_ids.size))
            message = f"Assigned label {self.label_panel.current_label()} to {int(assignable_ids.size)} cells"
            if skipped_count > 0 and not overwrite_existing:
                message = f"{message} | Skipped {skipped_count} labeled cells"
            self.statusBar().showMessage(message)
        elif raw_ids.size > 0 and not overwrite_existing:
            self.statusBar().showMessage("Selection already has labels. Enable overwrite to replace them.")
        self.interactor.clear_preview()

    def _remap_labels(self, source: int, target: int) -> None:
        before_state = self._capture_history_state()
        if self.label_engine.remap_label(source, target):
            self.is_dirty = True
            self._push_history(before_state)
            self._update_mesh_view()
            self._update_current_status_after_edit()
            self.statusBar().showMessage(f"Remapped label {source} to {target}")

    def _delete_label(self, label: int) -> None:
        if label <= 0:
            return
        before_state = self._capture_history_state()
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
        self._push_history(before_state)
        self._update_mesh_view()
        self._update_current_status_after_edit()
        self.statusBar().showMessage(f"Deleted label {label}")

    def _on_colormap_changed(self, colormap: dict) -> None:
        self.colormap = colormap
        save_colormap(colormap)
        self.vedo_widget.set_colormap(colormap)

    def _on_overwrite_mode_changed(self, enabled: bool) -> None:
        self.settings["overwrite_existing_labels"] = bool(enabled)
        self._schedule_settings_save()

    def _select_label_for_cell(self, cell_id: int) -> None:
        if cell_id < 0 or cell_id >= self.label_engine.size:
            return
        label = int(self.label_engine.label_array[cell_id])
        if label <= 0:
            return
        self.label_panel.set_current_label(label, sync_remap_source=True)

    def _add_landmark(self, name: str) -> None:
        landmark_name = name.strip() or f"Landmark {len(self.landmarks) + 1}"
        existing_index = self._find_landmark_index_by_name(landmark_name)
        if existing_index >= 0:
            self.active_landmark_index = existing_index
            self.landmark_panel.preserve_input_text_once()
            self._show_landmark_panel()
            self._update_landmark_view()
            self.landmark_panel.select_row(existing_index, update_input=False)
            self.landmark_panel.focus_name_input(clear=False)
            self.statusBar().showMessage(f"Selected existing landmark {self.landmarks[existing_index].get('name', landmark_name)}")
            return
        before_state = self._capture_history_state()
        self.landmark_panel.preserve_input_text_once()
        self.landmarks.append({"name": landmark_name, "position": None})
        self.active_landmark_index = len(self.landmarks) - 1
        self.landmark_dirty = True
        self._push_history(before_state)
        self._show_landmark_panel()
        self._update_landmark_view()
        self.landmark_panel.select_row(self.active_landmark_index, update_input=False)
        self.landmark_panel.focus_name_input(clear=True)
        self.statusBar().showMessage(f"Added landmark {landmark_name}")

    def _add_landmark_at_position(self, name: str, position) -> None:
        landmark_name = name.strip() or f"Landmark {len(self.landmarks) + 1}"
        coords = tuple(float(value) for value in position)
        existing_index = self._find_landmark_index_by_name(landmark_name)
        before_state = self._capture_history_state()
        if existing_index >= 0:
            self.active_landmark_index = existing_index
            self.landmarks[existing_index]["position"] = coords
            self.landmark_dirty = True
            self._push_history(before_state)
            self.landmark_panel.preserve_input_text_once()
            self._show_landmark_panel()
            self._update_landmark_view()
            self.landmark_panel.select_row(existing_index, update_input=False)
            self.landmark_panel.focus_name_input(clear=False)
            self.statusBar().showMessage(
                f"Updated landmark {self.landmarks[existing_index].get('name', landmark_name)}"
            )
            return
        self.landmarks.append({"name": landmark_name, "position": coords})
        self.active_landmark_index = len(self.landmarks) - 1
        self.landmark_dirty = True
        self._push_history(before_state)
        self.landmark_panel.preserve_input_text_once()
        self._show_landmark_panel()
        self._update_landmark_view()
        self.landmark_panel.select_row(self.active_landmark_index, update_input=False)
        self.landmark_panel.focus_name_input(clear=True)
        self.statusBar().showMessage(f"Added landmark {landmark_name}")

    def _rename_landmark(self, index: int, name: str) -> None:
        if index < 0 or index >= len(self.landmarks):
            return
        landmark_name = name.strip() or f"Landmark {index + 1}"
        before_state = self._capture_history_state()
        self.landmarks[index]["name"] = landmark_name
        self.landmark_dirty = True
        self._push_history(before_state)
        self._update_landmark_view()
        self.statusBar().showMessage(f"Renamed landmark to {landmark_name}")

    def _delete_landmark(self, index: int) -> None:
        if index < 0 or index >= len(self.landmarks):
            return
        landmark_name = str(self.landmarks[index].get("name") or f"Landmark {index + 1}")
        before_state = self._capture_history_state()
        del self.landmarks[index]
        if not self.landmarks:
            self.active_landmark_index = -1
        else:
            self.active_landmark_index = min(index, len(self.landmarks) - 1)
        self.landmark_dirty = True
        self.interactor.clear_preview(emit_preview=False)
        self.landmark_panel.set_pick_mode(False)
        self._push_history(before_state)
        self._update_landmark_view()
        self.statusBar().showMessage(f"Deleted landmark {landmark_name}")

    def _select_landmark(self, index: int) -> None:
        if index < 0 or index >= len(self.landmarks):
            return
        self.active_landmark_index = index
        self._show_landmark_panel()
        self._update_landmark_view()
        self.statusBar().showMessage(f"Active landmark: {self.landmarks[index].get('name', f'Landmark {index + 1}')}")

    def _begin_landmark_pick(self, index: int) -> None:
        if index < 0 or index >= len(self.landmarks) or self.vedo_widget.mesh is None:
            return
        self.active_landmark_index = index
        self._show_landmark_panel()
        self._update_landmark_view()
        self.landmark_panel.set_pick_mode(True, str(self.landmarks[index].get("name") or f"Landmark {index + 1}"))
        self.interactor.begin_landmark_pick()

    def _apply_landmark_pick(self, position) -> None:
        if self.active_landmark_index < 0 or self.active_landmark_index >= len(self.landmarks):
            return
        before_state = self._capture_history_state()
        coords = tuple(float(value) for value in position)
        self.landmarks[self.active_landmark_index]["position"] = coords
        self.landmark_dirty = True
        self.landmark_panel.set_pick_mode(False)
        self._push_history(before_state)
        self._update_landmark_view()
        self.statusBar().showMessage(
            f"Updated landmark {self.landmarks[self.active_landmark_index].get('name', self.active_landmark_index + 1)}"
        )

    def _on_mode_changed(self, mode: str) -> None:
        if mode != "LANDMARK_PICK":
            self.landmark_panel.set_pick_mode(False)
        self.statusBar().showMessage(f"Mode: {mode}")

    def _refresh_stats(self, total_cells: int) -> None:
        labeled = int((self.label_engine.label_array != 0).sum()) if self.label_engine.size else 0
        self.label_panel.refresh_stats(total_cells, labeled)

    def _consume_loaded_mesh(self, file_path: str, mesh, labels) -> None:
        mesh.filename = file_path
        self.interactor.clear_preview(emit_preview=False)
        self.label_engine.reset(labels)
        self.label_panel.ensure_labels(self.label_engine.unique_labels())
        self.vedo_widget.set_mesh(mesh, self.label_engine.label_array, self.colormap)
        self._reset_landmarks()
        self._autoload_landmarks(file_path)
        self.file_panel.set_busy(False)
        self.is_dirty = False
        self._clear_history()

        status = self._status_for_loaded_file(file_path)
        self._set_current_status(status, persist_only=True)
        self._sync_floating_action_buttons()
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
        if self.vedo_widget.mesh is None:
            return True
        if not self.is_dirty and not self.landmark_dirty:
            return True
        if self.is_dirty and self.landmark_dirty:
            message = "The current task has unsaved labels and landmarks. Save both before switching?"
        elif self.is_dirty:
            message = "The current task has unsaved label changes. Save to the task VTP before switching?"
        else:
            message = "The current task has unsaved landmarks. Export them to the task JSON before switching?"
        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            message,
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            if self.is_dirty and not self.quick_save_current():
                return False
            if self.landmark_dirty and not self.quick_save_landmarks():
                return False
            return True
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

        previous_path = normalize_path(self.current_path)
        status = self._current_entry_status()
        if status == STATUS_FAILED or status is None:
            status = self._base_status_for_work(normalized_target)

        self.current_path = normalized_target
        self.vedo_widget.mesh.filename = normalized_target
        self.is_dirty = False
        self._replace_current_project_entry(previous_path, normalized_target, status)
        if previous_path and previous_path != normalized_target:
            self._project_status_by_work_path.pop(previous_path, None)
            previous_relative_key = self._relative_status_key(previous_path)
            next_relative_key = self._relative_status_key(normalized_target)
            if previous_relative_key and previous_relative_key != next_relative_key:
                self._project_status_by_relative_path.pop(previous_relative_key, None)
        self._record_status(normalized_target, status)
        self._project_status_by_work_path[normalized_target] = status
        project_root = self._project_root_or_parent(normalized_target)
        self._remember_last_file(project_root, normalized_target)
        self._refresh_completion_action()
        self.statusBar().showMessage(f"Saved VTP to {normalized_target}")
        return True

    def _default_vtp_target(self) -> Path:
        if self.current_path:
            return Path(self.current_path).with_suffix(".vtp")
        return Path(self.last_open_dir) / "mesh.vtp"

    def _default_landmark_target(self) -> Path:
        if self.current_path:
            return Path(self.current_path).with_suffix(".landmarks.json")
        return Path(self.last_open_dir) / "mesh.landmarks.json"

    def _capture_history_state(self) -> dict:
        return {
            "labels": self.label_engine.label_array.copy(),
            "label_ui": self.label_panel.snapshot_state(),
            "is_dirty": bool(self.is_dirty),
            "landmarks": deepcopy(self.landmarks),
            "active_landmark_index": int(self.active_landmark_index),
            "landmark_dirty": bool(self.landmark_dirty),
        }

    def _push_history(self, before_state: dict) -> None:
        record = HistoryRecord(
            state_before=before_state,
            state_after=self._capture_history_state(),
        )
        self.undo_history.append(record)
        undo_limit = max(1, int(self.settings.get("undo_limit", 50)))
        if len(self.undo_history) > undo_limit:
            self.undo_history.pop(0)
        self.redo_history.clear()

    def _restore_history_state(self, state: dict) -> None:
        self.interactor.clear_preview(emit_preview=False)
        self.label_engine.label_array = np.asarray(state.get("labels", np.zeros(0, dtype=np.int32)), dtype=np.int32).copy()
        self.label_panel.restore_state(state.get("label_ui", {}))
        self.colormap = self.label_panel.colormap()
        save_colormap(self.colormap)
        self.vedo_widget.set_colormap(self.colormap)
        self.landmarks = deepcopy(state.get("landmarks", []))
        self.active_landmark_index = int(state.get("active_landmark_index", -1))
        self.landmark_dirty = bool(state.get("landmark_dirty", False))
        self.landmark_panel.set_pick_mode(False)
        self._update_mesh_view()
        self._update_landmark_view()
        self.is_dirty = bool(state.get("is_dirty", False))
        self._update_current_status_after_edit()

    def _update_mesh_view(self) -> None:
        self.vedo_widget.update_labels(self.label_engine.label_array)
        self._refresh_stats(self.label_engine.size)

    def _update_landmark_view(self) -> None:
        self.landmark_panel.set_landmarks(self.landmarks, self.active_landmark_index)
        self.vedo_widget.set_landmarks(self.landmarks, self.active_landmark_index)

    def _reset_landmarks(self) -> None:
        self.landmarks = []
        self.active_landmark_index = -1
        self.landmark_dirty = False
        self.landmark_panel.set_pick_mode(False)
        self._update_landmark_view()

    def _autoload_landmarks(self, file_path: str | Path) -> None:
        default_path = Path(file_path).with_suffix(".landmarks.json")
        if default_path.exists():
            self._import_landmarks_json(default_path)
            return
        self._update_landmark_view()

    def _find_landmark_index_by_name(self, name: str) -> int:
        target = str(name).strip().casefold()
        if not target:
            return -1
        for index, landmark in enumerate(self.landmarks):
            candidate = str(landmark.get("name") or "").strip().casefold()
            if candidate == target:
                return index
        return -1

    def _build_landmark_copy_name(self, name: str) -> str:
        base_name = str(name).strip() or f"Landmark {len(self.landmarks) + 1}"
        copy_name = f"{base_name}（副本）"
        while self._find_landmark_index_by_name(copy_name) >= 0:
            copy_name += "（副本）"
        return copy_name

    def _clear_history(self) -> None:
        self.undo_history.clear()
        self.redo_history.clear()

    def _clear_loaded_mesh(self) -> None:
        self.interactor.clear_preview(emit_preview=False)
        self.vedo_widget.clear_mesh()
        self.label_engine.reset(np.zeros(0, dtype=np.int32))
        self._reset_landmarks()
        self.current_path = None
        self.is_dirty = False
        self._clear_history()
        self._refresh_stats(0)
        self._refresh_completion_action()
        self.file_panel.set_current_path(None)

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
            entries = list(self.project_dataset.entries)
            for index, entry in enumerate(entries):
                if normalize_path(entry.work_path) != self.current_path and normalize_path(entry.source_path) != self.current_path:
                    continue
                entries[index] = replace(entry, status=status)
                break
            next_open_path = compute_next_open_path(
                ProjectDataset(
                    root_path=self.project_dataset.root_path,
                    entries=tuple(entries),
                    current_path=self.current_path,
                    next_open_path=self.project_dataset.next_open_path,
                    suggested_path=self.project_dataset.suggested_path,
                ),
                self._project_status_by_work_path,
                self.current_path,
            )
            self.project_dataset = ProjectDataset(
                root_path=self.project_dataset.root_path,
                entries=tuple(entries),
                current_path=self.current_path,
                next_open_path=next_open_path,
                suggested_path=self.project_dataset.suggested_path,
            )
            self.file_panel.update_status(self.current_path, status)
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
        if hasattr(self, "floating_complete_checkbox"):
            self.floating_complete_checkbox.blockSignals(True)
            self.floating_complete_checkbox.setChecked(is_completed)
            self.floating_complete_checkbox.blockSignals(False)
            self.floating_complete_checkbox.setEnabled(enabled)
        if hasattr(self, "import_json_action"):
            self.import_json_action.setEnabled(self._can_import_json())
        if hasattr(self, "clear_selection_action"):
            self.clear_selection_action.setEnabled(self.current_path is not None or self.vedo_widget.mesh is not None)
        self._sync_floating_action_buttons()

    def _sync_floating_action_buttons(self) -> None:
        if not hasattr(self, "floating_previous_model_button"):
            return
        busy = self.file_panel.progress.isVisible()
        has_current = self.current_path is not None
        has_previous = self.file_panel.has_previous_model()
        has_next = self.file_panel.has_next_model()
        self.floating_previous_model_button.setEnabled(has_current and not busy and has_previous)
        self.floating_next_model_button.setEnabled(not busy and has_next)
        self.floating_quick_save_button.setEnabled(has_current and not busy)
        self.floating_complete_checkbox.setEnabled(has_current and not busy)
        self.floating_complete_checkbox.setVisible(self.currentPanel == "label")
        self._position_floating_action_bar()

    def _replace_current_project_entry(self, previous_path: str | None, next_path: str, status: str) -> None:
        if self.project_dataset is None or previous_path is None:
            return

        entries = [
            replace(entry, status=self._project_status_by_work_path.get(entry.work_path, entry.status))
            for entry in self.project_dataset.entries
        ]
        updated = False
        for index, entry in enumerate(entries):
            if normalize_path(entry.work_path) != previous_path and normalize_path(entry.source_path) != previous_path:
                continue
            entries[index] = replace(
                entry,
                display_path=self._display_path_for_project_entry(next_path),
                work_path=next_path,
                status=status,
            )
            updated = True
            break

        if not updated:
            return

        temp_dataset = ProjectDataset(
            root_path=self.project_dataset.root_path,
            entries=tuple(entries),
            current_path=next_path,
            next_open_path=self.project_dataset.next_open_path,
            suggested_path=next_path,
        )
        next_open_path = compute_next_open_path(
            temp_dataset,
            {**self._project_status_by_work_path, next_path: status},
            next_path,
        )
        self.project_dataset = ProjectDataset(
            root_path=self.project_dataset.root_path,
            entries=tuple(entries),
            current_path=next_path,
            next_open_path=next_open_path,
            suggested_path=next_path,
        )
        self.file_panel.set_project(self.project_dataset)
        self.file_panel.set_current_path(next_path)

    def _display_path_for_project_entry(self, path: str | Path) -> str:
        normalized = normalize_path(path)
        if normalized is None:
            return Path(path).name
        target = Path(normalized)
        if self.project_dataset is not None:
            root = Path(self.project_dataset.root_path)
            try:
                return str(target.relative_to(root)).replace("\\", "/")
            except Exception:
                pass
        return target.name

    def _resolve_open_target_path(self, requested_path: str) -> str | None:
        entry = self._project_entry_for_path(requested_path)
        if entry is None:
            return requested_path if Path(requested_path).exists() else None

        work_exists = Path(entry.work_path).exists()
        source_exists = Path(entry.source_path).exists()
        requested_is_work = normalize_path(entry.work_path) == requested_path
        requested_is_source = normalize_path(entry.source_path) == requested_path

        if requested_is_source:
            if source_exists:
                return entry.source_path
            if work_exists:
                return entry.work_path
            return self._handle_deleted_project_entry(entry)

        if work_exists:
            return entry.work_path
        if source_exists:
            self._switch_project_entry_to_source(entry)
            self.statusBar().showMessage("标注文件已被删除，已改为打开原始 STL 文件")
            return entry.source_path
        return self._handle_deleted_project_entry(entry)

    def _project_entry_for_path(self, path: str) -> object | None:
        if self.project_dataset is None:
            return None
        normalized = normalize_path(path)
        if normalized is None:
            return None
        for entry in self.project_dataset.entries:
            if normalize_path(entry.work_path) == normalized or normalize_path(entry.source_path) == normalized:
                return entry
        return None

    def _switch_project_entry_to_source(self, entry) -> None:
        if self.project_dataset is None:
            return
        entries = list(self.project_dataset.entries)
        source_path = normalize_path(entry.source_path) or entry.source_path
        for index, candidate in enumerate(entries):
            if candidate != entry:
                continue
            entries[index] = replace(
                candidate,
                display_path=self._display_path_for_project_entry(source_path),
                work_path=source_path,
            )
            break
        self.project_dataset = ProjectDataset(
            root_path=self.project_dataset.root_path,
            entries=tuple(entries),
            current_path=self.current_path,
            next_open_path=compute_next_open_path(
                ProjectDataset(
                    root_path=self.project_dataset.root_path,
                    entries=tuple(entries),
                    current_path=self.current_path,
                    next_open_path=self.project_dataset.next_open_path,
                    suggested_path=self.project_dataset.suggested_path,
                ),
                self._project_status_by_work_path,
                self.current_path,
            ),
            suggested_path=self.project_dataset.suggested_path,
        )
        previous_work = normalize_path(entry.work_path)
        status = self._project_status_by_work_path.get(previous_work or "", entry.status)
        if previous_work and previous_work != source_path:
            self._project_status_by_work_path.pop(previous_work, None)
        self._project_status_by_work_path[source_path] = status
        self.file_panel.set_project(self.project_dataset)
        self.file_panel.set_current_path(self.current_path)

    def _handle_deleted_project_entry(self, entry) -> str | None:
        message = (
            "这个文件在本地已经被删除了。\n\n"
            "要继续保留在软件列表里吗？"
        )
        reply = QMessageBox.question(
            self,
            "文件已删除",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            return None
        self._remove_project_entry(entry)
        self.statusBar().showMessage("已从软件列表中移除本地已删除的文件")
        return None

    def _remove_project_entry(self, entry) -> None:
        if self.project_dataset is None:
            return
        entries = [candidate for candidate in self.project_dataset.entries if candidate != entry]
        work_path = normalize_path(entry.work_path)
        source_path = normalize_path(entry.source_path)
        removed_current = self.current_path is not None and (
            normalize_path(self.current_path) == work_path or normalize_path(self.current_path) == source_path
        )
        if work_path:
            self._project_status_by_work_path.pop(work_path, None)
            relative_key = self._relative_status_key(work_path)
            if relative_key:
                self._project_status_by_relative_path.pop(relative_key, None)
        if source_path and source_path != work_path:
            self._project_status_by_work_path.pop(source_path, None)
        current_path = self.current_path
        if removed_current:
            current_path = None
        next_open_path = compute_next_open_path(
            ProjectDataset(
                root_path=self.project_dataset.root_path,
                entries=tuple(entries),
                current_path=current_path,
                next_open_path=None,
                suggested_path=current_path,
            ),
            self._project_status_by_work_path,
            current_path,
        ) if entries else None
        self.project_dataset = ProjectDataset(
            root_path=self.project_dataset.root_path,
            entries=tuple(entries),
            current_path=current_path,
            next_open_path=next_open_path,
            suggested_path=current_path,
        )
        self.file_panel.set_project(self.project_dataset)
        self.file_panel.set_current_path(current_path)
        if removed_current:
            self._clear_loaded_mesh()
        self._refresh_completion_action()

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

    def _sync_toolbar_icon_button_size(
        self,
        toolbar: QToolBar,
        reference_action: QAction,
        icon_action: QAction,
    ) -> None:
        reference_button = toolbar.widgetForAction(reference_action)
        icon_button = toolbar.widgetForAction(icon_action)
        if not isinstance(reference_button, QToolButton) or not isinstance(icon_button, QToolButton):
            return

        button_height = max(reference_button.sizeHint().height(), reference_button.height(), 1)
        width = max(button_height + 12, 56)
        icon_size = max(button_height - 4, 28)
        icon_button.setFixedSize(width, button_height)
        icon_button.setIconSize(QSize(icon_size, icon_size))

    def _create_arrow_icon(self, direction: str) -> QIcon:
        icon_size = 96
        pixmap = QPixmap(icon_size, icon_size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(QColor("#ffffff"))
        pen.setWidth(14)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        arrow = QPainterPath()
        if direction == "left":
            arrow.moveTo(QPointF(80, 48))
            arrow.lineTo(QPointF(34, 48))
            arrow.moveTo(QPointF(34, 48))
            arrow.lineTo(QPointF(50, 32))
            arrow.moveTo(QPointF(34, 48))
            arrow.lineTo(QPointF(50, 64))
        else:
            arrow.moveTo(QPointF(16, 48))
            arrow.lineTo(QPointF(62, 48))
            arrow.moveTo(QPointF(62, 48))
            arrow.lineTo(QPointF(46, 32))
            arrow.moveTo(QPointF(62, 48))
            arrow.lineTo(QPointF(46, 64))
        painter.drawPath(arrow)

        painter.end()
        return QIcon(pixmap)

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
