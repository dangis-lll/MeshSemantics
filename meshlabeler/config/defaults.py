from __future__ import annotations

import colorsys
import os
from pathlib import Path


def resolve_app_dir() -> Path:
    env_path = os.environ.get("MESHLABELER_APP_DIR")
    if env_path:
        return Path(env_path)
    return Path.home() / ".meshlabeler"


APP_DIR = resolve_app_dir()
COLORMAP_PATH = APP_DIR / "colormap.json"
SETTINGS_PATH = APP_DIR / "settings.json"


def _rgb_triplet(hue: float, saturation: float, value: float) -> list[int]:
    rgb = colorsys.hsv_to_rgb(hue, saturation, value)
    return [int(round(channel * 255)) for channel in rgb]


def build_default_colormap() -> dict[str, list[int]]:
    colormap: dict[str, list[int]] = {
        "0": [232, 236, 242],
        "_default": [90, 117, 168],
    }
    group_hues = [
        205.0 / 360.0,
        152.0 / 360.0,
        35.0 / 360.0,
        325.0 / 360.0,
    ]
    value_steps = [0.97, 0.90, 0.83, 0.76, 0.69, 0.62, 0.55, 0.48]
    for group_index, hue in enumerate(group_hues):
        for offset, value in enumerate(value_steps):
            label = group_index * 8 + offset + 1
            colormap[str(label)] = _rgb_triplet(hue, 0.58, value)
    return colormap


DEFAULT_COLORMAP = build_default_colormap()

DEFAULT_SETTINGS = {
    "undo_limit": 50,
    "cache_limit": 20,
    "exclude_backfaces": True,
    "save_unlabeled_stl": False,
    "window_size": [1560, 980],
    "min_label": 1,
    "max_label": 255,
}
