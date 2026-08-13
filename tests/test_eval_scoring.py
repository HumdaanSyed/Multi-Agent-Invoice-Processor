"""Tests for evals.scoring - pure functions, no API calls."""

from invoice_agent.validate import validate_invoice

from evals.scoring import (
    aggregate,
    match_line_items,
    score_document,
    token_f1,
    totals_consistent,
)

GOOD_ITEM_A = {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0}
GOOD_ITEM_B = {"description": "Gadget", "quantity": 1, "unit_price": 3.0, "amount": 3.0}

GOLD = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-07-01",
    "vendor_name": "Acme Co",
    "bill_to": "Someone, 12 Main St",
    "line_items": [GOOD_ITEM_A, GOOD_ITEM_B],
    "subtotal": 13.0,
    "tax": 1.04,
    "total": 14.04,
    "due_date": "2026-07-31",
    "currency": "USD",
}


def _perfect_prediction() -> dict:
    return {**GOLD, "line_items": [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)]}


# --- totals_consistent -------------------------------------------------------


def test_totals_consistent_true():
    assert totals_consistent(13.0, 1.04, 14.04) is True


def test_totals_consistent_false():
    assert totals_consistent(13.0, 1.04, 999.99) is False


def test_totals_consistent_none_inputs():
    assert totals_consistent(None, 1.0, 14.04) is False


def test_totals_consistent_agrees_with_validate_invoice_good():
    """Guard: if TOLERANCE or the rounding convention ever changes, this
    must fail loudly rather than the two checks silently diverging."""
    result = validate_invoice(GOLD)
    has_math_flag = any("Subtotal + tax" in f for f in result.flags)
    assert totals_consistent(GOLD["subtotal"], GOLD["tax"], GOLD["total"]) is (not has_math_flag)


def test_totals_consistent_agrees_with_validate_invoice_broken():
    broken = {**GOLD, "total": 999.99}
    result = validate_invoice(broken)
    has_math_flag = any("Subtotal + tax" in f for f in result.flags)
    assert totals_consistent(broken["subtotal"], broken["tax"], broken["total"]) is (not has_math_flag)


# --- token_f1 ----------------------------------------------------------------


def test_token_f1_identical():
    assert token_f1("acme co", "acme co") == 1.0


def test_token_f1_partial_overlap():
    score = token_f1("acme co", "acme corp")
    assert 0 < score < 1


def test_token_f1_no_overlap():
    assert token_f1("acme co", "zzz zzz") == 0.0


def test_token_f1_none_input():
    assert token_f1(None, "acme co") is None


# --- match_line_items ---------------------------------------------------------


def test_match_line_items_identical_lists():
    matches, unmatched_pred, unmatched_gold = match_line_items(
        [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)], [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)]
    )
    assert matches == [(0, 0), (1, 1)]
    assert unmatched_pred == []
    assert unmatched_gold == []


def test_match_line_items_extra_predicted_item():
    extra = {"description": "Spurious", "quantity": 1, "unit_price": 1.0, "amount": 1.0}
    matches, unmatched_pred, unmatched_gold = match_line_items(
        [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B), extra], [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)]
    )
    assert len(matches) == 2
    assert unmatched_pred == [2]
    assert unmatched_gold == []


def test_match_line_items_missing_gold_item():
    matches, unmatched_pred, unmatched_gold = match_line_items(
        [dict(GOOD_ITEM_A)], [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)]
    )
    assert len(matches) == 1
    assert unmatched_pred == []
    assert unmatched_gold == [1]


def test_match_line_items_one_reordered_row_does_not_cascade():
    """The whole point of greedy matching over positional: a single
    shifted row doesn't misattribute errors to every subsequent item."""
    matches, unmatched_pred, unmatched_gold = match_line_items(
        [dict(GOOD_ITEM_B), dict(GOOD_ITEM_A)], [dict(GOOD_ITEM_A), dict(GOOD_ITEM_B)]
    )
    assert set(matches) == {(0, 1), (1, 0)}
    assert unmatched_pred == []
    assert unmatched_gold == []


# --- score_document ------------------------------------------------------------


def test_score_document_perfect_prediction():
    result = score_document("doc-1", {}, GOLD, _perfect_prediction(), error=None)
    assert result.document_exact_match is True
    assert result.consistency_pass is True
    assert result.validation_passed is True
    assert result.line_item_count_match is True
    assert all(o.fp == 0 and o.fn == 0 for o in result.field_outcomes)


def test_score_document_wrong_scalar_field():
    pred = {**_perfect_prediction(), "vendor_name": "Wrong Vendor"}
    result = score_document("doc-1", {}, GOLD, pred, error=None)
    assert result.document_exact_match is False
    vendor_outcome = next(o for o in result.field_outcomes if o.field == "vendor_name")
    assert vendor_outcome.tp == 0
    assert vendor_outcome.fp == 1
    assert vendor_outcome.fn == 1


