"""Entry point.

  RapidPrintAgent.exe              normal (tray + own window). First run installs itself.
  RapidPrintAgent.exe --minimized  start hidden in the tray (what the autostart uses)
  RapidPrintAgent.exe --console    same, with log on stderr (debugging)
  RapidPrintAgent.exe --no-tray    worker + local UI only (CI / headless test)
  RapidPrintAgent.exe --uninstall  remove autostart and pairing
  env RPA_NO_INSTALL=1             run in place, never copy to %LOCALAPPDATA%
"""
from __future__ import annotations

import os
import sys
import threading
import time

from . import APP_NAME, __version__


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    console = "--console" in argv or not getattr(sys, "frozen", False)

    from .log import setup
    log = setup(console=console)
    log.info("%s v%s starting (%s)", APP_NAME, __version__, " ".join(argv) or "no args")

    from . import install
    if "--uninstall" in argv:
        install.uninstall()
        return 0

    if install.ensure_installed():
        return 0  # the installed copy took over

    single = install.SingleInstance()
    if not single.acquire():
        log.info("already running; asking it to show its window")
        _show_running_instance()
        return 0

    from .config import Config
    from .worker import Worker
    from .local_ui import LocalUI
    from .updater import Updater

    config = Config()
    worker = Worker(config)
    stopping = threading.Event()
    tray = None
    window = None

    def quit_app():
        if stopping.is_set():
            return
        stopping.set()
        log.info("quit requested")
        worker.stop()
        try:
            ui.stop()
        except Exception:  # noqa: BLE001
            pass
        if tray:
            tray.stop()
        if window:
            window.destroy()
            # WinForms may keep the loop alive after destroy(); do not linger.
            threading.Timer(2.0, lambda: os._exit(0)).start()

    ui = LocalUI(config, worker, on_quit=quit_app)
    ui.start()
    worker.start()
    Updater(worker.api, worker.status, on_restart=quit_app).start()

    first_run = "--first-run" in argv or not config.paired
    show_now = first_run or ("--minimized" not in argv and "--open" in argv)

    if "--no-tray" in argv:
        try:
            while not stopping.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            quit_app()
    else:
        from .tray import Tray
        from . import window as window_mod
        tray = Tray(config, worker, ui, on_quit=quit_app)
        if window_mod.available():
            window = window_mod.AppWindow(ui.url, start_hidden=not show_now)
            ui.window = window
            try:
                window.run(on_ready=tray.run_detached)  # blocks on the GUI loop
            except Exception:  # noqa: BLE001
                log.exception("own window failed; falling back to the browser")
                ui.window = None
                window = None
                if show_now:
                    ui.open()
                try:
                    tray.run()  # blocks
                finally:
                    quit_app()
            else:
                quit_app()
        else:
            if show_now:
                ui.open()
            try:
                tray.run()  # blocks
            finally:
                quit_app()
    single.release()
    log.info("bye")
    return 0


def _show_running_instance():
    """Second launch: tell the running agent to show its window."""
    import urllib.request
    from .config import LOCAL_UI_PORT
    for port in (LOCAL_UI_PORT, LOCAL_UI_PORT + 1, LOCAL_UI_PORT + 2):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/show", data=b"{}",
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001
            continue
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{LOCAL_UI_PORT}/")


if __name__ == "__main__":
    sys.exit(main())
