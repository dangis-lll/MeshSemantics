from __future__ import annotations

from pathlib import Path

import numpy as np
import vedo

from meshlabeler.core.label_engine import LabelEngine


class FileIO:
    SUPPORTED_SUFFIXES = {".stl", ".vtp"}

    @classmethod
    def load_mesh(cls, file_path: str | Path):
        path = Path(file_path)
        mesh = vedo.load(str(path))
        if mesh is None:
            raise ValueError(f"Unable to load mesh: {path}")

        n_cells = int(mesh.dataset.GetNumberOfCells())
        try:
            labels = np.asarray(mesh.celldata["Label"]).reshape(-1)
        except Exception:
            labels = np.zeros(n_cells, dtype=np.uint8)

        if labels.size != n_cells:
            labels = np.resize(labels, n_cells).astype(np.uint8)

        mesh.celldata["Label"] = labels.astype("uint8").reshape(-1, 1)
        mesh.dataset.GetCellData().SetActiveScalars("Label")
        mesh.dataset.Modified()
        return mesh, labels.astype(np.int32)

    @classmethod
    def save_vtp(cls, mesh, file_path: str | Path, labels: np.ndarray) -> None:
        path = Path(file_path)
        mesh.celldata["Label"] = np.asarray(labels, dtype=np.uint8).reshape(-1, 1)
        mesh.dataset.GetCellData().SetActiveScalars("Label")
        mesh.dataset.Modified()
        vedo.write(mesh, str(path))

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
            cell_ids = label_engine.get_cells_by_label(label)
            if cell_ids.size == 0:
                continue
            sub_mesh = mesh.clone(deep=True).extract_cells(cell_ids.tolist())
            suffix = str(label)
            target = output_path / f"{mesh_name}_{suffix}.stl"
            vedo.write(sub_mesh, str(target))
            saved_files.append(target)
        return saved_files
