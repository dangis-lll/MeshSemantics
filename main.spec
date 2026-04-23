# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


hiddenimports = sorted(
    set(
        collect_submodules("vedo.plotter")
        + collect_submodules("vedo.visual")
        + [
            "vedo.plotter.runtime",
            "vedo.visual.runtime",
        ]
    )
)

project_root = Path.cwd()

datas = collect_data_files("vedo")
datas += [
    (str(path), "meshsemantics/ui")
    for path in sorted((project_root / "meshsemantics" / "ui").glob("*.ui"))
]
datas += [
    (str(path), "meshsemantics/assets")
    for path in sorted((project_root / "meshsemantics" / "assets").glob("*"))
    if path.is_file()
]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MeshSemantics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[r"C:\project\MeshSemantics\meshsemantics\assets\app.ico"],
)
