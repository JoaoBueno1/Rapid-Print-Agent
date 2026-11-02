# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec. Build with: powershell -File build\build.ps1
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "assets", "icon.png"), "assets"),
        (os.path.join(ROOT, "assets", "tray.png"), "assets"),
        (os.path.join(ROOT, "assets", "icon.ico"), "assets"),
    ],
    hiddenimports=[
        "pystray._win32",
        "win32timezone",
        "PIL.ImageWin",
        "pypdfium2_raw",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "xmlrpc", "email.mime"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RapidPrintAgent",
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    debug=False,
    strip=False,
    upx=False,
    console=False,          # tray app: no console window
    disable_windowed_traceback=False,
    version=os.path.join(ROOT, "build", "version_info.txt"),
    onefile=True,
)
