"""Deterministic business-rule validation for extracted invoices.

All checks here are plain Python - math, date parsing, duplicate lookup.
None of this is delegated to the LLM.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from invoice_agent.schema import Invoice

TOLERANCE = 0.01


class ValidationResult(BaseModel):
    """Outcome of running business-rule checks against an extracted invoice."""

    passed: bool
    flags: list[str] = Field(default_factory=list)
    needs_review: bool


def is_duplicate(vendor_name: str, invoice_number: str) -> bool:
    """Placeholder duplicate check.

    Phase 4 wires this to a Supabase lookup on (vendor_name, invoice_number).
    Always returns False until then.
    """
    return False


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_invoice(invoice: dict | Invoice) -> ValidationResult:
    """Run deterministic checks against an extracted invoice.

    Rules:
      - line items sum to subtotal (within $0.01)
      - subtotal + tax == total (within $0.01)
      - invoice_date and due_date (if present) are parseable ISO dates
      - due_date >= invoice_date
      - vendor_name/invoice_number is not a known duplicate (placeholder)
    """
    inv = invoice if isinstance(invoice, Invoice) else Invoice.model_validate(invoice)
    flags: list[str] = []

    line_item_sum = round(sum(item.amount for item in inv.line_items), 2)
    if abs(line_item_sum - inv.subtotal) > TOLERANCE:
        flags.append(
            f"Line items sum to {line_item_sum:.2f} but subtotal is {inv.subtotal:.2f}"
        )

    expected_total = round(inv.subtotal + inv.tax, 2)
    if abs(expected_total - inv.total) > TOLERANCE:
        flags.append(
            f"Subtotal + tax = {expected_total:.2f} but total is {inv.total:.2f}"
        )

    invoice_date = _parse_date(inv.invoice_date)
    if invoice_date is None:
        flags.append(f"invoice_date '{inv.invoice_date}' is not a valid ISO 8601 date")

    due_date = None
    if inv.due_date is not None:
        due_date = _parse_date(inv.due_date)
        if due_date is None:
            flags.append(f"due_date '{inv.due_date}' is not a valid ISO 8601 date")

    if invoice_date is not None and due_date is not None and due_date < invoice_date:
        flags.append(f"due_date {inv.due_date} is before invoice_date {inv.invoice_date}")

    if is_duplicate(inv.vendor_name, inv.invoice_number):
        flags.append(f"Possible duplicate: vendor={inv.vendor_name!r} number={inv.invoice_number!r}")

    passed = len(flags) == 0
    return ValidationResult(passed=passed, flags=flags, needs_review=not passed)
