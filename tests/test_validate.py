"""Tests for deterministic invoice validation (no API calls)."""

from datetime import date

import pytest

from invoice_agent.schema import Invoice
from invoice_agent.validate import CheckResult, parse_iso_date, run_checks, validate_invoice

EXPECTED_CHECK_IDS = [
    "line_items_sum",
    "totals_match",
    "invoice_date_valid",
    "due_date_valid",
    "due_date_after_invoice_date",
    "not_duplicate",
]

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


def test_parse_iso_date_valid():
    assert parse_iso_date("2026-07-01") == date(2026, 7, 1)


@pytest.mark.parametrize("value", [None, "", "15 June 2026", "2026-13-40", "not-a-date"])
def test_parse_iso_date_empty_or_non_iso_returns_none_never_raises(value):
    assert parse_iso_date(value) is None


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


def test_no_duplicate_checker_by_default():
    """No duplicate_checker passed -> no duplicate flag, no crash, no network."""
    result = validate_invoice(GOOD_INVOICE)
    assert result.passed


def test_duplicate_checker_flags_when_it_returns_true():
    result = validate_invoice(GOOD_INVOICE, duplicate_checker=lambda vendor, number: True)
    assert not result.passed
    assert any("duplicate" in flag.lower() for flag in result.flags)


def test_duplicate_checker_receives_vendor_and_invoice_number():
    seen = {}

    def checker(vendor_name: str, invoice_number: str) -> bool:
        seen["vendor_name"] = vendor_name
        seen["invoice_number"] = invoice_number
        return False

    validate_invoice(GOOD_INVOICE, duplicate_checker=checker)
    assert seen == {"vendor_name": "Acme Co", "invoice_number": "INV-1"}


# --- run_checks / CheckResult (Phase 8.5) -----------------------------------


def test_run_checks_returns_stable_ids_in_order():
    checks = run_checks(Invoice.model_validate(GOOD_INVOICE))
    assert [c.check_id for c in checks] == EXPECTED_CHECK_IDS


def test_run_checks_all_pass_for_clean_invoice_with_duplicate_checker():
    checks = run_checks(Invoice.model_validate(GOOD_INVOICE), duplicate_checker=lambda v, n: False)
    assert all(c.passed for c in checks)
    assert all(not c.skipped for c in checks)  # every rule is applicable - due_date is set, checker is given


def test_run_checks_skips_due_date_checks_when_absent():
    no_due_date = {**GOOD_INVOICE, "due_date": None}
    checks = run_checks(Invoice.model_validate(no_due_date))
    by_id = {c.check_id: c for c in checks}
    assert by_id["due_date_valid"].skipped is True
    assert by_id["due_date_valid"].passed is True
    assert by_id["due_date_after_invoice_date"].skipped is True
    assert by_id["due_date_after_invoice_date"].passed is True


def test_run_checks_skips_duplicate_check_when_no_checker():
    checks = run_checks(Invoice.model_validate(GOOD_INVOICE))
    by_id = {c.check_id: c for c in checks}
    assert by_id["not_duplicate"].skipped is True
    assert by_id["not_duplicate"].passed is True


def test_run_checks_on_check_callback_fires_once_per_check_in_order():
    seen: list[str] = []
    run_checks(Invoice.model_validate(GOOD_INVOICE), on_check=lambda check: seen.append(check.check_id))
    assert seen == EXPECTED_CHECK_IDS


def test_validation_result_checks_field_matches_flags_exactly():
    """checks (Phase 8.5, additive) must never disagree with the original
    passed/flags/needs_review fields - flags is derived from checks, not
    computed separately."""
    broken = {**GOOD_INVOICE, "total": 999.99, "subtotal": 50.0}
    result = validate_invoice(broken)
    checks_detail = [c.detail for c in result.checks if not c.passed and c.detail is not None]
    assert checks_detail == result.flags
    assert result.needs_review is (len(result.flags) > 0)


def test_check_result_detail_text_matches_legacy_flag_text_exactly():
    """Every existing flag string this module has always produced must
    survive byte-identical through the CheckResult refactor - a frontend
    keys UI copy off check_id/label now, but any caller still reading
    ValidationResult.flags (evals/scoring.py, tests/test_api_routes.py)
    must see the exact same text as before Phase 8.5."""
    broken = {
        **GOOD_INVOICE,
        "subtotal": 50.0,
        "total": 999.99,
        "invoice_date": "not-a-date",
        "due_date": "2026-06-01",
    }
    result = validate_invoice(broken, duplicate_checker=lambda v, n: True)
    assert "Line items sum to 10.00 but subtotal is 50.00" in result.flags
    assert "Subtotal + tax = 50.80 but total is 999.99" in result.flags
    assert "invoice_date 'not-a-date' is not a valid ISO 8601 date" in result.flags
    assert "Possible duplicate: vendor='Acme Co' number='INV-1'" in result.flags


def test_check_result_is_a_pydantic_model_with_expected_shape():
    check = CheckResult(check_id="x", label="X", passed=False, detail="bad")
    dumped = check.model_dump()
    assert dumped == {"check_id": "x", "label": "X", "passed": False, "detail": "bad", "skipped": False}
