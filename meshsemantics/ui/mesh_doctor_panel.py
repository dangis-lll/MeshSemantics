from __future__ import annotations

from dataclasses import asdict

from PyQt6 import uic
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractSpinBox, QLabel, QWidget

from meshsemantics.core.mesh_doctor import MeshDoctorCheckConfig, MeshDoctorRepairOptions, MeshDoctorReport, format_report
from meshsemantics.runtime import ui_path


class MeshDoctorPanel(QWidget):
    panel_activated = pyqtSignal()
    analyze_requested = pyqtSignal(dict)
    repair_requested = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("meshdoctor-panel")
        self._busy = False
        self._report_collapsed = True
        uic.loadUi(str(ui_path("mesh_doctor_panel.ui")), self)

        self._count_labels: dict[str, QLabel] = {
            "non_manifold": self.non_manifold_count_label,
            "self_intersection": self.self_intersection_count_label,
            "highly_creased": self.highly_creased_count_label,
            "spike": self.spike_count_label,
            "small_component": self.small_component_count_label,
            "small_tunnel": self.small_tunnel_count_label,
            "small_hole": self.small_hole_count_label,
        }
        self._check_rows = [
            (self.non_manifold_checkbox, self.non_manifold_count_label),
            (self.self_intersection_checkbox, self.self_intersection_count_label),
            (self.small_component_checkbox, self.small_component_count_label),
            (self.small_hole_checkbox, self.small_hole_count_label),
            (self.triangle_only_label, self.triangle_count_label),
        ]
        self._option_rows = [
            (self.max_component_size_label, self.max_component_size_spin, self.max_component_size_unit_label),
            (self.max_hole_perimeter_label, self.max_hole_perimeter_spin, self.max_hole_perimeter_unit_label),
        ]
        self._apply_ui_properties()
        self._configure_widgets()
        self._bind_signals()
        self._install_activation_filters()
        self.clear_report()

    def _apply_ui_properties(self) -> None:
        self.checks_frame.setProperty("panel", True)
        self.options_frame.setProperty("panel", True)
        self.repair_options_frame.setProperty("panel", True)
        self.actions_frame.setProperty("panel", True)
        self.report_frame.setProperty("panel", True)
        self.caption_label.setProperty("role", "caption")
        self.report_caption.setProperty("role", "caption")
        self.status_label.setStyleSheet("color: #20324a; font-weight: 600;")
        self.stats_label.setStyleSheet("color: #5a7397;")
        self.stats_label.setWordWrap(True)

    def _configure_widgets(self) -> None:
        for widget in (
            self.max_component_size_spin,
            self.max_tunnel_size_spin,
            self.max_hole_perimeter_spin,
            self.spike_sensitivity_spin,
            self.expand_level_spin,
        ):
            widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            widget.setAlignment(Qt.AlignmentFlag.AlignLeft)
            widget.setMinimumHeight(40)

        self.max_component_size_spin.setRange(0.0, 100000000.0)
        self.max_component_size_spin.setDecimals(2)
        self.max_component_size_spin.setValue(5.0)

        self.max_tunnel_size_spin.setRange(0.0, 100000000.0)
        self.max_tunnel_size_spin.setDecimals(2)
        self.max_tunnel_size_spin.setValue(2.5)

        self.max_hole_perimeter_spin.setRange(0.0, 100000000.0)
        self.max_hole_perimeter_spin.setDecimals(2)
        self.max_hole_perimeter_spin.setValue(2.5)

        self.spike_sensitivity_spin.setRange(0, 100)
        self.spike_sensitivity_spin.setValue(50)

        self.expand_level_spin.setRange(0, 100)
        self.expand_level_spin.setValue(2)

        self.report_toggle_button.setCheckable(True)
        self.report_toggle_button.setChecked(False)
        self.report_toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.report_toggle_button.setFixedWidth(84)

        for label in self._count_labels.values():
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #5a7397; min-width: 28px;")
        self.triangle_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.triangle_count_label.setStyleSheet("color: #5a7397; min-width: 28px;")
        self.caption_label.setText("Mesh Check")
        if hasattr(self, "report_caption"):
            self.report_caption.setText("Report")
        self.highly_creased_checkbox.setChecked(False)
        self.highly_creased_checkbox.hide()
        self.highly_creased_count_label.hide()
        self.spike_checkbox.setChecked(False)
        self.spike_checkbox.hide()
        self.spike_count_label.hide()
        self.small_tunnel_checkbox.setChecked(False)
        self.small_tunnel_checkbox.hide()
        self.small_tunnel_count_label.hide()
        self.spike_sensitivity_label.hide()
        self.spike_sensitivity_spin.hide()
        self.spike_sensitivity_unit_label.hide()
        self.max_tunnel_size_label.hide()
        self.max_tunnel_size_spin.hide()
        self.max_tunnel_size_unit_label.hide()
        self.expand_level_label.hide()
        self.expand_level_spin.hide()
        self.expand_level_unit_label.hide()
        self.merge_points_checkbox.setText("Merge duplicate points")
        self.fill_holes_checkbox.setText("Fill small holes")
        self.keep_largest_checkbox.setText("Keep largest component only")
        self.recompute_normals_checkbox.setText("Recompute normals")
        self._apply_responsive_layout()

    def _bind_signals(self) -> None:
        self.analyze_button.clicked.connect(self._emit_analyze_requested)
        self.repair_button.clicked.connect(self._emit_repair_requested)
        self.report_toggle_button.clicked.connect(self._toggle_report)

    def _install_activation_filters(self) -> None:
        self._wheel_block_widgets = [
            self.max_component_size_spin,
            self.max_component_size_spin.lineEdit(),
            self.max_tunnel_size_spin,
            self.max_tunnel_size_spin.lineEdit(),
            self.max_hole_perimeter_spin,
            self.max_hole_perimeter_spin.lineEdit(),
            self.spike_sensitivity_spin,
            self.spike_sensitivity_spin.lineEdit(),
            self.expand_level_spin,
            self.expand_level_spin.lineEdit(),
        ]
        self._activation_widgets = [
            self,
            self.checks_frame,
            self.options_frame,
            self.repair_options_frame,
            self.actions_frame,
            self.report_frame,
            self.non_manifold_checkbox,
            self.self_intersection_checkbox,
            self.highly_creased_checkbox,
            self.spike_checkbox,
            self.small_component_checkbox,
            self.small_tunnel_checkbox,
            self.small_hole_checkbox,
            self.max_component_size_spin,
            self.max_component_size_spin.lineEdit(),
            self.max_tunnel_size_spin,
            self.max_tunnel_size_spin.lineEdit(),
            self.max_hole_perimeter_spin,
            self.max_hole_perimeter_spin.lineEdit(),
            self.spike_sensitivity_spin,
            self.spike_sensitivity_spin.lineEdit(),
            self.expand_level_spin,
            self.expand_level_spin.lineEdit(),
            self.merge_points_checkbox,
            self.fill_holes_checkbox,
            self.keep_largest_checkbox,
            self.recompute_normals_checkbox,
            self.analyze_button,
            self.repair_button,
            self.report_toggle_button,
            self.report_edit,
            self.report_edit.viewport(),
        ]
        for widget in self._activation_widgets:
            widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched in self._wheel_block_widgets and event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        if watched in self._activation_widgets and event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress}:
            self.panel_activated.emit()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        content_width = max(0, self.scroll_area.viewport().width())
        self._relayout_checks(single_column=True)
        self._relayout_options(compact=content_width < 360)
        self._relayout_actions(vertical=content_width < 360)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _relayout_checks(self, single_column: bool) -> None:
        self._clear_layout(self.checks_layout)
        if single_column:
            self.checks_layout.addWidget(self.caption_label, 0, 0, 1, 2)
            for row, (label_widget, count_widget) in enumerate(self._check_rows, start=1):
                self.checks_layout.addWidget(label_widget, row, 0)
                self.checks_layout.addWidget(count_widget, row, 1)
            return

        self.checks_layout.addWidget(self.caption_label, 0, 0, 1, 4)
        for index, (label_widget, count_widget) in enumerate(self._check_rows):
            row = 1 + index // 2
            column = (index % 2) * 2
            self.checks_layout.addWidget(label_widget, row, column)
            self.checks_layout.addWidget(count_widget, row, column + 1)

    def _relayout_options(self, compact: bool) -> None:
        self._clear_layout(self.options_layout)
        if compact:
            row = 0
            for label_widget, editor_widget, unit_widget in self._option_rows:
                self.options_layout.addWidget(label_widget, row, 0, 1, 2)
                self.options_layout.addWidget(editor_widget, row + 1, 0)
                self.options_layout.addWidget(unit_widget, row + 1, 1)
                row += 2
            return

        for row, (label_widget, editor_widget, unit_widget) in enumerate(self._option_rows):
            self.options_layout.addWidget(label_widget, row, 0)
            self.options_layout.addWidget(editor_widget, row, 1)
            self.options_layout.addWidget(unit_widget, row, 2)

    def _relayout_actions(self, vertical: bool) -> None:
        self._clear_layout(self.actions_layout)
        if vertical:
            self.actions_layout.addWidget(self.analyze_button)
            self.actions_layout.addWidget(self.repair_button)
            return

        self.actions_layout.addWidget(self.analyze_button, 1)
        self.actions_layout.addWidget(self.repair_button, 1)

    def _toggle_report(self) -> None:
        self._set_report_collapsed(not self.report_toggle_button.isChecked())

    def _set_report_collapsed(self, collapsed: bool) -> None:
        self._report_collapsed = bool(collapsed)
        self.report_toggle_button.blockSignals(True)
        self.report_toggle_button.setChecked(not self._report_collapsed)
        self.report_toggle_button.setText("Hide" if not self._report_collapsed else "Show")
        self.report_toggle_button.blockSignals(False)
        self.report_edit.setVisible(not self._report_collapsed)

    def check_config(self) -> MeshDoctorCheckConfig:
        return MeshDoctorCheckConfig(
            non_manifold=self.non_manifold_checkbox.isChecked(),
            self_intersection=self.self_intersection_checkbox.isChecked(),
            highly_creased=self.highly_creased_checkbox.isChecked(),
            spike=self.spike_checkbox.isChecked(),
            small_component=self.small_component_checkbox.isChecked(),
            small_tunnel=self.small_tunnel_checkbox.isChecked(),
            small_hole=self.small_hole_checkbox.isChecked(),
            max_component_size=float(self.max_component_size_spin.value()),
            max_tunnel_size=float(self.max_tunnel_size_spin.value()),
            max_hole_perimeter=float(self.max_hole_perimeter_spin.value()),
            spike_sensitivity=int(self.spike_sensitivity_spin.value()),
            expand_level=int(self.expand_level_spin.value()),
        )

    def repair_options(self) -> MeshDoctorRepairOptions:
        return MeshDoctorRepairOptions(
            merge_points=self.merge_points_checkbox.isChecked(),
            remove_small_components=self.small_component_checkbox.isChecked(),
            fill_holes=self.fill_holes_checkbox.isChecked(),
            keep_largest_component=self.keep_largest_checkbox.isChecked(),
            recompute_normals=self.recompute_normals_checkbox.isChecked(),
        )

    def build_request_payload(self) -> dict:
        return {
            "check_config": asdict(self.check_config()),
            "repair_options": asdict(self.repair_options()),
        }

    def set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = bool(busy)
        for widget in (
            self.non_manifold_checkbox,
            self.self_intersection_checkbox,
            self.highly_creased_checkbox,
            self.spike_checkbox,
            self.small_component_checkbox,
            self.small_tunnel_checkbox,
            self.small_hole_checkbox,
            self.max_component_size_spin,
            self.max_tunnel_size_spin,
            self.max_hole_perimeter_spin,
            self.spike_sensitivity_spin,
            self.expand_level_spin,
            self.merge_points_checkbox,
            self.fill_holes_checkbox,
            self.keep_largest_checkbox,
            self.recompute_normals_checkbox,
            self.analyze_button,
            self.repair_button,
            self.report_toggle_button,
        ):
            widget.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def clear_report(self) -> None:
        self.status_label.setText("Ready.")
        self.stats_label.setText("Not checked yet.")
        self.report_edit.setPlainText("Click Analyze to check the mesh.\nSafe Cleanup only does low-risk fixes.")
        self.triangle_count_label.setText("-")
        for label in self._count_labels.values():
            label.setText("-")
        self._set_report_collapsed(True)

    def show_report(self, report: MeshDoctorReport, prefix: str | None = None) -> None:
        issue_count = len(report.issues)
        if issue_count == 0:
            self.status_label.setText(prefix or "No obvious problems found.")
        else:
            self.status_label.setText(prefix or f"Found {issue_count} problem(s).")
        self.stats_label.setText(
            f"{report.cell_count} cells, {report.point_count} points, {report.triangle_cell_count} triangle cells"
        )
        self.triangle_count_label.setText(str(report.triangle_cell_count))
        for key, label in self._count_labels.items():
            result = report.result_for(key)
            label.setText(str(result.count) if result is not None else "-")
        self.report_edit.setPlainText(format_report(report))
        self._set_report_collapsed(True)

    def append_note(self, text: str) -> None:
        current = self.report_edit.toPlainText().strip()
        next_text = text.strip()
        self.report_edit.setPlainText(f"{current}\n\n{next_text}" if current else next_text)

    def _emit_analyze_requested(self) -> None:
        self.analyze_requested.emit(self.build_request_payload())

    def _emit_repair_requested(self) -> None:
        self.repair_requested.emit(self.build_request_payload())
