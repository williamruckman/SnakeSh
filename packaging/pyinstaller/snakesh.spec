# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

import PySide6
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
ICON_PATH = SRC_ROOT / "snakesh" / "assets" / "snakesh-icon.png"
WINDOWS_ICON_PATH = SRC_ROOT / "snakesh" / "assets" / "snakesh-icon.ico"
MACOS_ICON_PATH = PROJECT_ROOT / "build" / "macos" / "SnakeSh.icns"
PYSIDE6_ROOT = Path(PySide6.__file__).resolve().parent


def _collect_selected_pyside_binaries():
    allowed_plugin_prefixes = {
        "PySide6/Qt/plugins/platforminputcontexts",
        "PySide6/Qt/plugins/platforms",
        "PySide6/Qt/plugins/wayland-decoration-client",
        "PySide6/Qt/plugins/wayland-graphics-integration-client",
        "PySide6/Qt/plugins/wayland-shell-integration",
        "PySide6/Qt/plugins/xcbglintegrations",
    }
    selected = []
    seen = set()

    for source_path, dest_dir in collect_dynamic_libs("PySide6"):
        if dest_dir not in allowed_plugin_prefixes:
            continue
        entry = (source_path, dest_dir)
        if entry in seen:
            continue
        seen.add(entry)
        selected.append(entry)

    qt_lib_dir = PYSIDE6_ROOT / "Qt" / "lib"
    for pattern in ("libQt6XcbQpa.so*", "libQt6Wayland*.so*"):
        for candidate in sorted(qt_lib_dir.glob(pattern)):
            entry = (str(candidate), "PySide6/Qt/lib")
            if entry in seen:
                continue
            seen.add(entry)
            selected.append(entry)

    return selected


def _filter_duplicate_private_qt_binaries(entries):
    filtered = []

    for entry in entries:
        dest_name = str(entry[0]).replace("\\", "/") if entry else ""
        basename = Path(dest_name).name
        if "/" not in dest_name and (
            basename.startswith("libQt6XcbQpa.so") or basename.startswith("libQt6Wayland")
        ):
            continue
        filtered.append(entry)

    return entries.__class__(filtered)

datas = collect_data_files("snakesh")
datas.extend(
    [
        (str(PROJECT_ROOT / "LICENSE"), "."),
        (str(PROJECT_ROOT / "NOTICE"), "."),
        (str(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    ]
)
hiddenimports = [
    *collect_submodules("keyring.backends"),
    *collect_submodules("pysnmp"),
    *collect_submodules("pyasn1"),
    "PySide6.QtCharts",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
]
pyside_binaries = _collect_selected_pyside_binaries()

a = Analysis(
    [str(SRC_ROOT / "snakesh" / "__main__.py")],
    pathex=[str(SRC_ROOT)],
    binaries=pyside_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "pyinstaller" / "runtime_hook_qt.py")],
    excludes=[],
    noarchive=False,
)
a.binaries = _filter_duplicate_private_qt_binaries(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SnakeSh",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(WINDOWS_ICON_PATH if sys.platform == "win32" and WINDOWS_ICON_PATH.is_file() else ICON_PATH),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SnakeSh",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SnakeSh.app",
        icon=str(MACOS_ICON_PATH if MACOS_ICON_PATH.is_file() else ICON_PATH),
        bundle_identifier="com.snakesh.app",
        info_plist={
            "UTExportedTypeDeclarations": [
                {
                    "UTTypeIdentifier": "com.snakesh.export",
                    "UTTypeDescription": "SnakeSh Export Bundle",
                    "UTTypeConformsTo": ["public.json"],
                    "UTTypeTagSpecification": {
                        "public.filename-extension": ["ssx"],
                        "public.mime-type": ["application/x-snakesh-export"],
                    },
                }
            ],
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "SnakeSh Export Bundle",
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "LSItemContentTypes": ["com.snakesh.export"],
                }
            ],
        },
    )
