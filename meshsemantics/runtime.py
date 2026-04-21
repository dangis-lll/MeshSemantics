from __future__ import annotations

import sys
from pathlib import Path


def package_dir() -> Path:
    if getattr(sys, "frozen", False):
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            return Path(bundle_dir) / "meshsemantics"
        return Path(sys.executable).resolve().parent / "meshsemantics"
    return Path(__file__).resolve().parent


def ui_path(filename: str) -> Path:
    return package_dir() / "ui" / filename


def asset_path(filename: str) -> Path:
    return package_dir() / "assets" / filename
