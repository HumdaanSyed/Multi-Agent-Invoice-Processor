"""Tests for the parts of invoice_agent.db that don't require a live Supabase
connection. insert_invoice/is_duplicate/upload_pdf need a real client and are
exercised manually (see docs/ROADMAP.md Phase 4 done-criteria), not here.
"""

import csv

from invoice_agent.db import export_invoice_csv

INVOICE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-07-01",
    "vendor_name": "Acme Co",
    "bill_to": "Someone",
    "line_items": [
        {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0},
        {"description": "Gadget", "quantity": 1, "unit_price": 3.0, "amount": 3.0},
    ],
    "subtotal": 13.0,
    "tax": 1.04,
    "total": 14.04,
    "due_date": "2026-07-31",
    "currency": "USD",
}


def test_export_writes_one_row_per_line_item(tmp_path):
    out_path = tmp_path / "invoices.csv"
    export_invoice_csv(INVOICE, out_path)

    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    assert rows[0]["invoice_number"] == "INV-1"
    assert rows[0]["line_item_description"] == "Widget"
    assert rows[1]["line_item_description"] == "Gadget"


def test_export_appends_without_duplicating_header(tmp_path):
    out_path = tmp_path / "invoices.csv"
    export_invoice_csv(INVOICE, out_path)
    other = {**INVOICE, "invoice_number": "INV-2"}
    export_invoice_csv(other, out_path)

    with open(out_path) as f:
        lines = f.readlines()

    header_lines = [line for line in lines if line.startswith("invoice_number,")]
    assert len(header_lines) == 1
    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4  # 2 line items x 2 invoices
