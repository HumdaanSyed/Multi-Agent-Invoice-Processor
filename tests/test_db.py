"""Tests for the parts of invoice_agent.db that don't require a live Supabase
connection. insert_invoice/is_duplicate/upload_pdf/insert_export_row/
iter_export_rows/export_status all call get_client() and need a real client -
exercised manually (see docs/ROADMAP.md's Phase 4 done-criteria and
docs/FRONTEND_PLAN.md's Phase 8.6 done-criteria), not here.
"""

from invoice_agent.db import CSV_FIELDS, LEDGER_CSV_FIELDS, export_ledger_row_to_csv_rows, invoice_csv_rows

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

# A ledger row, once fetched back from Supabase, has every key a plain
# invoice dict has (insert_export_row writes exactly _HEADER_FIELDS +
# line_items) plus its own id/written_at/thread_id columns.
LEDGER_ROW = {
    "id": 1,
    "written_at": "2026-07-02T10:00:00+00:00",
    "thread_id": "t-abc",
    **INVOICE,
}


def test_ledger_csv_fields_is_provenance_plus_csv_fields():
    """LEDGER_CSV_FIELDS must stay derived from CSV_FIELDS, not a separately
    hand-typed list - see export_ledger_row_to_csv_rows's docstring."""
    assert LEDGER_CSV_FIELDS == ["written_at", "thread_id"] + CSV_FIELDS


def test_export_ledger_row_to_csv_rows_one_row_per_line_item():
    rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    assert len(rows) == 2
    assert rows[0]["invoice_number"] == "INV-1"
    assert rows[0]["line_item_description"] == "Widget"
    assert rows[1]["line_item_description"] == "Gadget"


def test_export_ledger_row_to_csv_rows_adds_provenance_to_every_row():
    rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    for row in rows:
        assert row["written_at"] == "2026-07-02T10:00:00+00:00"
        assert row["thread_id"] == "t-abc"


def test_export_ledger_row_to_csv_rows_matches_invoice_csv_rows_shape():
    """The ledger's CSV output can't silently diverge from the frontend's
    per-invoice CSV download - both must build the same per-line-item
    fields from invoice_csv_rows()."""
    ledger_rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    plain_rows = invoice_csv_rows(LEDGER_ROW)

    assert len(ledger_rows) == len(plain_rows)
    for ledger_row, plain_row in zip(ledger_rows, plain_rows):
        for field in CSV_FIELDS:
            assert ledger_row[field] == plain_row[field]
