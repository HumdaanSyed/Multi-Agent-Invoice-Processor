"""Generate a handful of synthetic invoice PDFs for local smoke-testing.

These are NOT a substitute for the ~8-12 real-world invoices the roadmap
asks for in data/samples/ (a mix of clean digital + scanned/photographed,
from different vendors/layouts) - gather those separately, especially for
the Phase 6 eval set. This script just gives `extract_invoice` something
real to chew on right after Phase 1 lands, including one PDF with no text
layer (image-only) to exercise the scanned-document path.

Requires `reportlab`, which is NOT a project dependency (only needed here).
Run with: uv run --with reportlab python scripts/make_sample_invoices.py
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

INVOICES = [
    {
        "filename": "sample_01_clean_digital.pdf",
        "vendor": "Acme Cloud Hosting LLC",
        "bill_to": "Northwind Traders, 500 Market St, Springfield, IL",
        "invoice_number": "INV-2026-0417",
        "invoice_date": "2026-07-01",
        "due_date": "2026-07-31",
        "currency": "USD",
        "items": [
            ("Cloud compute - 32 vCPU-months", 32, 12.50),
            ("Object storage - 500 GB-months", 500, 0.02),
            ("Support plan - Business tier", 1, 199.00),
        ],
        "tax_rate": 0.08,
    },
    {
        "filename": "sample_02_clean_digital.pdf",
        "vendor": "Blue Ridge Office Supply Co.",
        "bill_to": "Kestrel Design Studio, 12 Harbor Rd, Portland, ME",
        "invoice_number": "BR-88213",
        "invoice_date": "2026-06-15",
        "due_date": "2026-07-15",
        "currency": "USD",
        "items": [
            ("Standing desk, 48in", 2, 340.00),
            ("Ergonomic chair", 3, 210.00),
            ("Printer paper, case", 4, 38.75),
        ],
        "tax_rate": 0.065,
    },
]

SCANNED_INVOICE = {
    "filename": "sample_03_scanned_no_text_layer.pdf",
    "vendor": "Riverside Plumbing & Heating",
    "bill_to": "Jordan Alvarez, 88 Elm St, Burlington, VT",
    "invoice_number": "RPH-4471",
    "invoice_date": "2026-05-20",
    "due_date": "2026-06-19",
    "currency": "USD",
    "items": [
        ("Emergency call-out fee", 1, 95.00),
        ("Water heater replacement - labor", 3, 85.00),
        ("Parts: 50gal water heater unit", 1, 640.00),
    ],
    "tax_rate": 0.0,
}


def _totals(items: list[tuple[str, float, float]], tax_rate: float) -> tuple[float, float, float]:
    subtotal = round(sum(qty * price for _, qty, price in items), 2)
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    return subtotal, tax, total


def _draw_invoice(c: canvas.Canvas, inv: dict) -> None:
    width, height = letter
    y = height - 1 * inch

    c.setFont("Helvetica-Bold", 18)
    c.drawString(1 * inch, y, inv["vendor"])
    y -= 0.35 * inch

    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, f"Invoice #: {inv['invoice_number']}")
    y -= 0.2 * inch
    c.drawString(1 * inch, y, f"Invoice Date: {inv['invoice_date']}")
    y -= 0.2 * inch
    c.drawString(1 * inch, y, f"Due Date: {inv['due_date']}")
    y -= 0.35 * inch

    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Bill To:")
    y -= 0.2 * inch
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, inv["bill_to"])
    y -= 0.45 * inch

    c.setFont("Helvetica-Bold", 10)
    c.drawString(1 * inch, y, "Description")
    c.drawString(4.3 * inch, y, "Qty")
    c.drawString(5.0 * inch, y, "Unit Price")
    c.drawString(6.2 * inch, y, "Amount")
    y -= 0.15 * inch
    c.line(1 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch

    c.setFont("Helvetica", 10)
    for desc, qty, price in inv["items"]:
        amount = round(qty * price, 2)
        c.drawString(1 * inch, y, desc)
        c.drawRightString(4.7 * inch, y, f"{qty:g}")
        c.drawRightString(5.9 * inch, y, f"${price:,.2f}")
        c.drawRightString(7.3 * inch, y, f"${amount:,.2f}")
        y -= 0.25 * inch

    y -= 0.1 * inch
    c.line(5.0 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch

    subtotal, tax, total = _totals(inv["items"], inv["tax_rate"])
    c.drawString(5.0 * inch, y, "Subtotal:")
    c.drawRightString(7.3 * inch, y, f"${subtotal:,.2f}")
    y -= 0.22 * inch
    c.drawString(5.0 * inch, y, f"Tax ({inv['tax_rate'] * 100:.1f}%):")
    c.drawRightString(7.3 * inch, y, f"${tax:,.2f}")
    y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(5.0 * inch, y, "Total:")
    c.drawRightString(7.3 * inch, y, f"${total:,.2f}")


def make_digital_pdf(inv: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=letter)
    _draw_invoice(c, inv)
    c.showPage()
    c.save()


def make_scanned_pdf(inv: dict, out_path: Path) -> None:
    """Render the invoice to a raster image, then embed only that image in
    the PDF (no text layer) - simulates a photographed/scanned document."""
    from PIL import Image
    from reportlab.lib.pagesizes import letter as _letter

    # Render at higher resolution onto an in-memory PDF, then rasterize it.
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=_letter)
    _draw_invoice(c, inv)
    c.showPage()
    c.save()

    try:
        import fitz  # PyMuPDF, for rasterizing to an image
    except ImportError:
        # Fallback: no rasterizer available, just ship the vector version.
        # (Still useful as a sample; just won't exercise the no-text-layer path.)
        out_path.write_bytes(buf.getvalue())
        return

    buf.seek(0)
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    doc.close()

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.save(out_path, "PDF")


def main() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    for inv in INVOICES:
        out_path = SAMPLES_DIR / inv["filename"]
        make_digital_pdf(inv, out_path)
        print(f"Wrote {out_path}")

    scanned_out = SAMPLES_DIR / SCANNED_INVOICE["filename"]
    make_scanned_pdf(SCANNED_INVOICE, scanned_out)
    print(f"Wrote {scanned_out}")


if __name__ == "__main__":
    main()
