from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from vtkmodules.vtkRenderingCore import vtkCellPicker

from meshsemantics.core.spline_selector import (
    build_surface_contour_line,
    project_world_to_display,
    select_cells_by_surface_loop,
)


@dataclass
class InteractionState:
    mode: str = "NORMAL"
    control_points_3d: list[tuple[float, float, float]] = field(default_factory=list)
    curve_points_3d: list[tuple[float, float, float]] = field(default_factory=list)
    spline_preview_cell_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    manual_cell_ids: set[int] = field(default_factory=set)
    excluded_spline_cell_ids: set[int] = field(default_factory=set)
    hover_point_index: int = -1
    hover_segment_index: int = -1
    closed: bool = False
    left_press_pos: tuple[float, float] | None = None
    left_dragging: bool = False
    vtk_drag_started: bool = False


class MeshInteractor(QObject):
    mode_changed = pyqtSignal(str)
    preview_changed = pyqtSignal(object)
    control_points_changed = pyqtSignal(object)
    history_requested = pyqtSignal(object)
    apply_requested = pyqtSignal(object)
    surface_double_clicked = pyqtSignal(object, int)
    landmark_picked = pyqtSignal(object)
    message = pyqtSignal(str)

    POINT_HIT_RADIUS_PX = 14.0
    SEGMENT_HIT_RADIUS_PX = 10.0
    PICK_RETRY_RADIUS_PX = 6.0

    def __init__(self, vedo_widget, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.vedo_widget = vedo_widget
        self.settings = settings
        self.state = InteractionState()
        self._interaction_context = "label"
        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.002)
        self._suppress_right_drag = False
        self._bind_events()

    def _bind_events(self) -> None:
        if self.vedo_widget.canvas is not None:
            self.vedo_widget.canvas.installEventFilter(self)

    def begin_spline(self) -> None:
        if self._interaction_context != "label":
            return
        self.state.spline_preview_cell_ids = np.zeros(0, dtype=np.int32)
        self.state.excluded_spline_cell_ids.clear()
        self.state.control_points_3d.clear()
        self.state.curve_points_3d.clear()
        self.state.hover_point_index = -1
        self.state.hover_segment_index = -1
        self.state.closed = False
        self.state.left_press_pos = None
        self.state.left_dragging = False
        self.state.vtk_drag_started = False
        self.state.mode = "SPLINE"
        self.mode_changed.emit(self.state.mode)
        self._emit_control_overlay()
        self._emit_selection_preview()
        self.message.emit(
            "Spline mode: left click to add points, drag to rotate the model, click the first point to close, Enter to preview."
        )

    def confirm_preview(self, emit_visuals: bool = True) -> bool:
        if self._interaction_context != "label" or self.state.mode != "SPLINE":
            return False
        if len(self.state.control_points_3d) < 3:
            self.message.emit("At least 3 control points are required.")
            return False
        loop_points = self._build_contour_line_points(closed=True)
        if len(loop_points) < 3:
            self.message.emit("The contour line is invalid. Adjust the control points and try again.")
            return False
        selected = select_cells_by_surface_loop(self.vedo_widget.mesh.dataset, loop_points)
        self.state.spline_preview_cell_ids = selected
        self.state.curve_points_3d = [tuple(point) for point in loop_points]
        self.state.closed = True
        self.state.mode = "CONFIRM"
        if emit_visuals:
            self._emit_control_overlay()
            self._emit_selection_preview()
            self.mode_changed.emit(self.state.mode)
        if selected.size == 0:
            self.message.emit("Surface-loop clipping found no cells. Adjust the contour and try again.")
        else:
            if emit_visuals:
                self.message.emit(
                    f"Previewing {self.current_selection().size} cells. Press E to apply or C to cancel."
                )
        return True

    def apply_preview(self) -> None:
        if self._interaction_context != "label":
            return
        if self.state.mode == "SPLINE":
            if not self.confirm_preview(emit_visuals=False):
                return
        selection = self.current_selection()
        if selection.size:
            self.apply_requested.emit(selection)

    def clear_preview(self, emit_preview: bool = True) -> None:
        self.state = InteractionState(mode="NORMAL")
        self._emit_control_overlay()
        if emit_preview:
            self._emit_selection_preview()
        self.mode_changed.emit(self.state.mode)

    def delete_highlighted_control_point(self) -> bool:
        if self._interaction_context != "label" or self.state.mode not in {"SPLINE", "CONFIRM"}:
            return False
        return self._delete_highlighted_control_point()

    def begin_landmark_pick(self) -> None:
        if self._interaction_context != "landmark":
            return
        self.state.mode = "LANDMARK_PICK"
        self.mode_changed.emit(self.state.mode)
        self.message.emit("Landmark pick mode: left click the mesh to place the active landmark.")

    def set_interaction_context(self, context: str) -> None:
        if context not in {"label", "landmark", "meshdoctor"}:
            return
        self._interaction_context = context
        self._suppress_right_drag = False

    def snapshot_state(self) -> dict:
        return {
            "mode": str(self.state.mode),
            "control_points_3d": [tuple(float(v) for v in point) for point in self.state.control_points_3d],
            "curve_points_3d": [tuple(float(v) for v in point) for point in self.state.curve_points_3d],
            "spline_preview_cell_ids": self.state.spline_preview_cell_ids.astype(np.int32).copy(),
            "manual_cell_ids": tuple(sorted(int(cell_id) for cell_id in self.state.manual_cell_ids)),
            "excluded_spline_cell_ids": tuple(sorted(int(cell_id) for cell_id in self.state.excluded_spline_cell_ids)),
            "hover_point_index": int(self.state.hover_point_index),
            "hover_segment_index": int(self.state.hover_segment_index),
            "closed": bool(self.state.closed),
        }

    def restore_state(self, snapshot: dict | None) -> None:
        if not isinstance(snapshot, dict):
            self.clear_preview()
            return
        self.state = InteractionState(
            mode=str(snapshot.get("mode", "NORMAL")),
            control_points_3d=[
                tuple(float(v) for v in point)
                for point in snapshot.get("control_points_3d", [])
            ],
            curve_points_3d=[
                tuple(float(v) for v in point)
                for point in snapshot.get("curve_points_3d", [])
            ],
            spline_preview_cell_ids=np.asarray(
                snapshot.get("spline_preview_cell_ids", np.zeros(0, dtype=np.int32)),
                dtype=np.int32,
            ).copy(),
            manual_cell_ids=set(int(cell_id) for cell_id in snapshot.get("manual_cell_ids", ())),
            excluded_spline_cell_ids=set(int(cell_id) for cell_id in snapshot.get("excluded_spline_cell_ids", ())),
            hover_point_index=int(snapshot.get("hover_point_index", -1)),
            hover_segment_index=int(snapshot.get("hover_segment_index", -1)),
            closed=bool(snapshot.get("closed", False)),
        )
        self.state.left_press_pos = None
        self.state.left_dragging = False
        self.state.vtk_drag_started = False
        self._emit_control_overlay()
        self._emit_selection_preview()
        self.mode_changed.emit(self.state.mode)

    def eventFilter(self, watched, event) -> bool:
        if watched is not self.vedo_widget.canvas or self.vedo_widget.mesh is None:
            return super().eventFilter(watched, event)

        if self.state.mode == "LANDMARK_PICK":
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                self.clear_preview(emit_preview=False)
                self.message.emit("Landmark pick cancelled.")
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                point = self._pick_surface_point(*self._to_vtk_display(event.position().x(), event.position().y()))
                if point is None:
                    self.message.emit("No surface point found under the cursor.")
                    return True
                self.clear_preview(emit_preview=False)
                self.landmark_picked.emit(point)
                return True

        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.RightButton:
            self._suppress_right_drag = True
            return True
        if (
            event.type() == QEvent.Type.MouseButtonDblClick
            and event.button() == Qt.MouseButton.LeftButton
            and self.state.mode == "NORMAL"
        ):
            self._emit_double_clicked_surface(*self._to_vtk_display(event.position().x(), event.position().y()))
            return True

        if (
            self._interaction_context == "label"
            and event.type() == QEvent.Type.KeyPress
            and self.state.mode in {"SPLINE", "CONFIRM"}
        ):
            key = event.key()
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.delete_highlighted_control_point():
                    return True

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton and self._interaction_context == "label":
                self._suppress_right_drag = True
                self._toggle_pick(*self._to_vtk_display(event.position().x(), event.position().y()))
                return True
            if event.button() == Qt.MouseButton.LeftButton and self.state.mode == "SPLINE":
                self.state.left_press_pos = (float(event.position().x()), float(event.position().y()))
                self.state.left_dragging = False
                self.state.vtk_drag_started = False
                self._update_hover_from_display(self._to_vtk_display(event.position().x(), event.position().y()))
                return True

        if event.type() == QEvent.Type.MouseMove:
            if self._suppress_right_drag:
                return True
            if self.state.mode == "SPLINE":
                if event.buttons() & Qt.MouseButton.MiddleButton:
                    return False
                return self._handle_spline_mouse_move(float(event.position().x()), float(event.position().y()))

        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
            self._suppress_right_drag = False
            return True

        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.state.mode == "SPLINE":
                return self._handle_spline_left_release(float(event.position().x()), float(event.position().y()))

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

    def _handle_spline_left_press(self, x: float, y: float) -> bool:
        display = self._to_vtk_display(x, y)
        before_snapshot = self.snapshot_state()
        point_index, point_dist = self._find_nearest_control_point(display)
        if point_index == 0 and len(self.state.control_points_3d) >= 3 and point_dist <= self.POINT_HIT_RADIUS_PX:
            self.state.closed = True
            self.state.curve_points_3d = self._build_contour_line_points(closed=True)
            self._emit_control_overlay()
            self.history_requested.emit(before_snapshot)
            self.message.emit("Contour closed. Press Enter to preview the selection.")
            return True

        segment_index, segment_dist = self._find_nearest_curve_segment(display)
        if segment_index >= 0 and segment_dist <= self.SEGMENT_HIT_RADIUS_PX:
            world_point = self._pick_surface_point(*display)
            if world_point is None:
                return True
            insert_index = min(segment_index + 1, len(self.state.control_points_3d))
            self.state.control_points_3d.insert(insert_index, world_point)
            self.state.hover_point_index = insert_index
            self._update_curve_preview()
            self.history_requested.emit(before_snapshot)
            self.message.emit(f"Inserted control point {insert_index + 1}.")
            return True

        world_point = self._pick_surface_point(*display)
        if world_point is None:
            return True
        self.state.control_points_3d.append(world_point)
        self.state.hover_point_index = len(self.state.control_points_3d) - 1
        self.state.closed = False
        self._update_curve_preview()
        self.history_requested.emit(before_snapshot)
        self.message.emit(f"Added control point {len(self.state.control_points_3d)}.")
        return True

    def _handle_spline_mouse_move(self, x: float, y: float) -> bool:
        display = self._to_vtk_display(x, y)
        if self.state.left_press_pos is not None:
            dx = float(x) - self.state.left_press_pos[0]
            dy = float(y) - self.state.left_press_pos[1]
            if (dx * dx + dy * dy) ** 0.5 > 4.0:
                self.state.left_dragging = True
                self.state.hover_point_index = -1
                self.state.hover_segment_index = -1
                if not self.state.vtk_drag_started:
                    self._forward_vtk_left_press(*self.state.left_press_pos)
                    self.state.vtk_drag_started = True
                self._forward_vtk_mouse_move(x, y)
                return True
        self._update_hover_from_display(display)
        return True

    def _handle_spline_left_release(self, x: float, y: float) -> bool:
        was_dragging = self.state.left_dragging
        vtk_drag_started = self.state.vtk_drag_started
        self.state.left_press_pos = None
        self.state.left_dragging = False
        self.state.vtk_drag_started = False
        if was_dragging:
            if vtk_drag_started:
                self._forward_vtk_left_release(x, y)
            self._update_hover_from_display(self._to_vtk_display(x, y))
            return True
        return self._handle_spline_left_press(x, y)

    def _update_hover_from_display(self, display: tuple[float, float]) -> None:
        previous_point_index = self.state.hover_point_index
        previous_segment_index = self.state.hover_segment_index
        point_index, point_dist = self._find_nearest_control_point(display)
        segment_index, segment_dist = self._find_nearest_curve_segment(display)
        self.state.hover_point_index = point_index if point_dist <= self.POINT_HIT_RADIUS_PX else -1
        self.state.hover_segment_index = segment_index if segment_dist <= self.SEGMENT_HIT_RADIUS_PX else -1
        if (
            self.state.hover_point_index == previous_point_index
            and self.state.hover_segment_index == previous_segment_index
        ):
            return
        self._emit_control_overlay()

    def _update_curve_preview(self) -> None:
        self.state.curve_points_3d = self._build_contour_line_points(closed=self.state.closed)
        self._emit_control_overlay()

    def _emit_control_overlay(self) -> None:
        self.control_points_changed.emit(
            {
                "control_points": self.state.control_points_3d.copy(),
                "surface_curve_points": self.state.curve_points_3d.copy(),
                "closed": bool(self.state.closed),
                "selected_index": int(self.state.hover_point_index),
            }
        )

    def _delete_highlighted_control_point(self) -> bool:
        index = self.state.hover_point_index
        if index < 0 and self.state.control_points_3d:
            index = len(self.state.control_points_3d) - 1
        if index < 0 or index >= len(self.state.control_points_3d):
            return False
        before_snapshot = self.snapshot_state()
        del self.state.control_points_3d[index]
        self.state.hover_point_index = -1
        if len(self.state.control_points_3d) < 3:
            self.state.closed = False
        self._update_curve_preview()
        self.history_requested.emit(before_snapshot)
        self.message.emit(f"Removed control point {index + 1}.")
        return True

    def _find_nearest_control_point(self, display: tuple[float, float]) -> tuple[int, float]:
        if not self.state.control_points_3d:
            return -1, float("inf")
        projected = self._project_curve_to_display(self.state.control_points_3d)
        if projected.size == 0:
            return -1, float("inf")
        target = np.asarray(display, dtype=np.float64)
        dists = np.linalg.norm(projected - target[None, :], axis=1)
        index = int(np.argmin(dists))
        return index, float(dists[index])

    def _find_nearest_curve_segment(self, display: tuple[float, float]) -> tuple[int, float]:
        curve = np.asarray(self.state.curve_points_3d, dtype=np.float64).reshape(-1, 3)
        if curve.shape[0] < 2:
            return -1, float("inf")
        display_curve = self._project_curve_to_display(curve)
        if display_curve.shape[0] < 2:
            return -1, float("inf")
        segment_count = display_curve.shape[0] - 1
        if self.state.closed and curve.shape[0] >= 3:
            segment_count = display_curve.shape[0] - 1
        point = np.asarray(display, dtype=np.float64)
        best_index = -1
        best_dist = float("inf")
        for idx in range(segment_count):
            dist = self._distance_to_segment(point, display_curve[idx], display_curve[idx + 1])
            if dist < best_dist:
                best_dist = dist
                best_index = idx
        return best_index, best_dist

    def _project_curve_to_display(
        self, world_points: list[tuple[float, float, float]] | np.ndarray
    ) -> np.ndarray:
        points = np.asarray(world_points, dtype=np.float64).reshape(-1, 3)
        if points.size == 0:
            return np.zeros((0, 2), dtype=np.float64)
        return project_world_to_display(self.vedo_widget.renderer, points)

    def _distance_to_segment(self, point: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        ab = b - a
        denom = float(np.dot(ab, ab))
        if denom <= 1e-12:
            return float(np.linalg.norm(point - a))
        t = float(np.clip(np.dot(point - a, ab) / denom, 0.0, 1.0))
        closest = a + t * ab
        return float(np.linalg.norm(point - closest))

    def _pick_surface_point(self, x: float, y: float) -> tuple[float, float, float] | None:
        candidate_offsets = [
            (0.0, 0.0),
            (-self.PICK_RETRY_RADIUS_PX, 0.0),
            (self.PICK_RETRY_RADIUS_PX, 0.0),
            (0.0, -self.PICK_RETRY_RADIUS_PX),
            (0.0, self.PICK_RETRY_RADIUS_PX),
            (-self.PICK_RETRY_RADIUS_PX, -self.PICK_RETRY_RADIUS_PX),
            (-self.PICK_RETRY_RADIUS_PX, self.PICK_RETRY_RADIUS_PX),
            (self.PICK_RETRY_RADIUS_PX, -self.PICK_RETRY_RADIUS_PX),
            (self.PICK_RETRY_RADIUS_PX, self.PICK_RETRY_RADIUS_PX),
        ]
        for dx, dy in candidate_offsets:
            self.picker.Pick(x + dx, y + dy, 0, self.vedo_widget.renderer)
            if self.picker.GetCellId() >= 0:
                return tuple(float(v) for v in self.picker.GetPickPosition())
        return None

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

    def _emit_double_clicked_surface(self, x: float, y: float) -> None:
        self.picker.Pick(x, y, 0, self.vedo_widget.renderer)
        cell_id = int(self.picker.GetCellId())
        if cell_id >= 0:
            point = tuple(float(v) for v in self.picker.GetPickPosition())
            self.surface_double_clicked.emit(point, cell_id)

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
        vtk_x, _vtk_y = self._to_vtk_display(x, y)
        self.vedo_widget.interactor.SetEventInformationFlipY(
            int(round(vtk_x)),
            int(round(y)),
            0,
            0,
            "0",
            0,
            None,
        )

    def _build_contour_line_points(self, closed: bool = False) -> list[tuple[float, float, float]]:
        world_points = np.asarray(self.state.control_points_3d, dtype=np.float64).reshape(-1, 3)
        if world_points.shape[0] < 2:
            return [tuple(point) for point in world_points]
        curve_points = build_surface_contour_line(
            self.vedo_widget.mesh.dataset,
            world_points,
            closed=closed,
        )
        return [tuple(point) for point in curve_points]
