"""Validation tests for the Pydantic data contracts (no API calls)."""

import pytest
from pydantic import ValidationError

from invoice_agent.schema import Invoice, LineItem


def test_invoice_valid_minimal():
    invoice = Invoice(
        invoice_number="INV-1",
        invoice_date="2026-07-01",
        vendor_name="Acme Co",
        bill_to="Someone",
        line_items=[LineItem(description="Widget", quantity=1, unit_price=10.0, amount=10.0)],
        subtotal=10.0,
        tax=0.8,
        total=10.8,
        currency="USD",
    )
    assert invoice.due_date is None
    assert invoice.line_items[0].amount == 10.0


def test_invoice_missing_required_field_raises():
    with pytest.raises(ValidationError):
        Invoice(
            invoice_date="2026-07-01",
            vendor_name="Acme Co",
            bill_to="Someone",
            line_items=[],
            subtotal=0.0,
            tax=0.0,
            total=0.0,
            currency="USD",
        )
