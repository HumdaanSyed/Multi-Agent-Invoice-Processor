"""Generate the Phase 6 eval set: 20 synthetic invoices with exact gold
labels, into data/eval/, plus the evals/dataset.jsonl manifest.

Gold is computed by the same _totals() math that draws each PDF, so the
label and the rendered document can't drift apart out of sync - this is
the whole reason this eval set is synthetic rather than hand-labeled real
invoices (the tradeoff that makes: narrower layout diversity than the
real world, so these numbers are an upper bound - is documented in
evals/report.py's rendered methodology section, not hidden).

Before writing anything, every gold invoice is run through
invoice_agent.validate.validate_invoice() and must pass - catches a typo'd
spec for free, before a single (paid) extraction call happens later.

Requires reportlab/pymupdf/pillow, none of which are project dependencies
(dev-only fixture generation, matching scripts/make_sample_invoices.py).
Run with:
  uv run --with reportlab --with pymupdf --with pillow \
      python scripts/generate_eval_set.py
"""

from __future__ import annotations

import io
import json
import sys
from datetime import date as _date
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_agent.validate import validate_invoice  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "data" / "eval"
DATASET_PATH = REPO_ROOT / "evals" / "dataset.jsonl"

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
CURRENCY_DECIMALS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}

# Forces spec 19 (9 items) to spill onto a second page without needing an
# unrealistically long item list - real invoicing systems commonly paginate
# on a fixed row count too, not just available vertical space.
ITEMS_PER_PAGE = 6

# --- the eval set ------------------------------------------------------------

