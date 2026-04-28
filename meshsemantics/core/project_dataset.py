from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from meshsemantics.core.file_io import FileIO
from meshsemantics.core.project_status_store import normalize_relative_status_key

STATUS_UNLABELED = "unlabeled"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed_to_load"

STATUS_ORDER = {
    STATUS_UNLABELED: 0,
    STATUS_IN_PROGRESS: 1,
    STATUS_COMPLETED: 2,
    STATUS_FAILED: 3,
}

@dataclass(frozen=True)
class ProjectEntry:
    display_path: str
    source_path: str
    work_path: str
    status: str
    modified_at: datetime
    is_current: bool = False


@dataclass(frozen=True)
class ProjectDataset:
    root_path: str
    entries: tuple[ProjectEntry, ...]
    current_path: str | None
    next_open_path: str | None
    suggested_path: str | None
    path_index_by_key: dict[str, int] = field(default_factory=dict, repr=False, compare=False)
    current_index: int | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        normalized_current = normalize_path(self.current_path)
        path_index = self.path_index_by_key
        if not path_index:
            path_index = _build_path_index(self.entries)
            object.__setattr__(self, "path_index_by_key", path_index)
        if self.current_index is None and normalized_current is not None:
            object.__setattr__(self, "current_index", path_index.get(_path_key(normalized_current) or ""))

    def contains_path(self, path: str | Path | None) -> bool:
        key = _path_key(path)
        if key is None:
            return False
        return key in self.path_index_by_key


def normalize_path(value: str | Path | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        return os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(value))))
    except Exception:
        return str(Path(value))


def _path_key(value: str | Path | None) -> str | None:
    normalized = normalize_path(value)
    if normalized is None:
        return None
    return os.path.normcase(normalized)


def _relative_status_key(path: str | Path, root: str | Path) -> str | None:
    try:
        return normalize_relative_status_key(Path(path).relative_to(Path(root)).with_suffix(""))
    except Exception:
        return None


