from __future__ import annotations

import colorsys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ColorChip(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(48)
        self.setProperty("panel", True)

    def set_rgb(self, rgb: tuple[int, int, int]) -> None:
        self.setStyleSheet(
            f"background: rgb({rgb[0]}, {rgb[1]}, {rgb[2]});"
            "border-radius: 14px; border: 1px solid rgba(255,255,255,0.12);"
        )


class LabelPanel(QDockWidget):
    label_changed = pyqtSignal(int)
    colormap_changed = pyqtSignal(dict)
    remap_requested = pyqtSignal(int, int)

    def __init__(self, colormap: dict[str, tuple[int, int, int]], max_label: int, parent=None) -> None:
        super().__init__("Labels", parent)
        self.setObjectName("label-panel")
        self._colormap = dict(colormap)

        content = QWidget(self)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        top = QFrame()
        top.setProperty("panel", True)
        top_layout = QGridLayout(top)
        top_layout.setContentsMargins(12, 12, 12, 12)
        top_layout.setHorizontalSpacing(10)
        top_layout.setVerticalSpacing(8)

        caption = QLabel("Active Label")
        caption.setProperty("role", "caption")
        self.label_spin = QSpinBox()
        self.label_spin.setRange(0, max(0, max_label))
        self.label_spin.valueChanged.connect(self._on_label_value_changed)
        self.add_label_button = QPushButton("Add Label")
        self.add_label_button.clicked.connect(self.add_next_label)
        self.color_chip = ColorChip()

        top_layout.addWidget(caption, 0, 0, 1, 2)
        top_layout.addWidget(self.label_spin, 1, 0)
        top_layout.addWidget(self.add_label_button, 1, 1)
        top_layout.addWidget(self.color_chip, 2, 0, 1, 2)

        swap_frame = QFrame()
        swap_frame.setProperty("panel", True)
        swap_layout = QVBoxLayout(swap_frame)
        swap_layout.setContentsMargins(12, 12, 12, 12)
        swap_layout.setSpacing(8)

        swap_label = QLabel("Remap Label")
        swap_label.setProperty("role", "caption")
        row = QHBoxLayout()
        self.swap_a = QComboBox()
        self.swap_b = QComboBox()
        self.swap_button = QPushButton("Apply")
        self.swap_button.clicked.connect(self._emit_swap)
        row.addWidget(self.swap_a)
        row.addWidget(self.swap_b)
        row.addWidget(self.swap_button)

        swap_layout.addWidget(swap_label)
        swap_layout.addLayout(row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Label", "Color"])
        self.table.verticalHeader().setVisible(False)
        self.table.itemDoubleClicked.connect(self._edit_color)
        self.table.itemChanged.connect(self._sync_colormap_from_table)

        outer.addWidget(top)
        outer.addWidget(swap_frame)
        outer.addWidget(self.table, 1)
        self.setWidget(content)

        self.set_colormap(colormap)
        self.label_spin.setValue(0)

    def current_label(self) -> int:
        return int(self.label_spin.value())

    def colormap(self) -> dict[str, tuple[int, int, int]]:
        return dict(self._colormap)

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
            color_item.setBackground(QColor(*rgb))
            self.table.setItem(row, 0, label_item)
            self.table.setItem(row, 1, color_item)
        self.table.blockSignals(False)

        labels = [str(item[0]) for item in items]
        self.swap_a.clear()
        self.swap_b.clear()
        self.swap_a.addItems(labels)
        self.swap_b.addItems(labels)
        self._refresh_chip()

    def ensure_label(self, label: int) -> bool:
        key = str(int(label))
        if key in self._colormap:
            return False
        self._colormap[key] = self._generate_distinct_color(int(label))
        self.set_colormap(self._colormap)
        self.colormap_changed.emit(self.colormap())
        return True

    def add_next_label(self) -> None:
        labels = [int(key) for key in self._colormap.keys() if key not in {"0", "_default"}]
        next_label = max(labels, default=0) + 1
        if next_label > self.label_spin.maximum():
            return
        self.ensure_label(next_label)
        self.label_spin.setValue(next_label)

    def refresh_stats(self, total_cells: int, labeled_cells: int) -> None:
        self.setWindowTitle(f"Labels  |  {labeled_cells}/{total_cells}")

    def _emit_swap(self) -> None:
        if self.swap_a.currentText() and self.swap_b.currentText():
            self.remap_requested.emit(int(self.swap_a.currentText()), int(self.swap_b.currentText()))

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
        self.ensure_label(value)
        self._refresh_chip()
        self.label_changed.emit(value)

    def _generate_distinct_color(self, label: int) -> tuple[int, int, int]:
        hue = (label * 0.61803398875) % 1.0
        saturation = 0.68 + 0.12 * ((label % 3) / 2.0)
        value = 0.92 - 0.08 * (label % 2)
        rgb = colorsys.hsv_to_rgb(hue, min(saturation, 0.88), value)
        return tuple(int(channel * 255) for channel in rgb)