SPECS = [
    dict(
        id="01", vendor="Acme Cloud Hosting LLC",
        bill_to="Northwind Traders, 500 Market St, Springfield, IL",
        invoice_number="INV-2026-1001", invoice_date="2026-07-01", due_date="2026-07-31",
        currency="USD", render="digital", tax_rate=0.08,
        items=[
            ("Cloud compute - 32 vCPU-months", 32, 12.50),
            ("Object storage - 500 GB-months", 500, 0.02),
            ("Support plan - Business tier", 1, 199.00),
        ],
    ),
    dict(
        id="02", vendor="Blue Ridge Office Supply Co.",
        bill_to="Kestrel Design Studio, 12 Harbor Rd, Portland, ME",
        invoice_number="BR-2026-2002", invoice_date="2026-06-15", due_date="2026-07-15",
        currency="USD", render="digital", tax_rate=0.065,
        items=[
            ("Standing desk, 48in", 2, 340.00),
            ("Ergonomic chair", 3, 210.00),
            ("Printer paper, case", 4, 38.75),
        ],
    ),
    dict(
        id="03", vendor="Riverside Plumbing & Heating",
        bill_to="Jordan Alvarez, 88 Elm St, Burlington, VT",
        invoice_number="RPH-2026-3003", invoice_date="2026-05-20", due_date="2026-06-19",
        currency="USD", render="digital", tax_rate=0.0,
        items=[
            ("Emergency call-out fee", 1, 95.00),
            ("Water heater replacement - labor", 3, 85.00),
            ("Parts: 50gal water heater unit", 1, 640.00),
        ],
    ),
    dict(
        id="04", vendor="Nordwind Logistik GmbH",
        bill_to="Hafen Warenhandel GmbH, Speicherstadt 5, Hamburg",
        invoice_number="NWL-2026-4004", invoice_date="2026-06-10", due_date="2026-07-10",
        currency="EUR", render="digital", tax_rate=0.19,
        items=[
            ("Freight - Hamburg to Rotterdam", 4, 320.00),
            ("Customs clearance fee", 1, 150.00),
        ],
    ),
    dict(
        id="05", vendor="Thames Analytics Ltd",
        bill_to="Whitfield & Sons, 22 Baker Street, London",
        invoice_number="TAL-2026-5005", invoice_date="2026-06-05", due_date="2026-07-05",
        currency="GBP", render="digital", tax_rate=0.20,
        items=[
            ("Market research report", 1, 1200.00),
            ("Data licensing - Q2", 1, 450.00),
            ("Analyst consultation, 3 hrs", 3, 175.00),
            ("Report formatting & delivery", 1, 90.00),
        ],
    ),
    dict(
        id="06", vendor="Dana Reyes Consulting",
        bill_to="Fenwick Realty Group, 900 Oak Ave, Denver, CO",
        invoice_number="DRC-2026-6006", invoice_date="2026-07-08", due_date=None,
        currency="USD", render="digital", tax_rate=0.0725,
        items=[
            ("Strategy consulting, 5 hrs", 5, 175.00),
        ],
    ),
    dict(
        id="07", vendor="Pinnacle Fabrication Inc.",
        bill_to="Ironclad Builders LLC, 4400 Industrial Pkwy, Tulsa, OK",
        invoice_number="PFI-2026-7007", invoice_date="2026-07-12", due_date="2026-08-11",
        currency="USD", render="digital", tax_rate=0.0725,
        items=[
            ("Steel plate, 1/2in x 4x8ft", 6, 145.00),
            ("Welding labor, hrs", 18, 65.00),
            ("Powder coating, per unit", 6, 40.00),
            ("Mounting brackets, set", 12, 22.50),
            ("Freight - local delivery", 1, 175.00),
            ("Design review & sign-off", 1, 250.00),
        ],
    ),
    dict(
        id="08", vendor="Harbor Freight Forwarding",
        bill_to="Coastal Import Traders, 77 Pier Ave, Long Beach, CA",
        invoice_number="HFF-2026-8008", invoice_date="2026-07-05", due_date=None,
        currency="USD", render="digital", tax_rate=0.0,
        items=[
            ("Container handling fee", 1, 450.00),
            ("Customs brokerage", 1, 220.00),
            ("Warehousing, 10 days", 10, 18.00),
            ("Inland trucking", 1, 380.00),
            ("Documentation fee", 1, 65.00),
        ],
    ),
    dict(
        id="09", vendor="Meridian Legal Group LLP",
        bill_to="Falcon Ridge Capital Partners, 1 Exchange Plaza, New York, NY",
        invoice_number="MLG-2026-9009", invoice_date="2026-06-20", due_date="2026-07-20",
        currency="USD", render="digital", tax_rate=0.0,
        items=[
            ("Legal counsel - M&A advisory, hrs", 80, 450.00),
            ("Due diligence review", 1, 8500.00),
        ],
    ),
    dict(
        id="10", vendor="Kestrel Design Studio",
        bill_to="Harborview Cafe, 5 Quay St, Dublin",
        invoice_number="KDS-2026-1010", invoice_date="2026-06-18", due_date=None,
        currency="EUR", render="digital", tax_rate=0.23,
        items=[
            ("Logo design package", 1, 650.00),
            ("Brand guidelines document", 1, 380.00),
            ("Business card design", 1, 120.00),
        ],
    ),
    dict(
        id="11", vendor="Cascade Janitorial Services",
        bill_to="Summit Office Park, 200 Ridge Rd, Spokane, WA",
        invoice_number="CJS-2026-1111", invoice_date="2026-06-25", due_date="2026-07-25",
        currency="USD", render="scanned", tax_rate=0.081,
        items=[
            ("Nightly office cleaning - June", 22, 45.00),
            ("Carpet deep clean", 1, 220.00),
            ("Window washing, exterior", 1, 175.00),
            ("Supplies restock", 1, 60.00),
        ],
    ),
    dict(
        id="12", vendor="Alpine Ski Rentals",
        bill_to="Meadowbrook Lodge, 10 Piste Rd, Chamonix",
        invoice_number="ASR-2026-1212", invoice_date="2026-01-15", due_date="2026-02-14",
        currency="EUR", render="scanned", tax_rate=0.20,
        items=[
            ("Ski equipment rental, 5 days", 5, 45.00),
            ("Helmet & boot rental, 5 days", 5, 18.00),
        ],
    ),
    dict(
        id="13", vendor="Quarry Stone & Gravel Co.",
        bill_to="Redstone Landscaping, 88 Hilltop Dr, Asheville, NC",
        invoice_number="QSG-2026-1313", invoice_date="2026-07-02", due_date=None,
        currency="USD", render="scanned", tax_rate=0.07,
        items=[
            ("Crushed gravel, per ton", 12, 38.00),
            ("River rock, per ton", 6, 52.00),
            ("Delivery fee", 1, 85.00),
            ("Screened topsoil, per yard", 8, 28.00),
            ("Equipment rental - loader, half day", 1, 150.00),
        ],
    ),
    dict(
        id="14", vendor="Beacon IT Staffing",
        bill_to="Redwood Financial Services, 300 Market St, San Francisco, CA",
        invoice_number="BIS-2026-1414", invoice_date="2026-06-28", due_date="2026-07-28",
        currency="USD", render="digital", tax_rate=0.0,
        items=[
            ("Contract developer - hrs", 37.5, 95.00),
            ("QA engineer - hrs", 22.25, 78.00),
            ("Project management - hrs", 12.5, 110.00),
        ],
    ),
    dict(
        id="15", vendor="Orchid Catering & Events",
        bill_to="Willowmere Events Center, 45 Garden Ln, Charleston, SC",
        invoice_number="OCE-2026-1515", invoice_date="2026-07-10", due_date="2026-08-09",
        currency="USD", render="digital", tax_rate=0.08875,
        items=[
            (
                "Plated dinner service for 120 guests, three-course menu "
                "with vegetarian option",
                120, 68.00,
            ),
            (
                "Signature cocktail bar package, four-hour open bar with "
                "two bartenders",
                1, 2400.00,
            ),
            (
                "Floral centerpieces and table linens, garden-themed "
                "arrangement",
                12, 85.00,
            ),
            (
                "Event staffing - servers and coordination, six-hour shift",
                8, 45.00,
            ),
        ],
    ),
    dict(
        id="16", vendor="Vertex Chemical Supply",
        bill_to="Coreline Manufacturing, 700 Foundry Rd, Pittsburgh, PA",
        ship_to="Coreline Manufacturing - Plant 2, 1500 Furnace Blvd, Pittsburgh, PA",
        invoice_number="VCS-2026-1616", invoice_date="2026-07-03", due_date="2026-08-02",
        currency="USD", render="digital", tax_rate=0.06,
        header_extras={"PO#": "PO-88213", "Account#": "ACCT-4471", "Terms": "Net 30"},
        items=[
            ("Industrial solvent, 55gal drum", 4, 310.00),
            ("Safety data sheet processing fee", 1, 75.00),
            ("Hazmat shipping surcharge", 1, 120.00),
        ],
    ),
    dict(
        id="17", vendor="Lumen Print Works",
        bill_to="Ashgrove Publishing House, 9 Fleet Lane, Manchester",
        invoice_number="LPW-2026-1717", invoice_date="2026-06-15", due_date=None,
        currency="GBP", render="digital", tax_rate=0.20,
        date_format="%d %B %Y",
        items=[
            ("Offset printing - 5000 units", 5000, 0.18),
            ("Perfect binding & trim", 1, 220.00),
        ],
    ),
    dict(
        id="18", vendor="Sable & Finch Bookbinding",
        bill_to="Ninth Street Rare Books, 14 Antiquarian Row, Boston, MA",
        invoice_number="SFB-2026-1818", invoice_date="2026-06-30", due_date="2026-07-30",
        currency="USD", render="digital", tax_rate=0.0,
        items=[
            ("Archival paper stock, sheet", 12000, 0.0325),
        ],
    ),
    dict(
        id="19", vendor="Copperline Electrical",
        bill_to="Brightwater Apartments, 500 Copperline Way, Austin, TX",
        invoice_number="CLE-2026-1919", invoice_date="2026-07-14", due_date="2026-08-13",
        currency="USD", render="digital", tax_rate=0.0825,
        items=[
            ("Panel upgrade - 200A service", 1, 1850.00),
            ("Circuit breaker, 20A", 8, 22.00),
            ("Romex wire, 12/2, per ft", 400, 0.85),
            ("Outlet installation, standard", 24, 35.00),
            ("GFCI outlet installation", 6, 55.00),
            ("Light fixture installation", 10, 65.00),
            ("Conduit, per ft", 150, 2.10),
            ("Permit & inspection fee", 1, 220.00),
            ("Labor - master electrician, hrs", 40, 95.00),
        ],
    ),
    dict(
        id="20", vendor="Tanaka Precision KK",
        bill_to="Pacific Rim Manufacturing, 500 Harbor Blvd, Oakland, CA",
        invoice_number="TPK-2026-2020", invoice_date="2026-06-22", due_date="2026-07-22",
        currency="JPY", render="digital", tax_rate=0.10,
        items=[
            ("Precision bearing assembly, unit", 50, 3200),
            ("CNC machining service, hrs", 20, 8500),
            ("Quality inspection & certification", 1, 45000),
        ],
    ),
]


