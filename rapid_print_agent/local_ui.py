"""Local web UI on http://127.0.0.1:47831 — pairing, status, local test print.

Served by the agent itself (stdlib http.server, loopback only). Opened from the
tray icon or automatically on first run. Looks like the TMS: same tokens, same
logo. No framework, one HTML page that polls /api/status.
"""
from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import APP_NAME, __version__
from .api import ApiError
from .config import LOCAL_UI_PORT

log = logging.getLogger("agent.ui")

_ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _asset(name: str) -> bytes:
    import sys
    base = Path(getattr(sys, "_MEIPASS", _ASSETS.parent)) / "assets"
    p = base / name
    if not p.exists():
        p = _ASSETS / name
    return p.read_bytes()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rapid Print Agent</title>
<link rel="icon" href="/icon.png">
<style>
:root{--surface:#FFFFFF;--sunken:#F8FAFC;--subtle:#F1F5F9;--text:#1B2A3F;--muted:#64748B;--muted-strong:#475569;--border:#E2E8F0;--border-strong:#CBD5E1;
--accent:#2563EB;--accent-subtle:#DBEAFE;--accent-text:#1E40AF;--danger:#DC2626;--danger-subtle:#FEE2E2;--danger-text:#991B1B;
--success:#15803D;--success-subtle:#DCFCE7;--success-text:#166534;--warning-subtle:#FEF3C7;--warning-text:#92400E;--brand:#1B2A3F}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;color:var(--text);background:var(--sunken)}
.top{background:var(--brand);color:#fff;padding:12px 16px;display:flex;align-items:center;gap:12px}
.top img{width:28px;height:28px}.top h1{font-size:16px;margin:0;font-weight:700}.top .v{margin-left:auto;font-size:12px;opacity:.8}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 8px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.card h2{font-size:14px;margin:0 0 8px;font-weight:700}.card p{margin:0 0 8px;color:var(--muted-strong);font-size:12px}
label{display:block;font-size:11px;font-weight:600;color:var(--muted-strong);margin:8px 0 4px}
input,select{height:36px;width:100%;padding:0 10px;border:1px solid var(--border-strong);border-radius:6px;font-size:14px;color:var(--text);background:var(--surface)}
input:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.15)}
.code{font-size:24px;letter-spacing:.3em;text-align:center;font-family:ui-monospace,Consolas,monospace}
.btn{display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 14px;border-radius:6px;border:1px solid var(--border-strong);background:var(--surface);font-weight:600;font-size:13px;cursor:pointer;color:var(--text)}
.btn:hover{background:var(--sunken)}.btn-primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn-primary:hover{background:var(--accent-text)}
.btn-danger{background:var(--danger-subtle);border-color:var(--danger-subtle);color:var(--danger-text)}.btn:disabled{opacity:.6;cursor:not-allowed}
.row{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}.row>div{flex:1;min-width:140px}.row .btn{margin-top:8px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:9999px;font-size:12px;font-weight:600;background:var(--subtle);color:var(--muted-strong)}
.chip::before{content:"";width:8px;height:8px;border-radius:9999px;background:currentColor}
.ok{background:var(--success-subtle);color:var(--success-text)}.bad{background:var(--danger-subtle);color:var(--danger-text)}.warn{background:var(--warning-subtle);color:var(--warning-text)}
.msg{padding:10px 12px;border-radius:6px;font-size:12px;font-weight:600;margin:8px 0;display:none}.msg.show{display:block}
.msg-ok{background:var(--success-subtle);color:var(--success-text)}.msg-bad{background:var(--danger-subtle);color:var(--danger-text)}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;padding:6px 8px;background:var(--subtle);color:var(--muted-strong);font-size:11px;text-transform:uppercase;letter-spacing:.02em}
td{padding:6px 8px;border-bottom:1px solid var(--border)}tr:last-child td{border-bottom:0}.muted{color:var(--muted)}
.kv{display:grid;grid-template-columns:140px 1fr;gap:4px 12px;font-size:12px}.kv b{color:var(--muted-strong);font-weight:600}
.steps{margin:0 0 8px;padding-left:18px;font-size:12px;color:var(--muted-strong)}.steps li{margin:3px 0}
.foot{color:var(--muted);font-size:11px;text-align:center;padding:8px;line-height:1.7}.foot b{color:var(--muted-strong)}
</style></head><body>
<div class="wrap">

