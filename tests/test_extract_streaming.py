"""Unit tests for invoice_agent.extract.extract_invoice_streaming's partial-
JSON field-detection logic (Phase 8.5) - no real Anthropic call. The
Anthropic client's `.messages.stream()` is replaced with a fake context
manager yielding hand-built text-delta events shaped like the real SDK's
TextEvent (`.type`, `.text`, `.snapshot`), exactly as
docs/FRONTEND_PLAN.md's Phase 8.5 testing guidance describes.
"""

from __future__ import annotations

import pytest

import invoice_agent.extract as extract_module
from invoice_agent.schema import Invoice

GOOD_INVOICE = Invoice.model_validate(
    {
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-01",
        "vendor_name": "Acme Corp",
        "bill_to": "Widgets Inc",
        "line_items": [{"description": "Widget", "quantity": 1, "unit_price": 10.0, "amount": 10.0}],
        "subtotal": 10.0,
        "tax": 0.0,
        "total": 10.0,
        "due_date": None,
        "currency": "USD",
    }
)


class _FakeTextEvent:
    def __init__(self, snapshot: str):
        self.type = "text"
        self.text = ""  # unused by extract_invoice_streaming - it reads .snapshot
        self.snapshot = snapshot


class _FakeFinalMessage:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output
        self.usage = "fake-usage"


class _FakeStream:
    def __init__(self, text_events: list[str], final_invoice: Invoice | None):
        self._text_events = [_FakeTextEvent(s) for s in text_events]
        self._final_invoice = final_invoice

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._text_events)

    def get_final_message(self):
        return _FakeFinalMessage(self._final_invoice)


class _FakeMessages:
    def __init__(self, stream: _FakeStream):
        self._stream = stream

    def stream(self, **kwargs):
        return self._stream


class _FakeAnthropicClient:
    def __init__(self, stream: _FakeStream):
        self.messages = _FakeMessages(stream)


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-1.4\nfake pdf content")
    return path


def _snapshots_for(*partials: str) -> list[str]:
    """Convenience: each arg is a raw JSON-ish snapshot string, in the
    order Claude would have streamed them (each strictly extends the
    last, as real accumulated snapshots do)."""
    return list(partials)


def test_field_confirmed_only_once_a_later_key_appears(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(
            _snapshots_for(
                '{"invoice_number": "INV-1"',
                '{"invoice_number": "INV-1", "vendor_name": "Acme Corp"',
            ),
            GOOD_INVOICE,
        )
    ))

    emitted: list[tuple[str, object]] = []
    invoice = extract_module.extract_invoice_streaming(
        pdf_path, on_field=lambda name, value: emitted.append((name, value))
    )

    assert invoice == GOOD_INVOICE
    # invoice_number is the only key ever confirmed live here - it's the
    # only one that stops being the *last* key in a snapshot (vendor_name
    # never does, across both snapshots fed in) - so it must be the first
    # thing emitted, ahead of the final flush's schema-order walk over
    # every other field.
    assert emitted[0] == ("invoice_number", "INV-1")
    assert emitted.count(("invoice_number", "INV-1")) == 1  # never re-emitted by the flush
    assert [name for name, _ in emitted] == list(Invoice.model_fields)


def test_unconfirmed_fields_are_flushed_from_final_parsed_output(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(_snapshots_for('{"invoice_number": "INV-1"'), GOOD_INVOICE)
    ))

    emitted: dict[str, object] = {}
    invoice = extract_module.extract_invoice_streaming(
        pdf_path, on_field=lambda name, value: emitted.setdefault(name, value)
    )

    assert invoice == GOOD_INVOICE
    # Every declared field fires exactly once, even the ones that never
    # got confirmed live - the final flush pulls them from parsed_output,
    # not from the (still-incomplete) partial buffer.
    assert set(emitted) == set(Invoice.model_fields)
    assert emitted["vendor_name"] == "Acme Corp"
    assert emitted["line_items"] == [li.model_dump(mode="json") for li in GOOD_INVOICE.line_items]


def test_each_field_emitted_at_most_once(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(
            _snapshots_for(
                '{"invoice_number": "INV-1"',
                '{"invoice_number": "INV-1", "invoice_date": "2026-01-01"',
                '{"invoice_number": "INV-1", "invoice_date": "2026-01-01", "vendor_name": "Acme Corp"',
            ),
            GOOD_INVOICE,
        )
    ))

    emitted: list[str] = []
    extract_module.extract_invoice_streaming(pdf_path, on_field=lambda name, value: emitted.append(name))

    assert emitted.count("invoice_number") == 1
    assert emitted.count("invoice_date") == 1


def test_unparseable_snapshots_fire_no_live_events_but_still_return_correct_invoice(monkeypatch, pdf_path):
    """Graceful degradation (invoice_agent/extract.py's docstring): if the
    partial parse never yields a usable dict, on_field never fires during
    iteration - but the function must still return the correct Invoice
    from the final parsed message, and still flush every field once."""
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(_snapshots_for("not json at all", "{ this is not valid json either"), GOOD_INVOICE)
    ))

    emitted: list[str] = []
    invoice = extract_module.extract_invoice_streaming(pdf_path, on_field=lambda name, value: emitted.append(name))

    assert invoice == GOOD_INVOICE
    assert set(emitted) == set(Invoice.model_fields)  # all from the final flush


def test_a_hallucinated_key_is_never_forwarded(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(
            _snapshots_for('{"not_a_real_field": "x", "invoice_number": "INV-1"'),
            GOOD_INVOICE,
        )
    ))

    emitted: list[str] = []
    extract_module.extract_invoice_streaming(pdf_path, on_field=lambda name, value: emitted.append(name))

    assert "not_a_real_field" not in emitted


def test_no_final_parsed_output_raises_value_error(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(_snapshots_for('{"invoice_number": "INV-1"'), None)
    ))

    with pytest.raises(ValueError, match="no parsed Invoice"):
        extract_module.extract_invoice_streaming(pdf_path, on_field=lambda name, value: None)


def test_on_response_receives_the_final_message(monkeypatch, pdf_path):
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(_snapshots_for('{"invoice_number": "INV-1"'), GOOD_INVOICE)
    ))

    captured = []
    extract_module.extract_invoice_streaming(
        pdf_path, on_response=captured.append, on_field=lambda name, value: None
    )
    assert len(captured) == 1
    assert captured[0].parsed_output == GOOD_INVOICE


def test_streamed_values_never_bypass_the_final_parsed_invoice(monkeypatch, pdf_path):
    """The core display-only invariant, behaviorally: feed on_field a
    snapshot implying a *wrong* vendor_name, and confirm the function's
    actual return value still matches the final parsed message - not
    anything derived from what on_field was handed."""
    monkeypatch.setattr(extract_module, "Anthropic", lambda **kw: _FakeAnthropicClient(
        _FakeStream(
            _snapshots_for('{"invoice_number": "INV-1", "vendor_name": "WRONG NAME, still streaming"'),
            GOOD_INVOICE,
        )
    ))

    seen_vendor_name_live = []
    invoice = extract_module.extract_invoice_streaming(
        pdf_path,
        on_field=lambda name, value: seen_vendor_name_live.append(value) if name == "vendor_name" else None,
    )

    assert invoice.vendor_name == "Acme Corp"  # the real, final value - not the partial one
    # vendor_name was still the last key in the snapshot, so it's only
    # ever emitted once, from the final flush, with the correct value -
    # the wrong partial text never got confirmed live at all.
    assert seen_vendor_name_live == ["Acme Corp"]
