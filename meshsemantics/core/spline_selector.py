from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from vtkmodules.vtkCommonComputationalGeometry import vtkKochanekSpline, vtkParametricSpline
from vtkmodules.vtkCommonCore import vtkPoints, reference
from vtkmodules.vtkCommonDataModel import vtkGenericCell, vtkPointLocator
from vtkmodules.vtkFiltersCore import vtkCleanPolyData, vtkClipPolyData, vtkPolyDataConnectivityFilter
from vtkmodules.vtkFiltersModeling import vtkDijkstraGraphGeodesicPath
from vtkmodules.vtkFiltersCore import vtkGenerateIds as vtkIdFilter
from vtkmodules.vtkFiltersModeling import vtkSelectPolyData
from vtkmodules.vtkFiltersSources import vtkParametricFunctionSource
from vtkmodules.vtkCommonDataModel import vtkCellLocator
from vtkmodules.util.numpy_support import vtk_to_numpy


DRS_CONTOUR_POINTS_SPACING = 0.3
_INT32_MIN = np.iinfo(np.int32).min
_DRS_TOPOLOGY_CACHE: dict[tuple[int, int, int, int], "_DrsTopology"] = {}
_CELL_LOCATOR_CACHE: dict[tuple[int, int, int, int], vtkCellLocator] = {}
_POINT_LOCATOR_CACHE: dict[tuple[int, int, int, int], vtkPointLocator] = {}


@dataclass(frozen=True)
class _DrsTopology:
    point_cells: np.ndarray
    cell_points: np.ndarray
    cell_neighbor_cells: np.ndarray
    cell_neighbor_point_1: np.ndarray
    cell_neighbor_point_2: np.ndarray


def _polydata_cache_key(polydata) -> tuple[int, int, int, int]:
    mesh_mtime = int(polydata.GetMeshMTime()) if hasattr(polydata, "GetMeshMTime") else int(polydata.GetMTime())
    return (
        id(polydata),
        mesh_mtime,
        int(polydata.GetNumberOfCells()),
        int(polydata.GetNumberOfPoints()),
    )


def _trim_cache(cache: dict, max_items: int = 4) -> None:
    if len(cache) > max_items:
        cache.clear()


def _cached_cell_locator(polydata) -> vtkCellLocator:
    cache_key = _polydata_cache_key(polydata)
    locator = _CELL_LOCATOR_CACHE.get(cache_key)
    if locator is None:
        locator = vtkCellLocator()
        locator.SetDataSet(polydata)
        locator.BuildLocator()
        _trim_cache(_CELL_LOCATOR_CACHE)
        _CELL_LOCATOR_CACHE[cache_key] = locator
    return locator


def _cached_point_locator(polydata) -> vtkPointLocator:
    cache_key = _polydata_cache_key(polydata)
    locator = _POINT_LOCATOR_CACHE.get(cache_key)
    if locator is None:
        locator = vtkPointLocator()
        locator.SetDataSet(polydata)
        locator.BuildLocator()
        _trim_cache(_POINT_LOCATOR_CACHE)
        _POINT_LOCATOR_CACHE[cache_key] = locator
    return locator


def surface_selection_topology_input(polydata) -> tuple[np.ndarray, int, int] | None:
    """Return a small, pickle-friendly topology payload for process workers."""
    if polydata is None:
        return None
    cell_count = int(polydata.GetNumberOfCells())
    point_count = int(polydata.GetNumberOfPoints())
    if cell_count == 0 or point_count == 0:
        return None
    cell_points = _polydata_cell_points(polydata)
    if cell_points.shape[0] != cell_count:
        return None
    return cell_points.astype(np.int64, copy=True), cell_count, point_count


