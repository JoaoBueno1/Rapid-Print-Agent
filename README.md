# Rapid Print Agent

Silent label printing on the dispatcher's PC. Pairs with a Rapid server by a
6-digit code, reports the Windows printers, long-polls a print queue and prints
PDF or raw ZPL with no dialog, the moment an order is booked.

Server side today: Rapid Express TMS (`src/routes/print_agent.py`,
`docs/RAPID_PRINT_AGENT.md`). The agent is generic: any server that exposes the
same six endpoints works (Rapid Labels and the Portal can reuse it).

## For the dispatcher

1. Download `RapidPrintAgent.exe` from **Settings › Printers** in the Rapid system.
2. Run it. Windows SmartScreen may say "unrecognised app": *More info › Run anyway*.
   It installs itself to `%LOCALAPPDATA%\RapidPrintAgent`, starts with Windows and
   opens `http://127.0.0.1:47831`.
3. Type the server address and the pairing code an admin generated in
   **Settings › Printers › Add computer**. Done: the computer shows up there.
4. An admin switches the printer on and sets who prints to it. From then on,
   booking an order prints the label here.

The icon in the tray: blue = connected · grey = paused · red dot = cannot reach
the server · orange dot = not paired. Right-click for status page, pause, local
test page, log folder, quit.

## For the developer

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
python run.py --console            # runs in place (set RPA_NO_INSTALL=1 to be sure)
pytest
powershell -ExecutionPolicy Bypass -File build\build.ps1   # dist\RapidPrintAgent.exe
```

Flags: `--console` (log to stderr), `--no-tray` (worker + UI only), `--uninstall`.
Env `RPA_NO_INSTALL=1` keeps a frozen exe from copying itself.

### How it prints

| Format | Path |
|---|---|
| ZPL | raw bytes to the spooler (`RAW` datatype). No driver. |
| PDF | pypdfium2 renders at the printer DPI (capped 300), GDI draws scaled to the printable area, centred, rotated if page and paper disagree. Custom paper size via DEVMODE when the server sends one. |
| "Microsoft Print to PDF" | gets an output file path (`Documents\RapidPrintAgent\`) so no Save dialog appears. Handy for testing without paper. |

### Reliability rules

- Loops never die: exceptions are logged, the loop restarts; network errors back off 2 s → 60 s.
- Never prints twice: last 500 job ids kept in `printed.json`; a repeated job is acked, not printed.
- Always answers: download or print failure is acked `failed` with the reason, visible in Settings › Printers.
- 401 from the server (computer removed) forgets the token and shows the pairing screen.
- Single instance (named mutex); a second launch opens the status page.
- Not a Windows service: needs a logged-in user, like every tray app.

### Protocol (header `X-Agent-Token`)

```
POST /api/print-agent/pair        {code, hostname, os, version} → {ok, token, agent_id, name}
POST /api/print-agent/heartbeat   {version, printers:[{name, driver, port, default}]} → {ok, paused}
GET  /api/print-agent/jobs?wait=15 → {ok, jobs:[{id, printer, document_kind, tracking_id, format, copies, paper_width_mm, paper_height_mm, kind}]}
GET  /api/print-agent/jobs/<id>/file → bytes, Content-Type application/pdf | application/x-zpl, X-Job-Format
POST /api/print-agent/jobs/<id>/ack  {status: printed|failed, error}
GET  /api/print-agent/version     → {version, url, sha256}
```

### Files on the PC

`%LOCALAPPDATA%\RapidPrintAgent\` → `RapidPrintAgent.exe`, `config.json` (server, token,
paused), `printed.json`, `logs\agent.log` (5 × 2 MB).

### Releasing

Bump `rapid_print_agent/__init__.py`, run `build\build.ps1`, upload
`dist\RapidPrintAgent.exe` to a GitHub release, then set on the server
`PRINT_AGENT_LATEST_VERSION`, `PRINT_AGENT_SHA256` and `PRINT_AGENT_DOWNLOAD_URL`
(direct `.exe` link). Agents check every 6 h, verify the hash and swap themselves.
Unsigned: SmartScreen warns on first run. A code-signing certificate removes that.

## Publicar uma versão

1. Subir `__version__` em `rapid_print_agent/__init__.py`.
2. `.\build\build.ps1` gera `dist\RapidPrintAgent.exe`.
3. `.\build\publish.ps1 -Version X.Y.Z` manda o exe para o branch `releases`
   (só exe + `SHA256SUMS` + `version.json`; o histórico desse branch pode ser
   reescrito sem dó).
4. Link de download, usado pelo botão da página Printers do TMS:
   `https://github.com/JoaoBueno1/Rapid-Print-Agent/raw/releases/RapidPrintAgent.exe`
5. Para os agentes instalados se atualizarem sozinhos, no TMS:
   `PRINT_AGENT_LATEST_VERSION=X.Y.Z` e `PRINT_AGENT_SHA256=<sha>`, com
   `PRINT_AGENT_DOWNLOAD_URL` apontando para o exe.
