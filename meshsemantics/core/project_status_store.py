from __future__ import annotations

import csv
import logging
from pathlib import Path


STATUS_TABLE_NAME = ".meshsemantics_status.csv"


def status_table_path(root: str | Path) -> Path:
    return Path(root).expanduser() / STATUS_TABLE_NAME


def normalize_relative_status_key(value: str | Path | None) -> str | None:
    if value in (None, ""):
        return None
    key = str(value).strip().replace("\\", "/")
    while "//" in key:
        key = key.replace("//", "/")
    return key or None



def load_project_statuses(root: str | Path) -> dict[str, str]:
    for table_path in [status_table_path(root)]:
        if not table_path.exists():
            continue

        statuses: dict[str, str] = {}
        try:
            with table_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    relative_path = str(row.get("relative_path", "")).strip()
                    status = str(row.get("status", "")).strip()
                    if not relative_path or not status:
                        continue
                    normalized_key = normalize_relative_status_key(relative_path)
                    if normalized_key is None:
                        continue
                    statuses[normalized_key] = status
        except Exception as exc:
            logging.warning("Failed to load project status table from %s: %s", table_path, exc)
            continue
        return statuses
    return {}


def save_project_statuses(root: str | Path, status_by_relative_path: dict[str, str]) -> None:
    table_path = status_table_path(root)
    table_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = table_path.with_name(f".{table_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["relative_path", "status"])
        for relative_path, status in sorted(status_by_relative_path.items()):
            if not relative_path or not status:
                continue
            normalized_key = normalize_relative_status_key(relative_path)
            if normalized_key is None:
                continue
            writer.writerow([normalized_key, status])
    temp_path.replace(table_path)
