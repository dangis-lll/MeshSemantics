from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk


@dataclass
class UndoRecord:
    op: str
    cell_ids: np.ndarray
    before: np.ndarray
    after: np.ndarray


class LabelEngine:
    def __init__(self, undo_limit: int = 50) -> None:
        self.undo_limit = max(1, int(undo_limit))
        self.label_array = np.zeros(0, dtype=np.int32)
        self.undo_stack: list[UndoRecord] = []
        self.redo_stack: list[UndoRecord] = []

    def reset(self, labels: np.ndarray | Iterable[int]) -> None:
        self.label_array = np.asarray(labels, dtype=np.int32).reshape(-1)
        self.undo_stack.clear()
        self.redo_stack.clear()

    @property
    def size(self) -> int:
        return int(self.label_array.size)

    def assign(self, cell_ids: np.ndarray | Iterable[int], label: int) -> bool:
        ids = np.unique(np.asarray(cell_ids, dtype=np.int32).reshape(-1))
        if ids.size == 0:
            return False
        before = self.label_array[ids].copy()
        after = np.full(ids.shape, int(label), dtype=np.int32)
        if np.array_equal(before, after):
            return False
        self.label_array[ids] = after
        self._push(UndoRecord("assign", ids, before, after))
        return True

    def remap_label(self, source: int, target: int) -> bool:
        if source == target:
            return False
        ids = np.flatnonzero(self.label_array == source).astype(np.int32)
        if ids.size == 0:
            return False
        before = self.label_array[ids].copy()
        after = np.full(ids.shape, int(target), dtype=np.int32)
        self.label_array[ids] = after
        self._push(UndoRecord("remap", ids, before, after))
        return True

    def swap_labels(self, a: int, b: int) -> bool:
        if a == b:
            return False
        mask = np.logical_or(self.label_array == a, self.label_array == b)
        ids = np.flatnonzero(mask).astype(np.int32)
        if ids.size == 0:
            return False
        before = self.label_array[ids].copy()
        swapped = before.copy()
        swapped[before == a] = b
        swapped[before == b] = a
        self.label_array[ids] = swapped
        self._push(UndoRecord("swap", ids, before, swapped))
        return True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        record = self.undo_stack.pop()
        self.label_array[record.cell_ids] = record.before
        self.redo_stack.append(record)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        record = self.redo_stack.pop()
        self.label_array[record.cell_ids] = record.after
        self.undo_stack.append(record)
        return True

    def get_cells_by_label(self, label: int) -> np.ndarray:
        return np.flatnonzero(self.label_array == int(label)).astype(np.int32)

    def get_vtk_array(self):
        arr = numpy_to_vtk(self.label_array.astype(np.int32), deep=True)
        arr.SetName("Label")
        return arr

    def labeled_ratio(self) -> float:
        if self.size == 0:
            return 0.0
        return float(np.count_nonzero(self.label_array)) / float(self.size)

    def unique_labels(self) -> list[int]:
        return [int(v) for v in np.unique(self.label_array)]

    def _push(self, record: UndoRecord) -> None:
        self.undo_stack.append(record)
        if len(self.undo_stack) > self.undo_limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
