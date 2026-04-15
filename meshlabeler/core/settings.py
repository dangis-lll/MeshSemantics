from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from meshlabeler.config.defaults import (
    APP_DIR,
    COLORMAP_PATH,
    DEFAULT_COLORMAP,
    DEFAULT_SETTINGS,
    SETTINGS_PATH,
)


def _workspace_fallback_dir() -> Path:
    return Path.cwd() / ".meshlabeler"


def ensure_app_files() -> None:
    try:
        app_dir = APP_DIR
        app_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        app_dir = _workspace_fallback_dir()
        app_dir.mkdir(parents=True, exist_ok=True)

    colormap_path = app_dir / COLORMAP_PATH.name
    settings_path = app_dir / SETTINGS_PATH.name

    if not colormap_path.exists():
        colormap_path.write_text(
            json.dumps(DEFAULT_COLORMAP, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if not settings_path.exists():
        settings_path.write_text(
            json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def resolve_storage_path(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except Exception:
        fallback_dir = _workspace_fallback_dir()
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / path.name


def _load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    ensure_app_files()
    path = resolve_storage_path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    data = deepcopy(fallback)
    if isinstance(payload, dict):
        data.update(payload)
    return data


def load_settings() -> dict[str, Any]:
    return _load_json(SETTINGS_PATH, DEFAULT_SETTINGS)


def save_settings(settings: dict[str, Any]) -> None:
    ensure_app_files()
    path = resolve_storage_path(SETTINGS_PATH)
    path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_colormap(path: str | Path | None = None) -> dict[str, tuple[int, int, int]]:
    src = Path(path) if path else COLORMAP_PATH
    raw = _load_json(src, DEFAULT_COLORMAP)
    colormap: dict[str, tuple[int, int, int]] = {}
    for key, value in raw.items():
        if isinstance(value, (list, tuple)) and len(value) == 3:
            colormap[str(key)] = tuple(int(max(0, min(255, v))) for v in value)
    if "_default" not in colormap:
        colormap["_default"] = tuple(DEFAULT_COLORMAP["_default"])
    if "0" not in colormap:
        colormap["0"] = tuple(DEFAULT_COLORMAP["0"])
    return colormap


def save_colormap(colormap: dict[str, tuple[int, int, int]], path: str | Path | None = None) -> None:
    dst = Path(path) if path else resolve_storage_path(COLORMAP_PATH)
    ensure_app_files()
    payload = {str(k): [int(c) for c in v] for k, v in colormap.items()}
    dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
