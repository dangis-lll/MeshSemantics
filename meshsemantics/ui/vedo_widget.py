from __future__ import annotations

import numpy as np
import vedo
from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkLookupTable
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor


class MeshSemanticsVTKCanvas(QVTKRenderWindowInteractor):
    _ALLOWED_PLAIN_KEYS = {
        Qt.Key.Key_S,
        Qt.Key.Key_E,
        Qt.Key.Key_C,
        Qt.Key.Key_M,
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
        Qt.Key.Key_Delete,
        Qt.Key.Key_Backspace,
    }
    _ALLOWED_CTRL_KEYS = {
        Qt.Key.Key_S,
        Qt.Key.Key_Z,
        Qt.Key.Key_Y,
    }

    def _should_block_key(self, event) -> bool:
        key = event.key()
        if key in {
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        }:
            return True

        modifiers = event.modifiers()
        allowed_mask = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        if modifiers & ~allowed_mask:
            return True

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return key not in self._ALLOWED_CTRL_KEYS

        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                return False
            return True

        return key not in self._ALLOWED_PLAIN_KEYS

    def keyPressEvent(self, event) -> None:
        if self._should_block_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if self._should_block_key(event):
            event.accept()
            return
        super().keyReleaseEvent(event)


class VedoWidget(QWidget):
    mesh_loaded = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame(self)
        self.shell.setObjectName("vedo-shell")
        self.shell.setProperty("panel", True)
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.shell)

        self.canvas = MeshSemanticsVTKCanvas(self.shell)
        shell_layout.addWidget(self.canvas)

        self.plotter = vedo.Plotter(qt_widget=self.canvas, bg="#edf3f8")
        self.renderer = self.plotter.renderer
        self.interactor = self.plotter.interactor
        self.render_window = self.canvas.GetRenderWindow()
        self._interactor_initialized = False
        self._render_scheduled = False

        self.mesh = None
        self.base_labels = np.zeros(0, dtype=np.int32)
        self.display_labels = np.zeros(0, dtype=np.int32)
        self.preview_cell_ids = np.zeros(0, dtype=np.int32)
        self._label_vtk_array = None
        self.lookup_table = vtkLookupTable()
        self.lookup_table.IndexedLookupOff()
        self.lookup_table.SetNumberOfTableValues(257)
        self.lookup_table.Build()

        self.cell_centers = np.zeros((0, 3), dtype=np.float64)
        self.cell_normals = np.zeros((0, 3), dtype=np.float64)
        self.control_points_actor = None
        self.control_line_actor = None
        self.selected_control_actor = None
        self.landmark_points_actor = None
        self.selected_landmark_actor = None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_interactor_ready()
        self._schedule_render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_render()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if not self.window().isMinimized():
                self._ensure_interactor_ready()
                self._schedule_render()

    def event(self, event) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.Expose:
            self._schedule_render()
        return handled

    def set_mesh(self, mesh, labels: np.ndarray, colormap: dict[str, tuple[int, int, int]]) -> None:
        self.plotter.clear()
        self.mesh = mesh.clone(deep=True)
        self.mesh.filename = getattr(mesh, "filename", "")
        self.base_labels = np.asarray(labels, dtype=np.int32).reshape(-1).copy()
        self.display_labels = self.base_labels.copy()
        self.preview_cell_ids = np.zeros(0, dtype=np.int32)
        self._rebuild_geometry_cache()
        self._apply_lookup_table(colormap)
        self._bind_label_array()
        self._mark_label_array_modified()
        self.mesh.lighting("default").phong().linecolor("#000000").linewidth(0.08)
        self._ensure_interactor_ready()
        self.plotter.show(self.mesh, resetcam=True)
        self._schedule_render()
        self.mesh_loaded.emit(int(self.base_labels.size))

    def clear_mesh(self) -> None:
        self.plotter.clear()
        self.mesh = None
        self.base_labels = np.zeros(0, dtype=np.int32)
        self.display_labels = np.zeros(0, dtype=np.int32)
        self.preview_cell_ids = np.zeros(0, dtype=np.int32)
        self._label_vtk_array = None
        self.cell_centers = np.zeros((0, 3), dtype=np.float64)
        self.cell_normals = np.zeros((0, 3), dtype=np.float64)
        self.control_points_actor = None
        self.control_line_actor = None
        self.selected_control_actor = None
        self.landmark_points_actor = None
        self.selected_landmark_actor = None
        self.render()

    def set_colormap(self, colormap: dict[str, tuple[int, int, int]]) -> None:
        if self.mesh is None:
            return
        self._apply_lookup_table(colormap)
        self.render()

    def update_labels(self, labels: np.ndarray) -> None:
        incoming = np.asarray(labels, dtype=np.int32).reshape(-1)
        if self.base_labels.shape != incoming.shape:
            self.base_labels = incoming.copy()
            self.display_labels = self.base_labels.copy()
            self.preview_cell_ids = np.zeros(0, dtype=np.int32)
            self._bind_label_array()
        else:
            self.base_labels[:] = incoming
            self.display_labels[:] = self.base_labels
            self.preview_cell_ids = np.zeros(0, dtype=np.int32)
        self._mark_label_array_modified()
        self.render()

    def preview_cells(self, cell_ids) -> None:
        if self.mesh is None:
            return
        ids = np.asarray(cell_ids, dtype=np.int32).reshape(-1)
        ids = ids[(ids >= 0) & (ids < self.display_labels.size)]
        previous_ids = self.preview_cell_ids
        if previous_ids.size:
            self.display_labels[previous_ids] = self.base_labels[previous_ids]
        if ids.size:
            self.display_labels[ids] = 256
        self.preview_cell_ids = ids
        self._mark_label_array_modified()
        self.render()

    def render(self) -> None:
        self._ensure_interactor_ready()
        if self.plotter.window:
            self.plotter.render()
        if self.render_window is not None:
            self.render_window.Render()
        self.canvas.update()

    def set_control_points(self, payload) -> None:
        if self.mesh is None:
            return
        self._remove_control_actors()
        control_points = payload
        curve_points = payload
        closed = False
        selected_index = -1
        if isinstance(payload, dict):
            control_points = payload.get("control_points", [])
            curve_points = payload.get("surface_curve_points", control_points)
            closed = bool(payload.get("closed", False))
            selected_index = int(payload.get("selected_index", -1))

        points = np.asarray(control_points, dtype=np.float64).reshape(-1, 3)
        path_points = np.asarray(curve_points, dtype=np.float64).reshape(-1, 3)
        if points.size == 0:
            self.render()
            return
        self.control_points_actor = vedo.Points(points, r=12).c("#00e5ff")
        self.plotter.add(self.control_points_actor)
        if 0 <= selected_index < len(points):
            self.selected_control_actor = vedo.Points(points[[selected_index]], r=18).c("#ffcc33")
            self.plotter.add(self.selected_control_actor)
        if len(path_points) >= 2:
            line_points = path_points
            if closed and len(path_points) >= 3 and not np.allclose(path_points[0], path_points[-1]):
                line_points = np.vstack([path_points, path_points[0]])
            self.control_line_actor = vedo.Line(line_points).c("#00e5ff").lw(3)
            self.plotter.add(self.control_line_actor)
        self.render()

    def set_landmarks(self, landmarks: list[dict], active_index: int = -1) -> None:
        if self.mesh is None:
            return
        self._remove_landmark_actors()
        visible_points = []
        active_point = None
        for index, landmark in enumerate(landmarks):
            point = landmark.get("position")
            if point is None:
                continue
            point_tuple = tuple(float(value) for value in point)
            visible_points.append(point_tuple)
            if index == active_index:
                active_point = point_tuple

        if visible_points:
            self.landmark_points_actor = vedo.Points(np.asarray(visible_points, dtype=np.float64), r=16).c("#ff5c7a")
            self.plotter.add(self.landmark_points_actor)
        if active_point is not None:
            self.selected_landmark_actor = vedo.Points(np.asarray([active_point], dtype=np.float64), r=24).c("#ffd166")
            self.plotter.add(self.selected_landmark_actor)
        self.render()

    def _apply_lookup_table(self, colormap: dict[str, tuple[int, int, int]]) -> None:
        lut = self.lookup_table
        lut.SetTableRange(0.0, 256.0)
        for index in range(257):
            rgb = colormap.get(str(index), colormap.get("_default", (220, 220, 80)))
            if index == 256:
                rgb = (136, 136, 136)
            lut.SetTableValue(index, rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)
        lut.Build()

    def _bind_label_array(self) -> None:
        if self.mesh is None:
            return
        self._label_vtk_array = numpy_to_vtk(self.display_labels, deep=False)
        self._label_vtk_array.SetName("Label")
        self.mesh.dataset.GetCellData().SetScalars(self._label_vtk_array)
        self.mesh.dataset.GetCellData().SetActiveScalars("Label")
        mapper = self.mesh.mapper
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToMapScalars()
        mapper.SetLookupTable(self.lookup_table)
        mapper.SetScalarRange(0.0, 256.0)
        mapper.ScalarVisibilityOn()

    def _mark_label_array_modified(self) -> None:
        if self.mesh is None:
            return
        if self._label_vtk_array is None:
            self._bind_label_array()
        if self._label_vtk_array is not None:
            self._label_vtk_array.Modified()
        self.mesh.dataset.GetCellData().SetActiveScalars("Label")
        self.mesh.dataset.GetCellData().Modified()
        self.mesh.dataset.Modified()
        mapper = self.mesh.mapper
        mapper.Modified()
        mapper.Update()
        self.mesh.modified()

    def _rebuild_geometry_cache(self) -> None:
        if self.mesh is None:
            self.cell_centers = np.zeros((0, 3), dtype=np.float64)
            self.cell_normals = np.zeros((0, 3), dtype=np.float64)
            return
        self.cell_centers = np.asarray(self.mesh.cell_centers().coordinates, dtype=np.float64)
        self.mesh.compute_normals(cells=True, points=False)
        normals = self.mesh.dataset.GetCellData().GetNormals()
        if normals is None:
            self.cell_normals = np.zeros_like(self.cell_centers)
        else:
            self.cell_normals = vtk_to_numpy(normals).astype(np.float64)

    def _remove_control_actors(self) -> None:
        for actor in [self.control_points_actor, self.control_line_actor, self.selected_control_actor]:
            if actor is not None:
                self.plotter.remove(actor)
        self.control_points_actor = None
        self.control_line_actor = None
        self.selected_control_actor = None

    def _remove_landmark_actors(self) -> None:
        for actor in [self.landmark_points_actor, self.selected_landmark_actor]:
            if actor is not None:
                self.plotter.remove(actor)
        self.landmark_points_actor = None
        self.selected_landmark_actor = None

    def _ensure_interactor_ready(self) -> None:
        if self._interactor_initialized or self.interactor is None:
            return
        try:
            self.interactor.Initialize()
        except Exception:
            return
        self._interactor_initialized = True

    def _schedule_render(self) -> None:
        if self._render_scheduled:
            return
        self._render_scheduled = True
        QTimer.singleShot(0, self._flush_render)

    def _flush_render(self) -> None:
        self._render_scheduled = False
        if not self.isVisible() or self.window().isMinimized():
            return
        self.render()
