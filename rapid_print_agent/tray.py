"""System tray icon (pystray). The icon tells the state at a glance:
blue = connected, grey = paused / not paired, red dot = cannot reach server."""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from . import APP_NAME, __version__

log = logging.getLogger("agent.tray")


def _icon_base() -> Image.Image:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "tray.png"
    if not base.exists():
        base = Path(__file__).resolve().parent.parent / "assets" / "tray.png"
    return Image.open(base).convert("RGBA")


def _variant(state: str) -> Image.Image:
    img = _icon_base().copy()
    if state == "paused":
        g = img.convert("LA").convert("RGBA")
        g.putalpha(img.getchannel("A"))
        img = g
    if state in ("offline", "unpaired"):
        d = ImageDraw.Draw(img)
        w, h = img.size
        r = w // 4
        colour = (220, 38, 38, 255) if state == "offline" else (245, 158, 11, 255)
        d.ellipse((w - 2 * r, h - 2 * r, w - 2, h - 2), fill=colour, outline=(255, 255, 255, 255), width=2)
    return img


class Tray:
    def __init__(self, config, worker, ui, on_quit):
        self.config = config
        self.worker = worker
        self.ui = ui
        self.on_quit = on_quit
        self.icon = None
        self._state = None

    def _menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem(f"{APP_NAME} v{__version__}", None, enabled=False),
            pystray.MenuItem("Open status page", lambda: self.ui.open(), default=True),
            pystray.MenuItem(lambda item: "Resume printing" if self.config.get("paused") else "Pause printing",
                             lambda: (self.config.set(paused=not self.config.get("paused")), self.worker.wake())),
            pystray.MenuItem("Print local test page", self._test),
            pystray.MenuItem("Open log folder", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.on_quit()),
        )

    def _test(self):
        printers = self.worker.status.snapshot().get("printers") or []
        name = next((p["name"] for p in printers if p.get("default")), printers[0]["name"] if printers else "")
        if not name:
            self._notify("No printer found on this computer.")
            return
        try:
            out = self.worker.local_test_print(name)
            self._notify(f"Test page sent to {name}" + (f" ({out})" if out else ""))
        except Exception as e:  # noqa: BLE001
            self._notify(f"Test page failed: {e}")

    def _open_log(self):
        import os
        from .config import log_dir
        os.startfile(str(log_dir()))  # noqa: S606

    def _notify(self, text: str):
        try:
            if self.icon:
                self.icon.notify(text, APP_NAME)
        except Exception:  # noqa: BLE001
            log.info("notify: %s", text)

    def _state_now(self) -> str:
        if not self.config.paired:
            return "unpaired"
        if self.config.get("paused") or self.worker.status.paused_by_server:
            return "paused"
        return "online" if self.worker.status.connected else "offline"

    def _refresh_loop(self):
        import time
        while True:
            try:
                st = self._state_now()
                if st != self._state and self.icon:
                    self._state = st
                    self.icon.icon = _variant(st)
                    self.icon.title = {
                        "online": f"{APP_NAME} — connected",
                        "offline": f"{APP_NAME} — cannot reach server",
                        "paused": f"{APP_NAME} — paused",
                        "unpaired": f"{APP_NAME} — not connected (open to pair)",
                    }[st]
            except Exception:  # noqa: BLE001
                log.exception("tray refresh")
            time.sleep(2)

    def run(self):
        """Blocks on the tray loop (must be the main thread on Windows)."""
        import pystray
        self._state = self._state_now()
        self.icon = pystray.Icon("RapidPrintAgent", _variant(self._state), APP_NAME, self._menu())
        threading.Thread(target=self._refresh_loop, name="tray-refresh", daemon=True).start()
        self.icon.run()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass
