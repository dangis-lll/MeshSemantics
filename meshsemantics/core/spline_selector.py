from __future__ import annotations

import numpy as np
from vtkmodules.vtkCommonComputationalGeometry import vtkParametricSpline
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkFiltersCore import vtkClipPolyData, vtkIdFilter
from vtkmodules.vtkFiltersModeling import vtkSelectPolyData
from vtkmodules.vtkFiltersSources import vtkParametricFunctionSource
from vtkmodules.vtkRenderingCore import vtkCellPicker
from vtkmodules.util.numpy_support import vtk_to_numpy


def build_vtk_spline(
    points_world: np.ndarray | list[tuple[float, float, float]],
    samples: int = 200,
    closed: bool = False,
) -> np.ndarray:
    points = np.asarray(points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] < 2:
        return points
    if points.shape[0] == 2:
        line = np.linspace(points[0], points[-1], max(2, int(samples)))
        if closed:
            line = np.vstack([line, line[0]])
        return line

    vtk_points = vtkPoints()
    for point in points:
        vtk_points.InsertNextPoint(float(point[0]), float(point[1]), float(point[2]))

    spline = vtkParametricSpline()
    spline.SetPoints(vtk_points)
    if hasattr(spline, "ClosedOn"):
        if closed and points.shape[0] >= 3:
            spline.ClosedOn()
        else:
            spline.ClosedOff()

    function_source = vtkParametricFunctionSource()
    function_source.SetParametricFunction(spline)
    function_source.SetUResolution(max(2, int(samples) - 1))
    function_source.Update()

    output_points = function_source.GetOutput().GetPoints()
    if output_points is None or output_points.GetNumberOfPoints() == 0:
        sampled = points.copy()
    else:
        sampled = vtk_to_numpy(output_points.GetData()).astype(np.float64)
        if sampled.size == 0 or np.isnan(sampled).any():
            sampled = points.copy()

    if closed and sampled.shape[0] >= 1 and not np.allclose(sampled[0], sampled[-1]):
        sampled = np.vstack([sampled, sampled[0]])
    return sampled


def smooth_closed_curve(points_2d: np.ndarray, samples: int = 200, closed: bool = True) -> np.ndarray:
    points = np.asarray(points_2d, dtype=np.float64)
    if points.shape[0] < 2:
        return points
    source = points
    if source.shape[0] == 2:
        return np.linspace(source[0], source[-1], samples)

    vtk_points = vtkPoints()
    for point in source:
        vtk_points.InsertNextPoint(float(point[0]), float(point[1]), 0.0)

    spline = vtkParametricSpline()
    spline.SetPoints(vtk_points)
    if hasattr(spline, "ClosedOn"):
        if closed and points.shape[0] >= 3:
            spline.ClosedOn()
        else:
            spline.ClosedOff()

    function_source = vtkParametricFunctionSource()
    function_source.SetParametricFunction(spline)
    function_source.SetUResolution(max(2, int(samples) - 1))
    function_source.Update()

    sampled = vtk_to_numpy(function_source.GetOutput().GetPoints().GetData())
    if sampled.size == 0 or np.isnan(sampled).any():
        return points
    return sampled[:, :2]


def points_in_polygon(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    x = points[:, 0]
    y = points[:, 1]
    poly_x = polygon[:, 0]
    poly_y = polygon[:, 1]
    inside = np.zeros(points.shape[0], dtype=bool)
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = poly_x[i], poly_y[i]
        xj, yj = poly_x[j], poly_y[j]
        intersects = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi
        )
        inside ^= intersects
        j = i
    return inside