# --- shared math / drawing ---------------------------------------------------


def _totals(items: list[tuple[str, float, float]], tax_rate: float) -> tuple[float, float, float]:
    subtotal = round(sum(qty * price for _, qty, price in items), 2)
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    return subtotal, tax, total


def _fmt_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS[currency]
    decimals = CURRENCY_DECIMALS[currency]
    return f"{symbol}{amount:,.{decimals}f}"


def _fmt_date_display(iso_date: str, date_format: str | None) -> str:
    if date_format is None:
        return iso_date
    return _date.fromisoformat(iso_date).strftime(date_format)


def _draw_header_row(c: canvas.Canvas, y: float) -> float:
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1 * inch, y, "Description")
    c.drawString(4.3 * inch, y, "Qty")
    c.drawString(5.0 * inch, y, "Unit Price")
    c.drawString(6.2 * inch, y, "Amount")
    y -= 0.15 * inch
    c.line(1 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch
    c.setFont("Helvetica", 10)
    return y


def _draw_invoice(c: canvas.Canvas, inv: dict) -> None:
    width, height = letter
    currency = inv["currency"]
    date_format = inv.get("date_format")
    y = height - 1 * inch

    c.setFont("Helvetica-Bold", 18)
    c.drawString(1 * inch, y, inv["vendor"])
    y -= 0.35 * inch

    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, f"Invoice #: {inv['invoice_number']}")
    y -= 0.2 * inch
    c.drawString(1 * inch, y, f"Invoice Date: {_fmt_date_display(inv['invoice_date'], date_format)}")
    y -= 0.2 * inch
    if inv.get("due_date"):
        c.drawString(1 * inch, y, f"Due Date: {_fmt_date_display(inv['due_date'], date_format)}")
        y -= 0.2 * inch

    header_extras = inv.get("header_extras")
    if header_extras:
        for label, value in header_extras.items():
            c.drawString(1 * inch, y, f"{label}: {value}")
            y -= 0.2 * inch

    y -= 0.15 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(1 * inch, y, "Bill To:")
    y -= 0.2 * inch
    c.setFont("Helvetica", 10)
    c.drawString(1 * inch, y, inv["bill_to"])
    y -= 0.3 * inch

    if inv.get("ship_to"):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(1 * inch, y, "Ship To:")
        y -= 0.2 * inch
        c.setFont("Helvetica", 10)
        c.drawString(1 * inch, y, inv["ship_to"])
        y -= 0.3 * inch

    y -= 0.1 * inch
    y = _draw_header_row(c, y)

    items_on_page = 0
    for desc, qty, price in inv["items"]:
        if items_on_page >= ITEMS_PER_PAGE:
            c.showPage()
            y = height - 1 * inch
            y = _draw_header_row(c, y)
            items_on_page = 0
        amount = round(qty * price, 2)
        c.drawString(1 * inch, y, desc)
        c.drawRightString(4.7 * inch, y, f"{qty:g}")
        c.drawRightString(5.9 * inch, y, _fmt_money(price, currency))
        c.drawRightString(7.3 * inch, y, _fmt_money(amount, currency))
        y -= 0.25 * inch
        items_on_page += 1

    y -= 0.1 * inch
    c.line(5.0 * inch, y, 7.3 * inch, y)
    y -= 0.25 * inch

    subtotal, tax, total = _totals(inv["items"], inv["tax_rate"])
    c.drawString(5.0 * inch, y, "Subtotal:")
    c.drawRightString(7.3 * inch, y, _fmt_money(subtotal, currency))
    y -= 0.22 * inch
    c.drawString(5.0 * inch, y, f"Tax ({inv['tax_rate'] * 100:.3f}%):")
    c.drawRightString(7.3 * inch, y, _fmt_money(tax, currency))
    y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(5.0 * inch, y, "Total:")
    c.drawRightString(7.3 * inch, y, _fmt_money(total, currency))


