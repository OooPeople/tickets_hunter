# -*- mode: python ; coding: utf-8 -*-
"""Build the offline uTiki profile upgrader as a separate onedir product."""

import os


project_root = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(project_root, "tools", "migrations", "convert_utiki_config.py")],
    pathex=[os.path.join(project_root, "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "ddddocr",
        "zendriver",
        "nodriver",
        "tornado",
        "playsound",
        "pyperclip",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="utiki-profile-upgrader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="utiki-profile-upgrader",
)
