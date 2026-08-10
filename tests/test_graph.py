"""Tests for graph routing logic that don't require live API calls."""

from langgraph.graph import END

from invoice_agent.graph import route_after_classification


def test_route_invoice_goes_to_extractor():
    state = {"doc_type": "invoice"}
    assert route_after_classification(state) == "extractor"


def test_route_receipt_goes_to_end():
    state = {"doc_type": "receipt"}
    assert route_after_classification(state) == END


def test_route_other_goes_to_end():
    state = {"doc_type": "other"}
    assert route_after_classification(state) == END
