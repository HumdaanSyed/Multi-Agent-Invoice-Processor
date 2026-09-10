"""End-to-end tests for the SSE streaming endpoints (Phase 8.5) - reuses
tests/test_api_routes.py's offline fixtures (router()/extractor() and
invoice_agent.db are monkeypatched, no real network, no real credentials).

`TestClient.get(..., stream=True)` (httpx's streaming mode under the hood)
lets these tests read Server-Sent Events as they arrive rather than
waiting for the whole response to buffer - a plain `.get()` would hang
until the connection closes, which for a still-open stream is never.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from invoice_agent import events
from tests.test_api_routes import FLAWED_INVOICE, GOOD_INVOICE, _make_client


@pytest.fixture(autouse=True)
def _reset_events_registry():
    events.reset()
    yield
    events.reset()


def _read_sse_events(response) -> list[dict]:
    """Parse a `text/event-stream` response body already read to
    completion (as it is for every test here - each run finishes/interrupts
    before the client even connects, so the stream is always a short,
    already-terminated replay, never a truly open-ended one)."""
    parsed = []
    current: dict = {}
    for raw_line in response.text.splitlines():
        if raw_line == "":
            if current:
                parsed.append(current)
                current = {}
            continue
        if raw_line.startswith(":"):
            continue  # keep-alive comment
        key, _, value = raw_line.partition(": ")
        if key == "id":
            current["id"] = int(value)
        elif key == "event":
            current["event"] = value
        elif key == "data":
            import json

            current["data"] = json.loads(value)
    if current:
        parsed.append(current)
    return parsed


def _reserve(client: TestClient) -> str:
    response = client.post("/invoices/reserve")
    assert response.status_code == 200
    return response.json()["thread_id"]


def _wait_until_subscribed(thread_id: str, timeout: float = 2.0) -> None:
    """Poll internal state until a subscriber has actually attached to
    thread_id's channel. Needed because TestClient's stream GET runs on a
    background thread (see _stream_in_background) - the trigger (POST
    /invoices or a resume) must not fire until that GET has genuinely
    reached events.subscribe(), or the run could finish and publish its
    events before anything is listening, which is exactly what
    invoice_agent/events.py's history-replay-on-late-subscribe exists to
    handle, but is not what these two tests are trying to exercise."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        channel = events._channels.get(thread_id)
        if channel is not None and channel.subscribers:
            return
        time.sleep(0.005)
    raise TimeoutError(f"No subscriber attached to {thread_id!r} within {timeout}s")