def _make_digital_pdf(inv: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=letter)
    _draw_invoice(c, inv)
    c.showPage()
    c.save()


def _make_scanned_pdf(inv: dict, out_path: Path) -> None:
    """Render to raster image(s) and embed only those - no text layer.
    Handles multi-page invoices too, unlike the Phase 1 sample generator
    (none of our scanned specs need >1 page, but it's a one-line fix)."""
    from PIL import Image

    import fitz  # PyMuPDF

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _draw_invoice(c, inv)
    c.showPage()
    c.save()

    buf.seek(0)
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    doc.close()

    if len(images) == 1:
        images[0].save(out_path, "PDF")
    else:
        images[0].save(out_path, "PDF", save_all=True, append_images=images[1:])


# --- gold + manifest ----------------------------------------------------------


def _build_gold(inv: dict) -> dict:
    subtotal, tax, total = _totals(inv["items"], inv["tax_rate"])
    line_items = [
        {
            "description": desc,
            "quantity": float(qty),
            "unit_price": float(price),
            "amount": round(qty * price, 2),
        }
        for desc, qty, price in inv["items"]
    ]
    return {
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date"],
        "vendor_name": inv["vendor"],
        "bill_to": inv["bill_to"],
        "line_items": line_items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "due_date": inv.get("due_date"),
        "currency": inv["currency"],
    }


