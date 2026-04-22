from __future__ import annotations

from dataclasses import dataclass

from vtkmodules.vtkCommonCore import vtkIdTypeArray
from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkSelection, vtkSelectionNode
from vtkmodules.vtkFiltersExtraction import vtkExtractSelection
from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper


def deep_copy_polydata(polydata: vtkPolyData) -> vtkPolyData:
    copied = vtkPolyData()
    copied.DeepCopy(polydata)
    return copied


def extract_polydata_cells(polydata: vtkPolyData, cell_ids: list[int] | tuple[int, ...]) -> vtkPolyData:
    ids = vtkIdTypeArray()
    for cell_id in cell_ids:
        ids.InsertNextValue(int(cell_id))

    selection_node = vtkSelectionNode()
    selection_node.SetFieldType(vtkSelectionNode.CELL)
    selection_node.SetContentType(vtkSelectionNode.INDICES)
    selection_node.SetSelectionList(ids)

    selection = vtkSelection()
    selection.AddNode(selection_node)

    extractor = vtkExtractSelection()
    extractor.SetInputData(0, polydata)
    extractor.SetInputData(1, selection)
    extractor.Update()

    geometry = vtkGeometryFilter()
    geometry.SetInputConnection(extractor.GetOutputPort())
    geometry.Update()

    extracted = vtkPolyData()
    extracted.DeepCopy(geometry.GetOutput())
    return extracted


def build_mesh_actor(polydata: vtkPolyData) -> tuple[vtkPolyDataMapper, vtkActor]:
    mapper = vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    actor = vtkActor()
    actor.SetMapper(mapper)
    return mapper, actor


@dataclass
class MeshDocument:
    dataset: vtkPolyData
    mapper: vtkPolyDataMapper
    actor: vtkActor
    filename: str = ""

    @classmethod
    def from_polydata(cls, polydata: vtkPolyData, filename: str = "") -> "MeshDocument":
        copied = deep_copy_polydata(polydata)
        mapper, actor = build_mesh_actor(copied)
        return cls(dataset=copied, mapper=mapper, actor=actor, filename=filename)

    def clone(self, deep: bool = True) -> "MeshDocument":
        if deep:
            return MeshDocument.from_polydata(self.dataset, filename=self.filename)

        copied = vtkPolyData()
        copied.ShallowCopy(self.dataset)
        mapper, actor = build_mesh_actor(copied)
        return MeshDocument(dataset=copied, mapper=mapper, actor=actor, filename=self.filename)

    def extract_cells(self, cell_ids: list[int] | tuple[int, ...]) -> "MeshDocument":
        extracted = extract_polydata_cells(self.dataset, cell_ids)
        return MeshDocument.from_polydata(extracted, filename=self.filename)

    def modified(self) -> None:
        self.dataset.Modified()
        self.mapper.Modified()
        self.mapper.Update()