def build_drs_topology_from_cell_points(
    cell_points: np.ndarray,
    cell_count: int,
    point_count: int,
) -> _DrsTopology | None:
    cell_points = np.asarray(cell_points, dtype=np.int64)
    cell_count = int(cell_count)
    point_count = int(point_count)
    if cell_count == 0 or point_count == 0 or cell_points.shape[0] != cell_count:
        return None

    valid = cell_points >= 0
    point_ids = cell_points[valid]
    owner_cells = np.repeat(np.arange(cell_count, dtype=np.int64), valid.sum(axis=1))
    point_cells = _padded_rows(point_ids, owner_cells)
    if point_cells.shape[0] < point_count:
        padded = np.full((point_count, point_cells.shape[1]), -1, dtype=np.int64)
        padded[: point_cells.shape[0], : point_cells.shape[1]] = point_cells
        point_cells = padded

    cell_sizes = valid.sum(axis=1).astype(np.int64, copy=False)
    if cell_points.shape[1] > 0 and np.all(cell_sizes == cell_points.shape[1]):
        edge_starts = cell_points
        edge_ends = np.roll(cell_points, -1, axis=1)
        edge_owner_array = np.repeat(np.arange(cell_count, dtype=np.int64), cell_points.shape[1])
        edge_a_array = np.minimum(edge_starts, edge_ends).reshape(-1)
        edge_b_array = np.maximum(edge_starts, edge_ends).reshape(-1)
    else:
        edge_owner: list[int] = []
        edge_a: list[int] = []
        edge_b: list[int] = []
        for cell_id, size in enumerate(cell_sizes.tolist()):
            if size < 2:
                continue
            points = cell_points[cell_id, :size]
            starts = points
            ends = np.roll(points, -1)
            edge_owner.extend([cell_id] * int(size))
            edge_a.extend(np.minimum(starts, ends).astype(np.int64).tolist())
            edge_b.extend(np.maximum(starts, ends).astype(np.int64).tolist())
        edge_owner_array = np.asarray(edge_owner, dtype=np.int64)
        edge_a_array = np.asarray(edge_a, dtype=np.int64)
        edge_b_array = np.asarray(edge_b, dtype=np.int64)

    neighbor_owner_parts: list[np.ndarray] = []
    neighbor_cell_parts: list[np.ndarray] = []
    neighbor_point_1_parts: list[np.ndarray] = []
    neighbor_point_2_parts: list[np.ndarray] = []
    if edge_owner_array.size:
        order = np.lexsort((edge_owner_array, edge_b_array, edge_a_array))
        sorted_owner = edge_owner_array[order]
        sorted_a = edge_a_array[order]
        sorted_b = edge_b_array[order]
        breaks = np.flatnonzero((np.diff(sorted_a) != 0) | (np.diff(sorted_b) != 0)) + 1
        starts = np.r_[0, breaks]
        ends = np.r_[breaks, sorted_owner.size]

        group_sizes = ends - starts
        paired = group_sizes == 2
        if np.any(paired):
            first = starts[paired]
            second = first + 1
            first_owner = sorted_owner[first]
            second_owner = sorted_owner[second]
            first_point = sorted_a[first]
            second_point = sorted_b[first]
            neighbor_owner_parts.append(np.concatenate([first_owner, second_owner]))
            neighbor_cell_parts.append(np.concatenate([second_owner, first_owner]))
            neighbor_point_1_parts.append(np.concatenate([first_point, first_point]))
            neighbor_point_2_parts.append(np.concatenate([second_point, second_point]))

        for start, end in zip(starts[~paired], ends[~paired]):
            owners = np.unique(sorted_owner[start:end])
            if owners.size < 2:
                continue
            a = int(sorted_a[start])
            b = int(sorted_b[start])
            owner_values: list[int] = []
            neighbor_values: list[int] = []
            for owner in owners.tolist():
                for neighbor in owners.tolist():
                    if neighbor == owner:
                        continue
                    owner_values.append(int(owner))
                    neighbor_values.append(int(neighbor))
            neighbor_owner_parts.append(np.asarray(owner_values, dtype=np.int64))
            neighbor_cell_parts.append(np.asarray(neighbor_values, dtype=np.int64))
            neighbor_point_1_parts.append(np.full(len(owner_values), a, dtype=np.int64))
            neighbor_point_2_parts.append(np.full(len(owner_values), b, dtype=np.int64))

    neighbor_owner = np.concatenate(neighbor_owner_parts) if neighbor_owner_parts else np.zeros(0, dtype=np.int64)
    neighbor_cell = np.concatenate(neighbor_cell_parts) if neighbor_cell_parts else np.zeros(0, dtype=np.int64)
    neighbor_point_1 = np.concatenate(neighbor_point_1_parts) if neighbor_point_1_parts else np.zeros(0, dtype=np.int64)
    neighbor_point_2 = np.concatenate(neighbor_point_2_parts) if neighbor_point_2_parts else np.zeros(0, dtype=np.int64)

    cell_neighbor_cells = _padded_rows(neighbor_owner, neighbor_cell)
    cell_neighbor_point_1 = _padded_rows(neighbor_owner, neighbor_point_1)
    cell_neighbor_point_2 = _padded_rows(neighbor_owner, neighbor_point_2)
    if cell_neighbor_cells.shape[0] < cell_count:
        width = cell_neighbor_cells.shape[1]
        padded_cells = np.full((cell_count, width), -1, dtype=np.int64)
        padded_p1 = np.full((cell_count, width), -1, dtype=np.int64)
        padded_p2 = np.full((cell_count, width), -1, dtype=np.int64)
        padded_cells[: cell_neighbor_cells.shape[0], :width] = cell_neighbor_cells
        padded_p1[: cell_neighbor_point_1.shape[0], :width] = cell_neighbor_point_1
        padded_p2[: cell_neighbor_point_2.shape[0], :width] = cell_neighbor_point_2
        cell_neighbor_cells = padded_cells
        cell_neighbor_point_1 = padded_p1
        cell_neighbor_point_2 = padded_p2

    return _DrsTopology(
        point_cells=point_cells,
        cell_points=cell_points,
        cell_neighbor_cells=cell_neighbor_cells,
        cell_neighbor_point_1=cell_neighbor_point_1,
        cell_neighbor_point_2=cell_neighbor_point_2,
    )


