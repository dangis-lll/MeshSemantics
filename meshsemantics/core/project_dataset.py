from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from meshsemantics.core.file_io import FileIO

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

PENDING_STATUSES = {STATUS_UNLABELED, STATUS_IN_PROGRESS}


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
    next_pending_path: str | None
    suggested_path: str | None

    def contains_path(self, path: str | Path | None) -> bool:
        normalized = normalize_path(path)
        if normalized is None:
            return False
        return any(_matches_entry_path(entry, normalized) for entry in self.entries)


def normalize_path(value: str | Path | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return str(Path(value))


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

    saved_status = {str(key).replace("/", "\\"): value for key, value in (status_by_relative_path or {}).items()}
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

        work_path = normalize_path(work)
        status = saved_status.get(str(work.relative_to(root).with_suffix("")).replace("/", "\\")) or (
            STATUS_IN_PROGRESS if vtp_item else STATUS_UNLABELED
        )
        entries.append(
            ProjectEntry(
                display_path=relative_key.replace("\\", "/"),
                source_path=str(source.resolve()),
                work_path=work_path or str(work),
                status=status if status in STATUS_ORDER else STATUS_UNLABELED,
                modified_at=modified_at,
                is_current=False,
            )
        )

    entries.sort(key=lambda entry: entry.display_path.lower())

    current_path = None
    next_pending_path = None
    if entries:
        if normalized_current and any(_matches_entry_path(entry, normalized_current) for entry in entries):
            current_path = normalized_current
        elif normalized_last and any(_matches_entry_path(entry, normalized_last) for entry in entries):
            current_path = normalized_last
        else:
            current_path = _pick_default_entry(entries)

        next_pending_path = _next_pending_entry(entries, current_path)

    marked_entries = tuple(
        replace(entry, is_current=_matches_entry_path(entry, current_path))
        for entry in entries
    )
    return ProjectDataset(
        root_path=str(root.resolve()),
        entries=marked_entries,
        current_path=current_path,
        next_pending_path=next_pending_path,
        suggested_path=current_path,
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
    normalized = normalize_path(path)
    if normalized is None:
        return None
    for entry in dataset.entries:
        if _matches_entry_path(entry, normalized):
            return entry
    return None


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
            relative_path = str(Path(entry.work_path).relative_to(root).with_suffix("")).replace("/", "\\")
        except Exception:
            continue
        index[relative_path] = entry.status
    return index


def compute_next_pending_path(
    dataset: ProjectDataset | None,
    status_by_work_path: dict[str, str] | None = None,
    current_path: str | Path | None = None,
) -> str | None:
    if dataset is None:
        return None
    target = normalize_path(current_path if current_path is not None else dataset.current_path)
    pending_paths: list[str] = []
    for entry in dataset.entries:
        status = _status_for_entry(entry, status_by_work_path)
        if status in PENDING_STATUSES:
            pending_paths.append(entry.work_path)
    if not pending_paths:
        return None
    if target is None:
        return pending_paths[0]
    for pending_path in pending_paths:
        if normalize_path(pending_path) != target:
            return pending_path
    return pending_paths[0]


def build_work_path_status_index(
    dataset: ProjectDataset | None,
    status_by_relative_path: dict[str, str] | None = None,
) -> dict[str, str]:
    if dataset is None:
        return {}
    relative_status = {str(key).replace("/", "\\"): value for key, value in (status_by_relative_path or {}).items()}
    root = Path(dataset.root_path)
    index: dict[str, str] = {}
    for entry in dataset.entries:
        relative_key = None
        try:
            relative_key = str(Path(entry.work_path).relative_to(root).with_suffix("")).replace("/", "\\")
        except Exception:
            relative_key = None
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
    next_pending_path = _next_pending_entry(sorted_entries, current)
    marked_entries = tuple(replace(entry, is_current=_matches_entry_path(entry, current)) for entry in sorted_entries)
    return ProjectDataset(root_path, marked_entries, current, next_pending_path, current)


def _pick_default_entry(entries: list[ProjectEntry]) -> str | None:
    if not entries:
        return None
    pending = [entry for entry in entries if entry.status in PENDING_STATUSES]
    if pending:
        return pending[0].work_path
    recent = max(entries, key=lambda entry: entry.modified_at)
    return recent.work_path


def _next_pending_entry(entries: list[ProjectEntry] | tuple[ProjectEntry, ...], current_path: str | None) -> str | None:
    normalized_current = normalize_path(current_path)
    pending = [entry for entry in entries if entry.status in PENDING_STATUSES]
    if not pending:
        return None
    if normalized_current is None:
        return pending[0].work_path
    for entry in pending:
        if not _matches_entry_path(entry, normalized_current):
            return entry.work_path
    return pending[0].work_path


def _status_for_entry(entry: ProjectEntry, status_by_work_path: dict[str, str] | None = None) -> str:
    if status_by_work_path is None:
        return entry.status
    status = status_by_work_path.get(entry.work_path)
    return status if status in STATUS_ORDER else entry.status


def _matches_entry_path(entry: ProjectEntry, path: str | None) -> bool:
    if path is None:
        return False
    return normalize_path(entry.work_path) == path or normalize_path(entry.source_path) == path


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
                            progress_callback(scanned_files, relative_key.replace("\\", "/"))
                            last_progress_time = now
        except OSError:
            continue

    if progress_callback is not None:
        progress_callback(scanned_files, "")
    return groups
