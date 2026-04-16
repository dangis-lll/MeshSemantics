from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import vedo

from meshsemantics.core.label_engine import LabelEngine


class FileIO:
    SUPPORTED_SUFFIXES = {".stl", ".vtp"}

    @staticmethod
    def _normalize_mesh(loaded, file_path: Path):
        mesh = loaded
        if isinstance(mesh, (list, tuple)):
            mesh = mesh[0] if mesh else None

        if mesh is None:
            raise ValueError(f"Unable to load mesh: {file_path}")

        if not hasattr(mesh, "dataset"):
            unpack = getattr(mesh, "unpack", None)
            if callable(unpack):
                items = unpack()
                if items:
                    mesh = items[0]

        if not hasattr(mesh, "dataset"):
            raise ValueError(f"Loaded object is not a mesh: {type(mesh).__name__}")

        return mesh

    @staticmethod
    def _coerce_cell_count(mesh) -> int:
        dataset = getattr(mesh, "dataset", None)
        if dataset is not None and hasattr(dataset, "GetNumberOfCells"):
            value = dataset.GetNumberOfCells()
            if value is not None:
                return int(value)

        value = getattr(mesh, "ncells", None)
        if callable(value):
            value = value()
        if value is not None:
            return int(value)

        cells = getattr(mesh, "cells", None)
        if cells is not None:
            try:
                return int(len(cells))
            except Exception:
                pass

        raise ValueError("Unable to determine cell count for mesh.")

    @classmethod
    def load_mesh(cls, file_path: str | Path):
        path = Path(file_path)
        loaded = vedo.Mesh(str(path)) if path.suffix.lower() == ".stl" else vedo.load(str(path))
        mesh = cls._normalize_mesh(loaded, path)

        n_cells = cls._coerce_cell_count(mesh)
        if path.suffix.lower() == ".vtp":
            try:
                labels = np.asarray(mesh.celldata["Label"]).reshape(-1)
            except Exception:
                labels = np.zeros(n_cells, dtype=np.uint8)
        else:
            labels = np.zeros(n_cells, dtype=np.uint8)

        if labels.size != n_cells:
            resized = np.zeros(n_cells, dtype=np.uint8)
            copy_count = min(labels.size, n_cells)
            if copy_count:
                resized[:copy_count] = np.asarray(labels[:copy_count], dtype=np.uint8)
            labels = resized

        mesh.celldata["Label"] = labels.astype("uint8").reshape(-1, 1)
        mesh.dataset.GetCellData().SetActiveScalars("Label")
        mesh.dataset.Modified()
        return mesh, labels.astype(np.int32)

    @classmethod
    def save_vtp(cls, mesh, file_path: str | Path, labels: np.ndarray) -> None:
        path = Path(file_path)
        export_mesh = mesh.clone(deep=True)
        export_mesh.celldata["Label"] = np.asarray(labels, dtype=np.uint8).reshape(-1, 1)

        cell_data = export_mesh.dataset.GetCellData()
        point_data = export_mesh.dataset.GetPointData()
        cell_data.SetActiveScalars("Label")

        # Normals are only needed for interactive preview and should not be persisted.
        if cell_data.GetNormals() is not None:
            cell_data.SetNormals(None)
        if point_data.GetNormals() is not None:
            point_data.SetNormals(None)
        if cell_data.HasArray("Normals"):
            cell_data.RemoveArray("Normals")
        if point_data.HasArray("Normals"):
            point_data.RemoveArray("Normals")

        export_mesh.dataset.Modified()
        vedo.write(export_mesh, str(path))

    @classmethod
    def save_labels_json(cls, file_path: str | Path, labels: np.ndarray) -> None:
        path = Path(file_path)
        payload = {
            "cell_count": int(np.asarray(labels).size),
            "labels": np.asarray(labels, dtype=np.int32).reshape(-1).tolist(),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def save_stl_per_label(
        cls,
        mesh,
        label_engine: LabelEngine,
        output_dir: str | Path,
        save_unlabeled: bool = False,
    ) -> list[Path]:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        mesh_name = Path(getattr(mesh, "filename", "mesh")).stem

        saved_files: list[Path] = []
        for label in label_engine.unique_labels():
            if label == 0 and not save_unlabeled:
                continue
            cell_ids = label_engine.get_cells_by_label(label)
            if cell_ids.size == 0:
                continue
            sub_mesh = mesh.clone(deep=True).extract_cells(cell_ids.tolist())
            suffix = str(label)
            target = output_path / f"{mesh_name}_{suffix}.stl"
            vedo.write(sub_mesh, str(target))
            saved_files.append(target)
        return saved_files
