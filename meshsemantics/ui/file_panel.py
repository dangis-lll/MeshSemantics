from __future__ import annotations

from PyQt6 import uic
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHeaderView,
    QLabel,
    QProgressBar,
    QMenu,
    QSizePolicy,
    QTableView,
    QWidget,
)

from meshsemantics.core.project_dataset import (
    ProjectDataset,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_UNLABELED,
)
from meshsemantics.runtime import ui_path


class FileTableModel(QAbstractTableModel):
    HEADERS = ("Mesh", "Status", "Modified")
    PAGE_SIZE = 500

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project: ProjectDataset | None = None
        self._visible_entry_rows: list[int] = []
        self._loaded_rows = 0
        self._filter_text = ""
        self._status_filter = "all"
        self._current_path: str | None = None
        self._status_overrides: dict[str, str] = {}
        self._entry_row_by_path: dict[str, int] = {}
        self._visible_row_by_path: dict[str, int] = {}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return min(self._loaded_rows, len(self._visible_entry_rows))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid():
            return False
        return self._loaded_rows < len(self._visible_entry_rows)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        if parent.isValid() or not self.canFetchMore(parent):
            return
        remaining = len(self._visible_entry_rows) - self._loaded_rows
        amount = min(self.PAGE_SIZE, remaining)
        if amount <= 0:
            return
        start = self._loaded_rows
        end = start + amount - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._loaded_rows += amount
        self.endInsertRows()

    def ensure_row_loaded(self, row: int) -> None:
        if row < 0:
            return
        target_loaded_rows = min(len(self._visible_entry_rows), row + 1)
        if target_loaded_rows <= self._loaded_rows:
            return
        start = self._loaded_rows
        end = target_loaded_rows - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._loaded_rows = target_loaded_rows
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        entry = self._entry_for_visible_row(index.row())
        if entry is None:
            return None
        column = index.column()
        status = self._status_for_entry(entry.work_path)
        is_current = entry.work_path == self._current_path or entry.source_path == self._current_path

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return entry.display_path
            if column == 1:
                return _status_text(status)
            if column == 2:
                return entry.modified_at.strftime("%Y-%m-%d %H:%M:%S")

        if role == Qt.ItemDataRole.UserRole:
            return entry.work_path

        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.work_path

        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 2}:
            return int(Qt.AlignmentFlag.AlignCenter)

        if role == Qt.ItemDataRole.FontRole:
            font = self.parent().font() if self.parent() is not None else None
            if font is not None and (is_current or (column == 1 and status == STATUS_COMPLETED)):
                font.setBold(True)
                return font

        if role == Qt.ItemDataRole.ForegroundRole and column == 1:
            return _status_color(status)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return str(section + 1)

    def set_project(self, project: ProjectDataset | None) -> None:
        self.beginResetModel()
        self._project = project
        self._current_path = project.current_path if project is not None else None
        self._status_overrides.clear()
        self._entry_row_by_path = {}
        if project is not None:
            for row, entry in enumerate(project.entries):
                self._entry_row_by_path[entry.work_path] = row
                self._entry_row_by_path[entry.source_path] = row
        self._visible_entry_rows = self._collect_visible_rows()
        self._loaded_rows = min(len(self._visible_entry_rows), self.PAGE_SIZE)
        self._rebuild_visible_path_index()
        self.endResetModel()

    def set_filter_text(self, text: str) -> None:
        next_filter = text.strip().lower()
        if next_filter == self._filter_text:
            return
        self.beginResetModel()
        self._filter_text = next_filter
        self._visible_entry_rows = self._collect_visible_rows()
        self._loaded_rows = min(len(self._visible_entry_rows), self.PAGE_SIZE)
        self._rebuild_visible_path_index()
        self.endResetModel()

    def set_status_filter(self, status_filter: str) -> None:
        if status_filter == self._status_filter:
            return
        self.beginResetModel()
        self._status_filter = status_filter
        self._visible_entry_rows = self._collect_visible_rows()
        self._loaded_rows = min(len(self._visible_entry_rows), self.PAGE_SIZE)
        self._rebuild_visible_path_index()
        self.endResetModel()

    def file_path_at(self, row: int) -> str | None:
        entry = self._entry_for_visible_row(row)
        return entry.work_path if entry is not None else None

    def row_for_path(self, path: str | None) -> int:
        if not path:
            return -1
        return self._visible_row_by_path.get(path, -1)

    def next_incomplete_path_after(self, path: str | None = None) -> str | None:
        if self._project is None:
            return None
        start_path = path or self._current_path
        start_row = self._visible_row_by_path.get(start_path, -1) if start_path else -1
        for visible_row in range(start_row + 1, len(self._visible_entry_rows)):
            entry_row = self._visible_entry_rows[visible_row]
            entry = self._project.entries[entry_row]
            if self._status_for_entry(entry.work_path) == STATUS_COMPLETED:
                continue
            return entry.work_path
        return None

    def first_incomplete_path(self) -> str | None:
        if self._project is None:
            return None
        for entry_row in self._visible_entry_rows:
            entry = self._project.entries[entry_row]
            if self._status_for_entry(entry.work_path) == STATUS_COMPLETED:
                continue
            return entry.work_path
        return None

    def previous_path_before(self, path: str | None = None) -> str | None:
        if self._project is None or not self._visible_entry_rows:
            return None
        start_path = path or self._current_path
        if start_path:
            start_row = self._visible_row_by_path.get(start_path, -1)
        else:
            start_row = len(self._visible_entry_rows)
        for visible_row in range(start_row - 1, -1, -1):
            entry_row = self._visible_entry_rows[visible_row]
            return self._project.entries[entry_row].work_path
        return None

    def set_current_path(self, path: str | None) -> None:
        if path == self._current_path:
            return
        previous_path = self._current_path
        self._current_path = path
        changed_rows: set[int] = set()
        for candidate in (previous_path, path):
            if not candidate:
                continue
            visible_row = self._visible_row_by_path.get(candidate)
            if visible_row is not None and visible_row < self._loaded_rows:
                changed_rows.add(visible_row)

        for visible_row in sorted(changed_rows):
            top_left = self.index(visible_row, 0)
            bottom_right = self.index(visible_row, len(self.HEADERS) - 1)
            self.dataChanged.emit(top_left, bottom_right)

    def update_status(self, path: str, status: str) -> None:
        row = self._entry_row_by_path.get(path)
        if row is None or self._project is None:
            return
        entry = self._project.entries[row]
        current_status = self._status_for_entry(entry.work_path)
        if current_status == status:
            return
        self._status_overrides[entry.work_path] = status
        if self._status_filter != "all":
            self.beginResetModel()
            self._visible_entry_rows = self._collect_visible_rows()
            self._loaded_rows = min(len(self._visible_entry_rows), max(self._loaded_rows, self.PAGE_SIZE))
            self._rebuild_visible_path_index()
            self.endResetModel()
            return
        visible_row = self._visible_row_by_path.get(entry.work_path)
        if visible_row is None or visible_row >= self._loaded_rows:
            return
        top_left = self.index(visible_row, 1)
        bottom_right = self.index(visible_row, 1)
        self.dataChanged.emit(top_left, bottom_right)

    def total_rows(self) -> int:
        return len(self._visible_entry_rows)

    def loaded_rows(self) -> int:
        return self._loaded_rows

    def _collect_visible_rows(self) -> list[int]:
        if self._project is None:
            return []

        visible_rows: list[int] = []
        for row, entry in enumerate(self._project.entries):
            if self._entry_matches_filters(entry):
                visible_rows.append(row)

        return visible_rows

    def _entry_matches_filters(self, entry) -> bool:
        filter_text = self._filter_text
        if filter_text and filter_text not in entry.display_path.lower():
            return False

        status_filter = self._status_filter
        status = self._status_for_entry(entry.work_path)
        if status_filter == "pending":
            return status != STATUS_COMPLETED
        if status_filter != "all":
            return status == status_filter
        return True

    def _rebuild_visible_path_index(self) -> None:
        self._visible_row_by_path = {}
        if self._project is None:
            return
        for visible_row, entry_row in enumerate(self._visible_entry_rows):
            entry = self._project.entries[entry_row]
            self._visible_row_by_path[entry.work_path] = visible_row
            self._visible_row_by_path[entry.source_path] = visible_row

    def _entry_for_visible_row(self, visible_row: int):
        if self._project is None:
            return None
        if visible_row < 0 or visible_row >= self.rowCount():
            return None
        entry_row = self._visible_entry_rows[visible_row]
        return self._project.entries[entry_row]

    def _status_for_entry(self, work_path: str) -> str:
        return self._status_overrides.get(work_path, self._status_from_project(work_path))

    def _status_from_project(self, work_path: str) -> str:
        row = self._entry_row_by_path.get(work_path)
        if row is None or self._project is None:
            return STATUS_UNLABELED
        return self._project.entries[row].status


