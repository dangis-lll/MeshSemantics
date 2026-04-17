from __future__ import annotations

import colorsys
import os
from pathlib import Path


def resolve_app_dir() -> Path:
    env_path = os.environ.get("MESHSEMANTICS_APP_DIR")
    if env_path:
        return Path(env_path)
    return Path.home() / ".meshsemantics"


APP_DIR = resolve_app_dir()
COLORMAP_PATH = APP_DIR / "colormap.json"
SETTINGS_PATH = APP_DIR / "settings.json"


def _rgb_triplet(hue: float, saturation: float, value: float) -> list[int]:
    rgb = colorsys.hsv_to_rgb(hue, saturation, value)
    return [int(round(channel * 255)) for channel in rgb]


def preset_label_rgb(label: int) -> list[int] | None:
    if not 1 <= int(label) <= 32:
        return None

    base_hues = [
        0.0 / 360.0,    # red
        30.0 / 360.0,   # orange
        55.0 / 360.0,   # yellow
        120.0 / 360.0,  # green
        180.0 / 360.0,  # cyan
        240.0 / 360.0,  # blue
        270.0 / 360.0,  # violet
        315.0 / 360.0,  # magenta
    ]
    saturation_steps = [0.90, 0.72, 0.54, 0.36]
    value = 0.94

    label_index = int(label) - 1
    hue = base_hues[label_index % 8]
    saturation = saturation_steps[label_index // 8]
    return _rgb_triplet(hue, saturation, value)


def build_default_colormap() -> dict[str, list[int]]:
    colormap: dict[str, list[int]] = {
        "0": [232, 236, 242],
        "_default": [90, 117, 168],
    }
    for label in range(1, 33):
        preset = preset_label_rgb(label)
        if preset is not None:
            colormap[str(label)] = preset
    return colormap


DEFAULT_COLORMAP = build_default_colormap()

DEFAULT_SETTINGS = {
    "undo_limit": 50,
    "cache_limit": 20,
    "exclude_backfaces": True,
    "save_unlabeled_stl": False,
    "overwrite_existing_labels": False,
    "window_size": [1560, 980],
    "last_open_dir": "",
    "last_file_by_folder": {},
    "min_label": 1,
    "max_label": 255,
}
