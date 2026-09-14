"""End-to-end tests for the export ledger (Phase 8.6) - GET /export.csv,
GET /export/status, and the graph's own insert_export_row wiring. Reuses
tests/test_api_routes.py's offline fixtures (router()/extractor() and
invoice_agent.db are monkeypatched, no real network, no real credentials).
"""

from __future__ import annotations

import csv
import io

import invoice_agent.db as db
from tests.test_api_routes import FLAWED_INVOICE, GOOD_INVOICE, _make_client, _upload


def test_export_status_reports_zero_rows_before_any_run(monkeypatch):
    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(db, "export_status", lambda: {"row_count": 0, "last_written_at": None})
        response = client.get("/export/status")
        assert response.status_code == 200
        assert response.json() == {"row_count": 0, "last_written_at": None}


def test_export_status_reflects_ledger_state(monkeypatch):
    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(
            db, "export_status", lambda: {"row_count": 3, "last_written_at": "2026-07-02T10:00:00+00:00"}
        )
        response = client.get("/export/status")
        assert response.json() == {"row_count": 3, "last_written_at": "2026-07-02T10:00:00+00:00"}


def test_export_csv_downloads_every_ledger_row(monkeypatch):
    ledger_rows = [
        {**GOOD_INVOICE, "written_at": "2026-07-01T00:00:00+00:00", "thread_id": "t-1"},
        {**GOOD_INVOICE, "invoice_number": "INV-2", "written_at": "2026-07-02T00:00:00+00:00", "thread_id": "t-2"},
    ]
    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(db, "iter_export_rows", lambda page_size=500: iter(ledger_rows))
        response = client.get("/export.csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="invoices.csv"' in response.headers["content-disposition"]

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["invoice_number"] for r in rows] == ["INV-1", "INV-2"]
        assert rows[0]["thread_id"] == "t-1"
        assert rows[0]["written_at"] == "2026-07-01T00:00:00+00:00"
        assert rows[0]["line_item_description"] == GOOD_INVOICE["line_items"][0]["description"]


def test_export_csv_streams_a_header_even_with_an_empty_ledger(monkeypatch):
    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(db, "iter_export_rows", lambda page_size=500: iter([]))
        response = client.get("/export.csv")
        assert response.status_code == 200
        assert response.text.strip() == ",".join(db.LEDGER_CSV_FIELDS)


def test_completed_run_writes_one_ledger_row_with_the_thread_id(monkeypatch):
    calls: list[tuple[str, dict]] = []
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        monkeypatch.setattr(db, "insert_export_row", lambda thread_id, inv: calls.append((thread_id, inv)) or {})
        response = _upload(client)
        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    assert len(calls) == 1
    thread_id, invoice = calls[0]
    assert thread_id == response.json()["thread_id"]
    assert invoice["invoice_number"] == "INV-1"


def test_flagged_run_writes_no_ledger_row_until_resolved(monkeypatch):
    """The pitfall this phase calls out explicitly: an invoice must never
    appear in the ledger unless it passed validation and was persisted -
    a run sitting at needs_review must not have written anything yet."""
    calls: list[tuple[str, dict]] = []
    with _make_client(monkeypatch, invoice=FLAWED_INVOICE) as client:
        monkeypatch.setattr(db, "insert_export_row", lambda thread_id, inv: calls.append((thread_id, inv)) or {})
        response = _upload(client)
        assert response.json()["status"] == "needs_review"

    assert calls == []


def test_three_successful_runs_produce_three_append_only_ledger_rows(monkeypatch):
    """docs/FRONTEND_PLAN.md's Phase 8.6 done-criteria: processing
    invoices - including re-running one that already completed, with a
    correction - produces one ledger row per successful write, never an
    overwrite. is_duplicate is mocked to always return False here (as in
    every _make_client-based test), so a third run against the exact same
    invoice content still completes and still appends its own row."""
    calls: list[tuple[str, dict]] = []
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        monkeypatch.setattr(db, "insert_export_row", lambda thread_id, inv: calls.append((thread_id, inv)) or {})

        first = _upload(client)
        second = _upload(client)
        # "re-running a corrected one" - a fresh run resubmitting the same
        # invoice, exercising that the ledger appends rather than
        # overwrites the earlier row for the same invoice_number.
        third = _upload(client)

        for response in (first, second, third):
            assert response.json()["status"] == "completed"

    assert len(calls) == 3
    thread_ids = {thread_id for thread_id, _ in calls}
    assert len(thread_ids) == 3  # three distinct runs, not the same thread reused
