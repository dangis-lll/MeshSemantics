from __future__ import annotations

import numpy as np
from vtkmodules.vtkCommonComputationalGeometry import vtkKochanekSpline, vtkParametricSpline
from vtkmodules.vtkCommonCore import vtkIdList, vtkPoints, reference
from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkPointLocator
from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkClipPolyData, vtkPolyDataConnectivityFilter, vtkPolyDataNormals
from vtkmodules.vtkFiltersModeling import vtkDijkstraGraphGeodesicPath
from vtkmodules.vtkFiltersCore import vtkGenerateIds as vtkIdFilter
from vtkmodules.vtkFiltersModeling import vtkSelectPolyData
from vtkmodules.vtkFiltersSources import vtkParametricFunctionSource
from vtkmodules.vtkCommonDataModel import vtkCellLocator
from vtkmodules.vtkRenderingCore import vtkCellPicker
from vtkmodules.util.numpy_support import vtk_to_numpy


DRS_CONTOUR_POINTS_SPACING = 0.3


def _polyline_length(points: np.ndarray, closed: bool = False) -> float:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if pts.shape[0] < 2:
        return 0.0
    length = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    if closed and pts.shape[0] >= 3 and not np.allclose(pts[0], pts[-1]):
        length += float(np.linalg.norm(pts[0] - pts[-1]))
    return length


def build_vtk_spline(
    points_world: np.ndarray | list[tuple[float, float, float]],
    samples: int | None = None,
    closed: bool = False,
    spacing: float = DRS_CONTOUR_POINTS_SPACING,
) -> np.ndarray:
    points = np.asarray(points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] < 2:
        return points
    if points.shape[0] == 2:
        if samples is None:
            samples = max(2, int(_polyline_length(points, closed=False) / max(spacing, 1e-6)) + 1)
        line = np.linspace(points[0], points[-1], max(2, int(samples)))
        if closed:
            line = np.vstack([line, line[0]])
        return line

    vtk_points = vtkPoints()
    for point in points:
        vtk_points.InsertNextPoint(float(point[0]), float(point[1]), float(point[2]))

    spline = vtkParametricSpline()
    spline.SetPoints(vtk_points)
    spline.SetXSpline(vtkKochanekSpline())
    spline.SetYSpline(vtkKochanekSpline())
    spline.SetZSpline(vtkKochanekSpline())
    spline.SetClosed(bool(closed and points.shape[0] >= 3))
    spline.ParameterizeByLengthOff()

    function_source = vtkParametricFunctionSource()
    function_source.SetParametricFunction(spline)
    function_source.SetScalarModeToNone()
    function_source.GenerateTextureCoordinatesOff()
    if samples is None:
        samples = max(1, int(_polyline_length(points, closed=closed) / max(spacing, 1e-6)))
    function_source.SetUResolution(max(1, int(samples) - 1))
    function_source.Update()

    clean_polydata = vtkCleanPolyData()
    clean_polydata.SetInputData(function_source.GetOutput())
    clean_polydata.Update()

    output_points = clean_polydata.GetOutput().GetPoints()
    if output_points is None or output_points.GetNumberOfPoints() == 0:
        sampled = points.copy()
    else:
        sampled = vtk_to_numpy(output_points.GetData()).astype(np.float64)
        if sampled.size == 0 or np.isnan(sampled).any():
            sampled = points.copy()

    if closed and sampled.shape[0] >= 1 and not np.allclose(sampled[0], sampled[-1]):
        sampled = np.vstack([sampled, sampled[0]])
    return sampled


def estimate_contour_samples(
    control_points_world: np.ndarray | list[tuple[float, float, float]],
    closed: bool = False,
    min_samples: int = 2,
    target_spacing: float = DRS_CONTOUR_POINTS_SPACING,
) -> int:
    points = np.asarray(control_points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] < 2:
        return max(2, int(min_samples))
    curve_length = _polyline_length(points, closed=closed)
    if curve_length <= 1e-6:
        return max(2, int(min_samples))
    estimated = int(round(curve_length / max(target_spacing, 1e-6)))
    return max(int(min_samples), estimated + 1)


def snap_points_to_surface(polydata, points_world) -> np.ndarray:
    points = np.asarray(points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] == 0:
        return points

    locator = vtkCellLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()

    generic_cell = vtkGenericCell()
    cell_id = reference(0)
    sub_id = reference(0)
    dist2 = reference(0.0)

    snapped = np.empty_like(points)
    for index, point in enumerate(points):
        closest = [0.0, 0.0, 0.0]
        locator.FindClosestPoint(point, closest, generic_cell, cell_id, sub_id, dist2)
        snapped[index] = closest
    return snapped


