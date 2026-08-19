"""Deterministic business-rule validation for extracted invoices.

All checks here are plain Python - math, date parsing, duplicate lookup.
None of this is delegated to the LLM.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from pydantic import BaseModel, Field

from invoice_agent.schema import Invoice

TOLERANCE = 0.01

DuplicateChecker = Callable[[str, str], bool]


class ValidationResult(BaseModel):
    """Outcome of running business-rule checks against an extracted invoice."""

    passed: bool
    flags: list[str] = Field(default_factory=list)
    needs_review: bool


def parse_iso_date(value: str | None) -> date | None:
    """Best-effort ISO 8601 (YYYY-MM-DD) parse - `None` for an empty/missing
    value or a non-ISO string alike, never raises. Public (not prefixed
    `_parse_date`) because `frontend/forms.py` reuses this exact parser
    rather than hand-duplicating it - the frontend needs to know the same
    thing this module does about whether a date is genuinely ISO before
    deciding how to render/re-submit it."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_invoice(
    invoice: dict | Invoice,
    duplicate_checker: Optional[DuplicateChecker] = None,
) -> ValidationResult:
    """Run deterministic checks against an extracted invoice.

    Rules:
      - line items sum to subtotal (within $0.01)
      - subtotal + tax == total (within $0.01)
      - invoice_date and due_date (if present) are parseable ISO dates
      - due_date >= invoice_date
      - vendor_name/invoice_number is not a known duplicate

    `duplicate_checker(vendor_name, invoice_number) -> bool` is optional and
    defaults to no check at all, so unit tests stay deterministic and
    offline. The graph's `validator` node wires this to
    `invoice_agent.db.is_duplicate` for real runs against Supabase.
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

    invoice_date = parse_iso_date(inv.invoice_date)
    if invoice_date is None:
        flags.append(f"invoice_date '{inv.invoice_date}' is not a valid ISO 8601 date")

    due_date = None
    if inv.due_date is not None:
        due_date = parse_iso_date(inv.due_date)
        if due_date is None:
            flags.append(f"due_date '{inv.due_date}' is not a valid ISO 8601 date")

    if invoice_date is not None and due_date is not None and due_date < invoice_date:
        flags.append(f"due_date {inv.due_date} is before invoice_date {inv.invoice_date}")

    if duplicate_checker is not None and duplicate_checker(inv.vendor_name, inv.invoice_number):
        flags.append(f"Possible duplicate: vendor={inv.vendor_name!r} number={inv.invoice_number!r}")

    passed = len(flags) == 0
    return ValidationResult(passed=passed, flags=flags, needs_review=not passed)