<div class="card" id="pairCard" style="display:none">
  <h2>Connect this computer</h2>
  <ol class="steps">
    <li>In the Rapid system, open <b>Settings › Printers</b> and click <b>Add computer</b>.</li>
    <li>Type the server address and the 6-digit code shown there.</li>
  </ol>
  <form id="pairForm">
    <label for="server">Server</label>
    <input id="server" name="server" placeholder="https://aws.rapidexpress.com.au" autocomplete="off">
    <label for="code">Pairing code</label>
    <input id="code" name="code" class="code" maxlength="7" inputmode="numeric" placeholder="000 000" autocomplete="one-time-code" autofocus>
    <div class="msg" id="pairMsg"></div>
    <button class="btn btn-primary" id="pairBtn" type="submit" style="margin-top:12px">Connect</button>
  </form>
</div>

<div id="statusWrap" style="display:none">
  <div class="card">
    <h2 style="display:flex;align-items:center;gap:10px">Status <span class="chip" id="conn">…</span></h2>
    <div class="kv">
      <b>Server</b><span id="srv"></span>
      <b>Paired as</b><span id="agentName"></span>
      <b>Last contact</b><span id="lastOk" class="muted"></span>
      <b>Printed / failed</b><span><span id="nPrinted">0</span> / <span id="nFailed">0</span> since start</span>
      <b>Last problem</b><span id="lastErr" class="muted">none</span>
    </div>
    <div class="msg" id="updMsg"></div>
    <div class="row" style="margin-top:12px">
      <button class="btn" id="pauseBtn" type="button">Pause printing</button>
      <button class="btn" type="button" onclick="fetch('/api/open-log',{method:'POST'})">Open log folder</button>
      <button class="btn btn-danger" id="unpairBtn" type="button">Disconnect</button>
    </div>
    <p style="margin-top:12px">Which labels print here is decided in the Rapid system: <b>Settings › Printers</b>. Switch the printer on there and set who prints to it.</p>
  </div>

  <div class="card">
    <h2>Printers on this computer</h2>
    <p>Reported to the server every 30 s. Print a local test page to check paper size and margins.</p>
    <div class="row">
      <div style="flex:3"><label for="tp">Printer</label><select id="tp"></select></div>
      <div><label for="tw">Width mm</label><input id="tw" type="number" value="100"></div>
      <div><label for="th">Height mm</label><input id="th" type="number" value="150"></div>
      <button class="btn" id="testBtn" type="button">Test page</button>
    </div>
    <div class="msg" id="testMsg"></div>
  </div>

  <div class="card">
    <h2>Recent jobs on this computer</h2>
    <table><thead><tr><th>Time</th><th>Job</th><th>Label</th><th>Printer</th><th>Result</th><th>Detail</th></tr></thead><tbody id="recent"></tbody></table>
    <p id="noRecent" class="muted" style="margin-top:8px">Nothing printed yet.</p>
  </div>
