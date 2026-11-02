"""Self-install, autostart, single instance, uninstall. Windows only.

First run of the downloaded exe: copy itself to %LOCALAPPDATA%\\RapidPrintAgent\\,
register HKCU\\...\\Run so it starts with Windows, launch the installed copy and
exit. No admin rights, no installer wizard. `--uninstall` undoes it.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import APP_ID
from .config import base_dir

log = logging.getLogger("agent.install")

EXE_NAME = f"{APP_ID}.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX_NAME = "Global\\RapidPrintAgent-single-instance"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_path() -> Path:
    return base_dir() / EXE_NAME


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def is_installed_copy() -> bool:
    try:
        return current_exe() == install_path().resolve()
    except OSError:
        return False


def set_autostart(enabled: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, f'"{install_path()}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, APP_ID)
            except FileNotFoundError:
                pass


def ensure_installed() -> bool:
    """Returns True when this process should exit because the installed copy
    was (re)launched."""
    if not is_frozen() or os.environ.get("RPA_NO_INSTALL"):
        return False
    if is_installed_copy():
        try:
            set_autostart(True)  # idempotent; also repairs a removed key
        except OSError as e:
            log.warning("could not set autostart: %s", e)
        return False
    target = install_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current_exe(), target)
        set_autostart(True)
    except OSError as e:
        log.error("install failed (%s); running from the download location", e)
        return False
    log.info("installed to %s, launching it", target)
    subprocess.Popen([str(target), "--first-run"], close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return True


def uninstall():
    try:
        set_autostart(False)
    except OSError:
        pass
    cfg = base_dir()
    for name in ("config.json", "printed.json"):
        try:
            (cfg / name).unlink()
        except OSError:
            pass
    print("Rapid Print Agent: autostart removed and pairing forgotten.")
    print(f"You can delete the folder {cfg} after closing the agent.")


class SingleInstance:
    """Named mutex. Second launch → tell the user, open the UI, exit."""

    def __init__(self):
        self.handle = None
        self.already = False

    def acquire(self) -> bool:
        try:
            import win32api
            import win32event
            import winerror
        except ImportError:
            return True
        self.handle = win32event.CreateMutex(None, False, MUTEX_NAME)
        self.already = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        return not self.already

    def release(self):
        if self.handle:
            try:
                import win32api
                win32api.CloseHandle(self.handle)
            except Exception:  # noqa: BLE001
                pass
