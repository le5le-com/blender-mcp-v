# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the blender-mcp server as a standalone executable.

Build with:  uv run pyinstaller blender-mcp.spec --noconfirm
"""
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

mcp_submodules = collect_submodules(
    "mcp", filter=lambda name: not name.startswith("mcp.cli")
)

hiddenimports = (
    collect_submodules("uvicorn")
    + mcp_submodules
    + collect_submodules("blender_mcp")
    + ["httpx", "anyio", "starlette", "sse_starlette"]
)

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/blender_mcp/bundled/addon.py", "blender_mcp/bundled")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="blender-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
