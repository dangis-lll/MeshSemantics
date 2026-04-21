from __future__ import annotations

import colorsys
from pathlib import Path

from PyQt6 import uic
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QFrame,
    QAbstractSpinBox,
    QSizePolicy,
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from meshsemantics.config.defaults import preset_label_rgb


class ColorChip(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setProperty("panel", True)

    def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        self.setStyleSheet(
            f"background: rgb({rgb[0]}, {rgb[1]}, {rgb[2]});"
            "border-radius: 14px; border: 1px solid rgba(255,255,255,0.12);"
        )


class LabelPanel(QWidget):
    panel_activated = pyqtSignal()
    label_changed = pyqtSignal(int)
    colormap_changed = pyqtSignal(dict)
    remap_requested = pyqtSignal(int, int)
    delete_requested = pyqtSignal(int)
    overwrite_mode_changed = pyqtSignal(bool)
    completion_toggle_requested = pyqtSignal()
    quick_save_requested = pyqtSignal()

    def __init__(self, colormap: dict[str, tuple[int, int, int]], max_label: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("label-panel")
        self._colormap = dict(colormap)
        self._is_completed = False
        self._checkbox_unchecked_asset = self._asset_url("checkbox-indicator.png")
        self._checkbox_checked_asset = self._asset_url("checkbox-indicator-checked.png")
        uic.loadUi(str(Path(__file__).with_name("label_panel.ui")), self)

        content = self
        self._apply_ui_properties()
        self._replace_color_chip_placeholder()
        self._configure_widgets(max_label)
        self._bind_signals()
        self._activation_widgets = [
            self,
            content,
            self.label_spin,
            self.label_spin.lineEdit(),
            self.add_label_button,
            self.swap_a,
            self.swap_a.lineEdit(),
            self.swap_b,
            self.swap_b.lineEdit(),
            self.swap_button,
            self.table,
            self.table.viewport(),
            self.overwrite_checkbox,
            self.delete_label_button,
        ]
        for widget in self._activation_widgets:
            widget.installEventFilter(self)

        self.set_colormap(colormap)
        self.label_spin.setValue(0)

    def _apply_ui_properties(self) -> None:
        self.caption_label.setProperty("role", "caption")
        self.swap_label.setProperty("role", "caption")
        self.table_label.setProperty("role", "caption")
        self.top_frame.setProperty("panel", True)
        self.swap_frame.setProperty("panel", True)
        self.table_frame.setProperty("panel", True)

    def _replace_color_chip_placeholder(self) -> None:
        placeholder = self.color_chip_placeholder
        layout = self.active_row
        index = layout.indexOf(placeholder)
        self.color_chip = ColorChip(self)
        self.color_chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.insertWidget(index, self.color_chip, 1)
        layout.removeWidget(placeholder)
        placeholder.deleteLater()

    def _configure_widgets(self, max_label: int) -> None:
        self.label_spin.setRange(0, max(0, max_label))
        self.label_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.label_spin.lineEdit().setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.label_spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.swap_a.setRange(0, max(0, max_label))
        self.swap_b.setRange(0, max(0, max_label))
        self.swap_a.lineEdit().setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.swap_b.lineEdit().setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)

        self.overwrite_checkbox.setObjectName("overwrite-toggle")
        self.overwrite_checkbox.setChecked(False)
        self.overwrite_checkbox.setStyleSheet(
            self._indicator_checkbox_qss("overwrite-toggle", checked_color="#c73333")
        )
        self.add_label_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.delete_label_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Label", "Color"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def _bind_signals(self) -> None:
        self.label_spin.valueChanged.connect(self._on_label_value_changed)
        self.add_label_button.clicked.connect(self.add_next_label)
        self.swap_button.clicked.connect(self._emit_swap)
        self.overwrite_checkbox.clicked.connect(self._emit_overwrite_mode_changed)
        self.delete_label_button.clicked.connect(self._emit_delete_label)
        self.table.itemDoubleClicked.connect(self._edit_color)
        self.table.itemChanged.connect(self._sync_colormap_from_table)
        self.table.itemSelectionChanged.connect(self._sync_current_label_from_selection)

    def current_label(self) -> int:
        return int(self.label_spin.value())

    def colormap(self) -> dict[str, tuple[int, int, int]]:
        return dict(self._colormap)

    def snapshot_state(self) -> dict:
        return {
            "colormap": self.colormap(),
            "current_label": self.current_label(),
            "overwrite_existing": self.overwrite_existing_labels(),
        }

    def restore_state(self, state: dict) -> None:
        colormap = state.get("colormap", self.colormap())
        current_label = int(state.get("current_label", 0))
        overwrite_existing = bool(state.get("overwrite_existing", False))
        self.set_colormap(colormap)
        self.set_overwrite_existing_labels(overwrite_existing)
        self.label_spin.setValue(current_label)

    def set_colormap(self, colormap: dict[str, tuple[int, int, int]]) -> None:
        self._colormap = dict(colormap)
        self.table.blockSignals(True)
        items = sorted(
            ((k, v) for k, v in self._colormap.items() if k != "_default"),
            key=lambda item: int(item[0]),
        )
        self.table.setRowCount(len(items))
        for row, (key, rgb) in enumerate(items):
            label_item = QTableWidgetItem(str(key))
            color_item = QTableWidgetItem(f"{rgb[0]}, {rgb[1]}, {rgb[2]}")
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            color_item.setFlags(color_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            color_item.setBackground(QColor(*rgb))
            self.table.setItem(row, 0, label_item)
            self.table.setItem(row, 1, color_item)
        self.table.blockSignals(False)

        max_existing_label = self._max_existing_label()
        self.label_spin.setRange(0, max_existing_label)
        self.swap_a.setRange(0, max_existing_label)
        self.swap_b.setRange(0, max_existing_label)
        self._select_row_for_label(self.current_label())
        self._refresh_chip()

    def ensure_label(self, label: int) -> bool:
        key = str(int(label))
        if key in self._colormap:
            return False
        self._colormap[key] = self._generate_distinct_color(int(label))
        self.set_colormap(self._colormap)
        self.colormap_changed.emit(self.colormap())
        return True

    def ensure_labels(self, labels: list[int]) -> bool:
        changed = False
        for label in sorted({int(v) for v in labels if int(v) >= 0}):
            key = str(label)
            if key in self._colormap:
                continue
            self._colormap[key] = self._generate_distinct_color(label)
            changed = True
        if changed:
            self.set_colormap(self._colormap)
            self.colormap_changed.emit(self.colormap())
        return changed

    def add_next_label(self) -> None:
        next_label = self._max_existing_label() + 1
        if self.ensure_label(next_label):
            self.label_spin.setValue(next_label)

    def remove_label(self, label: int) -> bool:
        key = str(int(label))
        if key == "0" or key not in self._colormap:
            return False
        previous_label = self._previous_existing_label(int(label))
        del self._colormap[key]
        self.set_colormap(self._colormap)
        if self.current_label() == int(label):
            self.label_spin.setValue(previous_label)
        self.colormap_changed.emit(self.colormap())
        return True

    def refresh_stats(self, total_cells: int, labeled_cells: int) -> None:
        self.setToolTip(f"Labels | {labeled_cells}/{total_cells}")

    def set_completion_state(self, is_completed: bool) -> None:
        self._is_completed = bool(is_completed)

    def overwrite_existing_labels(self) -> bool:
        return self.overwrite_checkbox.isChecked()

    def set_overwrite_existing_labels(self, enabled: bool) -> None:
        self.overwrite_checkbox.blockSignals(True)
        self.overwrite_checkbox.setChecked(bool(enabled))
        self.overwrite_checkbox.blockSignals(False)

    def set_current_label(self, label: int, sync_remap_source: bool = True) -> None:
        label = int(label)
        self.label_spin.setValue(label)
        if sync_remap_source:
            self.swap_a.blockSignals(True)
            self.swap_a.setValue(label)
            self.swap_a.blockSignals(False)

    def _asset_url(self, filename: str) -> str:
        path = Path(__file__).resolve().parents[1] / "assets" / filename
        return path.as_posix()

    def _completion_checkbox_qss(self) -> str:
        return self._indicator_checkbox_qss("completion-toggle", checked_color="#c73333")

    def _indicator_checkbox_qss(self, object_name: str, checked_color: str = "#2f5f9a") -> str:
        return (
            f"QCheckBox#{object_name} {{"
            " color: #334a68;"
            " font-weight: 600;"
            " spacing: 8px;"
            " min-height: 16px;"
            " padding: 8px 14px;"
            " border: 1px solid rgba(132, 162, 210, 0.34);"
            " border-radius: 10px;"
            " background: rgba(255, 255, 255, 0.98);"
            "}"
            f"QCheckBox#{object_name}:checked {{"
            f" color: {checked_color};"
            " font-weight: 800;"
            "}"
            f"QCheckBox#{object_name}:hover {{"
            " background: rgba(245, 249, 255, 0.98);"
            "}"
            f"QCheckBox#{object_name}::indicator {{"
            " width: 18px;"
            " height: 18px;"
            f" image: url({self._checkbox_unchecked_asset});"
            "}"
            f"QCheckBox#{object_name}::indicator:checked {{"
            f" image: url({self._checkbox_checked_asset});"
            "}"
        )

    def _emit_swap(self) -> None:
        self.remap_requested.emit(int(self.swap_a.value()), int(self.swap_b.value()))

    def _emit_delete_label(self) -> None:
        label = self.selected_table_label()
        if label <= 0:
            return
        self.delete_requested.emit(label)

    def _emit_overwrite_mode_changed(self) -> None:
        self.overwrite_mode_changed.emit(self.overwrite_existing_labels())

    def _edit_color(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        current = item.background().color()
        color = QColorDialog.getColor(current, self, "Pick label color")
        if not color.isValid():
            return
        item.setText(f"{color.red()}, {color.green()}, {color.blue()}")
        item.setBackground(color)
        self._sync_colormap_from_table()

    def _sync_colormap_from_table(self) -> None:
        next_map = {"_default": self._colormap.get("_default", (220, 220, 80))}
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            color_item = self.table.item(row, 1)
            if not label_item or not color_item:
                continue
            try:
                label = str(int(label_item.text()))
                parts = [int(p.strip()) for p in color_item.text().split(",")]
                if len(parts) != 3:
                    continue
                next_map[label] = tuple(max(0, min(255, v)) for v in parts)
            except Exception:
                continue
        if "0" not in next_map:
            next_map["0"] = self._colormap.get("0", (204, 204, 204))
        self._colormap = next_map
        self._refresh_chip()
        self.colormap_changed.emit(self.colormap())

    def _refresh_chip(self) -> None:
        rgb = self._colormap.get(
            str(self.current_label()),
            self._colormap.get("_default", (220, 220, 80)),
        )
        self.color_chip.set_rgb(rgb)

    def _on_label_value_changed(self, value: int) -> None:
        self._select_row_for_label(value)
        self.swap_a.blockSignals(True)
        self.swap_a.setValue(int(value))
        self.swap_a.blockSignals(False)
        self._refresh_chip()
        self.label_changed.emit(value)

    def selected_table_label(self) -> int:
        items = self.table.selectedItems()
        if not items:
            return 0
        label_item = self.table.item(items[0].row(), 0)
        if label_item is None:
            return 0
        try:
            return int(label_item.text())
        except ValueError:
            return 0

    def _sync_current_label_from_selection(self) -> None:
        label = self.selected_table_label()
        if label == self.current_label():
            return
        self.label_spin.blockSignals(True)
        self.label_spin.setValue(label)
        self.label_spin.blockSignals(False)
        self.swap_a.blockSignals(True)
        self.swap_a.setValue(label)
        self.swap_a.blockSignals(False)
        self._refresh_chip()
        self.label_changed.emit(label)

    def _select_row_for_label(self, label: int) -> None:
        target = str(int(label))
        self.table.blockSignals(True)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            if label_item is not None and label_item.text() == target:
                self.table.selectRow(row)
                break
        self.table.blockSignals(False)

    def _previous_existing_label(self, label: int) -> int:
        labels = sorted(
            int(key) for key in self._colormap.keys()
            if key not in {"0", "_default"} and int(key) < int(label)
        )
        return labels[-1] if labels else 0

    def _max_existing_label(self) -> int:
        labels = [
            int(key)
            for key in self._colormap.keys()
            if key != "_default"
        ]
        return max(labels, default=0)

    def _generate_distinct_color(self, label: int) -> tuple[int, int, int]:
        preset = preset_label_rgb(label)
        if preset is not None:
            return tuple(preset)
        hue = (label * 0.61803398875) % 1.0
        saturation = 0.68 + 0.12 * ((label % 3) / 2.0)
        value = 0.92 - 0.08 * (label % 2)
        rgb = colorsys.hsv_to_rgb(hue, min(saturation, 0.88), value)
        return tuple(int(channel * 255) for channel in rgb)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._activation_widgets and event.type() in {
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        }:
            self.panel_activated.emit()
        return super().eventFilter(watched, event)
