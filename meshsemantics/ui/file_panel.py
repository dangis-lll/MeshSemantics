from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from meshsemantics.core.project_dataset import (
    PENDING_STATUSES,
    ProjectDataset,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_UNLABELED,
)


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

        if role == Qt.ItemDataRole.FontRole and is_current:
            font = self.parent().font() if self.parent() is not None else None
            if font is not None:
                font.setBold(True)
                return font

        if role == Qt.ItemDataRole.ForegroundRole and status == STATUS_FAILED:
            return QColor("#8c2f2f")

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

    def set_current_path(self, path: str | None) -> None:
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
        filter_text = self._filter_text
        status_filter = self._status_filter

        for row, entry in enumerate(self._project.entries):
            if filter_text and filter_text not in entry.display_path.lower():
                continue

            status = self._status_for_entry(entry.work_path)
            if status_filter == "pending":
                if status not in PENDING_STATUSES:
                    continue
            elif status_filter != "all" and status != status_filter:
                continue

            visible_rows.append(row)

        return visible_rows

    def _rebuild_visible_path_index(self) -> None:
        self._visible_row_by_path = {}
        if self._project is None:
            return
        for visible_row, entry_row in enumerate(self._visible_entry_rows):
            entry = self._project.entries[entry_row]
            self._visible_row_by_path[entry.work_path] = visible_row
            self._visible_row_by_path[entry.source_path] = visible_row

    def _status_for_entry(self, work_path: str) -> str:
        return self._status_overrides.get(work_path, self._status_from_project(work_path))

    def _status_from_project(self, work_path: str) -> str:
        row = self._entry_row_by_path.get(work_path)
        if row is None or self._project is None:
            return STATUS_UNLABELED
        return self._project.entries[row].status

    def _entry_for_visible_row(self, visible_row: int):
        if self._project is None:
            return None
        if visible_row < 0 or visible_row >= self.rowCount():
            return None
        entry_row = self._visible_entry_rows[visible_row]
        return self._project.entries[entry_row]


class FilePanel(QDockWidget):
    open_requested = pyqtSignal(str)
    next_todo_requested = pyqtSignal()

    def __init__(self, cache_limit: int = 20, parent=None) -> None:
        super().__init__("Task Queue", parent)
        self.setObjectName("file-panel")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._cache_limit = cache_limit
        self._project: ProjectDataset | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_search_text)

        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        panel = QFrame()
        panel.setProperty("panel", True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search meshes")

        self.status_filter = QComboBox()
        self.status_filter.addItem("All", "all")
        self.status_filter.addItem("To Do", "pending")
        self.status_filter.addItem("Unlabeled", STATUS_UNLABELED)
        self.status_filter.addItem("In Progress", STATUS_IN_PROGRESS)
        self.status_filter.addItem("Completed", STATUS_COMPLETED)
        self.status_filter.addItem("Failed", STATUS_FAILED)

        controls.addWidget(self.search_edit, 1)
        controls.addWidget(self.status_filter)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.project_label = QLabel("No folder opened")
        self.project_label.setProperty("role", "caption")
        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "caption")
        self.next_todo_button = QPushButton("Next To Do")
        action_row.addWidget(self.project_label, 1)
        action_row.addWidget(self.summary_label)
        action_row.addWidget(self.next_todo_button)

        self.model = FileTableModel(self)

        self.table = QTableView()
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

        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "caption")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress_label.setVisible(False)

        panel_layout.addLayout(controls)
        panel_layout.addLayout(action_row)
        panel_layout.addWidget(self.table, 1)
        panel_layout.addWidget(self.progress_label)
        panel_layout.addWidget(self.progress)
        layout.addWidget(panel)
        self.setWidget(content)

        self.search_edit.textChanged.connect(self._queue_search_text)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        self.next_todo_button.clicked.connect(self.next_todo_requested.emit)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self._sync_buttons()

    def set_project(self, project: ProjectDataset | None) -> None:
        self._project = project
        if project is not None:
            self.project_label.setText(project.root_path)
        else:
            self.project_label.setText("No folder opened")
        self.model.set_project(project)
        self._update_summary()
        self._restore_selection(project.current_path if project is not None else None)
        self._sync_buttons()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.progress.setVisible(busy)
        self.progress_label.setVisible(busy)
        self.progress_label.setText(message)
        self.next_todo_button.setEnabled(not busy and bool(self._project and self._project.next_pending_path))
        self.table.setEnabled(not busy)

    def selected_path(self) -> str | None:
        current_index = self.table.currentIndex()
        if not current_index.isValid():
            return None
        return self.model.file_path_at(current_index.row())

    def stop(self) -> None:
        self._search_timer.stop()

    def set_current_path(self, path: str | None) -> None:
        self.model.set_current_path(path)
        self._restore_selection(path)
        self._sync_buttons()
        if self._project is not None:
            self._project = ProjectDataset(
                root_path=self._project.root_path,
                entries=self._project.entries,
                current_path=path,
                next_pending_path=self._project.next_pending_path,
                suggested_path=self._project.suggested_path,
            )

    def update_status(self, path: str, status: str, next_pending_path: str | None = None) -> None:
        self.model.update_status(path, status)
        if self._project is not None:
            self._project = ProjectDataset(
                root_path=self._project.root_path,
                entries=self._project.entries,
                current_path=self._project.current_path,
                next_pending_path=next_pending_path,
                suggested_path=self._project.suggested_path,
            )
        self._update_summary()
        self._sync_buttons()

    def _queue_search_text(self) -> None:
        self._search_timer.start()

    def _apply_search_text(self) -> None:
        self.model.set_filter_text(self.search_edit.text())
        self._update_summary()
        self._restore_selection(self._project.current_path if self._project is not None else None)
        self._sync_buttons()

    def _on_status_filter_changed(self) -> None:
        self.model.set_status_filter(self.status_filter.currentData())
        self._update_summary()
        self._restore_selection(self._project.current_path if self._project is not None else None)
        self._sync_buttons()

    def _emit_selected(self, index: QModelIndex) -> None:
        path = self.model.file_path_at(index.row())
        if path is not None:
            self.open_requested.emit(path)

    def _restore_selection(self, selected_path: str | None) -> None:
        if not selected_path:
            self.table.clearSelection()
            return
        row = self.model.row_for_path(selected_path)
        if row < 0:
            self.table.clearSelection()
            return
        while self.model.canFetchMore():
            if row < self.model.loaded_rows():
                break
            self.model.fetchMore()
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, 0))

    def _sync_buttons(self) -> None:
        busy = self.progress.isVisible()
        self.next_todo_button.setEnabled(not busy and bool(self._project and self._project.next_pending_path))

    def _update_summary(self) -> None:
        total = len(self._project.entries) if self._project is not None else 0
        visible = self.model.total_rows()
        loaded = self.model.loaded_rows()
        if total == 0:
            self.summary_label.setText("")
            return
        if visible == total:
            self.summary_label.setText(f"{loaded}/{total}")
            return
        self.summary_label.setText(f"{loaded}/{visible} shown")

    def _on_scroll_changed(self, value: int) -> None:
        scroll_bar = self.table.verticalScrollBar()
        if value >= scroll_bar.maximum() - 8 and self.model.canFetchMore():
            self.model.fetchMore()
            self._update_summary()


def _status_text(status: str) -> str:
    labels = {
        STATUS_UNLABELED: "Unlabeled",
        STATUS_IN_PROGRESS: "In Progress",
        STATUS_COMPLETED: "Completed",
        STATUS_FAILED: "Failed",
    }
    return labels.get(status, status)
