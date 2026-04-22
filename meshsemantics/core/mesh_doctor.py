from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

import numpy as np
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkCommonCore import vtkIdList, vtkIdTypeArray, vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkSelection, vtkSelectionNode, vtkTriangle
from vtkmodules.vtkFiltersCore import (
    vtkCleanPolyData,
    vtkFeatureEdges,
    vtkPolyDataConnectivityFilter,
    vtkPolyDataNormals,
    vtkTriangleFilter,
)
from vtkmodules.vtkFiltersExtraction import vtkExtractSelection
from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter
from vtkmodules.vtkFiltersModeling import vtkFillHolesFilter
from vtkmodules.vtkCommonDataModel import vtkCellLocator, vtkPointLocator


CHECK_TITLES = {
    "non_manifold": "Non-manifold Edges",
    "self_intersection": "Self-intersections",
    "highly_creased": "Highly Creased Areas",
    "spike": "Spikes",
    "small_component": "Small Components",
    "small_tunnel": "Small Tunnels",
    "small_hole": "Small Holes",
}


@dataclass(frozen=True)
class MeshDoctorCheckConfig:
    non_manifold: bool = True
    self_intersection: bool = True
    highly_creased: bool = True
    spike: bool = True
    small_component: bool = True
    small_tunnel: bool = True
    small_hole: bool = True
    max_component_size: float = 5.0
    max_tunnel_size: float = 2.5
    max_hole_perimeter: float = 2.5
    spike_sensitivity: int = 50
    expand_level: int = 2


@dataclass(frozen=True)
class MeshDoctorRepairOptions:
    merge_points: bool = True
    remove_small_components: bool = True
    fill_holes: bool = True
    keep_largest_component: bool = False
    recompute_normals: bool = True


@dataclass(frozen=True)
class MeshDoctorCheckResult:
    key: str
    title: str
    count: int
    level: str
    detail: str
    cell_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class MeshDoctorReport:
    point_count: int
    cell_count: int
    triangle_cell_count: int
    check_results: tuple[MeshDoctorCheckResult, ...]

    @property
    def issues(self) -> tuple[MeshDoctorCheckResult, ...]:
        return tuple(item for item in self.check_results if item.count > 0)

    def result_for(self, key: str) -> MeshDoctorCheckResult | None:
        for item in self.check_results:
            if item.key == key:
                return item
        return None


@dataclass(frozen=True)
class MeshDoctorRepairResult:
    polydata: vtkPolyData
    report: MeshDoctorReport
    operations: tuple[str, ...]
    changed_topology: bool


@dataclass
class _AnalysisContext:
    polydata: vtkPolyData
    adjacency: list[set[int]] | None = None
    edge_map: dict[tuple[int, int], set[int]] | None = None
    cell_normals: np.ndarray | None = None
    cell_centers: np.ndarray | None = None
    cell_locator: vtkCellLocator | None = None
    triangle_point_ids: np.ndarray | None = None
    triangle_points: np.ndarray | None = None


def copy_polydata(polydata: vtkPolyData) -> vtkPolyData:
    copied = vtkPolyData()
    copied.DeepCopy(polydata)
    return copied


def triangulate_polydata(polydata: vtkPolyData) -> vtkPolyData:
    triangle = vtkTriangleFilter()
    triangle.SetInputData(polydata)
    triangle.Update()
    output = vtkPolyData()
    output.DeepCopy(triangle.GetOutput())
    output.BuildCells()
    output.BuildLinks()
    return output


