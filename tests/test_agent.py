"""Agent logic without a printer or a server: config, idempotency store,
worker job handling (API and printing faked), local UI pairing validation,
version compare."""
import json
import threading
from pathlib import Path

import pytest

from rapid_print_agent.api import ApiError
from rapid_print_agent.config import Config, PrintedStore
from rapid_print_agent.updater import _ver
from rapid_print_agent.worker import Status, Worker
from rapid_print_agent import printing


class FakeApi:
    def __init__(self):
        self.acks = []
        self.files = {}
        self.fail_download = None

    def job_file(self, job_id):
        if self.fail_download:
            raise self.fail_download
        return self.files.get(job_id, (b"%PDF-1.4 fake", "pdf"))

    def ack(self, job_id, status, error=""):
        self.acks.append((job_id, status, error))

    def pair(self, code):
        if code != "123456":
            raise ApiError("Invalid or already used code.", 400, retryable=False)
        return {"ok": True, "token": "rpa_abc", "name": "DESK-1"}


@pytest.fixture
def cfg(tmp_path):
    return Config(tmp_path / "config.json")


@pytest.fixture
def worker(cfg, tmp_path, monkeypatch):
    api = FakeApi()
    w = Worker(cfg, Status(), api=api, printed=PrintedStore(tmp_path / "printed.json"))
    calls = []
    monkeypatch.setattr(printing, "printer_exists", lambda name: name != "GONE")
    monkeypatch.setattr(printing, "print_pdf", lambda *a, **k: calls.append(("pdf", a, k)))
    monkeypatch.setattr(printing, "print_raw", lambda *a, **k: calls.append(("zpl", a, k)))
    w._calls = calls
    return w


def test_config_roundtrip(tmp_path):
    c = Config(tmp_path / "c.json")
    assert not c.paired and c.server_url.startswith("https://")
    c.set(token="rpa_x", server_url="https://srv.example/")
    c2 = Config(tmp_path / "c.json")
    assert c2.paired and c2.server_url == "https://srv.example"


def test_printed_store_persists_and_caps(tmp_path):
    p = tmp_path / "p.json"
    s = PrintedStore(p)
    for i in range(600):
        s.add(i)
    assert 599 in s and 50 not in s
    s2 = PrintedStore(p)
    assert 599 in s2 and len(json.loads(p.read_text())) == 500


def test_job_prints_pdf_and_acks(worker):
    job = {"id": 1, "printer": "Zebra", "format": "pdf", "copies": 2, "paper_width_mm": 100, "paper_height_mm": 150, "tracking_id": "RX-1"}
    worker.handle_job(job)
    assert worker.api.acks == [(1, "printed", "")]
    kind, args, kw = worker._calls[0]
    assert kind == "pdf" and args[0] == "Zebra" and kw["copies"] == 2 and kw["paper_w_mm"] == 100
    # same job again: acked, not printed
    worker.handle_job(job)
    assert len(worker._calls) == 1 and worker.api.acks[-1] == (1, "printed", "")
    assert worker.status.snapshot()["jobs_printed"] == 1


def test_job_zpl_goes_raw(worker):
    worker.api.files[2] = (b"^XA^FDhi^FS^XZ", "zpl")
    worker.handle_job({"id": 2, "printer": "Zebra", "format": "zpl", "copies": 1})
    assert worker._calls[0][0] == "zpl" and worker.api.acks == [(2, "printed", "")]


def test_job_missing_printer_fails_with_reason(worker):
    worker.handle_job({"id": 3, "printer": "GONE", "format": "pdf"})
    assert worker.api.acks[0][0] == 3 and worker.api.acks[0][1] == "failed"
    assert "not installed" in worker.api.acks[0][2]
    assert worker.status.snapshot()["jobs_failed"] == 1


def test_download_422_is_not_acked_again(worker):
    worker.api.fail_download = ApiError("Label not available", 422, retryable=False)
    worker.handle_job({"id": 4, "printer": "Zebra", "format": "pdf"})
    assert worker.api.acks == []  # server already failed it
    worker.api.fail_download = ApiError("boom", 500)
    worker.handle_job({"id": 5, "printer": "Zebra", "format": "pdf"})
    assert worker.api.acks[-1][0] == 5 and worker.api.acks[-1][1] == "failed"


def test_print_error_is_reported(worker, monkeypatch):
    def bad(*a, **k):
        raise printing.PrintError("Spooler refused")
    monkeypatch.setattr(printing, "print_pdf", bad)
    worker.handle_job({"id": 6, "printer": "Zebra", "format": "pdf"})
    assert worker.api.acks == [(6, "failed", "Spooler refused")]


def test_local_ui_pair_validation(cfg, worker):
    from rapid_print_agent.local_ui import LocalUI
    ui = LocalUI(cfg, worker)
    assert ui.do_pair("", "123456")["ok"] is False
    assert "6 digits" in ui.do_pair("srv.example", "12")["error"]
    r = ui.do_pair("srv.example", "000 000")
    assert r["ok"] is False and "Invalid" in r["error"]
    r = ui.do_pair("srv.example/", "123 456")
    assert r["ok"] and cfg.paired and cfg.server_url == "https://srv.example" and cfg.get("agent_name") == "DESK-1"


def test_version_compare():
    assert _ver("1.0.0") < _ver("1.0.1") < _ver("1.2") < _ver("2")
    assert _ver("v1.0.0") == _ver("1.0.0")


def test_file_output_only_for_file_drivers(tmp_path):
    assert printing.file_output_for("Zebra ZD420", str(tmp_path), "RX-1") is None
    out = printing.file_output_for("Microsoft Print to PDF", str(tmp_path), "RX-1/x")
    assert out.endswith(".pdf") and Path(out).parent == tmp_path and "RX-1x" in out