def test_score_document_due_date_both_none_is_true_negative():
    gold = {**GOLD, "due_date": None}
    pred = {**_perfect_prediction(), "due_date": None}
    result = score_document("doc-1", {}, gold, pred, error=None)
    due_date_outcome = next(o for o in result.field_outcomes if o.field == "due_date")
    assert due_date_outcome.tn == 1
    assert due_date_outcome.exact is True
    assert result.document_exact_match is True


def test_score_document_due_date_hallucinated_is_false_positive():
    gold = {**GOLD, "due_date": None}
    pred = {**_perfect_prediction(), "due_date": "2026-08-01"}
    result = score_document("doc-1", {}, gold, pred, error=None)
    due_date_outcome = next(o for o in result.field_outcomes if o.field == "due_date")
    assert due_date_outcome.fp == 1
    assert due_date_outcome.exact is False


def test_score_document_due_date_missed_is_false_negative():
    pred = {**_perfect_prediction(), "due_date": None}
    result = score_document("doc-1", {}, GOLD, pred, error=None)
    due_date_outcome = next(o for o in result.field_outcomes if o.field == "due_date")
    assert due_date_outcome.fn == 1
    assert due_date_outcome.fp == 0


def test_score_document_due_date_unparseable_but_present_is_false_positive_and_negative():
    """A non-empty prediction that fails to normalize (e.g. 'Q3 2026') is a
    wrong assertion, not silence - must match invoice_date's fp+fn
    convention, not be scored the same as a genuinely missing prediction."""
    pred = {**_perfect_prediction(), "due_date": "Q3 2026"}
    result = score_document("doc-1", {}, GOLD, pred, error=None)
    due_date_outcome = next(o for o in result.field_outcomes if o.field == "due_date")
    assert due_date_outcome.fp == 1
    assert due_date_outcome.fn == 1
    assert due_date_outcome.tp == 0


def test_score_document_extraction_failure_is_all_misses_no_false_positives():
    result = score_document("doc-1", {}, GOLD, None, error="API timeout")
    assert result.document_exact_match is False
    assert result.consistency_pass is False
    assert result.validation_passed is False
    assert all(o.fp == 0 for o in result.field_outcomes)
    assert any(o.fn == 1 for o in result.field_outcomes)


def test_score_document_extraction_failure_due_date_none_gold_is_true_negative():
    gold = {**GOLD, "due_date": None}
    result = score_document("doc-1", {}, gold, None, error="boom")
    due_date_outcome = next(o for o in result.field_outcomes if o.field == "due_date")
    assert due_date_outcome.tn == 1
    assert due_date_outcome.fn == 0


def test_score_document_extra_line_item_is_false_positive_not_false_negative():
    extra = {"description": "Spurious", "quantity": 1, "unit_price": 1.0, "amount": 1.0}
    pred = _perfect_prediction()
    pred["line_items"].append(extra)
    result = score_document("doc-1", {}, GOLD, pred, error=None)
    assert result.line_item_count_match is False
    amount_outcomes = [o for o in result.field_outcomes if o.field == "line_items.amount"]
    assert sum(o.fp for o in amount_outcomes) == 1
    assert sum(o.fn for o in amount_outcomes) == 0


# --- aggregate ----------------------------------------------------------------


def test_aggregate_perfect_run():
    results = [score_document(f"doc-{i}", {}, GOLD, _perfect_prediction(), error=None) for i in range(3)]
    agg = aggregate(results)
    assert agg.n_documents == 3
    assert agg.document_exact_match_rate == 1.0
    assert agg.extraction_success_rate == 1.0
    assert agg.field_exact_match_rate == 1.0
    assert agg.micro_f1 == 1.0
    assert agg.macro_f1 == 1.0


def test_aggregate_mixed_run():
    good = score_document("doc-good", {}, GOLD, _perfect_prediction(), error=None)
    bad_pred = {**_perfect_prediction(), "vendor_name": "Wrong"}
    bad = score_document("doc-bad", {}, GOLD, bad_pred, error=None)
    failed = score_document("doc-failed", {}, GOLD, None, error="boom")

    agg = aggregate([good, bad, failed])
    assert agg.n_documents == 3
    assert agg.extraction_success_rate == 2 / 3
    assert agg.document_exact_match_rate == 1 / 3
    assert 0 < agg.field_exact_match_rate < 1
    vendor_counts = agg.field_counts["vendor_name"]
    assert vendor_counts.tp == 1  # good
    assert vendor_counts.fp == 1  # bad (wrong value)
    assert vendor_counts.fn == 2  # bad (wrong value) + failed (missed)


def test_aggregate_empty_list_does_not_crash():
    agg = aggregate([])
    assert agg.n_documents == 0
    assert agg.document_exact_match_rate is None
    assert agg.micro_f1 is None