def project_spline_to_surface_by_closest_points(polydata, spline_points_world: np.ndarray) -> np.ndarray:
    spline = np.asarray(spline_points_world, dtype=np.float64).reshape(-1, 3)
    if spline.shape[0] == 0:
        return spline
    return snap_points_to_surface(polydata, spline)


def _polydata_with_cell_normals(polydata):
    normals = polydata.GetCellData().GetNormals()
    if normals is not None and normals.GetNumberOfTuples() == polydata.GetNumberOfCells():
        return polydata

    normal_filter = vtkPolyDataNormals()
    normal_filter.SetInputData(polydata)
    normal_filter.ComputeCellNormalsOn()
    normal_filter.ComputePointNormalsOff()
    normal_filter.SplittingOff()
    normal_filter.ConsistencyOn()
    normal_filter.Update()
    return normal_filter.GetOutput()


def _find_closest_point_and_cell(locator: vtkCellLocator, point: np.ndarray) -> tuple[np.ndarray, int]:
    generic_cell = vtkGenericCell()
    cell_id = reference(0)
    sub_id = reference(0)
    dist2 = reference(0.0)
    closest = [0.0, 0.0, 0.0]
    locator.FindClosestPoint(
        [float(point[0]), float(point[1]), float(point[2])],
        closest,
        generic_cell,
        cell_id,
        sub_id,
        dist2,
    )
    return np.asarray(closest, dtype=np.float64), int(cell_id)


def _intersect_locator_line(locator: vtkCellLocator, p1: np.ndarray, p2: np.ndarray) -> np.ndarray | None:
    t = reference(0.0)
    x = [0.0, 0.0, 0.0]
    pcoords = [0.0, 0.0, 0.0]
    sub_id = reference(0)
    cell_id = reference(0)
    start = [float(p1[0]), float(p1[1]), float(p1[2])]
    end = [float(p2[0]), float(p2[1]), float(p2[2])]
    try:
        hit = locator.IntersectWithLine(start, end, 1e-6, t, x, pcoords, sub_id, cell_id)
    except TypeError:
        hit = locator.IntersectWithLine(start, end, 1e-6, t, x, pcoords, sub_id)
    if not hit:
        return None
    return np.asarray(x, dtype=np.float64)


def project_spline_to_surface_by_normals(
    polydata,
    control_points_world: np.ndarray | list[tuple[float, float, float]],
    spline_points_world: np.ndarray,
    closed: bool = False,
) -> np.ndarray:
    """Project spline points onto the surface using DRS-style control-point normals."""
    controls = np.asarray(control_points_world, dtype=np.float64).reshape(-1, 3)
    spline = np.asarray(spline_points_world, dtype=np.float64).reshape(-1, 3)
    if controls.shape[0] < 2 or spline.shape[0] < 2:
        return snap_points_to_surface(polydata, spline)

    normal_polydata = _polydata_with_cell_normals(polydata)
    locator = vtkCellLocator()
    locator.SetDataSet(normal_polydata)
    locator.BuildLocator()

    normals = normal_polydata.GetCellData().GetNormals()
    if normals is None or normals.GetNumberOfTuples() == 0:
        return snap_points_to_surface(polydata, spline)

    control_normals = []
    control_indices = []
    for control in controls:
        _closest, cell_id = _find_closest_point_and_cell(locator, control)
        cell_id = max(0, min(int(cell_id), normals.GetNumberOfTuples() - 1))
        normal = np.asarray(normals.GetTuple(cell_id), dtype=np.float64)
        norm = float(np.linalg.norm(normal))
        if norm <= 1e-12:
            normal = np.array([0.0, 0.0, 1.0], dtype=np.float64)
        else:
            normal = normal / norm
        control_normals.append(normal)
        control_indices.append(int(np.argmin(np.linalg.norm(spline - control[None, :], axis=1))))

    control_indices = np.asarray(control_indices, dtype=np.int32)
    order = np.argsort(control_indices)
    control_indices = control_indices[order]
    control_normals = [control_normals[int(i)] for i in order]

    bounds = normal_polydata.GetBounds()
    ray_length = float(np.linalg.norm([bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]])) * 2.0
    if not np.isfinite(ray_length) or ray_length <= 0.0:
        ray_length = 1.0

    closest_fallback = snap_points_to_surface(normal_polydata, spline)
    projected = np.empty_like(spline)
    for index, point in enumerate(spline):
        insert_at = int(np.searchsorted(control_indices, index, side="right"))
        if insert_at <= 0:
            normal = control_normals[0]
        elif insert_at >= len(control_normals):
            if closed and len(control_normals) > 1:
                normal = control_normals[-1] + control_normals[0]
                norm = float(np.linalg.norm(normal))
                normal = normal / norm if norm > 1e-12 else control_normals[-1]
            else:
                normal = control_normals[-1]
        else:
            normal = control_normals[insert_at - 1] + control_normals[insert_at]
            norm = float(np.linalg.norm(normal))
            normal = normal / norm if norm > 1e-12 else control_normals[insert_at - 1]

        p1 = point + normal * ray_length
        p2 = point - normal * ray_length
        hit = _intersect_locator_line(locator, p1, p2)
        projected[index] = hit if hit is not None else closest_fallback[index]

    deltas = np.linalg.norm(np.diff(projected, axis=0), axis=1)
    keep = np.ones(projected.shape[0], dtype=bool)
    keep[1:] = deltas > 1e-6
    projected = projected[keep]

    if closed and projected.shape[0] >= 3 and not np.allclose(projected[0], projected[-1]):
        projected = np.vstack([projected, projected[0]])
    return projected


