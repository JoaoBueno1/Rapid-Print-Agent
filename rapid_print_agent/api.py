"""HTTP client for the server side (TMS: src/routes/print_agent.py)."""
from __future__ import annotations

import logging
import platform
import socket

import requests

from . import __version__
from .config import Config

log = logging.getLogger("agent.api")


class ApiError(Exception):
    def __init__(self, message, status=None, retryable=True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Api:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"RapidPrintAgent/{__version__} ({platform.system()} {platform.release()})"

    # ── plumbing ──────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return self.config.server_url + path

    def _headers(self) -> dict:
        return {"X-Agent-Token": self.config.get("token") or ""}

    def _request(self, method, path, *, timeout, auth=True, **kw):
        headers = kw.pop("headers", {})
        if auth:
            headers.update(self._headers())
        try:
            r = self.session.request(method, self._url(path), headers=headers, timeout=timeout, **kw)
        except requests.RequestException as e:
            raise ApiError(f"Cannot reach server: {e.__class__.__name__}", retryable=True) from e
        if r.status_code == 401:
            raise ApiError("Server rejected the token. Pair this computer again.", 401, retryable=False)
        if r.status_code == 503:
            raise ApiError("Auto-print is switched off on the server (PRINT_AGENT_ENABLED).", 503, retryable=True)
        return r

    @staticmethod
    def _json(r):
        try:
            return r.json()
        except ValueError:
            raise ApiError(f"Unexpected response ({r.status_code}) from server", r.status_code)

    # ── calls ─────────────────────────────────────────────────────────────
    def pair(self, code: str) -> dict:
        body = {
            "code": code.replace(" ", ""),
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "version": __version__,
        }
        r = self._request("POST", "/api/print-agent/pair", json=body, timeout=20, auth=False)
        data = self._json(r)
        if r.status_code != 200 or not data.get("ok"):
            raise ApiError(data.get("error") or f"Pairing failed ({r.status_code})", r.status_code, retryable=False)
        return data

    def heartbeat(self, printers: list[dict]) -> dict:
        r = self._request("POST", "/api/print-agent/heartbeat",
                          json={"version": __version__, "printers": printers}, timeout=20)
        data = self._json(r)
        if r.status_code != 200:
            raise ApiError(data.get("error") or f"Heartbeat failed ({r.status_code})", r.status_code)
        return data

    def poll_jobs(self, wait: int = 15) -> list[dict]:
        r = self._request("GET", f"/api/print-agent/jobs?wait={wait}", timeout=wait + 15)
        data = self._json(r)
        if r.status_code != 200:
            raise ApiError(data.get("error") or f"Poll failed ({r.status_code})", r.status_code)
        return data.get("jobs") or []

    def job_file(self, job_id: int) -> tuple[bytes, str]:
        r = self._request("GET", f"/api/print-agent/jobs/{job_id}/file", timeout=60)
        if r.status_code != 200:
            try:
                msg = r.json().get("error")
            except ValueError:
                msg = None
            raise ApiError(msg or f"Could not download job {job_id} ({r.status_code})", r.status_code,
                           retryable=r.status_code >= 500)
        fmt = r.headers.get("X-Job-Format") or ("zpl" if "zpl" in (r.headers.get("Content-Type") or "") else "pdf")
        return r.content, fmt

    def ack(self, job_id: int, status: str, error: str = ""):
        r = self._request("POST", f"/api/print-agent/jobs/{job_id}/ack",
                          json={"status": status, "error": error[:2000]}, timeout=20)
        if r.status_code != 200:
            raise ApiError(f"Ack failed ({r.status_code})", r.status_code)

    def version(self) -> dict:
        r = self._request("GET", "/api/print-agent/version", timeout=15, auth=False)
        return self._json(r)
