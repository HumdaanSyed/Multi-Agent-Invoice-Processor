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
    """Outcome of running business-rule checks against an extracted invoice.

    `checks` (Phase 8.5) is additive - the per-rule detail behind `flags`,
    for a UI that wants to show every check (including the ones that
    passed), not just the failures. `passed` / `flags` / `needs_review`
    keep their exact original meaning and are derived from `checks`, not
    computed separately, so the two can never disagree.
    """

    passed: bool
    flags: list[str] = Field(default_factory=list)
    needs_review: bool
    checks: list["CheckResult"] = Field(default_factory=list)


class CheckResult(BaseModel):
    """One validation rule's outcome.

    `check_id` is a stable identifier a frontend can key off to map a check
    to a label/ordering across streamed events - renaming one silently
    breaks that mapping, so treat these six strings as a public contract,
    not an implementation detail.
    """

    check_id: str
    label: str
    passed: bool
    detail: Optional[str] = None
    skipped: bool = False


ValidationResult.model_rebuild()

CheckCallback = Callable[[CheckResult], None]


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


def run_checks(
    inv: Invoice,
    duplicate_checker: Optional[DuplicateChecker] = None,
    on_check: Optional[CheckCallback] = None,
) -> list[CheckResult]:
    """Run every business-rule check and return one CheckResult per rule,
    in a stable order. `on_check`, if given, fires once per check as it
    completes (Phase 8.5's live validation panel) - checks run in
    milliseconds, so a client wanting to show them resolving one at a time
    staggers the *reveal* itself; nothing here adds delay to achieve that
    (REVIEW.md: artificial backend delays are a defect, not a feature).

    Each check's `detail` string is byte-identical to what this module has
    always produced in `ValidationResult.flags` - existing tests assert
    exact flag text, and that must keep working unchanged.
    """
    # Shared intermediate values, computed once up front - due_date_valid
    # and due_date_after_invoice_date both need parsed dates, and
    # line_items_sum/totals_match both need rounded sums.
    line_item_sum = round(sum(item.amount for item in inv.line_items), 2)
    expected_total = round(inv.subtotal + inv.tax, 2)
    invoice_date = parse_iso_date(inv.invoice_date)
    due_date = parse_iso_date(inv.due_date) if inv.due_date is not None else None

    line_items_ok = abs(line_item_sum - inv.subtotal) <= TOLERANCE
    totals_ok = abs(expected_total - inv.total) <= TOLERANCE
    invoice_date_ok = invoice_date is not None
    due_date_ok = due_date is not None if inv.due_date is not None else None  # None = not applicable
    order_ok = due_date >= invoice_date if invoice_date is not None and due_date is not None else None
    is_dup = duplicate_checker(inv.vendor_name, inv.invoice_number) if duplicate_checker is not None else None

    # One row per check: (check_id, label, passed, detail-if-failed,
    # applicable). `applicable=False` means "not applicable to this
    # invoice" (no due_date, no duplicate_checker given) - `skipped=True`
    # on the resulting CheckResult, not a pass/fail judgment at all.
    rows: list[tuple[str, str, bool, str, bool]] = [
        (
            "line_items_sum",
            "Line items sum to subtotal",
            line_items_ok,
            f"Line items sum to {line_item_sum:.2f} but subtotal is {inv.subtotal:.2f}",
            True,
        ),
        (
            "totals_match",
            "Subtotal plus tax equals total",
            totals_ok,
            f"Subtotal + tax = {expected_total:.2f} but total is {inv.total:.2f}",
            True,
        ),
        (
            "invoice_date_valid",
            "Invoice date is a valid date",
            invoice_date_ok,
            f"invoice_date '{inv.invoice_date}' is not a valid ISO 8601 date",
            True,
        ),
        (
            "due_date_valid",
            "Due date is a valid date",
            True if due_date_ok is None else due_date_ok,
            f"due_date '{inv.due_date}' is not a valid ISO 8601 date",
            due_date_ok is not None,
        ),
        (
            "due_date_after_invoice_date",
            "Due date is on or after invoice date",
            True if order_ok is None else order_ok,
            f"due_date {inv.due_date} is before invoice_date {inv.invoice_date}",
            order_ok is not None,
        ),
        (
            "not_duplicate",
            "Not a duplicate of an existing invoice",
            True if is_dup is None else not is_dup,
            f"Possible duplicate: vendor={inv.vendor_name!r} number={inv.invoice_number!r}",
            is_dup is not None,
        ),
    ]

    results: list[CheckResult] = []
    for check_id, label, passed, detail_if_failed, applicable in rows:
        result = CheckResult(
            check_id=check_id,
            label=label,
            passed=passed,
            detail=None if passed or not applicable else detail_if_failed,
            skipped=not applicable,
        )
        results.append(result)
        if on_check is not None:
            on_check(result)

    return results


def validate_invoice(
    invoice: dict | Invoice,
    duplicate_checker: Optional[DuplicateChecker] = None,
    on_check: Optional[CheckCallback] = None,
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

    `on_check`, if given, is forwarded to `run_checks` - see its docstring.
    """
    inv = invoice if isinstance(invoice, Invoice) else Invoice.model_validate(invoice)
    checks = run_checks(inv, duplicate_checker=duplicate_checker, on_check=on_check)

    flags = [c.detail for c in checks if not c.passed and c.detail is not None]
    passed = len(flags) == 0
    return ValidationResult(passed=passed, flags=flags, needs_review=not passed, checks=checks)
