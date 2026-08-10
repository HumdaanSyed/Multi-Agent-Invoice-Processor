"""Tests for graph routing logic that don't require live API calls."""

from langgraph.graph import END

from invoice_agent import db, graph as graph_module
from invoice_agent.graph import route_after_classification, route_after_validation

GOOD_INVOICE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-07-01",
    "vendor_name": "Acme Co",
    "bill_to": "Someone",
    "line_items": [
        {"description": "Widget", "quantity": 1, "unit_price": 10.0, "amount": 10.0},
    ],
    "subtotal": 10.0,
    "tax": 0.8,
    "total": 10.8,
    "due_date": "2026-07-31",
    "currency": "USD",
}


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


def test_validator_wires_db_is_duplicate(monkeypatch):
    """Guards against a future refactor silently dropping the duplicate
    check - validate_invoice()'s duplicate_checker defaults to a no-op, so
    the graph must be the one explicitly wiring in the real db.is_duplicate."""
    calls = []

    def fake_is_duplicate(vendor_name, invoice_number):
        calls.append((vendor_name, invoice_number))
        return True

    monkeypatch.setattr(db, "is_duplicate", fake_is_duplicate)

    result = graph_module.validator({"invoice": GOOD_INVOICE})

    assert calls == [("Acme Co", "INV-1")]
    assert result["status"] == "needs_review"
    assert any("duplicate" in flag.lower() for flag in result["validation"]["flags"])