def build_surface_contour_line(
    polydata,
    control_points_world: np.ndarray | list[tuple[float, float, float]],
    samples: int | None = None,
    closed: bool = False,
) -> np.ndarray:
    if samples is None:
        samples = estimate_contour_samples(control_points_world, closed=closed)
    sampled = build_vtk_spline(control_points_world, samples=samples, closed=closed)
    if sampled.shape[0] == 0:
        return sampled

    snapped = project_spline_to_surface_by_closest_points(polydata, sampled)
    if snapped.shape[0] <= 1:
        return snapped

    deltas = np.linalg.norm(np.diff(snapped, axis=0), axis=1)
    keep = np.ones(snapped.shape[0], dtype=bool)
    keep[1:] = deltas > 1e-6
    snapped = snapped[keep]

    if closed and snapped.shape[0] >= 3:
        if not np.allclose(snapped[0], snapped[-1]):
            snapped = np.vstack([snapped, snapped[0]])
        elif snapped.shape[0] > 1 and np.allclose(snapped[-1], snapped[-2]):
            snapped = snapped[:-1]
            snapped = np.vstack([snapped, snapped[0]])
    return snapped


def build_surface_spline_loop(
    polydata,
    control_points_world: np.ndarray | list[tuple[float, float, float]],
    samples: int | None = None,
    closed: bool = False,
) -> np.ndarray:
    return build_surface_contour_line(
        polydata,
        control_points_world,
        samples=samples,
        closed=closed,
    )


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
    if points.shape[0] >= 2 and np.allclose(points[0], points[-1]):
        points = points[:-1]
    if points.shape[0] < 3:
        return np.zeros(0, dtype=np.int32)

    drs_selected = select_cells_by_drs_surface_loop(polydata, points)
    if drs_selected.size:
        return drs_selected

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
    selector.SetSelectionModeToLargestRegion()
    selector.Update()

    clip = vtkClipPolyData()
    clip.SetInputConnection(selector.GetOutputPort())
    clip.SetValue(0.0)
    clip.GenerateClippedOutputOff()
    clip.Update()

    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputConnection(clip.GetOutputPort())
    connectivity.SetExtractionModeToLargestRegion()
    connectivity.Update()

    output = connectivity.GetOutput()
    cell_data = output.GetCellData()
    id_array = None
    for name in ["OriginalCellId", "vtkCellIds", "vtkIdFilter_Ids"]:
        id_array = cell_data.GetArray(name)
        if id_array is not None:
            break
    if id_array is None:
        return np.zeros(0, dtype=np.int32)
    return np.unique(vtk_to_numpy(id_array).astype(np.int32))


def select_cells_by_drs_surface_loop(polydata, loop_points_world) -> np.ndarray:
    points = np.asarray(loop_points_world, dtype=np.float64).reshape(-1, 3)
    if points.shape[0] >= 2 and np.allclose(points[0], points[-1]):
        points = points[:-1]
    if points.shape[0] < 3 or polydata is None or polydata.GetNumberOfCells() == 0:
        return np.zeros(0, dtype=np.int32)

    closest_loop_ids = _closest_mesh_point_ids(polydata, points)
    if closest_loop_ids.size < 3:
        return np.zeros(0, dtype=np.int32)

    boundary_ids = _drs_geodesic_boundary_point_ids(polydata, closest_loop_ids)
    if boundary_ids.size < 3:
        return np.zeros(0, dtype=np.int32)

    selected_cells = _drs_cells_inside_boundary(polydata, boundary_ids)
    return selected_cells.astype(np.int32, copy=False)


