# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec. Build with: powershell -File build\build.ps1
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Own window: pywebview (WinForms host via pythonnet + WebView2 loader DLLs).
_wv_datas, _wv_bins, _wv_hidden = [], [], []
for _pkg in ("webview", "pythonnet", "clr_loader"):
    d, b, h = collect_all(_pkg)
    _wv_datas += d; _wv_bins += b; _wv_hidden += h

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=_wv_bins,
    datas=_wv_datas + [
        (os.path.join(ROOT, "assets", "icon.png"), "assets"),
        (os.path.join(ROOT, "assets", "tray.png"), "assets"),
        (os.path.join(ROOT, "assets", "icon.ico"), "assets"),
    ],
    hiddenimports=[
        "pystray._win32",
        "win32timezone",
        "PIL.ImageWin",
        "pypdfium2_raw",
        "clr",
        "webview.platforms.winforms",
    ] + _wv_hidden,
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