class FilePanel(QDockWidget):
    open_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)
    delete_local_requested = pyqtSignal(str)
    previous_model_requested = pyqtSignal(str)
    next_model_requested = pyqtSignal(str)

    def __init__(self, cache_limit: int = 20, parent=None) -> None:
        super().__init__("Task Queue", parent)
        self.setObjectName("file-panel")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._floating_size = QSize(540, 720)
        self._floating_resize_pending = False
        self._preferred_width = 540
        self._cache_limit = cache_limit
        self._project: ProjectDataset | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_search_text)
        content = QWidget(self)
        uic.loadUi(str(ui_path("file_panel.ui")), content)
        self._content = content
        self.panel_frame = content.panel_frame
        self.search_edit = content.search_edit
        self.status_filter = content.status_filter
        self.table = content.table
        self.progress_label = content.progress_label
        self.progress = content.progress
        self._apply_ui_properties()
        self._configure_widgets()

        self.model = FileTableModel(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().resizeSection(1, 110)
        self.table.horizontalHeader().resizeSection(2, 170)
        self.table.doubleClicked.connect(self._emit_selected)
        self.table.selectionModel().selectionChanged.connect(self._sync_buttons)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.setWidget(content)

        self.search_edit.textChanged.connect(self._queue_search_text)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.topLevelChanged.connect(self._on_top_level_changed)
        self._sync_buttons()

    def _apply_ui_properties(self) -> None:
        self.panel_frame.setProperty("panel", True)
        self.progress_label.setProperty("role", "caption")

    def _configure_widgets(self) -> None:
        self.progress_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.progress_label.setMinimumWidth(0)
        self.progress_label.setVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.status_filter.addItem("All", "all")
        self.status_filter.addItem("To Do", "pending")
        self.status_filter.addItem("Unlabeled", STATUS_UNLABELED)
        self.status_filter.addItem("In Progress", STATUS_IN_PROGRESS)
        self.status_filter.addItem("Completed", STATUS_COMPLETED)
        self.status_filter.addItem("Failed", STATUS_FAILED)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self._preferred_width > 0:
            hint.setWidth(self._preferred_width)
        return hint

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        if self._preferred_width > 0:
            hint.setWidth(min(hint.width(), self._preferred_width))
        return hint

    def resizeEvent(self, event) -> None:
        if event.size().width() > 0:
            self._preferred_width = event.size().width()
        super().resizeEvent(event)

    def set_project(
        self,
        project: ProjectDataset | None,
        *,
        restore_selection: bool = True,
        preserve_view: bool = False,
    ) -> None:
        current_width = self.width()
        previous_row = self.table.currentIndex().row() if preserve_view and self.table.currentIndex().isValid() else -1
        previous_scroll = self.table.verticalScrollBar().value() if preserve_view else None
        self._project = project
        self.model.set_project(project)
        if restore_selection:
            self._restore_selection(project.current_path if project is not None else None)
        elif preserve_view and previous_row >= 0:
            self.model.ensure_row_loaded(previous_row)
            if previous_row < self.model.rowCount():
                self.table.selectRow(previous_row)
            if previous_scroll is not None:
                self.table.verticalScrollBar().setValue(previous_scroll)
        self._sync_buttons()
        if current_width > 0:
            self._preferred_width = current_width
            self.resize(current_width, self.height())
            self.updateGeometry()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.progress.setVisible(busy)
        self.progress_label.setVisible(busy)
        self.progress_label.setText(message)
        self.progress_label.setToolTip(message if busy else "")
        self.table.setEnabled(not busy)

    def selected_path(self) -> str | None:
        current_index = self.table.currentIndex()
        if not current_index.isValid():
            return None
        return self.model.file_path_at(current_index.row())

    def stop(self) -> None:
        self._search_timer.stop()

    def set_current_path(self, path: str | None, *, restore_selection: bool = True) -> None:
        if self._project is not None and path == self._project.current_path:
            self.model.set_current_path(path)
            return
        self.model.set_current_path(path)
        if restore_selection:
            self._restore_selection(path)
        self._sync_buttons()
        if self._project is not None:
            self._project = ProjectDataset(
                root_path=self._project.root_path,
                entries=self._project.entries,
                current_path=path,
                next_open_path=self._project.next_open_path,
                suggested_path=self._project.suggested_path,
            )

    def update_status(self, path: str, status: str) -> None:
        self.model.update_status(path, status)
        self._sync_buttons()

    def _queue_search_text(self) -> None:
        self._search_timer.start()

    def _apply_search_text(self) -> None:
        self.model.set_filter_text(self.search_edit.text())
        self._restore_selection(self._project.current_path if self._project is not None else None)
        self._sync_buttons()

    def _on_status_filter_changed(self) -> None:
        self.model.set_status_filter(self.status_filter.currentData())
        self._restore_selection(self._project.current_path if self._project is not None else None)
        self._sync_buttons()

    def _emit_selected(self, index: QModelIndex) -> None:
        path = self.model.file_path_at(index.row())
        if path is not None:
            self.open_requested.emit(path)

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        path = self.model.file_path_at(index.row())
        if path is None:
            return

        menu = QMenu(self.table)
        remove_action = menu.addAction("Remove From List")
        delete_action = menu.addAction("Delete Local File")
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen == remove_action:
            self.remove_requested.emit(path)
        elif chosen == delete_action:
            self.delete_local_requested.emit(path)

    def _restore_selection(self, selected_path: str | None) -> None:
        if not selected_path:
            self.table.clearSelection()
            return
        row = self.model.row_for_path(selected_path)
        if row < 0:
            self.table.clearSelection()
            return
        self.model.ensure_row_loaded(row)
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, 0))

    def _sync_buttons(self) -> None:
        return

    def has_previous_model(self) -> bool:
        return self.model.previous_path_before(self.selected_path()) is not None

    def has_next_model(self) -> bool:
        selected_path = self.selected_path()
        if selected_path:
            return self.model.next_incomplete_path_after(selected_path) is not None
        return self.model.first_incomplete_path() is not None

    def open_previous_model(self) -> None:
        previous_path = self.model.previous_path_before(self.selected_path())
        if previous_path is not None:
            self.previous_model_requested.emit(previous_path)

    def _open_next_model(self) -> None:
        selected_path = self.selected_path()
        if selected_path:
            next_path = self.model.next_incomplete_path_after(selected_path)
        else:
            next_path = self.model.first_incomplete_path()
        if next_path is not None:
            self.next_model_requested.emit(next_path)

    def _on_scroll_changed(self, value: int) -> None:
        scroll_bar = self.table.verticalScrollBar()
        if value >= scroll_bar.maximum() - 8 and self.model.canFetchMore():
            self.model.fetchMore()

    def _on_top_level_changed(self, floating: bool) -> None:
        if floating:
            self._floating_resize_pending = True
            QTimer.singleShot(0, self._apply_floating_size)
        else:
            self._floating_resize_pending = False

    def _apply_floating_size(self) -> None:
        if not self._floating_resize_pending:
            return
        if not self.isFloating():
            self._floating_resize_pending = False
            return
        top_level = self.window()
        if top_level is None or not top_level.isVisible() or not top_level.isWindow():
            QTimer.singleShot(0, self._apply_floating_size)
            return
        top_level.resize(self._floating_size)
        self._floating_resize_pending = False


def _status_text(status: str) -> str:
    labels = {
        STATUS_UNLABELED: "Unlabeled",
        STATUS_IN_PROGRESS: "In Progress",
        STATUS_COMPLETED: "Completed",
        STATUS_FAILED: "Failed",
    }
    return labels.get(status, status)


def _status_color(status: str) -> QColor:
    colors = {
        STATUS_UNLABELED: QColor("#6b7280"),
        STATUS_IN_PROGRESS: QColor("#2563eb"),
        STATUS_COMPLETED: QColor("#15803d"),
        STATUS_FAILED: QColor("#b91c1c"),
    }
    return colors.get(status, QColor("#374151"))
