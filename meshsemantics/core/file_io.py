from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import vedo
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

from meshsemantics.core.label_engine import LabelEngine


class FileIO:
    SUPPORTED_SUFFIXES = {".stl", ".vtp", ".ply"}

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

    @staticmethod
    def pack_rgb(rgb: np.ndarray) -> np.ndarray:
        values = np.asarray(rgb, dtype=np.uint32).reshape(-1, 3)
        values = np.clip(values, 0, 255).astype(np.uint32, copy=False)
        return ((values[:, 0] << 16) | (values[:, 1] << 8) | values[:, 2]).astype(np.uint32)

    @staticmethod
    def unpack_rgb(packed: np.ndarray) -> np.ndarray:
        values = np.asarray(packed, dtype=np.uint32).reshape(-1)
        rgb = np.empty((values.size, 3), dtype=np.uint8)
        rgb[:, 0] = ((values >> 16) & 255).astype(np.uint8)
        rgb[:, 1] = ((values >> 8) & 255).astype(np.uint8)
        rgb[:, 2] = (values & 255).astype(np.uint8)
        return rgb

    @classmethod
    def _read_cell_color(cls, mesh, n_cells: int) -> np.ndarray | None:
        dataset = getattr(mesh, "dataset", None)
        if dataset is None:
            return None
        array = dataset.GetCellData().GetArray("Color")
        if array is None:
            return None
        values = np.asarray(vtk_to_numpy(array))
        components = int(array.GetNumberOfComponents())
        if components >= 3:
            return cls.pack_rgb(values.reshape(-1, components)[:, :3])
        packed = values.reshape(-1).astype(np.uint32, copy=False)
        if packed.size != n_cells:
            return None
        return packed.copy()

    @staticmethod
    def _candidate_point_color_arrays(dataset):
        point_data = dataset.GetPointData()
        scalars = point_data.GetScalars()
        if scalars is not None:
            yield scalars
        preferred_names = (
            "RGB",
            "RGBA",
            "Color",
            "Colors",
            "colors",
            "DiffuseColor",
            "red",
        )
        for name in preferred_names:
            array = point_data.GetArray(name)
            if array is not None:
                yield array
        for index in range(point_data.GetNumberOfArrays()):
            array = point_data.GetArray(index)
            if array is not None:
                yield array

    @classmethod
    def _read_point_rgb(cls, mesh) -> np.ndarray | None:
        dataset = getattr(mesh, "dataset", None)
        if dataset is None:
            return None
        point_count = int(dataset.GetNumberOfPoints())
        if point_count <= 0:
            return None

        point_data = dataset.GetPointData()
        red = cls._first_point_array(point_data, ("red", "Red", "r"))
        green = cls._first_point_array(point_data, ("green", "Green", "g"))
        blue = cls._first_point_array(point_data, ("blue", "Blue", "b"))
        if red is not None and green is not None and blue is not None:
            rgb = np.column_stack([
                np.asarray(vtk_to_numpy(red)).reshape(-1),
                np.asarray(vtk_to_numpy(green)).reshape(-1),
                np.asarray(vtk_to_numpy(blue)).reshape(-1),
            ])
            if rgb.shape[0] == point_count:
                return np.clip(rgb, 0, 255).astype(np.uint8)

        seen: set[int] = set()
        for array in cls._candidate_point_color_arrays(dataset):
            address = id(array)
            if address in seen:
                continue
            seen.add(address)
            components = int(array.GetNumberOfComponents())
            if components < 3:
                continue
            values = np.asarray(vtk_to_numpy(array)).reshape(-1, components)
            if values.shape[0] != point_count:
                continue
            rgb = values[:, :3]
            if np.issubdtype(rgb.dtype, np.floating) and rgb.size and float(np.nanmax(rgb)) <= 1.0:
                rgb = rgb * 255.0
            return np.clip(rgb, 0, 255).astype(np.uint8)
        return None

    @staticmethod
    def _first_point_array(point_data, names: tuple[str, ...]):
        for name in names:
            array = point_data.GetArray(name)
            if array is not None:
                return array
        return None

    @classmethod
    def _compute_cell_color_from_points(cls, mesh, n_cells: int) -> np.ndarray | None:
        dataset = getattr(mesh, "dataset", None)
        point_rgb = cls._read_point_rgb(mesh)
        if dataset is None or point_rgb is None:
            return None
        colors = np.zeros((n_cells, 3), dtype=np.uint8)
        for cell_id in range(n_cells):
            cell = dataset.GetCell(int(cell_id))
            point_ids = [
                int(cell.GetPointId(index))
                for index in range(cell.GetNumberOfPoints())
                if 0 <= int(cell.GetPointId(index)) < point_rgb.shape[0]
            ]
            if not point_ids:
                continue
            colors[cell_id] = np.rint(point_rgb[point_ids].astype(np.float32).mean(axis=0)).astype(np.uint8)
        return cls.pack_rgb(colors)

    @classmethod
    def _ensure_cell_color(cls, mesh, n_cells: int) -> np.ndarray:
        packed = cls._read_cell_color(mesh, n_cells)
        if packed is None:
            packed = cls._compute_cell_color_from_points(mesh, n_cells)
        if packed is None or packed.size != n_cells:
            packed = np.zeros(n_cells, dtype=np.uint32)
        packed = np.asarray(packed, dtype=np.uint32).reshape(-1)
        cls._write_cell_color(mesh, packed)
        return packed

    @staticmethod
    def _write_cell_color(mesh, packed_color: np.ndarray) -> None:
        dataset = getattr(mesh, "dataset", None)
        if dataset is None:
            return
        values = np.asarray(packed_color, dtype=np.uint32).reshape(-1)
        vtk_array = numpy_to_vtk(values, deep=True)
        vtk_array.SetName("Color")
        dataset.GetCellData().RemoveArray("Color")
        dataset.GetCellData().AddArray(vtk_array)
        dataset.Modified()

    @classmethod
    def load_mesh(cls, file_path: str | Path):
        path = Path(file_path)
        loaded = vedo.Mesh(str(path)) if path.suffix.lower() == ".stl" else vedo.load(str(path))
        mesh = cls._normalize_mesh(loaded, path)

        n_cells = cls._coerce_cell_count(mesh)
        if path.suffix.lower() in {".vtp", ".ply"}:
            try:
                labels = np.asarray(mesh.celldata["Label"], dtype=np.int32).reshape(-1)
            except Exception:
                logging.warning("Mesh %s is missing a readable Label array; defaulting labels to 0.", path)
                labels = np.zeros(n_cells, dtype=np.int32)
        else:
            labels = np.zeros(n_cells, dtype=np.int32)

        if labels.size != n_cells:
            logging.warning(
                "Mesh %s has a Label array of size %s but %s cells; resizing labels to match cell count.",
                path,
                labels.size,
                n_cells,
            )
            resized = np.zeros(n_cells, dtype=np.int32)
            copy_count = min(labels.size, n_cells)
            if copy_count:
                resized[:copy_count] = np.asarray(labels[:copy_count], dtype=np.int32)
            labels = resized

        mesh.celldata["Label"] = labels.astype(np.int32).reshape(-1, 1)
        cls._ensure_cell_color(mesh, n_cells)
        mesh.dataset.GetCellData().SetActiveScalars("Label")
        mesh.dataset.Modified()
        return mesh, labels.astype(np.int32)

    @classmethod
    def save_vtp(cls, mesh, file_path: str | Path, labels: np.ndarray) -> None:
        path = Path(file_path)
        export_mesh = mesh.clone(deep=True)
        cell_data = export_mesh.dataset.GetCellData()
        for transient_name in ("DisplayLabel", "DisplayColor"):
            if cell_data.HasArray(transient_name):
                cell_data.RemoveArray(transient_name)
        export_mesh.celldata["Label"] = np.asarray(labels, dtype=np.int32).reshape(-1, 1)
        color = cls._read_cell_color(export_mesh, int(np.asarray(labels).size))
        if color is None:
            color = cls._compute_cell_color_from_points(export_mesh, int(np.asarray(labels).size))
        if color is not None:
            cls._write_cell_color(export_mesh, color)

        if cell_data.HasArray("Normals"):
            cell_data.RemoveArray("Normals")

        export_mesh.dataset.GetCellData().SetActiveScalars("Label")
        export_mesh.dataset.Modified()
        vedo.write(export_mesh, str(path))

    @classmethod
    def save_labels_json(cls, file_path: str | Path, labels: np.ndarray, mesh=None) -> None:
        path = Path(file_path)
        label_values = np.asarray(labels, dtype=np.int32).reshape(-1)
        payload = {
            "cell_count": int(label_values.size),
            "labels": label_values.tolist(),
        }
        if mesh is not None:
            color = cls._read_cell_color(mesh, int(label_values.size))
            if color is None:
                color = cls._compute_cell_color_from_points(mesh, int(label_values.size))
            if color is not None and color.size == label_values.size:
                payload["color_encoding"] = "packed_rgb_uint32"
                payload["colors"] = np.asarray(color, dtype=np.uint32).reshape(-1).tolist()
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load_labels_json(cls, file_path: str | Path, expected_cell_count: int | None = None) -> np.ndarray:
        path = Path(file_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")

        labels = payload.get("labels")
        if not isinstance(labels, list):
            raise ValueError("missing_labels")

        values = np.asarray(labels, dtype=np.int32).reshape(-1)
        declared_count = payload.get("cell_count")
        if declared_count is not None and int(declared_count) != int(values.size):
            raise ValueError("invalid_count")

        if expected_cell_count is not None and int(values.size) != int(expected_cell_count):
            raise ValueError("cell_count_mismatch")
        return values

    @classmethod
    def save_landmarks_json(cls, file_path: str | Path, landmarks: list[dict]) -> None:
        path = Path(file_path)
        payload = {
            "landmark_count": int(len(landmarks)),
            "landmarks": [
                {
                    "name": str(item.get("name") or ""),
                    "coordinates": (
                        None
                        if item.get("position") is None
                        else [float(v) for v in item.get("position", ())]
                    ),
                }
                for item in landmarks
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load_landmarks_json(cls, file_path: str | Path) -> list[dict]:
        path = Path(file_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")

        raw_landmarks = payload.get("landmarks")
        if not isinstance(raw_landmarks, list):
            raise ValueError("missing_landmarks")

        landmarks: list[dict] = []
        for index, item in enumerate(raw_landmarks):
            if not isinstance(item, dict):
                raise ValueError("invalid_landmark")
            name = str(item.get("name") or f"Landmark {index + 1}").strip()
            coords = item.get("coordinates", item.get("position"))
            position = None
            if coords is not None:
                if not isinstance(coords, (list, tuple)) or len(coords) != 3:
                    raise ValueError("invalid_landmark")
                position = tuple(float(value) for value in coords)
            landmarks.append({"name": name or f"Landmark {index + 1}", "position": position})

        declared_count = payload.get("landmark_count")
        if declared_count is not None and int(declared_count) != int(len(landmarks)):
            raise ValueError("invalid_count")
        return landmarks

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
