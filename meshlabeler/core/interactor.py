from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from vtkmodules.vtkRenderingCore import vtkCellPicker

from meshlabeler.core.spline_selector import (
    build_vtk_spline,
    select_cells_by_screen_polygon,
    select_cells_by_surface_loop,
)


@dataclass
class InteractionState:
    mode: str = "NORMAL"
    control_points_2d: list[tuple[float, float]] = field(default_factory=list)
    control_points_3d: list[tuple[float, float, float]] = field(default_factory=list)
    spline_preview_cell_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    manual_cell_ids: set[int] = field(default_factory=set)
    excluded_spline_cell_ids: set[int] = field(default_factory=set)
    left_press_pos: tuple[float, float] | None = None
    left_dragging: bool = False
    vtk_drag_started: bool = False


class MeshInteractor(QObject):
    mode_changed = pyqtSignal(str)
    preview_changed = pyqtSignal(object)
    control_points_changed = pyqtSignal(object)
    apply_requested = pyqtSignal(object)
    message = pyqtSignal(str)

    def __init__(self, vedo_widget, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.vedo_widget = vedo_widget
        self.settings = settings
        self.state = InteractionState()
        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.0005)
        self._suppress_right_drag = False
        self._bind_events()

    def _bind_events(self) -> None:
        if self.vedo_widget.canvas is not None:
            self.vedo_widget.canvas.installEventFilter(self)

    def begin_spline(self) -> None:
        self.state.spline_preview_cell_ids = np.zeros(0, dtype=np.int32)
        self.state.excluded_spline_cell_ids.clear()
        self.state.control_points_2d.clear()
        self.state.control_points_3d.clear()
        self.state.mode = "SPLINE"
        self.mode_changed.emit(self.state.mode)
        self.control_points_changed.emit([])
        self._emit_selection_preview()
        self.message.emit("Spline mode: left click to place control points, Enter to preview.")

    def confirm_preview(self) -> None:
        if self.state.mode != "SPLINE":
            return
        if len(self.state.control_points_2d) < 3:
            self.message.emit("At least 3 control points are required.")
            return
        loop_points = self._build_surface_curve_points(closed=True)
        selected = select_cells_by_surface_loop(self.vedo_widget.mesh.dataset, loop_points)
        if selected.size == 0:
            polygon = np.asarray(self.state.control_points_2d, dtype=np.float64)
            selected = select_cells_by_screen_polygon(
                self.vedo_widget.renderer,
                self.vedo_widget.cell_centers,
                self.vedo_widget.cell_normals,
                polygon,
                exclude_backfaces=bool(self.settings.get("exclude_backfaces", True)),
                visible_only=True,
            )
        self.state.spline_preview_cell_ids = selected
        self.state.mode = "CONFIRM"
        self.control_points_changed.emit(
            {
                "control_points": self.state.control_points_3d.copy(),
                "surface_curve_points": loop_points,
                "closed": True,
            }
        )
        self._emit_selection_preview()
        self.mode_changed.emit(self.state.mode)
        self.message.emit(
            f"Previewing {self.current_selection().size} cells. Press E to apply or C to cancel."
        )

    def apply_preview(self) -> None:
        selection = self.current_selection()
        if selection.size:
            self.apply_requested.emit(selection)

    def clear_preview(self) -> None:
        self.state = InteractionState(mode="NORMAL")
        self.control_points_changed.emit([])
        self._emit_selection_preview()
        self.mode_changed.emit(self.state.mode)

    def eventFilter(self, watched, event) -> bool:
        if watched is not self.vedo_widget.canvas or self.vedo_widget.mesh is None:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.RightButton:
            self._suppress_right_drag = True
            return True

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                self._suppress_right_drag = True
                self._toggle_pick(*self._to_vtk_display(event.position().x(), event.position().y()))
                return True
            if event.button() == Qt.MouseButton.LeftButton and self.state.mode == "SPLINE":
                self.state.left_press_pos = (float(event.position().x()), float(event.position().y()))
                self.state.left_dragging = False
                self.state.vtk_drag_started = False
                return True

        if event.type() == QEvent.Type.MouseMove:
            if self._suppress_right_drag:
                return True
            if self.state.mode == "SPLINE" and self.state.left_press_pos is not None:
                dx = float(event.position().x()) - self.state.left_press_pos[0]
                dy = float(event.position().y()) - self.state.left_press_pos[1]
                if (dx * dx + dy * dy) ** 0.5 > 4.0:
                    self.state.left_dragging = True
                    if not self.state.vtk_drag_started:
                        self._forward_vtk_left_press(*self.state.left_press_pos)
                        self.state.vtk_drag_started = True
                    self._forward_vtk_mouse_move(float(event.position().x()), float(event.position().y()))
                return True

        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            self._suppress_right_drag = False
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.state.mode == "SPLINE":
                if self.state.vtk_drag_started:
                    self._forward_vtk_left_release(float(event.position().x()), float(event.position().y()))
                elif not self.state.left_dragging:
                    self._add_control_point(*self._to_vtk_display(event.position().x(), event.position().y()))
                self.state.left_press_pos = None
                self.state.left_dragging = False
                self.state.vtk_drag_started = False
                return True
            self.state.left_press_pos = None
            self.state.left_dragging = False
            self.state.vtk_drag_started = False
            return False

        return super().eventFilter(watched, event)

    def current_selection(self) -> np.ndarray:
        manual = np.asarray(sorted(self.state.manual_cell_ids), dtype=np.int32)
        spline = self.state.spline_preview_cell_ids.astype(np.int32)
        if spline.size and self.state.excluded_spline_cell_ids:
            excluded = np.asarray(sorted(self.state.excluded_spline_cell_ids), dtype=np.int32)
            spline = spline[~np.isin(spline, excluded)]
        if manual.size == 0:
            return spline.copy()
        if spline.size == 0:
            return manual.copy()
        return np.unique(np.concatenate([manual, spline])).astype(np.int32)

    def _add_control_point(self, x: float, y: float) -> None:
        self.picker.Pick(x, y, 0, self.vedo_widget.renderer)
        if self.picker.GetCellId() < 0:
            return
        self.state.control_points_2d.append((float(x), float(y)))
        self.state.control_points_3d.append(tuple(self.picker.GetPickPosition()))
        self.control_points_changed.emit(
            {
                "control_points": self.state.control_points_3d.copy(),
                "surface_curve_points": self._build_surface_curve_points(closed=False),
                "closed": False,
            }
        )
        self.message.emit(f"Added control point {len(self.state.control_points_2d)}.")

    def _toggle_pick(self, x: float, y: float) -> None:
        self.picker.Pick(x, y, 0, self.vedo_widget.renderer)
        cell_id = self.picker.GetCellId()
        if cell_id < 0:
            return
        cell_id = int(cell_id)
        if cell_id in self.state.manual_cell_ids:
            self.state.manual_cell_ids.remove(cell_id)
            action = "Deselected"
        elif cell_id in self.state.excluded_spline_cell_ids:
            self.state.excluded_spline_cell_ids.remove(cell_id)
            action = "Re-selected"
        elif np.any(self.state.spline_preview_cell_ids == cell_id):
            self.state.excluded_spline_cell_ids.add(cell_id)
            action = "Deselected"
        else:
            self.state.manual_cell_ids.add(cell_id)
            action = "Selected"
        self._emit_selection_preview()
        self.message.emit(f"{action} cell {cell_id}. Total selected: {self.current_selection().size}")

    def _emit_selection_preview(self) -> None:
        self.preview_changed.emit(self.current_selection())

    def _to_vtk_display(self, x: float, y: float) -> tuple[float, float]:
        canvas = self.vedo_widget.canvas
        dpr = float(canvas.devicePixelRatioF()) if hasattr(canvas, "devicePixelRatioF") else 1.0
        width = max(1.0, float(canvas.width()) * dpr)
        height = max(1.0, float(canvas.height()) * dpr)
        vtk_x = float(x) * dpr
        vtk_y = height - float(y) * dpr
        vtk_x = min(max(vtk_x, 0.0), width - 1.0)
        vtk_y = min(max(vtk_y, 0.0), height - 1.0)
        return vtk_x, vtk_y

    def _build_surface_curve_points(self, closed: bool = False) -> list[tuple[float, float, float]]:
        world_points = np.asarray(self.state.control_points_3d, dtype=np.float64).reshape(-1, 3)
        if world_points.shape[0] < 2:
            return [tuple(point) for point in world_points]
        curve_points = build_vtk_spline(
            world_points,
            samples=max(96, world_points.shape[0] * 24),
            closed=closed,
        )
        return [tuple(point) for point in curve_points]

    def _forward_vtk_left_press(self, x: float, y: float) -> None:
        self._set_vtk_event_position(x, y)
        self.vedo_widget.interactor.LeftButtonPressEvent()

    def _forward_vtk_mouse_move(self, x: float, y: float) -> None:
        self._set_vtk_event_position(x, y)
        self.vedo_widget.interactor.MouseMoveEvent()

    def _forward_vtk_left_release(self, x: float, y: float) -> None:
        self._set_vtk_event_position(x, y)
        self.vedo_widget.interactor.LeftButtonReleaseEvent()

    def _set_vtk_event_position(self, x: float, y: float) -> None:
        vtk_x, vtk_y = self._to_vtk_display(x, y)
        self.vedo_widget.interactor.SetEventInformationFlipY(
            int(round(vtk_x)),
            int(round(y)),
            0,
            0,
            "0",
            0,
            None,
        )
