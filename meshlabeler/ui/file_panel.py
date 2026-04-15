from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
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

from meshlabeler.core.project_dataset import (
    PENDING_STATUSES,
    ProjectDataset,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_UNLABELED,
)


@dataclass(frozen=True)
class DisplayEntry:
    display_path: str
    modified_at: datetime
    status: str
    work_path: str
    is_current: bool


class FileTableModel(QAbstractTableModel):
    HEADERS = ("Mesh", "Status", "Modified")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[DisplayEntry] = []
        self._visible_entries: list[DisplayEntry] = []
        self._filter_text = ""
        self._status_filter = "all"

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        entry = self._visible_entries[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return entry.display_path
            if column == 1:
                return _status_text(entry.status)
            if column == 2:
                return entry.modified_at.strftime("%Y-%m-%d %H:%M:%S")

        if role == Qt.ItemDataRole.UserRole:
            return entry.work_path

        if role == Qt.ItemDataRole.ToolTipRole:
            return entry.work_path

        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 2}:
            return int(Qt.AlignmentFlag.AlignCenter)

        if role == Qt.ItemDataRole.FontRole and entry.is_current:
            font = self.parent().font() if self.parent() is not None else None
            if font is not None:
                font.setBold(True)
                return font

        if role == Qt.ItemDataRole.ForegroundRole and entry.status == STATUS_FAILED:
            return QColor("#8c2f2f")

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return str(section + 1)

    def set_entries(self, entries: list[DisplayEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self._visible_entries = self._apply_filters()
        self.endResetModel()

    def set_filter_text(self, text: str) -> None:
        self.beginResetModel()
        self._filter_text = text.strip().lower()
        self._visible_entries = self._apply_filters()
        self.endResetModel()

    def set_status_filter(self, status_filter: str) -> None:
        self.beginResetModel()
        self._status_filter = status_filter
        self._visible_entries = self._apply_filters()
        self.endResetModel()

    def file_path_at(self, row: int) -> str | None:
        if row < 0 or row >= len(self._visible_entries):
            return None
        return self._visible_entries[row].work_path

    def row_for_path(self, path: str | None) -> int:
        if not path:
            return -1
        for row, entry in enumerate(self._visible_entries):
            if entry.work_path == path:
                return row
        return -1

    def _apply_filters(self) -> list[DisplayEntry]:
        entries = list(self._entries)
        if self._filter_text:
            entries = [entry for entry in entries if self._filter_text in entry.display_path.lower()]
        if self._status_filter == "pending":
            entries = [entry for entry in entries if entry.status in PENDING_STATUSES]
        elif self._status_filter != "all":
            entries = [entry for entry in entries if entry.status == self._status_filter]
        return entries


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
        self.next_todo_button = QPushButton("Next To Do")
        action_row.addWidget(self.project_label, 1)
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

        self.search_edit.textChanged.connect(self.model.set_filter_text)
        self.status_filter.currentIndexChanged.connect(self._on_status_filter_changed)
        self.next_todo_button.clicked.connect(self.next_todo_requested.emit)
        self._sync_buttons()

    def set_project(self, project: ProjectDataset | None) -> None:
        self._project = project
        entries = []
        if project is not None:
            entries = [
                DisplayEntry(
                    display_path=entry.display_path,
                    modified_at=entry.modified_at,
                    status=entry.status,
                    work_path=entry.work_path,
                    is_current=entry.is_current,
                )
                for entry in project.entries
            ]
            self.project_label.setText(project.root_path)
        else:
            self.project_label.setText("No folder opened")
        self.model.set_entries(entries)
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
        return

    def _on_status_filter_changed(self) -> None:
        self.model.set_status_filter(self.status_filter.currentData())
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
        self.table.selectRow(row)
        self.table.scrollTo(self.model.index(row, 0))

    def _sync_buttons(self) -> None:
        busy = self.progress.isVisible()
        self.next_todo_button.setEnabled(not busy and bool(self._project and self._project.next_pending_path))


def _status_text(status: str) -> str:
    labels = {
        STATUS_UNLABELED: "Unlabeled",
        STATUS_IN_PROGRESS: "In Progress",
        STATUS_COMPLETED: "Completed",
        STATUS_FAILED: "Failed",
    }
    return labels.get(status, status)
