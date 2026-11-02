"""The two loops: heartbeat (printers + liveness) and jobs (long-poll → print → ack).

Design rules:
- never die: every iteration is wrapped, errors go to `Status` and the log,
  and the loop sleeps with backoff (2 s … 60 s) while the server is unreachable;
- never print twice: `PrintedStore` remembers printed job ids across restarts,
  and a repeated job is acked as printed without touching the printer;
- always answer: a job that fails to download or print is acked `failed` with
  the reason, so Settings > Printers shows it instead of a silent "queued".
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import __version__
from .api import Api, ApiError
from .config import Config, PrintedStore
from . import printing

log = logging.getLogger("agent.worker")

HEARTBEAT_SEC = 30
POLL_WAIT_SEC = 15
BACKOFF_MAX = 60


@dataclass
class Status:
    connected: bool = False
    last_ok: datetime | None = None
    last_error: str = ""
    last_error_at: datetime | None = None
    paused_by_server: bool = False
    printers: list = field(default_factory=list)
    jobs_printed: int = 0
    jobs_failed: int = 0
    recent: list = field(default_factory=list)  # [{time, job, printer, status, detail}]
    update_available: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def ok(self):
        with self.lock:
            self.connected = True
            self.last_ok = datetime.now()
            self.last_error = ""

    def fail(self, msg: str, disconnect=True):
        with self.lock:
            if disconnect:
                self.connected = False
            self.last_error = msg
            self.last_error_at = datetime.now()

    def record(self, job, status, detail=""):
        with self.lock:
            self.recent.insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "job": job.get("id"),
                "label": job.get("tracking_id") or ("test page" if job.get("document_kind") == "test" else "-"),
                "printer": job.get("printer"),
                "status": status,
                "detail": detail,
            })
            del self.recent[30:]
            if status == "printed":
                self.jobs_printed += 1
            elif status == "failed":
                self.jobs_failed += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "connected": self.connected,
                "last_ok": self.last_ok.strftime("%H:%M:%S") if self.last_ok else None,
                "last_error": self.last_error,
                "last_error_at": self.last_error_at.strftime("%H:%M:%S") if self.last_error_at else None,
                "paused_by_server": self.paused_by_server,
                "printers": list(self.printers),
                "jobs_printed": self.jobs_printed,
                "jobs_failed": self.jobs_failed,
                "recent": list(self.recent),
                "update_available": self.update_available,
                "version": __version__,
            }


class Worker:
    def __init__(self, config: Config, status: Status | None = None, api: Api | None = None,
                 printed: PrintedStore | None = None):
        self.config = config
        self.status = status or Status()
        self.api = api or Api(config)
        self.printed = printed or PrintedStore()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self):
        if self._threads:
            return
        for name, fn in (("heartbeat", self._heartbeat_loop), ("jobs", self._jobs_loop)):
            t = threading.Thread(target=self._guard, args=(name, fn), name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        """Pairing just happened or settings changed: do not wait for the next tick."""
        self._wake.set()

    def _sleep(self, seconds: float):
        self._wake.wait(seconds)
        self._wake.clear()

    def _guard(self, name, fn):
        """A loop that raises is restarted, not lost."""
        while not self._stop.is_set():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log.exception("%s loop crashed, restarting in 5 s: %s", name, e)
                self.status.fail(f"{name} loop error: {e}", disconnect=False)
                self._sleep(5)

    # ── heartbeat ─────────────────────────────────────────────────────────
    def _heartbeat_loop(self):
        backoff = 2
        while not self._stop.is_set():
            if not self.config.paired:
                self.status.fail("Not paired yet.")
                self._sleep(3)
                continue
            try:
                printers = printing.list_printers()
                out = self.api.heartbeat(printers)
                with self.status.lock:
                    self.status.printers = printers
                    self.status.paused_by_server = bool(out.get("paused"))
                self.status.ok()
                backoff = 2
                self._sleep(HEARTBEAT_SEC)
            except ApiError as e:
                self._on_api_error(e)
                self._sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)

    # ── jobs ──────────────────────────────────────────────────────────────
    def _jobs_loop(self):
        backoff = 2
        while not self._stop.is_set():
            if not self.config.paired:
                self._sleep(3)
                continue
            if self.config.get("paused"):
                self._sleep(2)
                continue
            try:
                jobs = self.api.poll_jobs(POLL_WAIT_SEC)
                self.status.ok()
                backoff = 2
            except ApiError as e:
                self._on_api_error(e)
                self._sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue
            for job in jobs:
                if self._stop.is_set():
                    break
                self.handle_job(job)

    def _on_api_error(self, e: ApiError):
        log.warning("server: %s", e)
        self.status.fail(str(e))
        if e.status == 401:
            # Token revoked or computer removed in Settings: forget it so the UI
            # shows the pairing screen instead of looping on 401 forever.
            self.config.set(token="")

    # ── one job ───────────────────────────────────────────────────────────
    def handle_job(self, job: dict):
        job_id = job.get("id")
        printer = job.get("printer") or ""
        label = f"job {job_id} ({job.get('tracking_id') or job.get('document_kind')}) → {printer}"
        if job_id in self.printed:
            log.info("%s already printed, acking again", label)
            self._ack(job_id, "printed")
            return
        try:
            data, fmt = self.api.job_file(job_id)
        except ApiError as e:
            # 422 = server already marked it failed (label not available).
            log.warning("%s: download failed: %s", label, e)
            self.status.record(job, "failed", str(e))
            if e.status != 422:
                self._ack(job_id, "failed", f"Download failed: {e}")
            return
        try:
            self.print_bytes(job, data, fmt)
        except printing.PrintError as e:
            log.error("%s: %s", label, e)
            self.status.record(job, "failed", str(e))
            self._ack(job_id, "failed", str(e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("%s: unexpected error", label)
            self.status.record(job, "failed", f"Unexpected: {e}")
            self._ack(job_id, "failed", f"Unexpected error: {e}")
            return
        self.printed.add(job_id)
        self.status.record(job, "printed", f"{fmt.upper()} x{job.get('copies', 1)}")
        log.info("%s: printed (%s, %d bytes)", label, fmt, len(data))
        self._ack(job_id, "printed")

    def print_bytes(self, job: dict, data: bytes, fmt: str):
        printer = job.get("printer") or ""
        if not printer:
            raise printing.PrintError("Job has no printer.")
        if not printing.printer_exists(printer):
            raise printing.PrintError(f"Printer '{printer}' is not installed on this computer any more.")
        copies = int(job.get("copies") or 1)
        doc = f"Rapid {job.get('tracking_id') or job.get('document_kind') or 'label'}"
        if fmt == "zpl":
            printing.print_raw(printer, data, doc_name=doc, copies=copies)
            return
        out = printing.file_output_for(printer, self.config.get("pdf_output_dir") or "", doc)
        printing.print_pdf(
            printer, data, copies=copies,
            paper_w_mm=job.get("paper_width_mm"), paper_h_mm=job.get("paper_height_mm"),
            doc_name=doc, output_file=out,
        )

    def _ack(self, job_id, status, error=""):
        for attempt in range(3):
            try:
                self.api.ack(job_id, status, error)
                return
            except ApiError as e:
                log.warning("ack %s failed (%d/3): %s", job_id, attempt + 1, e)
                time.sleep(2)
        self.status.fail(f"Could not confirm job {job_id} to the server.", disconnect=False)

    # ── local test (no server) ────────────────────────────────────────────
    def local_test_print(self, printer: str, paper_w_mm=100, paper_h_mm=150) -> str:
        img = printing.local_test_image(printer, paper_w_mm, paper_h_mm)
        out = printing.file_output_for(printer, self.config.get("pdf_output_dir") or "", "test-page")
        printing.print_image(printer, img, paper_w_mm=paper_w_mm, paper_h_mm=paper_h_mm, output_file=out)
        self.status.record({"id": "-", "document_kind": "test", "printer": printer}, "printed", "local test page" + (f" → {out}" if out else ""))
        return out or ""
