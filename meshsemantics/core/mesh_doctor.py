from __future__ import annotations

from dataclasses import dataclass
import math

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


def analyze_polydata(polydata: vtkPolyData, config: MeshDoctorCheckConfig | None = None) -> MeshDoctorReport:
    active_config = config or MeshDoctorCheckConfig()
    working = triangulate_polydata(polydata)
    adjacency = _build_cell_adjacency(working)

    results: list[MeshDoctorCheckResult] = []

    non_manifold_result = _check_non_manifold(working) if active_config.non_manifold else _empty_result("non_manifold")
    results.append(non_manifold_result)

    self_intersection_result = (
        _check_self_intersection(working, adjacency) if active_config.self_intersection else _empty_result("self_intersection")
    )
    results.append(self_intersection_result)

    highly_creased_result = (
        _check_highly_creased(working) if active_config.highly_creased else _empty_result("highly_creased")
    )
    results.append(highly_creased_result)

    spike_result = _check_spike(working, active_config.spike_sensitivity) if active_config.spike else _empty_result("spike")
    results.append(spike_result)

    small_component_result = (
        _check_small_component(working, active_config.max_component_size) if active_config.small_component else _empty_result("small_component")
    )
    results.append(small_component_result)

    small_tunnel_result = (
        _check_small_tunnel(working, active_config.max_tunnel_size, adjacency) if active_config.small_tunnel else _empty_result("small_tunnel")
    )
    results.append(small_tunnel_result)

    small_hole_result = (
        _check_small_hole(working, active_config.max_hole_perimeter) if active_config.small_hole else _empty_result("small_hole")
    )
    results.append(small_hole_result)

    return MeshDoctorReport(
        point_count=int(working.GetNumberOfPoints()),
        cell_count=int(working.GetNumberOfCells()),
        triangle_cell_count=int(working.GetNumberOfPolys()),
        check_results=tuple(results),
    )


def repair_polydata(
    polydata: vtkPolyData,
    check_config: MeshDoctorCheckConfig | None = None,
    repair_options: MeshDoctorRepairOptions | None = None,
) -> MeshDoctorRepairResult:
    active_check_config = check_config or MeshDoctorCheckConfig()
    active_repair_options = repair_options or MeshDoctorRepairOptions()

    working = triangulate_polydata(polydata)
    operations: list[str] = ["Triangulated mesh"]
    changed_topology = False

    if active_repair_options.merge_points:
        working = _clean_polydata(working)
        operations.append("Merged duplicate points")
        changed_topology = True

    ordered_keys = (
        "non_manifold",
        "self_intersection",
        "small_tunnel",
        "highly_creased",
        "spike",
        "small_component",
        "small_hole",
    )
    for key in ordered_keys:
        report = analyze_polydata(working, active_check_config)
        result = report.result_for(key)
        if result is None:
            continue
        if result.count <= 0:
            continue
        candidate = copy_polydata(working)
        applied = False
        if result.key == "small_component":
            candidate = _remove_cells(working, result.cell_ids)
            applied = True
            op_text = f"Removed {result.count} small component region(s)"
        elif result.key == "small_hole" and active_repair_options.fill_holes:
            candidate = _fill_holes(working, active_check_config.max_hole_perimeter)
            applied = True
            op_text = f"Filled small holes up to {active_check_config.max_hole_perimeter:g} mm"
        elif result.key == "small_tunnel":
            expanded = _expand_cells(working, result.cell_ids, max(0, int(active_check_config.expand_level)))
            candidate = _remove_cells(working, expanded)
            if active_repair_options.fill_holes:
                candidate = _fill_holes(candidate, active_check_config.max_tunnel_size)
            applied = True
            op_text = (
                f"Removed {len(expanded)} cell(s) around small tunnels "
                f"with expansion level {int(active_check_config.expand_level)}"
            )
        elif result.key in {"non_manifold", "self_intersection", "highly_creased", "spike"}:
            expanded = _expand_cells(working, result.cell_ids, max(1, int(active_check_config.expand_level)))
            candidate = _remove_cells(working, expanded)
            if active_repair_options.fill_holes:
                candidate = _fill_holes(candidate, max(active_check_config.max_hole_perimeter, active_check_config.max_tunnel_size))
            applied = True
            op_text = f"Repaired {result.title} by processing {len(expanded)} cell(s)"

        if applied:
            if candidate.GetNumberOfCells() > 0:
                working = candidate
                if active_repair_options.merge_points:
                    working = _clean_polydata(working)
                operations.append(op_text)
                changed_topology = True
            else:
                operations.append(f"Skipped repairing {result.title} to avoid generating an empty mesh")

    if active_repair_options.keep_largest_component:
        working = _keep_largest_component(working)
        operations.append("Kept only the largest connected component")
        changed_topology = True

    if active_repair_options.recompute_normals:
        working = _compute_normals(working)
        operations.append("Recomputed normals")

    working.BuildCells()
    working.BuildLinks()
    final_report = analyze_polydata(working, active_check_config)
    return MeshDoctorRepairResult(
        polydata=working,
        report=final_report,
        operations=tuple(operations),
        changed_topology=changed_topology,
    )


