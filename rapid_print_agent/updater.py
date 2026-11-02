"""Self-update. Every 6 h asks the server for the latest version.

If the URL points at an .exe and a SHA-256 is given, download, verify, and swap
the installed exe through a tiny .cmd that waits for this process to exit. If
the URL is a release page (no .exe / no hash), only flag "update available" in
the UI with the link: never run something we could not verify.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

from . import __version__
from .api import ApiError
from .install import install_path, is_frozen, is_installed_copy

log = logging.getLogger("agent.update")

CHECK_EVERY_SEC = 6 * 3600


def _ver(v: str) -> tuple:
    out = []
    for part in (v or "0").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out + [0] * (3 - len(out)))


class Updater:
    def __init__(self, api, status, on_restart):
        self.api = api
        self.status = status
        self.on_restart = on_restart

    def start(self):
        threading.Thread(target=self._loop, name="updater", daemon=True).start()

    def _loop(self):
        time.sleep(60)  # not on startup: let pairing/heartbeat settle first
        while True:
            try:
                self.check_once()
            except Exception as e:  # noqa: BLE001
                log.warning("update check failed: %s", e)
            time.sleep(CHECK_EVERY_SEC)

    def check_once(self):
        try:
            info = self.api.version()
        except ApiError as e:
            log.info("version check skipped: %s", e)
            return
        latest = info.get("version") or ""
        url = info.get("url") or ""
        sha = (info.get("sha256") or "").lower()
        if _ver(latest) <= _ver(__version__):
            with self.status.lock:
                self.status.update_available = None
            return
        auto = bool(url.lower().endswith(".exe") and sha and is_frozen() and is_installed_copy())
        with self.status.lock:
            self.status.update_available = {"version": latest, "url": url, "auto": auto}
        log.info("update available: %s (auto=%s)", latest, auto)
        if auto:
            self._apply(url, sha)

    def _apply(self, url: str, sha: str):
        tmp = Path(tempfile.gettempdir()) / f"RapidPrintAgent-{int(time.time())}.exe"
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            h = hashlib.sha256()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest().lower() != sha:
            tmp.unlink(missing_ok=True)
            log.error("update download hash mismatch; ignoring")
            return
        target = install_path()
        script = Path(tempfile.gettempdir()) / "RapidPrintAgent-update.cmd"
        script.write_text(
            "@echo off\r\n"
            ":wait\r\n"
            f'tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul && (timeout /t 1 /nobreak >nul & goto wait)\r\n'
            f'copy /Y "{tmp}" "{target}" >nul\r\n'
            f'start "" "{target}" --minimized\r\n'
            f'del "{tmp}"\r\n'
            'del "%~f0"\r\n',
            encoding="ascii",
        )
        log.info("applying update, restarting")
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                         close_fds=True)
        self.on_restart()