</div>
<p class="foot">Rapid Print Agent v__VERSION__ · this computer: <b id="host"></b><br>Keeps running in the system tray. Closing this window only hides it; the tray icon brings it back.</p>
</div>
<script>
const $=s=>document.querySelector(s);const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let paired=null;
function msg(el,text,ok){el.textContent=text;el.className='msg show '+(ok?'msg-ok':'msg-bad');if(!text)el.className='msg'}
async function refresh(){
  let s;try{s=await (await fetch('/api/status')).json()}catch(e){$('#conn').textContent='UI lost';return}
  $('#host').textContent=s.hostname;
  if(paired!==s.paired){paired=s.paired;$('#pairCard').style.display=paired?'none':'block';$('#statusWrap').style.display=paired?'block':'none';if(!paired){$('#server').value=s.server_url||''}}
  if(!paired)return;
  const c=$('#conn');
  if(s.paused){c.textContent='Paused';c.className='chip warn'}else if(s.paused_by_server){c.textContent='Paused by server';c.className='chip warn'}else if(s.connected){c.textContent='Connected';c.className='chip ok'}else{c.textContent='Disconnected';c.className='chip bad'}
  $('#srv').textContent=s.server_url;$('#agentName').textContent=s.agent_name||'-';$('#lastOk').textContent=s.last_ok||'never';
  $('#nPrinted').textContent=s.jobs_printed;$('#nFailed').textContent=s.jobs_failed;
  $('#lastErr').textContent=s.last_error?(s.last_error+(s.last_error_at?' ('+s.last_error_at+')':'')):'none';
  $('#pauseBtn').textContent=s.paused?'Resume printing':'Pause printing';
  if(s.update_available){msg($('#updMsg'),'Update available: v'+s.update_available.version+'. '+(s.update_available.auto?'It installs itself in a moment.':'Download it from '+s.update_available.url),true)}else{msg($('#updMsg'),'',true)}
  const sel=$('#tp');const cur=sel.value;sel.innerHTML=(s.printers||[]).map(p=>'<option value="'+esc(p.name)+'"'+((cur?cur===p.name:p.default)?' selected':'')+'>'+esc(p.name)+(p.default?' (default)':'')+'</option>').join('');
  const tb=$('#recent');tb.innerHTML=(s.recent||[]).map(r=>'<tr><td>'+esc(r.time)+'</td><td class="muted">'+esc(r.job)+'</td><td>'+esc(r.label)+'</td><td>'+esc(r.printer)+'</td><td><span class="chip '+(r.status==='printed'?'ok':'bad')+'">'+esc(r.status)+'</span></td><td class="muted">'+esc(r.detail)+'</td></tr>').join('');
  $('#noRecent').style.display=(s.recent||[]).length?'none':'block';
}
$('#pairForm').onsubmit=async e=>{e.preventDefault();const b=$('#pairBtn');b.disabled=true;b.textContent='Connecting…';msg($('#pairMsg'),'',true);
  try{const r=await fetch('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({server:$('#server').value,code:$('#code').value})});const d=await r.json();
    if(d.ok){msg($('#pairMsg'),'Connected as '+d.name+'. Now switch the printer on in Settings › Printers.',true);setTimeout(refresh,800)}else{msg($('#pairMsg'),d.error||'Pairing failed',false)}}
  catch(err){msg($('#pairMsg'),'Agent UI error: '+err.message,false)}finally{b.disabled=false;b.textContent='Connect'}};
$('#pauseBtn').onclick=async()=>{await fetch('/api/pause',{method:'POST'});refresh()};
$('#unpairBtn').onclick=async()=>{if(!$('#unpairBtn').dataset.sure){$('#unpairBtn').dataset.sure='1';$('#unpairBtn').textContent='Click again to disconnect';setTimeout(()=>{delete $('#unpairBtn').dataset.sure;$('#unpairBtn').textContent='Disconnect'},4000);return}
  await fetch('/api/unpair',{method:'POST'});delete $('#unpairBtn').dataset.sure;$('#unpairBtn').textContent='Disconnect';refresh()};
$('#testBtn').onclick=async()=>{const b=$('#testBtn');b.disabled=true;msg($('#testMsg'),'Printing…',true);
  try{const r=await fetch('/api/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({printer:$('#tp').value,w:$('#tw').value,h:$('#th').value})});const d=await r.json();msg($('#testMsg'),d.ok?('Sent to '+$('#tp').value+(d.file?' → '+d.file:'')):d.error,d.ok)}
  catch(err){msg($('#testMsg'),'Agent UI error: '+err.message,false)}finally{b.disabled=false;setTimeout(refresh,500)}};
refresh();setInterval(refresh,3000);
</script></body></html>"""


class LocalUI:
    def __init__(self, config, worker, on_quit=None):
        self.config = config
        self.worker = worker
        self.on_quit = on_quit
        self.window = None  # AppWindow when the own window is in use
        self.server: ThreadingHTTPServer | None = None
        self.port = LOCAL_UI_PORT

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        ui = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "RapidPrintAgent"

            def log_message(self, fmt, *args):  # quiet
                log.debug("ui " + fmt, *args)

            def _send(self, code, body, ctype="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                ctype = self.headers.get("Content-Type") or ""
                if "json" in ctype:
                    try:
                        return json.loads(raw.decode("utf-8") or "{}")
                    except ValueError:
                        return {}
                return {k: v[0] for k, v in parse_qs(raw.decode("utf-8")).items()}

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    return self._send(200, PAGE.replace("__VERSION__", __version__), "text/html")
                if self.path == "/icon.png":
                    return self._send(200, _asset("icon.png"), "image/png")
                if self.path == "/api/status":
                    return self._send(200, ui.status_json())
                return self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                # Loopback only; still refuse cross-site posts from a browser tab.
                origin = self.headers.get("Origin")
                if origin and not origin.startswith("http://127.0.0.1") and not origin.startswith("http://localhost"):
                    return self._send(403, {"ok": False, "error": "forbidden"})
                body = self._body()
                try:
                    if self.path == "/api/pair":
                        return self._send(200, ui.do_pair(body.get("server") or "", body.get("code") or ""))
                    if self.path == "/api/unpair":
                        ui.config.set(token="", agent_name="")
                        ui.worker.status.fail("Disconnected.")
                        return self._send(200, {"ok": True})
                    if self.path == "/api/pause":
                        ui.config.set(paused=not ui.config.get("paused"))
                        ui.worker.wake()
                        return self._send(200, {"ok": True, "paused": ui.config.get("paused")})
                    if self.path == "/api/test":
                        try:
                            w = float(body.get("w") or 100)
                            h = float(body.get("h") or 150)
                        except ValueError:
                            return self._send(400, {"ok": False, "error": "Width and height must be numbers."})
                        out = ui.worker.local_test_print(body.get("printer") or "", w, h)
                        return self._send(200, {"ok": True, "file": out})
                    if self.path == "/api/open-log":
                        from .config import log_dir
                        import os
                        os.startfile(str(log_dir()))  # noqa: S606
                        return self._send(200, {"ok": True})
                    if self.path == "/api/show":
                        # A second launch of the exe lands here: bring the window up.
                        threading.Thread(target=ui.open, daemon=True).start()
                        return self._send(200, {"ok": True})
                    if self.path == "/api/quit":
                        if ui.on_quit:
                            threading.Timer(0.3, ui.on_quit).start()
                        return self._send(200, {"ok": True})
                except Exception as e:  # noqa: BLE001
                    log.exception("ui action failed: %s", self.path)
                    return self._send(200, {"ok": False, "error": str(e)})
                return self._send(404, {"ok": False, "error": "not found"})

        last_err = None
        for port in (LOCAL_UI_PORT, LOCAL_UI_PORT + 1, LOCAL_UI_PORT + 2):
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self.port = port
                break
            except OSError as e:
                last_err = e
        if not self.server:
            raise RuntimeError(f"Local UI port busy: {last_err}")
        t = threading.Thread(target=self.server.serve_forever, name="local-ui", daemon=True)
        t.start()
        log.info("local UI on %s", self.url)

    def stop(self):
        if self.server:
            self.server.shutdown()

    def open(self):
        """Show the agent: its own window when available, else the browser."""
        if self.window is not None:
            try:
                if self.window.show():
                    return
            except Exception:  # noqa: BLE001
                log.exception("window show failed; falling back to the browser")
        try:
            webbrowser.open(self.url)
        except Exception:  # noqa: BLE001
            pass

    # ── actions ───────────────────────────────────────────────────────────
    def status_json(self) -> dict:
        import socket
        s = self.worker.status.snapshot()
        s.update({
            "paired": self.config.paired,
            "paused": bool(self.config.get("paused")),
            "server_url": self.config.server_url,
            "agent_name": self.config.get("agent_name"),
            "hostname": socket.gethostname(),
            "app": APP_NAME,
        })
        return s

    def do_pair(self, server: str, code: str) -> dict:
        server = (server or "").strip().rstrip("/")
        if server and not server.startswith("http"):
            server = "https://" + server
        if not server:
            return {"ok": False, "error": "Server address is required."}
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) != 6:
            return {"ok": False, "error": "The code has 6 digits."}
        self.config.set(server_url=server)
        try:
            data = self.worker.api.pair(digits)
        except ApiError as e:
            return {"ok": False, "error": str(e)}
        self.config.set(token=data["token"], agent_name=data.get("name") or "")
        self.worker.status.fail("Paired, connecting…", disconnect=False)
        self.worker.wake()
        return {"ok": True, "name": data.get("name")}
