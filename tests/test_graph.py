"""Tests for graph routing logic that don't require live API calls."""

from langgraph.graph import END

from invoice_agent.graph import route_after_classification, route_after_validation


def test_route_invoice_goes_to_extractor():
    state = {"doc_type": "invoice"}
    assert route_after_classification(state) == "extractor"


def test_route_receipt_goes_to_end():
    state = {"doc_type": "receipt"}
    assert route_after_classification(state) == END


def test_route_other_goes_to_end():
    state = {"doc_type": "other"}
    assert route_after_classification(state) == END


def test_route_flagged_invoice_goes_to_human_review():
    state = {"validation": {"needs_review": True}}
    assert route_after_validation(state) == "human_review"


def test_route_clean_invoice_goes_to_output():
    state = {"validation": {"needs_review": False}}
    assert route_after_validation(state) == "output"