def _stream_in_background(client: TestClient, thread_id: str) -> "list[dict] | Exception":
    """Runs GET .../stream on a real OS thread and returns its parsed SSE
    events once the connection closes (the run reaching a terminal state).
    A plain sequential call can't observe live events at all here: TestClient
    dispatches each request synchronously, so a GET issued after a blocking
    POST/resume already returned only ever sees whatever's left in history -
    for a run that closed its own channel on completion, that's nothing.
    This mirrors the real front, opens the stream, THEN triggers the run.
    """
    box: dict = {}

    def _run():
        try:
            box["result"] = _read_sse_events(client.get(f"/invoices/{thread_id}/stream"))
        except Exception as exc:  # noqa: BLE001 - surfaced via box, not lost in the thread
            box["result"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    _wait_until_subscribed(thread_id)
    return thread, box


def test_stream_unknown_thread_id_returns_404(monkeypatch):
    with _make_client(monkeypatch) as client:
        response = client.get("/invoices/does-not-exist/stream")
        assert response.status_code == 404
        assert response.json()["error"] == "thread_not_found"


def test_stream_completed_run_connected_after_the_fact_gets_snapshot(monkeypatch):
    """POST /invoices is blocking (app/routes.py's module docstring) - by
    the time it returns, a straight-through completed run has already
    published its terminal run_end and closed its own channel (see
    app/service.py's _publish_run_end). A client that only opens the
    stream *after* that POST returns - the scenario this test covers -
    has nothing left to subscribe to, and correctly falls back to a
    one-shot snapshot instead of hanging. test_stream_live_during_processing
    below covers the case that actually matters for Phase 9B: a client
    that opens the stream *before* triggering the run."""
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        thread_id = _reserve(client)
        upload = client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert upload.status_code == 200
        assert upload.json()["status"] == "completed"
        assert events.has_channel(thread_id) is False

        response = client.get(f"/invoices/{thread_id}/stream")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        parsed = _read_sse_events(response)
        assert [e["event"] for e in parsed] == ["snapshot", "done"]
        assert parsed[0]["data"]["status"] == "completed"


def test_stream_live_during_processing_sees_the_full_run(monkeypatch):
    """The scenario Phase 9B actually depends on: the client opens the SSE
    connection *before* the triggering POST, exactly as
    docs/FRONTEND_PLAN.md's reserve -> subscribe -> upload sequence
    prescribes - and sees every field/check/stage event live, not just a
    post-hoc snapshot."""
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        thread_id = _reserve(client)
        thread, box = _stream_in_background(client, thread_id)

        upload = client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert upload.status_code == 200
        assert upload.json()["status"] == "completed"

        thread.join(timeout=5)
        result = box["result"]
        assert not isinstance(result, Exception), result
        event_types = [e["event"] for e in result]

        assert "validation_start" in event_types
        assert event_types.count("check") == 6
        assert event_types[-1] == "run_end"
        assert result[-1]["data"]["status"] == "completed"
        assert events.has_channel(thread_id) is False


def test_stream_flagged_run_emits_interrupted_and_stays_open(monkeypatch):
    with _make_client(monkeypatch, invoice=FLAWED_INVOICE) as client:
        thread_id = _reserve(client)
        upload = client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert upload.status_code == 200
        assert upload.json()["status"] == "needs_review"

        response = client.get(f"/invoices/{thread_id}/stream")
        parsed = _read_sse_events(response)
        event_types = [e["event"] for e in parsed]

        assert event_types[-1] == "interrupted"
        assert parsed[-1]["data"]["status"] == "needs_review"
        assert any(f.startswith("Line items sum to") for f in parsed[-1]["data"]["flags"])

        # The channel must still be open - resuming needs somewhere to
        # keep publishing (invoice_agent/events.py: an interrupt never
        # closes the channel, only the connection).
        assert events.has_channel(thread_id) is True


def test_resume_still_flagged_republishes_validation_events(monkeypatch):
    """A resume that doesn't actually fix anything (empty corrections -
    build_resume_command's "accept as-is" path) re-enters at
    human_review -> validator (invoice_agent/graph.py's unconditional
    edge) and re-flags the identical issue - the channel stays open (an
    interrupt never closes it), so both passes' events are readable in
    one plain sequential GET, with no concurrency needed: validation_start
    fires once per pass, so it appears twice across the full history,
    each followed by its own six checks."""
    with _make_client(monkeypatch, invoice=FLAWED_INVOICE) as client:
        thread_id = _reserve(client)
        client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )

        resume = client.post(f"/invoices/{thread_id}/resume", json={"corrections": {}})
        assert resume.status_code == 200
        assert resume.json()["status"] == "needs_review"
        assert events.has_channel(thread_id) is True

        response = client.get(f"/invoices/{thread_id}/stream")
        parsed = _read_sse_events(response)
        event_types = [e["event"] for e in parsed]

        assert event_types.count("validation_start") == 2
        assert event_types.count("check") == 12
        assert event_types.count("interrupted") == 2
        assert event_types[-1] == "interrupted"


def test_resume_to_success_closes_channel(monkeypatch):
    """The success case, verified the same way as the plain-completed-run
    test above: connecting *after* a terminal resume returns only ever
    sees the fallback snapshot, because _publish_run_end already closed
    the channel synchronously inside that blocking resume call - live
    events from a resume are only observable by a client already
    connected beforehand, which test_stream_live_during_processing already
    covers for the initial-run case (identical code path: both go through
    GraphService's _guarded_invoke -> graph.invoke -> _publish_run_end)."""
    with _make_client(monkeypatch, invoice=FLAWED_INVOICE) as client:
        thread_id = _reserve(client)
        client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        client.get(f"/invoices/{thread_id}/stream")  # drain the interrupt

        fixed_line_item = {**FLAWED_INVOICE["line_items"][0], "amount": 10.0}
        resume = client.post(
            f"/invoices/{thread_id}/resume",
            json={"corrections": {"line_items": [fixed_line_item]}},
        )
        assert resume.status_code == 200
        assert resume.json()["status"] == "completed"
        assert events.has_channel(thread_id) is False

        response = client.get(f"/invoices/{thread_id}/stream")
        parsed = _read_sse_events(response)
        assert [e["event"] for e in parsed] == ["snapshot", "done"]
        assert parsed[0]["data"]["status"] == "completed"


def test_reserve_then_upload_without_thread_id_still_works(monkeypatch):
    """POST /invoices with no thread_id at all - the pre-Phase-8.5 shape -
    must keep working byte-identically (existing tests/test_api_routes.py
    already covers this; this is a reserve-adjacent sanity check that the
    two paths don't interfere with each other)."""
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        response = client.post(
            "/invoices", files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"


def test_upload_rejects_thread_id_not_reserved(monkeypatch):
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        response = client.post(
            "/invoices",
            data={"thread_id": "made-up-id"},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert response.status_code == 422


def test_upload_rejects_thread_id_already_used(monkeypatch):
    with _make_client(monkeypatch, invoice=GOOD_INVOICE) as client:
        thread_id = _reserve(client)
        first = client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert first.status_code == 200

        second_reserve_reuse = client.post(
            "/invoices",
            data={"thread_id": thread_id},
            files={"file": ("invoice.pdf", b"%PDF-1.4\nfake", "application/pdf")},
        )
        assert second_reserve_reuse.status_code == 422