def _slug(vendor: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in vendor).strip("_")[:30].strip("_")


def main() -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)

    golds = {spec["id"]: _build_gold(spec) for spec in SPECS}

    all_valid = True
    for spec in SPECS:
        result = validate_invoice(golds[spec["id"]])
        print(f"  gold check {spec['id']}: {'OK' if result.passed else result.flags}")
        all_valid = all_valid and result.passed

    if not all_valid:
        raise SystemExit(
            "One or more gold invoices failed validate_invoice() - fix the spec before rendering."
        )
    print(f"{len(SPECS)}/{len(SPECS)} gold invoices valid\n")

    rows = []
    for spec in SPECS:
        filename = f"eval_{spec['id']}_{_slug(spec['vendor'])}.pdf"
        out_path = EVAL_DIR / filename
        if spec["render"] == "scanned":
            _make_scanned_pdf(spec, out_path)
        else:
            _make_digital_pdf(spec, out_path)
        print(f"Wrote {out_path}")

        rows.append(
            {
                "id": spec["id"],
                "pdf_path": f"data/eval/{filename}",
                "tags": {"render": spec["render"], "currency": spec["currency"]},
                "gold": golds[spec["id"]],
            }
        )

    with open(DATASET_PATH, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"\nWrote {DATASET_PATH} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
