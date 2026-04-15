from __future__ import annotations

import numpy as np
import vedo
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkLookupTable
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor


class VedoWidget(QWidget):
    mesh_loaded = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        shell = QFrame(self)
        shell.setProperty("panel", True)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(shell)

        self.canvas = QVTKRenderWindowInteractor(shell)
        shell_layout.addWidget(self.canvas)

        self.plotter = vedo.Plotter(qt_widget=self.canvas, bg="#09111f")
        self.renderer = self.plotter.renderer
        self.interactor = self.plotter.interactor

        self.mesh = None
        self.base_labels = np.zeros(0, dtype=np.int32)
        self.display_labels = np.zeros(0, dtype=np.int32)
        self.lookup_table = vtkLookupTable()
        self.lookup_table.IndexedLookupOff()
        self.lookup_table.SetNumberOfTableValues(257)
        self.lookup_table.Build()

        self.cell_centers = np.zeros((0, 3), dtype=np.float64)
        self.cell_normals = np.zeros((0, 3), dtype=np.float64)
        self.control_points_actor = None
        self.control_line_actor = None

    def set_mesh(self, mesh, labels: np.ndarray, colormap: dict[str, tuple[int, int, int]]) -> None:
        self.plotter.clear()
        self.mesh = mesh.clone(deep=True)
        self.mesh.filename = getattr(mesh, "filename", "")
        self.base_labels = np.asarray(labels, dtype=np.int32).reshape(-1)
        self.display_labels = self.base_labels.copy()
        self._rebuild_geometry_cache()
        self._apply_lookup_table(colormap)
        self._write_display_labels()
        self.mesh.lighting("default").phong().linewidth(0.2)
        self.plotter.show(self.mesh, resetcam=True)
        self.mesh_loaded.emit(int(self.base_labels.size))

    def set_colormap(self, colormap: dict[str, tuple[int, int, int]]) -> None:
        if self.mesh is None:
            return
        self._apply_lookup_table(colormap)
        self.render()

    def update_labels(self, labels: np.ndarray) -> None:
        self.base_labels = np.asarray(labels, dtype=np.int32).reshape(-1)
        self.display_labels = self.base_labels.copy()
        self._write_display_labels()
        self.render()

    def preview_cells(self, cell_ids) -> None:
        if self.mesh is None:
            return
        self.display_labels = self.base_labels.copy()
        ids = np.asarray(cell_ids, dtype=np.int32).reshape(-1)
        ids = ids[(ids >= 0) & (ids < self.display_labels.size)]
        self.display_labels[ids] = 256
        self._write_display_labels()
        self.render()

    def render(self) -> None:
        if self.plotter.window:
            self.plotter.render()

    def set_control_points(self, payload) -> None:
        if self.mesh is None:
            return
        self._remove_control_actors()
        control_points = payload
        curve_points = payload
        closed = False
        if isinstance(payload, dict):
            control_points = payload.get("control_points", [])
            curve_points = payload.get("surface_curve_points", control_points)
            closed = bool(payload.get("closed", False))

        points = np.asarray(control_points, dtype=np.float64).reshape(-1, 3)
        path_points = np.asarray(curve_points, dtype=np.float64).reshape(-1, 3)
        if points.size == 0:
            self.render()
            return
        self.control_points_actor = vedo.Points(points, r=12).c("#00e5ff")
        self.plotter.add(self.control_points_actor)
        if len(path_points) >= 2:
            line_points = path_points
            if closed and len(path_points) >= 3 and not np.allclose(path_points[0], path_points[-1]):
                line_points = np.vstack([path_points, path_points[0]])
            self.control_line_actor = vedo.Line(line_points).c("#00e5ff").lw(3)
            self.plotter.add(self.control_line_actor)
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

    def _write_display_labels(self) -> None:
        if self.mesh is None:
            return
        vtk_array = numpy_to_vtk(self.display_labels.astype(np.int32), deep=True)
        vtk_array.SetName("Label")
        self.mesh.dataset.GetCellData().SetScalars(vtk_array)
        self.mesh.dataset.GetCellData().SetActiveScalars("Label")
        self.mesh.dataset.Modified()

        mapper = self.mesh.mapper
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToMapScalars()
        mapper.SetLookupTable(self.lookup_table)
        mapper.SetScalarRange(0.0, 256.0)
        mapper.ScalarVisibilityOn()
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
        for actor in [self.control_points_actor, self.control_line_actor]:
            if actor is not None:
                self.plotter.remove(actor)
        self.control_points_actor = None
        self.control_line_actor = None
