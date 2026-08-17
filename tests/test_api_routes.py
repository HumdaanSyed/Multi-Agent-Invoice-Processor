"""End-to-end tests for the FastAPI app - offline, no real network, no real
credentials. `router()` and `extractor()` are monkeypatched at the
`invoice_agent.graph` module level (same pattern tests/test_graph.py uses
for `db.is_duplicate`) so no Anthropic call ever happens; `invoice_agent.db`
is monkeypatched the same way so no Supabase call happens.

Test trap this file exists to avoid repeating: `TestClient(app)` used as a
context manager RUNS THE LIFESPAN, which calls `load_dotenv()` by default -
and this repo's real `.env` holds real credentials. Every app here is built
via `create_app(checkpointer=..., load_env=False)` with an in-memory
SqliteSaver, so no test ever touches the real .env or the real
checkpoints/graph.sqlite.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

import invoice_agent.db as db
import invoice_agent.graph as graph_module
from app.main import create_app

GOOD_INVOICE = {
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

# Deliberately inconsistent (line items sum to 12.00, subtotal says 10.00) -
# for tests that need the validator to actually flag and interrupt.
FLAWED_INVOICE = {**GOOD_INVOICE, "line_items": [{**GOOD_INVOICE["line_items"][0], "amount": 12.0}]}


@pytest.fixture(autouse=True)
def _no_langfuse_credentials(monkeypatch):
    # Same isolation as tests/test_tracing.py - avoids any environment
    # bleed-through affecting /health/ready's langfuse check.
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)


def _make_client(monkeypatch, *, invoice: dict = FLAWED_INVOICE) -> TestClient:
    """Builds a TestClient wired to fake router/extractor/db calls and an
    in-memory checkpointer - no network, no real credentials, no shared
    on-disk state between tests. Defaults to FLAWED_INVOICE so most tests
    exercise the needs_review path without extra setup; pass GOOD_INVOICE
    for a test that needs a clean straight-through run."""

    def fake_router(state):
        return {"doc_type": "invoice", "status": "classified"}

    def fake_extractor(state):
        return {"invoice": dict(invoice), "status": "extracted"}

    monkeypatch.setattr(graph_module, "router", fake_router)
    monkeypatch.setattr(graph_module, "extractor", fake_extractor)
    monkeypatch.setattr(db, "upload_pdf", lambda path: "invoices/fakehash_x.pdf")
    monkeypatch.setattr(db, "insert_invoice", lambda inv: {**inv, "id": 1})
    monkeypatch.setattr(db, "export_invoice_csv", lambda inv, path: None)
    monkeypatch.setattr(db, "is_duplicate", lambda vendor, number: False)

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn)
    app = create_app(checkpointer=saver, load_env=False)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    with _make_client(monkeypatch) as c:
        yield c


def _upload(client: TestClient, content: bytes = b"%PDF-1.4\nfake pdf content"):
    return client.post("/invoices", files={"file": ("invoice.pdf", content, "application/pdf")})


# --- health ----------------------------------------------------------------


def test_health_is_static_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_reports_degraded_without_credentials(client):
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["anthropic"]["configured"] is False
    assert body["checks"]["checkpointer"]["configured"] is True


# --- happy path: needs_review -> resume -> completed -----------------------


def test_full_flow_needs_review_then_resume_completes(client):
    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_review"
    assert "Line items sum to 12.00 but subtotal is 10.00" in body["flags"]
    thread_id = body["thread_id"]

    response = client.get(f"/invoices/{thread_id}")
    assert response.json()["status"] == "needs_review"

    response = client.post(
        f"/invoices/{thread_id}/resume", json={"corrections": {"subtotal": 12.0, "total": 12.0}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["invoice"]["subtotal"] == 12.0
    assert body["invoice"]["vendor_name"] == "Acme Corp"  # untouched field survives the merge


def test_resume_on_completed_thread_is_409_not_silent_success(client):
    response = _upload(client)
    thread_id = response.json()["thread_id"]
    client.post(f"/invoices/{thread_id}/resume", json={"corrections": {"subtotal": 12.0, "total": 12.0}})

    response = client.post(f"/invoices/{thread_id}/resume", json={"corrections": {}})
    assert response.status_code == 409
    assert response.json()["error"] == "thread_not_interrupted"
    assert response.json()["thread_id"] == thread_id


def test_get_unknown_thread_is_404(client):
    response = client.get("/invoices/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"] == "thread_not_found"


def test_resume_unknown_thread_is_404(client):
    response = client.post("/invoices/does-not-exist/resume", json={"corrections": {}})
    assert response.status_code == 404


# --- output() failure -> failed -> retry -----------------------------------


def test_output_failure_reports_failed_and_empty_resume_retries(monkeypatch):
    # Needs a *clean* invoice (GOOD_INVOICE, not the default fixture's
    # FLAWED_INVOICE) so the run reaches output() directly instead of
    # interrupting for review first.
    fail = {"on": True}

    def flaky_upload_pdf(path):
        if fail["on"]:
            raise RuntimeError("simulated Supabase Storage outage")
        return "invoices/fakehash_x.pdf"

    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        # Applied after _make_client, which installs its own db.upload_pdf
        # default patch - this override must come second or it's clobbered.
        monkeypatch.setattr(db, "upload_pdf", flaky_upload_pdf)

        response = _upload(client, content=b"%PDF-1.4\nclean invoice")
        assert response.status_code == 503
        body = response.json()
        assert body["error"] == "persistence_failed"
        thread_id = body["thread_id"]
        # The RuntimeError's rich detail (vendor_name, storage path) must
        # never reach the client - only the safe, generic message does.
        assert "Acme Corp" not in response.text

        response = client.get(f"/invoices/{thread_id}")
        assert response.json()["status"] == "failed"
        assert response.json()["failed_at_node"] == "output"

        response = client.post(f"/invoices/{thread_id}/resume", json={"corrections": {"total": 1.0}})
        assert response.status_code == 409
        assert response.json()["error"] == "thread_failed_retry_only"

        fail["on"] = False
        response = client.post(f"/invoices/{thread_id}/resume", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "completed"


# --- uploads -----------------------------------------------------------


def test_non_pdf_upload_is_415(client):
    response = client.post("/invoices", files={"file": ("x.txt", b"not a pdf", "text/plain")})
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


def test_empty_upload_is_400(client):
    response = client.post("/invoices", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 400
    assert response.json()["error"] == "empty_upload"


def test_content_type_header_is_ignored_in_favor_of_magic_bytes(client):
    # A real PDF served with a misleading Content-Type must still be
    # accepted - Content-Type is client-supplied and not to be trusted
    # (see app/uploads.py).
    response = _upload(client, content=b"%PDF-1.4\nreal pdf")
    assert response.status_code == 200


# --- validation at the HTTP boundary ---------------------------------------


def test_resume_rejects_unknown_correction_field(client):
    response = _upload(client)
    thread_id = response.json()["thread_id"]
    response = client.post(f"/invoices/{thread_id}/resume", json={"corrections": {"bogus_field": 1}})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_resume_rejects_correction_that_nulls_a_required_field(client):
    response = _upload(client)
    thread_id = response.json()["thread_id"]
    response = client.post(f"/invoices/{thread_id}/resume", json={"corrections": {"vendor_name": None}})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_invoice"


# --- listing -----------------------------------------------------------


def test_list_invoices_includes_recent_runs(client):
    ids = {_upload(client).json()["thread_id"] for _ in range(3)}
    response = client.get("/invoices")
    assert response.status_code == 200
    listed_ids = {row["thread_id"] for row in response.json()["runs"]}
    assert ids <= listed_ids
