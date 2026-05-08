# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


hiddenimports = sorted(
    set(
        collect_submodules("vedo.plotter")
        + collect_submodules("vedo.visual")
        + collect_submodules("vtkmodules")
        + [
            "vedo.plotter.runtime",
            "vedo.visual.runtime",
            "vtkmodules.qt.QVTKRenderWindowInteractor",
            "vtkmodules.vtkRenderingOpenGL2",
        ]
    )
)

project_root = Path.cwd()
icon_icns = project_root / "build" / "app.icns"
icon_path = str(icon_icns) if icon_icns.exists() else None

datas = collect_data_files("vedo")
datas += collect_data_files("vtkmodules")
datas += [
    (str(path), "meshsemantics/ui")
    for path in sorted((project_root / "meshsemantics" / "ui").glob("*.ui"))
]
datas += [
    (str(path), "meshsemantics/assets")
    for path in sorted((project_root / "meshsemantics" / "assets").glob("*"))
    if path.is_file()
]
binaries = collect_dynamic_libs("vtkmodules")


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
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
    a.zipfiles,
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
    icon=icon_path,
)

app = BUNDLE(
    exe,
    name="MeshSemantics.app",
    icon=icon_path,
    bundle_identifier="com.meshsemantics.app",
    info_plist={
        "CFBundleDisplayName": "MeshSemantics",
        "CFBundleName": "MeshSemantics",
        "NSHighResolutionCapable": True,
    },
)
