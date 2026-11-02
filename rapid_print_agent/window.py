"""The agent's own window (pywebview on WebView2, the browser engine that
ships with Windows 10/11). It shows the same local page the tray has always
served, but as a real application window with our icon and title, not a
browser tab. Closing it hides it; the agent keeps running in the tray.

Falls back to the system browser when WebView2 is missing (old Windows 10
without the runtime) so the agent never refuses to start."""
from __future__ import annotations

import logging
import threading

from . import APP_NAME

log = logging.getLogger("agent.window")

WIDTH, HEIGHT = 1000, 780
MIN_SIZE = (760, 600)


def available() -> bool:
    try:
        import webview  # noqa: F401
        import clr  # noqa: F401  (pythonnet: the WinForms host)
        return True
    except Exception as e:  # noqa: BLE001
        log.info("own window unavailable (%s); using the browser", e)
        return False


class AppWindow:
    def __init__(self, url: str, start_hidden: bool):
        self.url = url
        self.start_hidden = start_hidden
        self._win = None
        self._ready = threading.Event()

    # -- main thread -----------------------------------------------------
    def run(self, on_ready):
        """Blocks on the GUI loop (must be the main thread). `on_ready` runs
        in a worker thread once the loop is up: that is where the tray starts."""
        import webview
        self._win = webview.create_window(
            APP_NAME, self.url, width=WIDTH, height=HEIGHT, min_size=MIN_SIZE,
            hidden=self.start_hidden, text_select=False, zoomable=False, confirm_close=False,
        )
        self._win.events.closing += self._on_closing

        def _ready():
            self._ready.set()
            try:
                on_ready()
            except Exception:  # noqa: BLE001
                log.exception("on_ready failed")

        webview.start(func=_ready, private_mode=False, debug=False)

    def _on_closing(self):
        # Hide instead of closing: the agent must survive the window.
        try:
            self._win.hide()
        except Exception:  # noqa: BLE001
            pass
        return False

    # -- any thread ------------------------------------------------------
    def show(self):
        if not self._win or not self._ready.wait(5):
            return False
        try:
            self._win.show()
            try:
                self._win.restore()
            except Exception:  # noqa: BLE001
                pass
            # Bring to front: WinForms will not steal focus on show().
            self._win.on_top = True
            self._win.on_top = False
            return True
        except Exception:  # noqa: BLE001
            log.exception("window show failed")
            return False

    def hide(self):
        try:
            if self._win:
                self._win.hide()
        except Exception:  # noqa: BLE001
            pass

    def destroy(self):
        try:
            if self._win:
                self._win.destroy()
        except Exception:  # noqa: BLE001
            pass
