"""Windows printing.

- ZPL: raw bytes straight to the spooler (datatype RAW). No driver involved,
  the printer interprets the ZPL itself. Fastest and pixel-exact.
- PDF: pypdfium2 renders each page to a bitmap at the printer's DPI, GDI draws
  it scaled to the printable area (aspect kept, centred, rotated when the page
  and the paper disagree on orientation). Paper size, when the server gives one,
  is applied through DEVMODE so a 100 x 150 label does not print on "Letter".

Everything here is synchronous and raises PrintError with a message meant to
be read by a human in Settings > Printers.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger("agent.print")

try:  # pywin32 only exists on Windows; keep the module importable for tests.
    import pywintypes  # noqa: F401
    import win32con
    import win32gui
    import win32print
    import win32ui
    from PIL import Image, ImageWin
    WINDOWS = True
except ImportError:  # pragma: no cover
    WINDOWS = False

DM_ORIENTATION = 0x1
DM_PAPERSIZE = 0x2
DM_PAPERLENGTH = 0x4
DM_PAPERWIDTH = 0x8
DMPAPER_USER = 256
DM_IN_BUFFER = 8
DM_OUT_BUFFER = 2

PDF_TARGET_DPI_CAP = 300  # thermal printers are 203 or 300; no point above


class PrintError(Exception):
    pass


# ── printers ──────────────────────────────────────────────────────────────────

def list_printers() -> list[dict]:
    """[{name, driver, default, port}] as Windows sees them."""
    if not WINDOWS:
        return []
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:  # noqa: BLE001
        default = ""
    out = []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for p in win32print.EnumPrinters(flags, None, 2):
        name = p.get("pPrinterName") or ""
        if not name:
            continue
        out.append({
            "name": name,
            "driver": p.get("pDriverName") or "",
            "port": p.get("pPortName") or "",
            "default": name == default,
        })
    return out


def printer_exists(name: str) -> bool:
    return any(p["name"] == name for p in list_printers())


# ── ZPL ───────────────────────────────────────────────────────────────────────

def print_raw(printer_name: str, data: bytes, doc_name: str = "Rapid label", copies: int = 1):
    if not WINDOWS:
        raise PrintError("Raw printing is only available on Windows.")
    if not data:
        raise PrintError("Empty ZPL document.")
    try:
        h = win32print.OpenPrinter(printer_name)
    except Exception as e:  # noqa: BLE001
        raise PrintError(f"Printer '{printer_name}' not found or not accessible: {e}") from e
    try:
        for _ in range(max(1, copies)):
            win32print.StartDocPrinter(h, 1, (doc_name, None, "RAW"))
            try:
                win32print.StartPagePrinter(h)
                win32print.WritePrinter(h, data)
                win32print.EndPagePrinter(h)
            finally:
                win32print.EndDocPrinter(h)
    except PrintError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PrintError(f"Spooler refused the ZPL job: {e}") from e
    finally:
        win32print.ClosePrinter(h)


# ── PDF ───────────────────────────────────────────────────────────────────────

def _devmode_for(printer_name: str, paper_w_mm, paper_h_mm):
    """DEVMODE with a custom paper size, validated by the driver. None = default."""
    if not (paper_w_mm and paper_h_mm):
        return None
    try:
        h = win32print.OpenPrinter(printer_name)
    except Exception as e:  # noqa: BLE001
        raise PrintError(f"Printer '{printer_name}' not found or not accessible: {e}") from e
    try:
        info = win32print.GetPrinter(h, 2)
        dm = info.get("pDevMode")
        if dm is None:
            return None
        dm.PaperSize = DMPAPER_USER
        dm.PaperWidth = int(round(float(paper_w_mm) * 10))
        dm.PaperLength = int(round(float(paper_h_mm) * 10))
        dm.Orientation = win32con.DMORIENT_PORTRAIT
        dm.Fields = dm.Fields | DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH | DM_ORIENTATION
        try:
            # Let the driver normalise it (some map USER size to a named form).
            win32print.DocumentProperties(0, h, printer_name, dm, dm, DM_IN_BUFFER | DM_OUT_BUFFER)
        except Exception as e:  # noqa: BLE001
            # Some drivers answer with an error here and still accept the
            # DEVMODE on CreateDC; _open_dc falls back to the default if not.
            log.warning("DocumentProperties did not accept custom paper on %s: %s (trying anyway)", printer_name, e)
        return dm
    finally:
        win32print.ClosePrinter(h)


def _open_dc(printer_name: str, devmode):
    if devmode is not None:
        try:
            hdc = win32gui.CreateDC("WINSPOOL", printer_name, devmode)
            return win32ui.CreateDCFromHandle(hdc)
        except Exception as e:  # noqa: BLE001
            log.warning("CreateDC with custom paper failed on %s: %s (using driver default)", printer_name, e)
    try:
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(printer_name)
        return dc
    except Exception as e:  # noqa: BLE001
        raise PrintError(f"Could not open printer '{printer_name}': {e}. Is it installed and online in Windows?") from e


def _render_pages(pdf_bytes: bytes, dpi: int):
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:  # noqa: BLE001
        raise PrintError(f"Invalid PDF: {e}") from e
    if len(pdf) == 0:
        raise PrintError("PDF has no pages.")
    scale = max(1.0, min(dpi, PDF_TARGET_DPI_CAP) / 72.0)
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=scale, draw_annots=True)
        img = bitmap.to_pil().convert("RGB")
        page.close()
        yield img
    pdf.close()


def print_pdf(printer_name: str, pdf_bytes: bytes, *, copies: int = 1, paper_w_mm=None, paper_h_mm=None,
              doc_name: str = "Rapid label", output_file: str | None = None):
    """Print a PDF. `output_file` is for drivers that write a file (Microsoft
    Print to PDF): without it those drivers pop a Save dialog, which is the one
    thing this agent exists to avoid."""
    if not WINDOWS:
        raise PrintError("PDF printing is only available on Windows.")
    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        raise PrintError("Downloaded file is not a PDF.")
    devmode = _devmode_for(printer_name, paper_w_mm, paper_h_mm)
    dc = _open_dc(printer_name, devmode)
    try:
        dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX) or 203
        dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) or dpi_x
        area_w = dc.GetDeviceCaps(win32con.HORZRES)
        area_h = dc.GetDeviceCaps(win32con.VERTRES)
        if area_w <= 0 or area_h <= 0:
            raise PrintError("Printer reports an empty printable area. Check the paper size in its Windows preferences.")
        pages = list(_render_pages(pdf_bytes, max(dpi_x, dpi_y)))
        log.info("print_pdf %s: %d page(s) x %d copies, area %dx%d px @ %d dpi, paper %sx%s mm",
                 printer_name, len(pages), copies, area_w, area_h, dpi_x, paper_w_mm, paper_h_mm)
        if output_file:
            dc.StartDoc(doc_name, output_file)
        else:
            dc.StartDoc(doc_name)
        try:
            for _ in range(max(1, copies)):
                for img in pages:
                    _draw_page(dc, img, area_w, area_h)
        finally:
            dc.EndDoc()
    finally:
        try:
            dc.DeleteDC()
        except Exception:  # noqa: BLE001
            pass


def _draw_page(dc, img, area_w, area_h):
    iw, ih = img.size
    # Rotate when page and paper disagree on orientation (landscape label on a
    # portrait roll, or vice versa): it fills the label instead of shrinking.
    if (iw > ih) != (area_w > area_h) and abs(iw - ih) > 0.05 * max(iw, ih):
        img = img.rotate(90, expand=True)
        iw, ih = img.size
    scale = min(area_w / iw, area_h / ih)
    w, h = int(iw * scale), int(ih * scale)
    x0 = (area_w - w) // 2
    y0 = (area_h - h) // 2
    dc.StartPage()
    try:
        dib = ImageWin.Dib(img)
        dib.draw(dc.GetHandleOutput(), (x0, y0, x0 + w, y0 + h))
    finally:
        dc.EndPage()


# ── test page without server ──────────────────────────────────────────────────

def local_test_image(printer_name: str, paper_w_mm=100, paper_h_mm=150) -> "Image.Image":
    """A simple test label rendered locally (no server needed)."""
    from PIL import ImageDraw, ImageFont
    dpi = 203
    w, h = int(paper_w_mm / 25.4 * dpi), int(paper_h_mm / 25.4 * dpi)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, w - 10, h - 10), outline="black", width=3)
    try:
        font_b = ImageFont.truetype("arialbd.ttf", 48)
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font_b = font = ImageFont.load_default()
    d.text((30, 40), "Rapid Print Agent", fill="black", font=font_b)
    lines = ["Local test page", f"Printer: {printer_name}", f"Paper: {paper_w_mm:g} x {paper_h_mm:g} mm",
             time.strftime("%Y-%m-%d %H:%M"), "", "Corners should be visible on all four sides."]
    y = 120
    for ln in lines:
        d.text((30, y), ln, fill="black", font=font)
        y += 40
    for (x, yy) in ((10, 10), (w - 10, 10), (10, h - 10), (w - 10, h - 10)):
        d.line((x - 15, yy, x + 15, yy), fill="black", width=3)
        d.line((x, yy - 15, x, yy + 15), fill="black", width=3)
    return img


def print_image(printer_name: str, img, *, paper_w_mm=None, paper_h_mm=None, doc_name="Rapid test page",
                output_file: str | None = None):
    if not WINDOWS:
        raise PrintError("Printing is only available on Windows.")
    devmode = _devmode_for(printer_name, paper_w_mm, paper_h_mm)
    dc = _open_dc(printer_name, devmode)
    try:
        area_w = dc.GetDeviceCaps(win32con.HORZRES)
        area_h = dc.GetDeviceCaps(win32con.VERTRES)
        if output_file:
            dc.StartDoc(doc_name, output_file)
        else:
            dc.StartDoc(doc_name)
        try:
            _draw_page(dc, img.convert("RGB"), area_w, area_h)
        finally:
            dc.EndDoc()
    finally:
        try:
            dc.DeleteDC()
        except Exception:  # noqa: BLE001
            pass


def file_output_for(printer_name: str, out_dir: str, job_label: str) -> str | None:
    """Drivers that write files instead of printing need a path, or they show a
    dialog. Only 'Microsoft Print to PDF' (and the XPS writer) today."""
    lowered = printer_name.lower()
    if "print to pdf" not in lowered and "xps document writer" not in lowered:
        return None
    base = Path(out_dir) if out_dir else Path.home() / "Documents" / "RapidPrintAgent"
    base.mkdir(parents=True, exist_ok=True)
    ext = ".pdf" if "pdf" in lowered else ".oxps"
    safe = "".join(c for c in job_label if c.isalnum() or c in "-_") or "job"
    return str(base / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}{ext}")