def build_surface_selection_topology(payload) -> _DrsTopology | None:
    if payload is None:
        return None
    cell_points, cell_count, point_count = payload
    return build_drs_topology_from_cell_points(cell_points, cell_count, point_count)


def install_drs_topology_cache(polydata, topology: _DrsTopology | None) -> bool:
    if polydata is None or topology is None:
        return False
    if topology.cell_points.shape[0] != int(polydata.GetNumberOfCells()):
        return False
    if topology.point_cells.shape[0] < int(polydata.GetNumberOfPoints()):
        return False
    _trim_cache(_DRS_TOPOLOGY_CACHE)
    _DRS_TOPOLOGY_CACHE[_polydata_cache_key(polydata)] = topology
    return True


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

    locator = _cached_cell_locator(polydata)

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
    locator = _cached_point_locator(polydata)

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


def _padded_rows(row_ids: np.ndarray, values: np.ndarray, fill_value: int = -1) -> np.ndarray:
    row_ids = np.asarray(row_ids, dtype=np.int64).reshape(-1)
    if row_ids.size == 0:
        return np.full((0, 0), fill_value, dtype=np.int64)
    counts = np.bincount(row_ids)
    width = int(counts.max(initial=0))
    if width <= 0:
        return np.full((int(counts.size), 0), fill_value, dtype=np.int64)
    rows = np.full((int(counts.size), width), fill_value, dtype=np.int64)
    order = np.argsort(row_ids, kind="stable")
    sorted_rows = row_ids[order]
    sorted_values = np.asarray(values, dtype=np.int64).reshape(-1)[order]
    starts = np.r_[0, np.flatnonzero(np.diff(sorted_rows)) + 1]
    for start, end in zip(starts, np.r_[starts[1:], sorted_rows.size]):
        row = int(sorted_rows[start])
        rows[row, : end - start] = sorted_values[start:end]
    return rows


def _polydata_cell_points(polydata) -> np.ndarray:
    polys = polydata.GetPolys()
    if polys is None or polys.GetNumberOfCells() == 0:
        return np.full((0, 0), -1, dtype=np.int64)

    if hasattr(polys, "GetOffsetsArray") and hasattr(polys, "GetConnectivityArray"):
        offsets_array = polys.GetOffsetsArray()
        connectivity_array = polys.GetConnectivityArray()
        if offsets_array is not None and connectivity_array is not None:
            offsets = vtk_to_numpy(offsets_array).astype(np.int64, copy=False)
            connectivity = vtk_to_numpy(connectivity_array).astype(np.int64, copy=False)
            if offsets.size >= 2:
                sizes = np.diff(offsets)
                width = int(sizes.max(initial=0))
                if width > 0 and np.all(sizes == width):
                    return connectivity.reshape(-1, width)
                cell_points = np.full((sizes.size, width), -1, dtype=np.int64)
                for cell_id, (start, end) in enumerate(zip(offsets[:-1], offsets[1:])):
                    cell_points[cell_id, : int(end - start)] = connectivity[int(start) : int(end)]
                return cell_points

    data_array = polys.GetData()
    if data_array is None:
        return np.full((0, 0), -1, dtype=np.int64)
    data = vtk_to_numpy(data_array).astype(np.int64, copy=False)
    rows: list[np.ndarray] = []
    offset = 0
    width = 0
    while offset < data.size:
        size = int(data[offset])
        offset += 1
        if size <= 0 or offset + size > data.size:
            break
        row = data[offset : offset + size]
        rows.append(row)
        width = max(width, size)
        offset += size
    cell_points = np.full((len(rows), width), -1, dtype=np.int64)
    for cell_id, row in enumerate(rows):
        cell_points[cell_id, : row.size] = row
    return cell_points