def project_world_to_display(renderer, world_points: np.ndarray) -> np.ndarray:
    width, height = renderer.GetSize()
    if width <= 0 or height <= 0:
        return np.zeros((world_points.shape[0], 2), dtype=np.float64)

    camera = renderer.GetActiveCamera()
    aspect = width / max(height, 1)
    matrix = camera.GetCompositeProjectionTransformMatrix(aspect, -1, 1)
    mat = np.array([[matrix.GetElement(r, c) for c in range(4)] for r in range(4)], dtype=np.float64)

    pts = np.column_stack([world_points, np.ones(world_points.shape[0], dtype=np.float64)])
    clip = pts @ mat.T
    w = np.where(np.abs(clip[:, 3]) < 1e-12, 1e-12, clip[:, 3])
    ndc = clip[:, :3] / w[:, None]
    x = (ndc[:, 0] + 1.0) * 0.5 * width
    y = (ndc[:, 1] + 1.0) * 0.5 * height
    return np.column_stack([x, y])


def camera_forward_vector(renderer) -> np.ndarray:
    camera = renderer.GetActiveCamera()
    position = np.array(camera.GetPosition(), dtype=np.float64)
    focal = np.array(camera.GetFocalPoint(), dtype=np.float64)
    vec = focal - position
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return np.array([0.0, 0.0, -1.0], dtype=np.float64)
    return vec / norm


def select_cells_by_screen_polygon(
    renderer,
    cell_centers: np.ndarray,
    cell_normals: np.ndarray,
    polygon_points: np.ndarray,
    exclude_backfaces: bool = True,
    visible_only: bool = True,
) -> np.ndarray:
    polygon = smooth_closed_curve(polygon_points, closed=True)
    if polygon.shape[0] < 3 or cell_centers.size == 0:
        return np.zeros(0, dtype=np.int32)

    centers_2d = project_world_to_display(renderer, cell_centers)
    mask = points_in_polygon(centers_2d, polygon)
    if exclude_backfaces and cell_normals.size:
        view_dir = camera_forward_vector(renderer)
        facing = np.einsum("ij,j->i", cell_normals, view_dir) < 0.0
        mask &= facing
    candidate_ids = np.flatnonzero(mask).astype(np.int32)
    if not visible_only or candidate_ids.size == 0:
        return candidate_ids

    projected_candidates = centers_2d[candidate_ids]
    picker = vtkCellPicker()
    picker.SetTolerance(0.0005)
    visible_ids: list[int] = []
    for cell_id, point in zip(candidate_ids.tolist(), projected_candidates.tolist()):
        picker.Pick(float(point[0]), float(point[1]), 0.0, renderer)
        if picker.GetCellId() == int(cell_id):
            visible_ids.append(int(cell_id))
    return np.asarray(visible_ids, dtype=np.int32)


def select_cells_by_surface_loop(polydata, loop_points_world) -> np.ndarray:
    points = np.asarray(loop_points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] < 3:
        return np.zeros(0, dtype=np.int32)

    loop = vtkPoints()
    for point in points:
        loop.InsertNextPoint(float(point[0]), float(point[1]), float(point[2]))

    id_filter = vtkIdFilter()
    id_filter.SetInputData(polydata)
    id_filter.CellIdsOn()
    id_filter.PointIdsOff()
    if hasattr(id_filter, "SetCellIdsArrayName"):
        id_filter.SetCellIdsArrayName("OriginalCellId")
    id_filter.Update()

    selector = vtkSelectPolyData()
    selector.SetInputConnection(id_filter.GetOutputPort())
    selector.SetLoop(loop)
    selector.GenerateSelectionScalarsOn()
    selector.SetSelectionModeToSmallestRegion()
    selector.Update()

    clip = vtkClipPolyData()
    clip.SetInputConnection(selector.GetOutputPort())
    clip.InsideOutOn()
    clip.SetValue(0.0)
    clip.Update()

    output = clip.GetOutput()
    cell_data = output.GetCellData()
    id_array = None
    for name in ["OriginalCellId", "vtkIdFilter_Ids"]:
        id_array = cell_data.GetArray(name)
        if id_array is not None:
            break
    if id_array is None:
        return np.zeros(0, dtype=np.int32)
    return np.unique(vtk_to_numpy(id_array).astype(np.int32))
