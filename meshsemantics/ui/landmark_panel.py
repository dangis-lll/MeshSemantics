from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class LandmarkPanel(QDockWidget):
    panel_activated = pyqtSignal()
    add_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(int, str)
    delete_requested = pyqtSignal(int)
    select_requested = pyqtSignal(int)
    pick_requested = pyqtSignal(int)
    save_requested = pyqtSignal()
    import_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__("Landmarks", parent)
        self.setObjectName("landmark-panel")
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._floating_size = QSize(420, 760)
        self._active_index = -1
        self._manual_name_width: int | None = None
        self._preserve_input_text = False

        content = QWidget(self)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        top = QFrame()
        top.setProperty("panel", True)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(12, 12, 12, 12)
        top_layout.setSpacing(10)

        caption = QLabel("Landmark Editor")
        caption.setProperty("role", "caption")
        top_layout.addWidget(caption)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Landmark name")
        self.name_edit.returnPressed.connect(self._emit_add_requested)
        self.add_button = QPushButton("Add")
        self.add_button.setAutoDefault(True)
        self.add_button.clicked.connect(self._emit_add_requested)
        self.rename_button = QPushButton("Rename")
        self.rename_button.clicked.connect(self._emit_rename_requested)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.add_button)
        row.addWidget(self.rename_button)
        top_layout.addLayout(row)

        self.selection_hint = QLabel("Double click a row to make it active, then pick a point on the mesh.")
        self.selection_hint.setWordWrap(True)
        self.selection_hint.setStyleSheet("color: #6e89ab; font-size: 11px;")
        top_layout.addWidget(self.selection_hint)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.pick_button = QPushButton("Pick On Mesh")
        self.pick_button.clicked.connect(self._emit_pick_requested)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._emit_delete_requested)
        self.pick_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delete_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.pick_button)
        action_row.addWidget(self.delete_button)
        top_layout.addLayout(action_row)

        table_frame = QFrame()
        table_frame.setProperty("panel", True)
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(12, 12, 12, 12)
        table_layout.setSpacing(8)

        table_label = QLabel("Landmark List")
        table_label.setProperty("role", "caption")
        table_layout.addWidget(table_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "X", "Y", "Z", "State"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(52)
        for column in range(self.table.columnCount()):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().sectionResized.connect(self._remember_manual_column_width)
        self.table.itemDoubleClicked.connect(self._emit_select_requested)
        self.table.itemSelectionChanged.connect(self._sync_name_from_selection)
        table_layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.import_button = QPushButton("Import JSON")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.export_button = QPushButton("Export JSON")
        self.export_button.clicked.connect(self.export_requested.emit)
        self.import_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.export_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer.addWidget(self.import_button)
        footer.addWidget(self.export_button)

        outer.addWidget(top)
        outer.addWidget(table_frame, 1)
        outer.addLayout(footer)
        self.setWidget(content)
        self._activation_widgets = [
            self,
            content,
            self.name_edit,
            self.add_button,
            self.rename_button,
            self.pick_button,
            self.delete_button,
            self.table,
            self.table.viewport(),
            self.import_button,
            self.export_button,
        ]
        for widget in self._activation_widgets:
            widget.installEventFilter(self)

        self.topLevelChanged.connect(self._on_top_level_changed)
        self._apply_default_column_widths()
        self._sync_action_state()

    def set_landmarks(self, landmarks: Iterable[dict], active_index: int = -1) -> None:
        rows = list(landmarks)
        self._active_index = int(active_index)
        selected_row = self.selected_row()
        panel_width = int(self.width())
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for row, landmark in enumerate(rows):
            coords = landmark.get("position")
            name = str(landmark.get("name") or f"Landmark {row + 1}")
            values = [name]
            if coords is None:
                values.extend(["-", "-", "-"])
            else:
                values.extend([f"{float(coords[idx]):.4f}" for idx in range(3)])
            state = "Active" if row == self._active_index else ""
            values.append(state)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self._apply_default_column_widths()

        if self._preserve_input_text:
            pass
        elif 0 <= self._active_index < self.table.rowCount():
            self.table.selectRow(self._active_index)
        elif 0 <= selected_row < self.table.rowCount():
            self.table.selectRow(selected_row)
        elif self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self.name_edit.clear()
        if not self._preserve_input_text:
            self._sync_name_from_selection()
        self._sync_action_state()
        if not self.isFloating() and panel_width > 0 and self.width() != panel_width:
            self.resize(panel_width, self.height())
        self._preserve_input_text = False

    def set_active_index(self, index: int) -> None:
        self._active_index = int(index)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 4)
            if item is not None:
                item.setText("Active" if row == self._active_index else "")
        if 0 <= index < self.table.rowCount():
            self.table.selectRow(index)
        self._sync_action_state()

    def focus_name_input(self, clear: bool = False) -> None:
        if clear:
            self.name_edit.clear()
        self.name_edit.setFocus()

    def preserve_input_text_once(self) -> None:
        self._preserve_input_text = True

    def select_row(self, index: int, update_input: bool = True) -> None:
        if index < 0 or index >= self.table.rowCount():
            return
        self.table.selectRow(index)
        if update_input:
            self._sync_name_from_selection()

    def selected_row(self) -> int:
        items = self.table.selectedItems()
        if not items:
            return -1
        return int(items[0].row())

    def set_pick_mode(self, active: bool, landmark_name: str | None = None) -> None:
        if active:
            target = landmark_name or "selected landmark"
            self.pick_button.setText(f"Click Mesh For {target}")
            self.selection_hint.setText("Pick mode is active. Left click the mesh to place the landmark.")
        else:
            self.pick_button.setText("Pick On Mesh")
            self.selection_hint.setText("Double click a row to make it active, then pick a point on the mesh.")

    def _on_top_level_changed(self, floating: bool) -> None:
        features = QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        self.setFeatures(features)
        if floating:
            QTimer.singleShot(0, self._apply_floating_size)

    def _apply_floating_size(self) -> None:
        if self.isFloating():
            self.resize(self._floating_size)
            self._apply_default_column_widths()

    def _emit_add_requested(self) -> None:
        name = self.name_edit.text().strip()
        self.add_requested.emit(name)

    def _emit_rename_requested(self) -> None:
        index = self.selected_row()
        if index < 0:
            return
        self.rename_requested.emit(index, self.name_edit.text().strip())

    def _emit_delete_requested(self) -> None:
        index = self.selected_row()
        if index < 0:
            return
        self.delete_requested.emit(index)

    def _emit_select_requested(self, item: QTableWidgetItem) -> None:
        self.select_requested.emit(int(item.row()))

    def _emit_pick_requested(self) -> None:
        index = self.selected_row()
        if index < 0:
            return
        self.pick_requested.emit(index)

    def _sync_name_from_selection(self) -> None:
        row = self.selected_row()
        if row < 0:
            if self.table.rowCount() == 0:
                self.name_edit.clear()
            self._sync_action_state()
            return
        name_item = self.table.item(row, 0)
        if name_item is not None:
            self.name_edit.setText(name_item.text())
        self._sync_action_state()

    def _sync_action_state(self) -> None:
        has_selection = self.selected_row() >= 0
        self.rename_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.pick_button.setEnabled(has_selection)
        self.export_button.setEnabled(self.table.rowCount() > 0)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._activation_widgets and event.type() in {
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        }:
            self.panel_activated.emit()
        if watched is self.table.viewport() and event.type() == event.Type.Resize:
            self._apply_default_column_widths()
        return super().eventFilter(watched, event)

    def _handle_delete_shortcut(self) -> None:
        if self.selected_row() >= 0:
            self._emit_delete_requested()

    def _remember_manual_column_width(self, logical_index: int, _old_size: int, new_size: int) -> None:
        if logical_index == 0:
            self._manual_name_width = int(new_size)

    def _apply_default_column_widths(self) -> None:
        header = self.table.horizontalHeader()
        available_width = max(180, self.table.viewport().width())
        fixed_widths = {
            1: 82,
            2: 82,
            3: 82,
            4: 72,
        }
        for column, width in fixed_widths.items():
            header.resizeSection(column, width)

        font_metrics = QFontMetrics(self.table.font())
        content_width = max(
            [font_metrics.horizontalAdvance("Name") + 28]
            + [
                font_metrics.horizontalAdvance(str(self.table.item(row, 0).text())) + 28
                for row in range(self.table.rowCount())
                if self.table.item(row, 0) is not None
            ]
        )
        reserved_width = sum(fixed_widths.values()) + 12
        target_width = max(140, available_width - reserved_width)
        if self._manual_name_width is not None:
            target_width = min(max(140, self._manual_name_width), max(140, available_width - reserved_width))
        else:
            target_width = min(max(140, content_width), max(140, available_width - reserved_width))
        header.resizeSection(0, target_width)
