"""Tests for frontend.forms - offline, pure functions, no Streamlit."""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from invoice_agent.db import CSV_FIELDS
from frontend.forms import (
    build_corrections,
    invoice_to_csv_bytes,
    line_items_to_rows,
    parse_iso_date,
    rows_to_line_items,
)

GOOD_INVOICE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-01-01",
    "vendor_name": "Acme Corp",
    "bill_to": "Widgets Inc",
    "line_items": [
        {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0},
        {"description": "Gadget", "quantity": 1, "unit_price": 3.5, "amount": 3.5},
    ],
    "subtotal": 13.5,
    "tax": 0.0,
    "total": 13.5,
    "due_date": None,
    "currency": "USD",
}


# --- parse_iso_date --------------------------------------------------------


def test_parse_iso_date_valid():
    assert parse_iso_date("2026-07-01") == date(2026, 7, 1)


@pytest.mark.parametrize("value", [None, ""])
def test_parse_iso_date_empty_returns_none(value):
    assert parse_iso_date(value) is None


def test_parse_iso_date_non_iso_returns_none_not_raises():
    # validate_invoice() itself flags this as invalid, so it's a real
    # value the interrupt payload can legitimately hand us.
    assert parse_iso_date("15 June 2026") is None


# --- line_items_to_rows / rows_to_line_items --------------------------


def test_line_items_to_rows_roundtrips():
    rows = line_items_to_rows(GOOD_INVOICE["line_items"])
    assert rows == [
        {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0},
        {"description": "Gadget", "quantity": 1, "unit_price": 3.5, "amount": 3.5},
    ]
    back = rows_to_line_items(rows)
    assert back == GOOD_INVOICE["line_items"]


def test_rows_to_line_items_drops_fully_blank_row():
    rows = [
        {"description": "Widget", "quantity": 1, "unit_price": 1.0, "amount": 1.0},
        {"description": None, "quantity": None, "unit_price": None, "amount": None},
    ]
    result = rows_to_line_items(rows)
    assert len(result) == 1
    assert result[0]["description"] == "Widget"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_rows_to_line_items_rejects_non_finite_numbers(bad_value):
    rows = [{"description": "Widget", "quantity": bad_value, "unit_price": 1.0, "amount": 1.0}]
    with pytest.raises(ValueError, match="finite"):
        rows_to_line_items(rows)


def test_rows_to_line_items_rejects_missing_number_with_description_present():
    rows = [{"description": "Widget", "quantity": None, "unit_price": 1.0, "amount": 1.0}]
    with pytest.raises(ValueError, match="quantity"):
        rows_to_line_items(rows)


def test_rows_to_line_items_rejects_missing_description_with_numbers_present():
    rows = [{"description": "", "quantity": 1.0, "unit_price": 1.0, "amount": 1.0}]
    with pytest.raises(ValueError, match="description"):
        rows_to_line_items(rows)


# --- build_corrections ------------------------------------------------


def test_build_corrections_emits_all_ten_fields():
    result = build_corrections(
        invoice_number="INV-1",
        invoice_date="2026-01-01",
        vendor_name="Acme Corp",
        bill_to="Widgets Inc",
        line_items=GOOD_INVOICE["line_items"],
        subtotal=13.5,
        tax=0.0,
        total=13.5,
        due_date=None,
        currency="USD",
    )
    assert set(result.keys()) == {
        "invoice_number",
        "invoice_date",
        "vendor_name",
        "bill_to",
        "line_items",
        "subtotal",
        "tax",
        "total",
        "due_date",
        "currency",
    }
    assert result["due_date"] is None  # explicit None round-trips, not omitted


def test_build_corrections_due_date_set():
    result = build_corrections(
        invoice_number="INV-1",
        invoice_date="2026-01-01",
        vendor_name="Acme Corp",
        bill_to="Widgets Inc",
        line_items=[],
        subtotal=0.0,
        tax=0.0,
        total=0.0,
        due_date="2026-02-01",
        currency="USD",
    )
    assert result["due_date"] == "2026-02-01"


# --- invoice_to_csv_bytes -----------------------------------------------


def test_invoice_to_csv_bytes_header_matches_db_csv_fields():
    csv_bytes = invoice_to_csv_bytes(GOOD_INVOICE)
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
    header = next(reader)
    assert header == CSV_FIELDS


def test_invoice_to_csv_bytes_one_row_per_line_item():
    csv_bytes = invoice_to_csv_bytes(GOOD_INVOICE)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert len(rows) == 2
    assert rows[0]["vendor_name"] == "Acme Corp"
    assert rows[0]["line_item_description"] == "Widget"
    assert rows[1]["line_item_description"] == "Gadget"
    # Header fields repeat identically across every line-item row.
    assert rows[0]["invoice_number"] == rows[1]["invoice_number"] == "INV-1"


def test_invoice_to_csv_bytes_zero_line_items_still_emits_one_row():
    invoice = {**GOOD_INVOICE, "line_items": []}
    csv_bytes = invoice_to_csv_bytes(invoice)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["line_item_description"] == ""
