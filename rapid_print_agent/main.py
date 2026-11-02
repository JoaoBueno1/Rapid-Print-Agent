"""Entry point.

  RapidPrintAgent.exe              normal (tray). First run installs itself.
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
        log.info("already running; opening the status page")
        import webbrowser
        from .config import LOCAL_UI_PORT
        webbrowser.open(f"http://127.0.0.1:{LOCAL_UI_PORT}/")
        return 0

    from .config import Config
    from .worker import Worker
    from .local_ui import LocalUI
    from .updater import Updater

    config = Config()
    worker = Worker(config)
    stopping = threading.Event()

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

    ui = LocalUI(config, worker, on_quit=quit_app)
    ui.start()
    worker.start()
    Updater(worker.api, worker.status, on_restart=quit_app).start()

    first_run = "--first-run" in argv or not config.paired
    if first_run and "--no-tray" not in argv:
        ui.open()

    tray = None
    if "--no-tray" in argv:
        try:
            while not stopping.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            quit_app()
    else:
        from .tray import Tray
        tray = Tray(config, worker, ui, on_quit=quit_app)
        try:
            tray.run()  # blocks
        finally:
            quit_app()
    single.release()
    log.info("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
