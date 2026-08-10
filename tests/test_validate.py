"""Tests for deterministic invoice validation (no API calls)."""

from invoice_agent.validate import validate_invoice

GOOD_INVOICE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-07-01",
    "vendor_name": "Acme Co",
    "bill_to": "Someone",
    "line_items": [
        {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0},
    ],
    "subtotal": 10.0,
    "tax": 0.8,
    "total": 10.8,
    "due_date": "2026-07-31",
    "currency": "USD",
}


def test_clean_invoice_passes():
    result = validate_invoice(GOOD_INVOICE)
    assert result.passed
    assert result.flags == []
    assert result.needs_review is False


def test_broken_total_is_flagged():
    broken = {**GOOD_INVOICE, "total": 999.99}
    result = validate_invoice(broken)
    assert not result.passed
    assert result.needs_review is True
    assert any("Subtotal + tax" in flag for flag in result.flags)


def test_broken_line_item_sum_is_flagged():
    broken = {**GOOD_INVOICE, "subtotal": 50.0, "total": 50.8}
    result = validate_invoice(broken)
    assert not result.passed
    assert any("Line items sum" in flag for flag in result.flags)


def test_due_date_before_invoice_date_is_flagged():
    broken = {**GOOD_INVOICE, "due_date": "2026-06-01"}
    result = validate_invoice(broken)
    assert not result.passed
    assert any("before invoice_date" in flag for flag in result.flags)


def test_unparseable_date_is_flagged():
    broken = {**GOOD_INVOICE, "invoice_date": "not-a-date"}
    result = validate_invoice(broken)
    assert not result.passed
    assert any("not a valid ISO 8601 date" in flag for flag in result.flags)