def scan_project_dataset(
    folder: str | Path,
    *,
    last_file: str | Path | None = None,
    current_file: str | Path | None = None,
    status_by_relative_path: dict[str, str] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> ProjectDataset:
    root = Path(folder).expanduser()
    if not root.exists():
        return ProjectDataset(str(root), tuple(), None, None, None)
    normalized_root = normalize_path(root) or str(root)

    saved_status = {
        normalized_key: value
        for key, value in (status_by_relative_path or {}).items()
        if (normalized_key := normalize_relative_status_key(key)) is not None
    }
    groups = _scan_supported_mesh_files(root, progress_callback=progress_callback)

    entries: list[ProjectEntry] = []
    normalized_current = normalize_path(current_file)
    normalized_last = normalize_path(last_file)

    for relative_key, variants in groups.items():
        stl_item = variants.get(".stl")
        vtp_item = variants.get(".vtp")
        source_item = stl_item or vtp_item
        work_item = vtp_item or stl_item
        source = source_item[0] if source_item is not None else None
        work = work_item[0] if work_item is not None else None
        if source is None or work is None:
            continue

        modified_at = datetime.fromtimestamp(0)
        for _, candidate_mtime in variants.values():
            modified_at = max(modified_at, datetime.fromtimestamp(candidate_mtime))

        source_path = normalize_path(source) or str(source)
        work_path = normalize_path(work) or str(work)
        status = saved_status.get(_relative_status_key(work, root) or "") or (
            STATUS_IN_PROGRESS if vtp_item else STATUS_UNLABELED
        )
        entries.append(
            ProjectEntry(
                display_path=normalize_relative_status_key(relative_key) or relative_key,
                source_path=source_path,
                work_path=work_path,
                status=status if status in STATUS_ORDER else STATUS_UNLABELED,
                modified_at=modified_at,
                is_current=False,
            )
        )

    entries.sort(key=lambda entry: entry.display_path.lower())

    current_path = None
    next_open_path = None
    if entries:
        if normalized_current and any(_matches_entry_path(entry, normalized_current) for entry in entries):
            current_path = normalized_current
        elif normalized_last and any(_matches_entry_path(entry, normalized_last) for entry in entries):
            current_path = normalized_last
        else:
            current_path = _pick_default_entry(entries)

        next_open_path = _next_open_entry(entries, current_path)

    return _build_dataset(
        normalized_root,
        entries,
        current_path,
        next_open_path=next_open_path,
        suggested_path=current_path,
        preserve_order=True,
    )


def update_entry_status(dataset: ProjectDataset, path: str | Path, status: str) -> ProjectDataset:
    normalized = normalize_path(path)
    if normalized is None:
        return dataset
    updated_entries = []
    for entry in dataset.entries:
        if _matches_entry_path(entry, normalized):
            updated_entries.append(replace(entry, status=status))
        else:
            updated_entries.append(entry)
    current_path = normalize_path(dataset.current_path)
    return _rebuild_dataset(dataset.root_path, updated_entries, current_path, preserve_order=True)


def update_entry_status_and_current(dataset: ProjectDataset, path: str | Path, status: str) -> ProjectDataset:
    normalized = normalize_path(path)
    if normalized is None:
        return dataset
    updated_entries = []
    for entry in dataset.entries:
        if _matches_entry_path(entry, normalized):
            updated_entries.append(replace(entry, status=status))
        else:
            updated_entries.append(entry)
    return _rebuild_dataset(dataset.root_path, updated_entries, normalized, preserve_order=True)


def mark_current_entry(dataset: ProjectDataset, path: str | Path | None) -> ProjectDataset:
    current_path = normalize_path(path)
    return _rebuild_dataset(dataset.root_path, list(dataset.entries), current_path, preserve_order=True)


def find_entry(dataset: ProjectDataset | None, path: str | Path | None) -> ProjectEntry | None:
    if dataset is None:
        return None
    key = _path_key(path)
    if key is None:
        return None
    index = dataset.path_index_by_key.get(key)
    if index is None:
        return None
    return dataset.entries[index]


def build_status_index(dataset: ProjectDataset | None) -> dict[str, str]:
    if dataset is None:
        return {}
    return {entry.work_path: entry.status for entry in dataset.entries}


def build_relative_status_index(dataset: ProjectDataset | None) -> dict[str, str]:
    if dataset is None:
        return {}
    root = Path(dataset.root_path)
    index: dict[str, str] = {}
    for entry in dataset.entries:
        try:
            relative_path = _relative_status_key(entry.work_path, root)
        except Exception:
            continue
        if relative_path is None:
            continue
        index[relative_path] = entry.status
    return index


def compute_next_open_path(
    dataset: ProjectDataset | None,
    status_by_work_path: dict[str, str] | None = None,
    current_path: str | Path | None = None,
) -> str | None:
    if dataset is None:
        return None
    target = normalize_path(current_path if current_path is not None else dataset.current_path)
    total_entries = len(dataset.entries)
    if total_entries <= 1:
        return None

    current_index = dataset.current_index
    if target is not None and _path_key(dataset.current_path) != _path_key(target):
        current_index = dataset.path_index_by_key.get(_path_key(target) or "")
    start_index = current_index if current_index is not None else -1
    for offset in range(1, total_entries):
        candidate_index = (start_index + offset) % total_entries
        entry = dataset.entries[candidate_index]
        if _status_for_entry(entry, status_by_work_path) == STATUS_COMPLETED:
            continue
        return entry.work_path
    return None


def build_work_path_status_index(
    dataset: ProjectDataset | None,
    status_by_relative_path: dict[str, str] | None = None,
) -> dict[str, str]:
    if dataset is None:
        return {}
    relative_status = {
        normalized_key: value
        for key, value in (status_by_relative_path or {}).items()
        if (normalized_key := normalize_relative_status_key(key)) is not None
    }
    root = Path(dataset.root_path)
    index: dict[str, str] = {}
    for entry in dataset.entries:
        relative_key = None
        relative_key = _relative_status_key(entry.work_path, root)
        status = relative_status.get(relative_key) if relative_key is not None else None
        index[entry.work_path] = status if status in STATUS_ORDER else entry.status
    return index


def _rebuild_dataset(
    root_path: str,
    entries: list[ProjectEntry],
    current_path: str | None,
    *,
    preserve_order: bool = False,
) -> ProjectDataset:
    sorted_entries = list(entries) if preserve_order else sorted(entries, key=lambda entry: entry.display_path.lower())
    current = None
    if current_path and any(_matches_entry_path(entry, current_path) for entry in sorted_entries):
        current = current_path
    else:
        current = _pick_default_entry(sorted_entries)
    next_open_path = _next_open_entry(sorted_entries, current)
    return _build_dataset(
        root_path,
        sorted_entries,
        current,
        next_open_path=next_open_path,
        suggested_path=current,
        preserve_order=True,
    )


def _pick_default_entry(entries: list[ProjectEntry]) -> str | None:
    if not entries:
        return None
    available = [entry for entry in entries if entry.status != STATUS_COMPLETED]
    if available:
        return available[0].work_path
    recent = max(entries, key=lambda entry: entry.modified_at)
    return recent.work_path


def _next_open_entry(entries: list[ProjectEntry] | tuple[ProjectEntry, ...], current_path: str | None) -> str | None:
    total_entries = len(entries)
    if total_entries <= 1:
        return None

    normalized_current = normalize_path(current_path)
    current_index = next(
        (index for index, entry in enumerate(entries) if _matches_entry_path(entry, normalized_current)),
        None,
    )
    start_index = current_index if current_index is not None else -1
    for offset in range(1, total_entries):
        candidate_index = (start_index + offset) % total_entries
        entry = entries[candidate_index]
        if entry.status == STATUS_COMPLETED:
            continue
        return entry.work_path
    return None


def _status_for_entry(entry: ProjectEntry, status_by_work_path: dict[str, str] | None = None) -> str:
    if status_by_work_path is None:
        return entry.status
    status = status_by_work_path.get(entry.work_path)
    return status if status in STATUS_ORDER else entry.status


def _matches_entry_path(entry: ProjectEntry, path: str | None) -> bool:
    if path is None:
        return False
    return _path_key(entry.work_path) == _path_key(path) or _path_key(entry.source_path) == _path_key(path)


def _build_path_index(entries: tuple[ProjectEntry, ...]) -> dict[str, int]:
    index: dict[str, int] = {}
    for row, entry in enumerate(entries):
        for candidate in (entry.work_path, entry.source_path):
            key = _path_key(candidate)
            if key is not None and key not in index:
                index[key] = row
    return index


def _build_dataset(
    root_path: str,
    entries: list[ProjectEntry] | tuple[ProjectEntry, ...],
    current_path: str | None,
    *,
    next_open_path: str | None,
    suggested_path: str | None,
    preserve_order: bool = False,
) -> ProjectDataset:
    ordered_entries = list(entries) if preserve_order else sorted(entries, key=lambda entry: entry.display_path.lower())
    normalized_current = normalize_path(current_path)
    current = None
    if normalized_current and any(_matches_entry_path(entry, normalized_current) for entry in ordered_entries):
        current = normalized_current
    else:
        current = _pick_default_entry(ordered_entries)

    marked_entries = tuple(replace(entry, is_current=_matches_entry_path(entry, current)) for entry in ordered_entries)
    path_index = _build_path_index(marked_entries)
    current_index = path_index.get(_path_key(current) or "") if current is not None else None
    return ProjectDataset(
        root_path=root_path,
        entries=marked_entries,
        current_path=current,
        next_open_path=next_open_path,
        suggested_path=suggested_path,
        path_index_by_key=path_index,
        current_index=current_index,
    )


def _scan_supported_mesh_files(
    root: Path,
    *,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, dict[str, tuple[Path, float]]]:
    groups: dict[str, dict[str, tuple[Path, float]]] = {}
    stack = [root]
    supported_suffixes = {suffix.lower() for suffix in FileIO.SUPPORTED_SUFFIXES}
    scanned_files = 0
    last_progress_time = 0.0

    while stack:
        current_dir = stack.pop()
        try:
            with os.scandir(current_dir) as iterator:
                for item in iterator:
                    try:
                        if item.is_dir(follow_symlinks=False):
                            stack.append(Path(item.path))
                            continue
                        if not item.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue

                    suffix = Path(item.name).suffix.lower()
                    if suffix not in supported_suffixes:
                        continue

                    scanned_files += 1
                    path = Path(item.path)
                    try:
                        relative_key = str(path.relative_to(root).with_suffix(""))
                    except Exception:
                        relative_key = path.stem

                    try:
                        modified_at = item.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        modified_at = 0.0

                    group = groups.setdefault(relative_key, {})
                    group[suffix] = (path, modified_at)

                    if progress_callback is not None:
                        now = time.monotonic()
                        if scanned_files == 1 or scanned_files % 250 == 0 or now - last_progress_time >= 0.2:
                            progress_callback(scanned_files, normalize_relative_status_key(relative_key) or relative_key)
                            last_progress_time = now
        except OSError:
            continue

    if progress_callback is not None:
        progress_callback(scanned_files, "")
    return groups
