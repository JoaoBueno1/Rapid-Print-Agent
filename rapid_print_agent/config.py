"""Where the agent keeps its state: %LOCALAPPDATA%\\RapidPrintAgent\\.

config.json   server_url, token, agent_name, paused, pdf_output_dir
printed.json  last 500 job ids already printed (idempotency across restarts)
logs\\         rotating log
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from pathlib import Path

from . import APP_ID

DEFAULT_SERVER = "https://aws.rapidexpress.com.au"
LOCAL_UI_PORT = 47831
PRINTED_KEEP = 500


def base_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    d = Path(root) / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = base_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Config:
    """Thread-safe JSON config. Only the few keys we need."""

    _lock = threading.RLock()

    def __init__(self, path: Path | None = None):
        self.path = path or (base_dir() / "config.json")
        self.data: dict = {}
        self.load()

    def load(self):
        with self._lock:
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}
            self.data.setdefault("server_url", DEFAULT_SERVER)
            self.data.setdefault("token", "")
            self.data.setdefault("agent_name", "")
            self.data.setdefault("paused", False)
            self.data.setdefault("pdf_output_dir", "")
            self.data.setdefault("default_test_printer", "")

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, **kwargs):
        with self._lock:
            self.data.update(kwargs)
            self.save()

    @property
    def paired(self) -> bool:
        return bool(self.get("token"))

    @property
    def server_url(self) -> str:
        return (self.get("server_url") or DEFAULT_SERVER).rstrip("/")


class PrintedStore:
    """Job ids we already printed. A job that comes back (lost ack, server
    retry) is acked again as printed instead of printing a second label."""

    def __init__(self, path: Path | None = None):
        self.path = path or (base_dir() / "printed.json")
        self._lock = threading.Lock()
        try:
            ids = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ids = []
        self._ids = deque(ids, maxlen=PRINTED_KEEP)
        self._set = set(self._ids)

    def __contains__(self, job_id) -> bool:
        with self._lock:
            return job_id in self._set

    def add(self, job_id):
        with self._lock:
            if job_id in self._set:
                return
            if len(self._ids) == self._ids.maxlen:
                self._set.discard(self._ids[0])
            self._ids.append(job_id)
            self._set.add(job_id)
            try:
                self.path.write_text(json.dumps(list(self._ids)), encoding="utf-8")
            except OSError:
                pass
