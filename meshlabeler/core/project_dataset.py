from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meshlabeler.core.file_io import FileIO

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
    status_by_file: dict[str, str] | None = None,
) -> ProjectDataset:
    root = Path(folder).expanduser()
    if not root.exists():
        return ProjectDataset(str(root), tuple(), None, None, None)

    saved_status = {normalize_path(key): value for key, value in (status_by_file or {}).items()}
    groups: dict[str, dict[str, Path]] = {}

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in FileIO.SUPPORTED_SUFFIXES:
            continue
        try:
            relative_key = str(path.relative_to(root).with_suffix(""))
        except Exception:
            relative_key = path.stem
        group = groups.setdefault(relative_key, {})
        group[path.suffix.lower()] = path

    entries: list[ProjectEntry] = []
    normalized_current = normalize_path(current_file)
    normalized_last = normalize_path(last_file)

    for relative_key, variants in groups.items():
        stl_path = variants.get(".stl")
        vtp_path = variants.get(".vtp")
        source = stl_path or vtp_path
        work = vtp_path or stl_path
        if source is None or work is None:
            continue

        modified_at = datetime.fromtimestamp(0)
        for candidate in variants.values():
            try:
                modified_at = max(modified_at, datetime.fromtimestamp(candidate.stat().st_mtime))
            except OSError:
                continue

        work_path = normalize_path(work)
        status = saved_status.get(work_path) or (STATUS_IN_PROGRESS if vtp_path else STATUS_UNLABELED)
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

    entries.sort(
        key=lambda entry: (
            STATUS_ORDER.get(entry.status, 99),
            entry.display_path.lower(),
        )
    )

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
    return _rebuild_dataset(dataset.root_path, updated_entries, current_path)


def mark_current_entry(dataset: ProjectDataset, path: str | Path | None) -> ProjectDataset:
    current_path = normalize_path(path)
    return _rebuild_dataset(dataset.root_path, list(dataset.entries), current_path)


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


def _rebuild_dataset(root_path: str, entries: list[ProjectEntry], current_path: str | None) -> ProjectDataset:
    sorted_entries = sorted(
        entries,
        key=lambda entry: (
            STATUS_ORDER.get(entry.status, 99),
            entry.display_path.lower(),
        )
    )
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


def _matches_entry_path(entry: ProjectEntry, path: str | None) -> bool:
    if path is None:
        return False
    return normalize_path(entry.work_path) == path or normalize_path(entry.source_path) == path
