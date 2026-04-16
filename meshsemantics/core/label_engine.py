from __future__ import annotations

from typing import Iterable

import numpy as np


class LabelEngine:
    def __init__(self, undo_limit: int = 50) -> None:
        self.label_array = np.zeros(0, dtype=np.int32)

    def reset(self, labels: np.ndarray | Iterable[int]) -> None:
        self.label_array = np.asarray(labels, dtype=np.int32).reshape(-1)

    @property
    def size(self) -> int:
        return int(self.label_array.size)

    def assign(self, cell_ids: np.ndarray | Iterable[int], label: int) -> bool:
        ids = np.unique(np.asarray(cell_ids, dtype=np.int32).reshape(-1))
        ids = ids[(ids >= 0) & (ids < self.label_array.size)]
        if ids.size == 0:
            return False
        before = self.label_array[ids].copy()
        after = np.full(ids.shape, int(label), dtype=np.int32)
        if np.array_equal(before, after):
            return False
        self.label_array[ids] = after
        return True

    def remap_label(self, source: int, target: int) -> bool:
        if source == target:
            return False
        ids = np.flatnonzero(self.label_array == source).astype(np.int32)
        if ids.size == 0:
            return False
        before = self.label_array[ids].copy()
        after = np.full(ids.shape, int(target), dtype=np.int32)
        if np.array_equal(before, after):
            return False
        self.label_array[ids] = after
        return True

    def get_cells_by_label(self, label: int) -> np.ndarray:
        return np.flatnonzero(self.label_array == int(label)).astype(np.int32)

    def labeled_ratio(self) -> float:
        if self.size == 0:
            return 0.0
        return float(np.count_nonzero(self.label_array)) / float(self.size)

    def unique_labels(self) -> list[int]:
        return [int(v) for v in np.unique(self.label_array)]