def _closest_mesh_point_ids(polydata, points: np.ndarray) -> np.ndarray:
    locator = vtkPointLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()

    ids: list[int] = []
    for point in points:
        point_id = int(locator.FindClosestPoint([float(point[0]), float(point[1]), float(point[2])]))
        if point_id >= 0 and (not ids or ids[-1] != point_id):
            ids.append(point_id)
    if len(ids) >= 2 and ids[0] == ids[-1]:
        ids.pop()
    return np.asarray(ids, dtype=np.int64)


def _drs_geodesic_boundary_point_ids(polydata, loop_ids: np.ndarray) -> np.ndarray:
    path_finder = vtkDijkstraGraphGeodesicPath()
    path_finder.SetInputData(polydata)
    path_finder.StopWhenEndReachedOn()

    boundary: list[int] = [int(loop_ids[0])]
    loop_count = int(loop_ids.size)
    for index in range(loop_count):
        current_id = int(loop_ids[index])
        next_id = int(loop_ids[(index + 1) % loop_count])
        if current_id == next_id:
            continue
        path_finder.SetStartVertex(next_id)
        path_finder.SetEndVertex(current_id)
        path_finder.Update()
        path_ids = path_finder.GetIdList()
        if path_ids is None or path_ids.GetNumberOfIds() == 0:
            continue
        for path_index in range(1, path_ids.GetNumberOfIds()):
            point_id = int(path_ids.GetId(path_index))
            if boundary[-1] != point_id:
                boundary.append(point_id)

    if len(boundary) >= 2 and boundary[0] == boundary[-1]:
        boundary.pop()
    return np.asarray(boundary, dtype=np.int64)


def _drs_cells_inside_boundary(polydata, boundary_ids: np.ndarray) -> np.ndarray:
    cell_count = int(polydata.GetNumberOfCells())
    point_count = int(polydata.GetNumberOfPoints())
    if cell_count == 0 or point_count == 0:
        return np.zeros(0, dtype=np.int32)

    boundary_points = set(int(point_id) for point_id in boundary_ids.tolist())
    cell_marks = np.full(cell_count, np.iinfo(np.int32).min, dtype=np.int32)
    point_marks = np.full(point_count, np.iinfo(np.int32).min, dtype=np.int32)

    current_front = list(boundary_points)
    for point_id in current_front:
        if 0 <= point_id < point_count:
            point_marks[point_id] = 0

    max_front_cell = -1
    current_front_number = 1
    while current_front:
        next_front: list[int] = []
        for point_id in current_front:
            cell_ids = vtkIdList()
            polydata.GetPointCells(int(point_id), cell_ids)
            for cell_index in range(cell_ids.GetNumberOfIds()):
                cell_id = int(cell_ids.GetId(cell_index))
                if cell_marks[cell_id] != np.iinfo(np.int32).min:
                    continue
                if current_front_number > 0:
                    max_front_cell = cell_id
                cell_marks[cell_id] = current_front_number
                ids = polydata.GetCell(cell_id).GetPointIds()
                for local_index in range(ids.GetNumberOfIds()):
                    neighbor_point_id = int(ids.GetId(local_index))
                    if point_marks[neighbor_point_id] == np.iinfo(np.int32).min:
                        point_marks[neighbor_point_id] = 1
                        next_front.append(neighbor_point_id)
        current_front = next_front
        current_front_number += 1

    if max_front_cell < 0:
        return np.zeros(0, dtype=np.int32)

    current_cells = [max_front_cell]
    cell_marks[max_front_cell] = -1
    while current_cells:
        next_cells: list[int] = []
        for cell_id in current_cells:
            ids = polydata.GetCell(int(cell_id)).GetPointIds()
            cell_point_ids = [int(ids.GetId(i)) for i in range(ids.GetNumberOfIds())]
            for index, point_1 in enumerate(cell_point_ids):
                point_2 = cell_point_ids[(index + 1) % len(cell_point_ids)]
                mark_1 = point_marks[point_1]
                mark_2 = point_marks[point_2]
                if mark_1 != 0:
                    point_marks[point_1] = -1
                if mark_1 == 0 and mark_2 == 0:
                    continue
                neighbors = vtkIdList()
                polydata.GetCellEdgeNeighbors(int(cell_id), int(point_1), int(point_2), neighbors)
                for neighbor_index in range(neighbors.GetNumberOfIds()):
                    neighbor_cell_id = int(neighbors.GetId(neighbor_index))
                    if cell_marks[neighbor_cell_id] != -1:
                        cell_marks[neighbor_cell_id] = -1
                        next_cells.append(neighbor_cell_id)
        current_cells = next_cells

    # DRS uses SelectionModeToLargestRegion with vtkClipPolyData(value=0).
    # The largest side is marked negative, so clipping keeps the opposite side.
    return np.flatnonzero(cell_marks > 0).astype(np.int32)
