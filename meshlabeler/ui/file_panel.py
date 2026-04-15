from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHeaderView,
    QLineEdit,
    QProgressBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from meshlabeler.core.file_io import FileIO


@dataclass
class FileEntry:
    path: Path
    modified_at: datetime
    reviewed: bool


class FileTableModel(QAbstractTableModel):
    HEADERS = ("文件名", "修改日期", "是否审核过")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[FileEntry] = []
        self._visible_entries: list[FileEntry] = []
        self._filter_text = ""

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
                return entry.path.name
            if column == 1:
                return entry.modified_at.strftime("%Y-%m-%d %H:%M:%S")
            if column == 2:
                return "是" if entry.reviewed else "否"

        if role == Qt.ItemDataRole.UserRole:
            return str(entry.path)

        if role == Qt.ItemDataRole.TextAlignmentRole and column in {1, 2}:
            return int(Qt.AlignmentFlag.AlignCenter)

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return str(section + 1)

    def set_entries(self, entries: list[FileEntry]) -> None:
        self.beginResetModel()
        self._entries = entries
        self._visible_entries = self._apply_filter(entries, self._filter_text)
        self.endResetModel()

    def set_filter_text(self, text: str) -> None:
        self.beginResetModel()
        self._filter_text = text.strip().lower()
        self._visible_entries = self._apply_filter(self._entries, self._filter_text)
        self.endResetModel()

    def file_path_at(self, row: int) -> str | None:
        if row < 0 or row >= len(self._visible_entries):
            return None
        return str(self._visible_entries[row].path)

    def row_for_path(self, path: str | Path) -> int:
        target = str(Path(path))
        for row, entry in enumerate(self._visible_entries):
            if str(entry.path) == target:
                return row
        return -1

    def _apply_filter(self, entries: list[FileEntry], text: str) -> list[FileEntry]:
        if not text:
            return list(entries)
        return [entry for entry in entries if text in entry.path.name.lower()]


class FilePanel(QDockWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, cache_limit: int = 20, parent=None) -> None:
        super().__init__("Files", parent)
        self.setObjectName("file-panel")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        self.root_path: Path | None = None

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
        self.search_edit.setPlaceholderText("筛选文件名")

        self.model = FileTableModel(self)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(80)
        self.table.horizontalHeader().resizeSection(0, 160)
        self.table.horizontalHeader().resizeSection(1, 170)
        self.table.horizontalHeader().resizeSection(2, 100)
        self.table.doubleClicked.connect(self._emit_selected)
        self.table.clicked.connect(self._emit_selected)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        panel_layout.addWidget(self.search_edit)
        panel_layout.addWidget(self.table, 1)
        panel_layout.addWidget(self.progress)
        layout.addWidget(panel)
        self.setWidget(content)

        self.search_edit.textChanged.connect(self.model.set_filter_text)

    def set_root_path(self, path: str, selected_path: str | None = None) -> None:
        root = Path(path)
        self.root_path = root
        entries = self._scan_entries(root)
        self.model.set_entries(entries)
        self._restore_selection(selected_path)

    def stop(self) -> None:
        return

    def refresh_entry_states(self) -> None:
        if self.root_path is not None:
            current_index = self.table.currentIndex()
            current_path = self.model.file_path_at(current_index.row()) if current_index.isValid() else None
            self.set_root_path(str(self.root_path), current_path)

    def _emit_selected(self, index: QModelIndex) -> None:
        path = self.model.file_path_at(index.row())
        if path is not None:
            self.file_selected.emit(path)

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

    def _scan_entries(self, root: Path) -> list[FileEntry]:
        if not root.exists():
            return []

        entries: list[FileEntry] = []
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in FileIO.SUPPORTED_SUFFIXES or not path.is_file():
                continue
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified_at = datetime.fromtimestamp(0)
            entries.append(
                FileEntry(
                    path=path,
                    modified_at=modified_at,
                    reviewed=self._is_reviewed(path),
                )
            )
        return entries

    def _is_reviewed(self, path: Path) -> bool:
        if path.suffix.lower() == ".vtp":
            return True
        sibling_json = path.with_suffix(".json")
        sibling_vtp = path.with_suffix(".vtp")
        return sibling_json.exists() or sibling_vtp.exists()