def _drs_topology(polydata) -> _DrsTopology | None:
    cell_count = int(polydata.GetNumberOfCells())
    point_count = int(polydata.GetNumberOfPoints())
    if cell_count == 0 or point_count == 0:
        return None

    cache_key = _polydata_cache_key(polydata)
    cached = _DRS_TOPOLOGY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    payload = surface_selection_topology_input(polydata)
    if payload is None:
        return None
    topology = build_drs_topology_from_cell_points(*payload)
    install_drs_topology_cache(polydata, topology)
    return topology


def _drs_cells_inside_boundary(polydata, boundary_ids: np.ndarray) -> np.ndarray:
    cell_count = int(polydata.GetNumberOfCells())
    point_count = int(polydata.GetNumberOfPoints())
    if cell_count == 0 or point_count == 0:
        return np.zeros(0, dtype=np.int32)

    topology = _drs_topology(polydata)
    if topology is None:
        return np.zeros(0, dtype=np.int32)

    boundary = np.unique(boundary_ids[(boundary_ids >= 0) & (boundary_ids < point_count)].astype(np.int64))
    if boundary.size < 3:
        return np.zeros(0, dtype=np.int32)

    boundary_mask = np.zeros(point_count, dtype=bool)
    boundary_mask[boundary] = True
    cell_marks = np.full(cell_count, _INT32_MIN, dtype=np.int32)
    point_marks = np.full(point_count, _INT32_MIN, dtype=np.int32)
    point_marks[boundary] = 0

    max_front_cell = -1
    current_front_number = 1
    current_front = boundary
    while current_front.size:
        incident_cells = topology.point_cells[current_front].reshape(-1)
        incident_cells = np.unique(incident_cells[incident_cells >= 0])
        unmarked_cells = incident_cells[cell_marks[incident_cells] == _INT32_MIN]
        if unmarked_cells.size:
            max_front_cell = int(unmarked_cells[-1])
            cell_marks[unmarked_cells] = current_front_number
            neighbor_points = topology.cell_points[unmarked_cells].reshape(-1)
            neighbor_points = np.unique(neighbor_points[neighbor_points >= 0])
            next_front = neighbor_points[point_marks[neighbor_points] == _INT32_MIN]
            point_marks[next_front] = 1
        else:
            next_front = np.zeros(0, dtype=np.int64)
        current_front = next_front
        current_front_number += 1

    if max_front_cell < 0:
        return np.zeros(0, dtype=np.int32)

    current_cells = np.asarray([max_front_cell], dtype=np.int64)
    cell_marks[max_front_cell] = -1
    while current_cells.size:
        neighbors = topology.cell_neighbor_cells[current_cells].reshape(-1)
        point_1 = topology.cell_neighbor_point_1[current_cells].reshape(-1)
        point_2 = topology.cell_neighbor_point_2[current_cells].reshape(-1)
        valid = neighbors >= 0
        if not np.any(valid):
            break
        neighbors = neighbors[valid]
        point_1 = point_1[valid]
        point_2 = point_2[valid]
        crosses_boundary = boundary_mask[point_1] & boundary_mask[point_2]
        candidates = neighbors[~crosses_boundary]
        candidates = np.unique(candidates[cell_marks[candidates] != -1])
        if candidates.size == 0:
            break
        cell_marks[candidates] = -1
        current_cells = candidates

    # DRS uses SelectionModeToLargestRegion with vtkClipPolyData(value=0).
    # The largest side is marked negative, so clipping keeps the opposite side.
    return np.flatnonzero(cell_marks > 0).astype(np.int32)