def analyze_polydata(
    polydata: vtkPolyData,
    config: MeshDoctorCheckConfig | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> MeshDoctorReport:
    active_config = config or MeshDoctorCheckConfig()
    working = triangulate_polydata(polydata)
    context = _AnalysisContext(polydata=working)
    return _build_report(working, active_config, context, progress_callback)


def repair_polydata(
    polydata: vtkPolyData,
    check_config: MeshDoctorCheckConfig | None = None,
    repair_options: MeshDoctorRepairOptions | None = None,
    initial_report: MeshDoctorReport | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> MeshDoctorRepairResult:
    active_check_config = check_config or MeshDoctorCheckConfig()
    active_repair_options = repair_options or MeshDoctorRepairOptions()

    working = triangulate_polydata(polydata)
    _emit_progress(progress_callback, 8, "Preparing safe cleanup...")
    starting_report = initial_report or _build_report(working, active_check_config, _AnalysisContext(polydata=working))
    if not starting_report.issues:
        _emit_progress(progress_callback, 100, "No checked issues were found.")
        return MeshDoctorRepairResult(
            polydata=working,
            report=starting_report,
            operations=("Skipped safe cleanup because no checked issues were found.",),
            changed_topology=False,
        )

    operations: list[str] = ["Triangulated mesh"]
    changed_topology = False

    _emit_progress(progress_callback, 22, "Merging duplicate points...")
    if active_repair_options.merge_points:
        merged = _clean_polydata(working)
        if _topology_signature(merged) != _topology_signature(working):
            changed_topology = True
            operations.append("Merged duplicate points")
        else:
            operations.append("Duplicate-point merge found no changes")
        working = merged

    _emit_progress(progress_callback, 45, "Cleaning small components...")
    if active_repair_options.remove_small_components and active_check_config.small_component:
        filtered, removed_regions = _remove_small_components_by_size(working, active_check_config.max_component_size)
        if removed_regions > 0 and filtered.GetNumberOfCells() > 0:
            working = filtered
            operations.append(
                f"Removed {removed_regions} connected component(s) smaller than {active_check_config.max_component_size:g} mm"
            )
            changed_topology = True
        else:
            operations.append("Small-component cleanup found no removable regions")

    _emit_progress(progress_callback, 64, "Filtering connected regions...")
    if active_repair_options.keep_largest_component:
        largest = _keep_largest_component(working)
        if _topology_signature(largest) != _topology_signature(working):
            working = largest
            operations.append("Kept only the largest connected component")
            changed_topology = True
        else:
            operations.append("Largest-component filter found a single connected mesh")

    _emit_progress(progress_callback, 80, "Filling small holes...")
    if active_repair_options.fill_holes and active_check_config.small_hole:
        before_loops = _count_small_hole_loops(working, active_check_config.max_hole_perimeter)
        if before_loops > 0:
            filled = _fill_holes(working, active_check_config.max_hole_perimeter)
            after_loops = _count_small_hole_loops(filled, active_check_config.max_hole_perimeter)
            if _topology_signature(filled) != _topology_signature(working):
                working = filled
                operations.append(
                    f"Filled {max(0, before_loops - after_loops)} small hole(s) up to {active_check_config.max_hole_perimeter:g} mm"
                )
                changed_topology = True
            else:
                operations.append("Small-hole fill ran but did not change the mesh")
        else:
            operations.append("Small-hole fill found no eligible boundary loops")

    _emit_progress(progress_callback, 92, "Recomputing normals...")
    if active_repair_options.recompute_normals:
        working = _compute_normals(working)
        operations.append("Recomputed normals")

    working.BuildCells()
    working.BuildLinks()
    final_report = _build_report(working, active_check_config, _AnalysisContext(polydata=working))
    _emit_progress(progress_callback, 100, "Safe cleanup completed.")
    return MeshDoctorRepairResult(
        polydata=working,
        report=final_report,
        operations=tuple(operations),
        changed_topology=changed_topology,
    )


def _topology_signature(polydata: vtkPolyData) -> tuple[int, int]:
    return int(polydata.GetNumberOfPoints()), int(polydata.GetNumberOfCells())


def format_report(report: MeshDoctorReport) -> str:
    enabled_results = [item for item in report.check_results if item.detail != "This check is disabled."]
    lines = [
        "Mesh Check Summary",
        "",
        f"Points: {report.point_count}",
        f"Cells: {report.cell_count}",
        f"Triangle Cells: {report.triangle_cell_count}",
        "",
        "Checked Items:",
    ]
    if not enabled_results:
        lines.append("- No checks were enabled.")
        return "\n".join(lines)
    for item in enabled_results:
        if item.count > 0:
            lines.append(f"- {item.title}: {item.detail} Affected faces: {len(item.cell_ids)}.")
        else:
            lines.append(f"- {item.title}: OK")
    return "\n".join(lines)


def _empty_result(key: str) -> MeshDoctorCheckResult:
    return MeshDoctorCheckResult(
        key=key,
        title=CHECK_TITLES[key],
        count=0,
        level="info",
        detail="This check is disabled.",
        cell_ids=(),
    )


def _build_report(
    polydata: vtkPolyData,
    config: MeshDoctorCheckConfig,
    context: _AnalysisContext | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> MeshDoctorReport:
    active_context = context or _AnalysisContext(polydata=polydata)
    ordered_keys = tuple(CHECK_TITLES.keys())
    enabled_keys = [key for key in ordered_keys if _is_check_enabled(config, key)]
    completed = 0
    results = []
    for key in ordered_keys:
        result = _run_single_check(polydata, config, key, active_context) or _empty_result(key)
        results.append(result)
        if key in enabled_keys:
            completed += 1
            message = f"Checking {CHECK_TITLES[key]}..."
            percent = 100 if not enabled_keys else int(10 + (completed / len(enabled_keys)) * 90)
            _emit_progress(progress_callback, percent, message)
    return MeshDoctorReport(
        point_count=int(polydata.GetNumberOfPoints()),
        cell_count=int(polydata.GetNumberOfCells()),
        triangle_cell_count=int(polydata.GetNumberOfPolys()),
        check_results=tuple(results),
    )


def _run_single_check(
    polydata: vtkPolyData,
    config: MeshDoctorCheckConfig,
    key: str,
    context: _AnalysisContext | None = None,
) -> MeshDoctorCheckResult | None:
    active_context = context or _AnalysisContext(polydata=polydata)
    check_map: dict[str, tuple[bool, Callable[[], MeshDoctorCheckResult]]] = {
        "non_manifold": (config.non_manifold, lambda: _check_non_manifold(polydata)),
        "self_intersection": (
            config.self_intersection,
            lambda: _check_self_intersection(polydata, _get_adjacency(active_context), active_context),
        ),
        "highly_creased": (
            config.highly_creased,
            lambda: _check_highly_creased(polydata, context=active_context),
        ),
        "spike": (
            config.spike,
            lambda: _check_spike(polydata, config.spike_sensitivity),
        ),
        "small_component": (
            config.small_component,
            lambda: _check_small_component(polydata, config.max_component_size),
        ),
        "small_tunnel": (
            config.small_tunnel,
            lambda: _check_small_tunnel(polydata, config.max_tunnel_size, _get_adjacency(active_context), active_context),
        ),
        "small_hole": (
            config.small_hole,
            lambda: _check_small_hole(polydata, config.max_hole_perimeter),
        ),
    }
    enabled, runner = check_map[key]
    return runner() if enabled else _empty_result(key)


def _is_check_enabled(config: MeshDoctorCheckConfig, key: str) -> bool:
    return {
        "non_manifold": config.non_manifold,
        "self_intersection": config.self_intersection,
        "highly_creased": config.highly_creased,
        "spike": config.spike,
        "small_component": config.small_component,
        "small_tunnel": config.small_tunnel,
        "small_hole": config.small_hole,
    }[key]


def _emit_progress(callback: Callable[[int, str], None] | None, value: int, text: str) -> None:
    if callback is not None:
        callback(int(max(0, min(100, value))), text)


def _check_non_manifold(polydata: vtkPolyData) -> MeshDoctorCheckResult:
    edges = vtkFeatureEdges()
    edges.SetInputData(polydata)
    edges.BoundaryEdgesOff()
    edges.NonManifoldEdgesOn()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.Update()

    output = edges.GetOutput()
    count = int(output.GetNumberOfCells())
    cell_ids = _map_edge_polydata_to_original_cells(polydata, output)
    detail = "Detected non-manifold edges." if count > 0 else "No non-manifold edges found."
    return MeshDoctorCheckResult("non_manifold", CHECK_TITLES["non_manifold"], count, "error", detail, tuple(cell_ids))


def _check_highly_creased(
    polydata: vtkPolyData,
    angle_threshold: float = 120.0,
    context: _AnalysisContext | None = None,
) -> MeshDoctorCheckResult:
    active_context = context or _AnalysisContext(polydata=polydata)
    normals = _get_cell_normals(active_context)
    edge_map = _get_edge_map(active_context)
    flagged: set[int] = set()
    for _, cell_ids in edge_map.items():
        if len(cell_ids) != 2:
            continue
        first, second = tuple(cell_ids)
        dot = float(np.clip(np.dot(normals[first], normals[second]), -1.0, 1.0))
        angle = math.degrees(math.acos(dot))
        if angle >= float(angle_threshold):
            flagged.add(first)
            flagged.add(second)
    count = len(flagged)
    detail = (
        f"Marked regions with normal-angle differences greater than {angle_threshold:g}\N{DEGREE SIGN}."
        if count > 0
        else "No obviously highly creased areas found."
    )
    return MeshDoctorCheckResult("highly_creased", CHECK_TITLES["highly_creased"], count, "warning", detail, tuple(sorted(flagged)))


def _check_spike(polydata: vtkPolyData, sensitivity: int) -> MeshDoctorCheckResult:
    min_angle_threshold = 3.0 + 0.24 * float(max(0, min(100, sensitivity)))
    context = _AnalysisContext(polydata=polydata)
    triangle_points = _get_triangle_points_array(context)
    min_angles = _triangle_min_angles_deg(triangle_points)
    flagged = np.flatnonzero(min_angles < min_angle_threshold).astype(np.int32).tolist()
    detail = (
        f"Used a minimum-angle threshold of {min_angle_threshold:.1f}\N{DEGREE SIGN}."
        if flagged
        else "No obvious spikes found."
    )
    return MeshDoctorCheckResult("spike", CHECK_TITLES["spike"], len(flagged), "warning", detail, tuple(flagged))


def _check_small_component(polydata: vtkPolyData, max_size: float) -> MeshDoctorCheckResult:
    region_map = _component_region_map(polydata)
    flagged_regions = 0
    flagged_cells: list[int] = []
    for cell_ids in region_map.values():
        if not cell_ids:
            continue
        scale = _component_scale(polydata, cell_ids)
        if scale < float(max_size):
            flagged_regions += 1
            flagged_cells.extend(cell_ids)
    detail = (
        f"Marked connected regions with a bounding-box size below {float(max_size):g} mm."
        if flagged_regions
        else "No small components found."
    )
    return MeshDoctorCheckResult(
        "small_component",
        CHECK_TITLES["small_component"],
        flagged_regions,
        "warning",
        detail,
        tuple(sorted(set(flagged_cells))),
    )


def _check_small_hole(polydata: vtkPolyData, max_perimeter: float) -> MeshDoctorCheckResult:
    loops = _extract_boundary_loops(polydata)
    flagged_loops = 0
    flagged_cells: set[int] = set()
    for point_ids, perimeter in loops:
        if perimeter < float(max_perimeter):
            flagged_loops += 1
            flagged_cells.update(_cells_touching_points(polydata, point_ids))
    detail = (
        f"Marked boundary holes with a perimeter below {float(max_perimeter):g} mm."
        if flagged_loops
        else "No small holes found."
    )
    return MeshDoctorCheckResult(
        "small_hole",
        CHECK_TITLES["small_hole"],
        flagged_loops,
        "warning",
        detail,
        tuple(sorted(flagged_cells)),
    )


def _check_small_tunnel(
    polydata: vtkPolyData,
    max_size: float,
    adjacency: list[set[int]],
    context: _AnalysisContext | None = None,
) -> MeshDoctorCheckResult:
    if polydata.GetNumberOfCells() <= 0:
        return MeshDoctorCheckResult("small_tunnel", CHECK_TITLES["small_tunnel"], 0, "warning", "The current mesh is empty.", ())
    active_context = context or _AnalysisContext(polydata=polydata, adjacency=adjacency)
    normals = _get_cell_normals(active_context)
    centers = _get_cell_centers(active_context)
    locator = _get_cell_locator(active_context)

    flagged: set[int] = set()
    half_length = max(float(max_size), 0.1) * 0.5
    for cell_id in range(polydata.GetNumberOfCells()):
        center = centers[cell_id]
        normal = normals[cell_id]
        p1 = center - normal * half_length
        p2 = center + normal * half_length
        hit_ids = vtkIdList()
        locator.FindCellsAlongLine(p1.tolist(), p2.tolist(), 1e-6, hit_ids)
        if hit_ids.GetNumberOfIds() < 2:
            continue
        current_adjacent = adjacency[cell_id] | {cell_id}
        for index in range(hit_ids.GetNumberOfIds()):
            other_id = int(hit_ids.GetId(index))
            if other_id in current_adjacent:
                continue
            if np.dot(normal, normals[other_id]) > -0.2:
                continue
            if np.linalg.norm(center - centers[other_id]) <= float(max_size):
                flagged.add(cell_id)
                flagged.add(other_id)

    group_count = _count_groups(flagged, adjacency)
    detail = (
        f"Marked narrow regions with a thickness below {float(max_size):g} mm."
        if group_count
        else "No obvious small tunnels found."
    )
    return MeshDoctorCheckResult("small_tunnel", CHECK_TITLES["small_tunnel"], group_count, "warning", detail, tuple(sorted(flagged)))


def _check_self_intersection(
    polydata: vtkPolyData,
    adjacency: list[set[int]],
    context: _AnalysisContext | None = None,
) -> MeshDoctorCheckResult:
    active_context = context or _AnalysisContext(polydata=polydata, adjacency=adjacency)

    flagged: set[int] = set()
    triangle_tester = vtkTriangle()
    triangle_points = _get_triangle_points_array(active_context)
    mins = triangle_points.min(axis=1) - 1e-6
    maxs = triangle_points.max(axis=1) + 1e-6
    order = np.argsort(mins[:, 0], kind="mergesort")
    cell_count = int(polydata.GetNumberOfCells())
    for position in range(cell_count):
        cell_id = int(order[position])
        points = triangle_points[cell_id]
        related = adjacency[cell_id] | {cell_id}
        max_x = maxs[cell_id, 0]
        scan = position + 1
        while scan < cell_count:
            other_id = int(order[scan])
            if mins[other_id, 0] > max_x:
                break
            scan += 1
            if other_id in related:
                continue
            if other_id <= cell_id:
                continue
            if mins[other_id, 1] > maxs[cell_id, 1] or maxs[other_id, 1] < mins[cell_id, 1]:
                continue
            if mins[other_id, 2] > maxs[cell_id, 2] or maxs[other_id, 2] < mins[cell_id, 2]:
                continue
            other_points = triangle_points[other_id]
            if triangle_tester.TrianglesIntersect(
                points[0].tolist(),
                points[1].tolist(),
                points[2].tolist(),
                other_points[0].tolist(),
                other_points[1].tolist(),
                other_points[2].tolist(),
            ):
                flagged.add(cell_id)
                flagged.add(other_id)
    detail = "Detected potentially self-intersecting cells." if flagged else "No obvious self-intersections found."
    return MeshDoctorCheckResult(
        "self_intersection",
        CHECK_TITLES["self_intersection"],
        len(flagged),
        "error",
        detail,
        tuple(sorted(flagged)),
    )


def _compute_normals(polydata: vtkPolyData) -> vtkPolyData:
    normals = vtkPolyDataNormals()
    normals.SetInputData(polydata)
    normals.AutoOrientNormalsOn()
    normals.ConsistencyOn()
    normals.SplittingOff()
    normals.ComputeCellNormalsOn()
    normals.ComputePointNormalsOn()
    normals.Update()
    return copy_polydata(normals.GetOutput())


def _clean_polydata(polydata: vtkPolyData) -> vtkPolyData:
    clean = vtkCleanPolyData()
    clean.SetInputData(polydata)
    clean.PointMergingOn()
    clean.Update()
    output = copy_polydata(clean.GetOutput())
    output.BuildCells()
    output.BuildLinks()
    return output


def _fill_holes(polydata: vtkPolyData, size: float) -> vtkPolyData:
    fill_holes = vtkFillHolesFilter()
    fill_holes.SetInputData(polydata)
    fill_holes.SetHoleSize(max(float(size), 0.0))
    fill_holes.Update()
    output = copy_polydata(fill_holes.GetOutput())
    output.BuildCells()
    output.BuildLinks()
    return output


def _keep_largest_component(polydata: vtkPolyData) -> vtkPolyData:
    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputData(polydata)
    connectivity.SetExtractionModeToLargestRegion()
    connectivity.Update()
    output = copy_polydata(connectivity.GetOutput())
    output.BuildCells()
    output.BuildLinks()
    return output


def _remove_cells(polydata: vtkPolyData, remove_ids: tuple[int, ...] | list[int] | set[int]) -> vtkPolyData:
    remove_set = {int(item) for item in remove_ids}
    if not remove_set:
        return copy_polydata(polydata)

    keep_ids = [cell_id for cell_id in range(polydata.GetNumberOfCells()) if cell_id not in remove_set]
    if not keep_ids:
        return vtkPolyData()

    selection_list = vtkIdTypeArray()
    for cell_id in keep_ids:
        selection_list.InsertNextValue(int(cell_id))

    selection_node = vtkSelectionNode()
    selection_node.SetFieldType(vtkSelectionNode.CELL)
    selection_node.SetContentType(vtkSelectionNode.INDICES)
    selection_node.SetSelectionList(selection_list)

    selection = vtkSelection()
    selection.AddNode(selection_node)

    extractor = vtkExtractSelection()
    extractor.SetInputData(0, polydata)
    extractor.SetInputData(1, selection)
    extractor.Update()

    geometry = vtkGeometryFilter()
    geometry.SetInputConnection(extractor.GetOutputPort())
    geometry.Update()

    output = copy_polydata(geometry.GetOutput())
    output.BuildCells()
    output.BuildLinks()
    return output


def _expand_cells(
    polydata: vtkPolyData,
    seed_ids: tuple[int, ...] | list[int] | set[int],
    levels: int,
    context: _AnalysisContext | None = None,
) -> tuple[int, ...]:
    expanded = {int(item) for item in seed_ids}
    if not expanded or levels <= 0:
        return tuple(sorted(expanded))

    active_context = context or _AnalysisContext(polydata=polydata)
    adjacency = _get_adjacency(active_context)
    frontier = set(expanded)
    for _ in range(int(levels)):
        next_frontier: set[int] = set()
        for cell_id in frontier:
            next_frontier.update(adjacency[cell_id])
        next_frontier -= expanded
        if not next_frontier:
            break
        expanded.update(next_frontier)
        frontier = next_frontier
    return tuple(sorted(expanded))


def _build_cell_adjacency(polydata: vtkPolyData) -> list[set[int]]:
    point_to_cells: list[set[int]] = [set() for _ in range(polydata.GetNumberOfPoints())]
    for cell_id in range(polydata.GetNumberOfCells()):
        cell = polydata.GetCell(cell_id)
        for point_index in range(cell.GetNumberOfPoints()):
            point_to_cells[cell.GetPointId(point_index)].add(cell_id)

    adjacency: list[set[int]] = [set() for _ in range(polydata.GetNumberOfCells())]
    for cells in point_to_cells:
        for cell_id in cells:
            adjacency[cell_id].update(cells - {cell_id})
    return adjacency


def _build_edge_to_cells(polydata: vtkPolyData) -> dict[tuple[int, int], set[int]]:
    edge_map: dict[tuple[int, int], set[int]] = {}
    for cell_id in range(polydata.GetNumberOfCells()):
        cell = polydata.GetCell(cell_id)
        point_count = cell.GetNumberOfPoints()
        for index in range(point_count):
            first = int(cell.GetPointId(index))
            second = int(cell.GetPointId((index + 1) % point_count))
            edge = (first, second) if first < second else (second, first)
            edge_map.setdefault(edge, set()).add(cell_id)
    return edge_map


def _cell_normals(polydata: vtkPolyData) -> np.ndarray:
    with_normals = _compute_normals(polydata)
    normals_array = with_normals.GetCellData().GetNormals()
    if normals_array is None or normals_array.GetNumberOfTuples() == 0:
        return np.zeros((polydata.GetNumberOfCells(), 3), dtype=np.float64)
    normals = np.asarray(vtk_to_numpy(normals_array), dtype=np.float64).reshape(-1, 3)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0.0] = 1.0
    return normals / lengths


def _cell_centers(polydata: vtkPolyData) -> np.ndarray:
    context = _AnalysisContext(polydata=polydata)
    return _get_triangle_points_array(context).mean(axis=1)


def _triangle_points(polydata: vtkPolyData, cell_id: int) -> np.ndarray | None:
    cell = polydata.GetCell(int(cell_id))
    if cell is None or cell.GetNumberOfPoints() < 3:
        return None
    points = np.zeros((3, 3), dtype=np.float64)
    for index in range(3):
        points[index] = np.asarray(polydata.GetPoint(cell.GetPointId(index)), dtype=np.float64)
    return points


def _triangle_min_angle_deg(points: np.ndarray) -> float:
    edges = [
        points[1] - points[0],
        points[2] - points[1],
        points[0] - points[2],
    ]

    def angle(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom <= 1e-12:
            return 0.0
        dot = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
        return math.degrees(math.acos(dot))

    return min(
        angle(edges[0], -edges[2]),
        angle(edges[1], -edges[0]),
        angle(edges[2], -edges[1]),
    )


def _triangle_min_angles_deg(triangle_points: np.ndarray) -> np.ndarray:
    edge01 = triangle_points[:, 1] - triangle_points[:, 0]
    edge12 = triangle_points[:, 2] - triangle_points[:, 1]
    edge20 = triangle_points[:, 0] - triangle_points[:, 2]
    return np.minimum.reduce(
        (
            _vector_angles_deg(edge01, -edge20),
            _vector_angles_deg(edge12, -edge01),
            _vector_angles_deg(edge20, -edge12),
        )
    )


def _vector_angles_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    denom = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    safe = np.where(denom <= 1e-12, 1.0, denom)
    cosines = np.clip(np.sum(a * b, axis=1) / safe, -1.0, 1.0)
    angles = np.degrees(np.arccos(cosines))
    angles[denom <= 1e-12] = 0.0
    return angles


def _component_region_map(polydata: vtkPolyData) -> dict[int, list[int]]:
    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputData(polydata)
    connectivity.SetExtractionModeToAllRegions()
    connectivity.ColorRegionsOn()
    connectivity.Update()

    output = connectivity.GetOutput()
    region_ids_array = output.GetCellData().GetArray("RegionId")
    if region_ids_array is None:
        return {0: list(range(polydata.GetNumberOfCells()))}

    region_ids = np.asarray(vtk_to_numpy(region_ids_array), dtype=np.int32).reshape(-1)
    region_map: dict[int, list[int]] = {}
    for cell_id, region_id in enumerate(region_ids.tolist()):
        region_map.setdefault(int(region_id), []).append(int(cell_id))
    return region_map


def _remove_small_components_by_size(polydata: vtkPolyData, max_size: float) -> tuple[vtkPolyData, int]:
    region_map = _component_region_map(polydata)
    remove_ids: list[int] = []
    removed_regions = 0
    threshold = float(max_size)
    for cell_ids in region_map.values():
        if not cell_ids:
            continue
        if _component_scale(polydata, cell_ids) < threshold:
            remove_ids.extend(cell_ids)
            removed_regions += 1
    if not remove_ids:
        return copy_polydata(polydata), 0
    candidate = _remove_cells(polydata, remove_ids)
    if candidate.GetNumberOfCells() <= 0:
        return copy_polydata(polydata), 0
    return candidate, removed_regions


def _component_scale(polydata: vtkPolyData, cell_ids: list[int]) -> float:
    point_ids: set[int] = set()
    for cell_id in cell_ids:
        cell = polydata.GetCell(int(cell_id))
        for point_index in range(cell.GetNumberOfPoints()):
            point_ids.add(int(cell.GetPointId(point_index)))
    if not point_ids:
        return 0.0
    points = np.asarray([polydata.GetPoint(point_id) for point_id in sorted(point_ids)], dtype=np.float64)
    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    return float(np.linalg.norm(maxs - mins))


def _extract_boundary_loops(polydata: vtkPolyData) -> list[tuple[list[int], float]]:
    edges = vtkFeatureEdges()
    edges.SetInputData(polydata)
    edges.BoundaryEdgesOn()
    edges.NonManifoldEdgesOff()
    edges.FeatureEdgesOff()
    edges.ManifoldEdgesOff()
    edges.Update()

    boundary = edges.GetOutput()
    if boundary is None or boundary.GetNumberOfCells() == 0:
        return []

    connectivity = vtkPolyDataConnectivityFilter()
    connectivity.SetInputData(boundary)
    connectivity.SetExtractionModeToAllRegions()
    connectivity.ColorRegionsOn()
    connectivity.Update()
    connected = connectivity.GetOutput()

    locator = vtkPointLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()

    region_ids_array = connected.GetCellData().GetArray("RegionId")
    if region_ids_array is None:
        return []
    region_ids = np.asarray(vtk_to_numpy(region_ids_array), dtype=np.int32).reshape(-1)

    region_to_point_ids: dict[int, set[int]] = {}
    region_to_perimeter: dict[int, float] = {}
    for cell_id in range(connected.GetNumberOfCells()):
        region_id = int(region_ids[cell_id])
        cell = connected.GetCell(cell_id)
        if cell.GetNumberOfPoints() < 2:
            continue
        p1 = np.asarray(connected.GetPoint(cell.GetPointId(0)), dtype=np.float64)
        p2 = np.asarray(connected.GetPoint(cell.GetPointId(1)), dtype=np.float64)
        region_to_perimeter[region_id] = region_to_perimeter.get(region_id, 0.0) + float(np.linalg.norm(p2 - p1))
        region_points = region_to_point_ids.setdefault(region_id, set())
        region_points.add(int(locator.FindClosestPoint(p1.tolist())))
        region_points.add(int(locator.FindClosestPoint(p2.tolist())))

    return [
        (sorted(point_ids), float(region_to_perimeter.get(region_id, 0.0)))
        for region_id, point_ids in region_to_point_ids.items()
    ]


def _count_small_hole_loops(polydata: vtkPolyData, max_perimeter: float) -> int:
    threshold = float(max_perimeter)
    return sum(1 for _, perimeter in _extract_boundary_loops(polydata) if perimeter < threshold)


def _cells_touching_points(polydata: vtkPolyData, point_ids: list[int]) -> set[int]:
    cell_ids: set[int] = set()
    for point_id in point_ids:
        ids = vtkIdList()
        polydata.GetPointCells(int(point_id), ids)
        for index in range(ids.GetNumberOfIds()):
            cell_ids.add(int(ids.GetId(index)))
    return cell_ids


def _map_edge_polydata_to_original_cells(source: vtkPolyData, edge_polydata: vtkPolyData) -> list[int]:
    point_locator = vtkPointLocator()
    point_locator.SetDataSet(source)
    point_locator.BuildLocator()

    mapped_cells: set[int] = set()
    for cell_id in range(edge_polydata.GetNumberOfCells()):
        cell = edge_polydata.GetCell(cell_id)
        if cell.GetNumberOfPoints() < 2:
            continue
        source_ids = [
            int(point_locator.FindClosestPoint(edge_polydata.GetPoint(cell.GetPointId(index))))
            for index in range(2)
        ]
        neighbors = vtkIdList()
        source.GetCellEdgeNeighbors(-1, source_ids[0], source_ids[1], neighbors)
        for index in range(neighbors.GetNumberOfIds()):
            mapped_cells.add(int(neighbors.GetId(index)))
    return sorted(mapped_cells)


def _count_groups(cell_ids: set[int], adjacency: list[set[int]]) -> int:
    remaining = set(cell_ids)
    groups = 0
    while remaining:
        groups += 1
        seed = remaining.pop()
        stack = [seed]
        while stack:
            current = stack.pop()
            neighbors = adjacency[current] & remaining
            if not neighbors:
                continue
            remaining -= neighbors
            stack.extend(neighbors)
    return groups


def _get_adjacency(context: _AnalysisContext) -> list[set[int]]:
    if context.adjacency is None:
        context.adjacency = _build_cell_adjacency(context.polydata)
    return context.adjacency


def _get_edge_map(context: _AnalysisContext) -> dict[tuple[int, int], set[int]]:
    if context.edge_map is None:
        context.edge_map = _build_edge_to_cells(context.polydata)
    return context.edge_map


def _get_cell_normals(context: _AnalysisContext) -> np.ndarray:
    if context.cell_normals is None:
        context.cell_normals = _cell_normals(context.polydata)
    return context.cell_normals


def _get_cell_centers(context: _AnalysisContext) -> np.ndarray:
    if context.cell_centers is None:
        context.cell_centers = _get_triangle_points_array(context).mean(axis=1)
    return context.cell_centers


def _get_cell_locator(context: _AnalysisContext) -> vtkCellLocator:
    if context.cell_locator is None:
        locator = vtkCellLocator()
        locator.SetDataSet(context.polydata)
        locator.BuildLocator()
        context.cell_locator = locator
    return context.cell_locator


def _get_triangle_point_ids(context: _AnalysisContext) -> np.ndarray:
    if context.triangle_point_ids is None:
        polys = context.polydata.GetPolys()
        poly_data = None if polys is None else polys.GetData()
        if poly_data is None or poly_data.GetNumberOfTuples() == 0:
            context.triangle_point_ids = np.zeros((0, 3), dtype=np.int64)
        else:
            raw = np.asarray(vtk_to_numpy(poly_data), dtype=np.int64).reshape(-1, 4)
            if raw.shape[1] != 4 or np.any(raw[:, 0] != 3):
                triangle_ids = np.zeros((context.polydata.GetNumberOfCells(), 3), dtype=np.int64)
                for cell_id in range(context.polydata.GetNumberOfCells()):
                    cell = context.polydata.GetCell(cell_id)
                    for index in range(min(3, cell.GetNumberOfPoints())):
                        triangle_ids[cell_id, index] = int(cell.GetPointId(index))
                context.triangle_point_ids = triangle_ids
            else:
                context.triangle_point_ids = raw[:, 1:]
    return context.triangle_point_ids


def _get_triangle_points_array(context: _AnalysisContext) -> np.ndarray:
    if context.triangle_points is None:
        point_data = context.polydata.GetPoints().GetData()
        point_coords = np.asarray(vtk_to_numpy(point_data), dtype=np.float64)
        triangle_ids = _get_triangle_point_ids(context)
        context.triangle_points = point_coords[triangle_ids] if triangle_ids.size else np.zeros((0, 3, 3), dtype=np.float64)
    return context.triangle_points