def format_report(report: MeshDoctorReport) -> str:
    lines = [
        f"Points: {report.point_count}",
        f"Cells: {report.cell_count}",
        f"Triangle cells: {report.triangle_cell_count}",
        "",
        "Check Results:",
    ]
    for item in report.check_results:
        if item.count > 0:
            lines.append(f"- {item.title}: {item.count} | {item.detail}")
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


def _check_highly_creased(polydata: vtkPolyData, angle_threshold: float = 120.0) -> MeshDoctorCheckResult:
    normals = _cell_normals(polydata)
    edge_map = _build_edge_to_cells(polydata)
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
    flagged: list[int] = []
    for cell_id in range(polydata.GetNumberOfCells()):
        points = _triangle_points(polydata, cell_id)
        if points is None:
            continue
        min_angle = _triangle_min_angle_deg(points)
        if min_angle < min_angle_threshold:
            flagged.append(cell_id)
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


def _check_small_tunnel(polydata: vtkPolyData, max_size: float, adjacency: list[set[int]]) -> MeshDoctorCheckResult:
    if polydata.GetNumberOfCells() <= 0:
        return MeshDoctorCheckResult("small_tunnel", CHECK_TITLES["small_tunnel"], 0, "warning", "The current mesh is empty.", ())
    normals = _cell_normals(polydata)
    centers = _cell_centers(polydata)
    locator = vtkCellLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()

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


def _check_self_intersection(polydata: vtkPolyData, adjacency: list[set[int]]) -> MeshDoctorCheckResult:
    locator = vtkCellLocator()
    locator.SetDataSet(polydata)
    locator.BuildLocator()

    flagged: set[int] = set()
    triangle_tester = vtkTriangle()
    for cell_id in range(polydata.GetNumberOfCells()):
        points = _triangle_points(polydata, cell_id)
        if points is None:
            continue
        related = adjacency[cell_id] | {cell_id}
        mins = points.min(axis=0) - 1e-6
        maxs = points.max(axis=0) + 1e-6
        bounds = (mins[0], maxs[0], mins[1], maxs[1], mins[2], maxs[2])
        hit_ids = vtkIdList()
        locator.FindCellsWithinBounds(bounds, hit_ids)
        for index in range(hit_ids.GetNumberOfIds()):
            other_id = int(hit_ids.GetId(index))
            if other_id in related or other_id <= cell_id:
                continue
            other_points = _triangle_points(polydata, other_id)
            if other_points is None:
                continue
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


def _expand_cells(polydata: vtkPolyData, seed_ids: tuple[int, ...] | list[int] | set[int], levels: int) -> tuple[int, ...]:
    expanded = {int(item) for item in seed_ids}
    if not expanded or levels <= 0:
        return tuple(sorted(expanded))

    adjacency = _build_cell_adjacency(polydata)
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
    centers = np.zeros((polydata.GetNumberOfCells(), 3), dtype=np.float64)
    for cell_id in range(polydata.GetNumberOfCells()):
        points = _triangle_points(polydata, cell_id)
        if points is None:
            continue
        centers[cell_id] = points.mean(axis=0)
    return centers


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
