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
    results: list[CheckResult] = []

    def _emit(result: CheckResult) -> CheckResult:
        results.append(result)
        if on_check is not None:
            on_check(result)
        return result

    line_item_sum = round(sum(item.amount for item in inv.line_items), 2)
    line_items_ok = abs(line_item_sum - inv.subtotal) <= TOLERANCE
    _emit(
        CheckResult(
            check_id="line_items_sum",
            label="Line items sum to subtotal",
            passed=line_items_ok,
            detail=(
                None
                if line_items_ok
                else f"Line items sum to {line_item_sum:.2f} but subtotal is {inv.subtotal:.2f}"
            ),
        )
    )

    expected_total = round(inv.subtotal + inv.tax, 2)
    totals_ok = abs(expected_total - inv.total) <= TOLERANCE
    _emit(
        CheckResult(
            check_id="totals_match",
            label="Subtotal plus tax equals total",
            passed=totals_ok,
            detail=(
                None
                if totals_ok
                else f"Subtotal + tax = {expected_total:.2f} but total is {inv.total:.2f}"
            ),
        )
    )

    invoice_date = parse_iso_date(inv.invoice_date)
    invoice_date_ok = invoice_date is not None
    _emit(
        CheckResult(
            check_id="invoice_date_valid",
            label="Invoice date is a valid date",
            passed=invoice_date_ok,
            detail=(
                None
                if invoice_date_ok
                else f"invoice_date '{inv.invoice_date}' is not a valid ISO 8601 date"
            ),
        )
    )

    due_date: date | None = None
    if inv.due_date is None:
        _emit(
            CheckResult(
                check_id="due_date_valid",
                label="Due date is a valid date",
                passed=True,
                skipped=True,
            )
        )
    else:
        due_date = parse_iso_date(inv.due_date)
        due_date_ok = due_date is not None
        _emit(
            CheckResult(
                check_id="due_date_valid",
                label="Due date is a valid date",
                passed=due_date_ok,
                detail=(
                    None
                    if due_date_ok
                    else f"due_date '{inv.due_date}' is not a valid ISO 8601 date"
                ),
            )
        )

    if invoice_date is None or due_date is None:
        # Not applicable when either date failed to parse - the parse
        # failure above is already flagged; this check has nothing to add.
        _emit(
            CheckResult(
                check_id="due_date_after_invoice_date",
                label="Due date is on or after invoice date",
                passed=True,
                skipped=True,
            )
        )
    else:
        order_ok = due_date >= invoice_date
        _emit(
            CheckResult(
                check_id="due_date_after_invoice_date",
                label="Due date is on or after invoice date",
                passed=order_ok,
                detail=(
                    None
                    if order_ok
                    else f"due_date {inv.due_date} is before invoice_date {inv.invoice_date}"
                ),
            )
        )

    if duplicate_checker is None:
        _emit(
            CheckResult(
                check_id="not_duplicate",
                label="Not a duplicate of an existing invoice",
                passed=True,
                skipped=True,
            )
        )
    else:
        is_dup = duplicate_checker(inv.vendor_name, inv.invoice_number)
        _emit(
            CheckResult(
                check_id="not_duplicate",
                label="Not a duplicate of an existing invoice",
                passed=not is_dup,
                detail=(
                    None
                    if not is_dup
                    else f"Possible duplicate: vendor={inv.vendor_name!r} number={inv.invoice_number!r}"
                ),
            )
        )

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
