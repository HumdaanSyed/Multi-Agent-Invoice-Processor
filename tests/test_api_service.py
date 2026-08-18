"""Tests for app.service's pure logic (derive_status, build_resume_command)
and GraphService's concurrency contract - offline, no network, no live
Anthropic/Supabase calls.

Two sources of StateSnapshot are used, matching the pattern the Phase 8
design calls for:
  - A small fixture graph (`_build_fixture_graph`), shaped like the tail of
    invoice_agent.graph (validator -> human_review -> output) and compiled
    against a real in-memory SqliteSaver, so most cases run through real
    interrupt()/invoke() machinery instead of a hand-simulated shape.
  - Hand-built `StateSnapshot`/`Interrupt` instances for the few states that
    aren't reachable through a single synchronous invoke() - "processing"
    (only observable when a GET races an in-flight POST) and the
    fail-closed fallback branch.
"""

from __future__ import annotations

import sqlite3
from typing import Optional, TypedDict

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Interrupt, StateSnapshot, interrupt

from app.errors import (
    InvalidInvoice,
    ServerBusy,
    ThreadBusy,
    ThreadConflict,
    ThreadFailedRetryOnly,
    ThreadNotFound,
)
from app.models import InvoiceCorrections
from app.service import GraphService, build_resume_command, derive_status

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


class _FixtureState(TypedDict):
    doc_type: str
    invoice: Optional[dict]
    validation: Optional[dict]
    status: str


def _build_fixture_graph(*, interrupt_flags: list[str] | None = None, fail_output: dict | None = None):
    """A minimal graph shaped like the tail of invoice_agent.graph, so
    derive_status can be exercised against real StateSnapshots produced by
    real interrupt()/invoke() calls, not just hand-built ones.

    `fail_output`, if given, is a mutable `{"on": bool}` the test can flip
    between invokes - needed to exercise the failed -> retry -> completed
    sequence, which requires the *same* thread_id to fail once and then
    succeed on a later `invoke(None, ...)`.
    """

    def validator(state: _FixtureState) -> dict:
        if interrupt_flags is not None:
            return {"status": "needs_review", "validation": {"needs_review": True, "flags": interrupt_flags}}
        return {"status": "validated", "validation": {"needs_review": False, "flags": []}}

    def human_review(state: _FixtureState) -> dict:
        interrupt({"invoice": state["invoice"], "flags": state["validation"]["flags"]})
        return {"status": "reviewed"}

    def output(state: _FixtureState) -> dict:
        if fail_output is not None and fail_output.get("on"):
            raise RuntimeError(
                f"output failed persisting vendor={state['invoice'].get('vendor_name')!r}"
            )
        return {"status": "completed"}

    def route(state: _FixtureState) -> str:
        return "human_review" if state["validation"]["needs_review"] else "output"

    graph = StateGraph(_FixtureState)
    graph.add_node("validator", validator)
    graph.add_node("human_review", human_review)
    graph.add_node("output", output)
    graph.add_edge(START, "validator")
    graph.add_conditional_edges("validator", route, {"human_review": "human_review", "output": "output"})
    graph.add_edge("human_review", "output")
    graph.add_edge("output", END)

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(conn))


def _initial_state(invoice: dict | None = None) -> _FixtureState:
    return {"doc_type": "invoice", "invoice": invoice or GOOD_INVOICE, "validation": None, "status": "extracted"}


# --- derive_status: real snapshots via the fixture graph -------------------


def test_derive_status_none_for_unknown_thread():
    graph = _build_fixture_graph()
    snapshot = graph.get_state({"configurable": {"thread_id": "does-not-exist"}})
    assert derive_status(snapshot) is None


def test_derive_status_completed():
    graph = _build_fixture_graph()
    cfg = {"configurable": {"thread_id": "t-completed"}}
    graph.invoke(_initial_state(), config=cfg)
    result = derive_status(graph.get_state(cfg))
    assert result.status == "completed"


def test_derive_status_needs_review_reads_interrupt_payload_not_stale_values():
    flags = ["Line items sum to 90.00 but subtotal is 100.00"]
    graph = _build_fixture_graph(interrupt_flags=flags)
    cfg = {"configurable": {"thread_id": "t-review"}}
    graph.invoke(_initial_state(), config=cfg)

    result = derive_status(graph.get_state(cfg))
    assert result.status == "needs_review"
    assert result.flags == flags
    assert result.invoice == GOOD_INVOICE


def test_derive_status_failed_takes_priority_over_stale_status():
    fail = {"on": True}
    graph = _build_fixture_graph(fail_output=fail)
    cfg = {"configurable": {"thread_id": "t-failed"}}
    with pytest.raises(RuntimeError):
        graph.invoke(_initial_state(), config=cfg)

    snapshot = graph.get_state(cfg)
    # values["status"] is stale here (set by validator, before output ran)
    # - derive_status must not be fooled by it into reporting "processing".
    assert snapshot.values["status"] == "validated"
    result = derive_status(snapshot)
    assert result.status == "failed"
    assert result.failed_at_node == "output"


def test_derive_status_completed_after_retry_via_invoke_none():
    fail = {"on": True}
    graph = _build_fixture_graph(fail_output=fail)
    cfg = {"configurable": {"thread_id": "t-retry"}}
    with pytest.raises(RuntimeError):
        graph.invoke(_initial_state(), config=cfg)
    assert derive_status(graph.get_state(cfg)).status == "failed"

    fail["on"] = False
    graph.invoke(None, config=cfg)  # the retry mechanism GraphService uses
    assert derive_status(graph.get_state(cfg)).status == "completed"


# --- derive_status: hand-built snapshots for states a single synchronous
# invoke() can't produce ----------------------------------------------------


def test_derive_status_processing_when_next_nonempty_no_interrupt_no_error():
    snapshot = StateSnapshot(
        values={"status": "classified", "doc_type": "invoice", "invoice": None, "validation": None},
        next=("extractor",),
        config={"configurable": {"thread_id": "t-mid"}},
        metadata=None,
        created_at="2026-01-01T00:00:00Z",
        parent_config=None,
        tasks=(),
        interrupts=(),
    )
    result = derive_status(snapshot)
    assert result.status == "processing"
    assert result.current_node == "extractor"


def test_derive_status_skipped_reads_doc_type():
    snapshot = StateSnapshot(
        values={"status": "skipped", "doc_type": "receipt"},
        next=(),
        config={},
        metadata=None,
        created_at="2026-01-01T00:00:00Z",
        parent_config=None,
        tasks=(),
        interrupts=(),
    )
    result = derive_status(snapshot)
    assert result.status == "skipped"
    assert result.doc_type == "receipt"


def test_derive_status_fails_closed_on_unrecognized_terminal_status():
    """Unreachable in the real graph topology (every path to END goes
    through output() or the router's skip edge) but derive_status must not
    invent a success if it ever happens."""
    snapshot = StateSnapshot(
        values={"status": "pending"},
        next=(),
        config={},
        metadata=None,
        created_at="2026-01-01T00:00:00Z",
        parent_config=None,
        tasks=(),
        interrupts=(),
    )
    result = derive_status(snapshot)
    assert result.status == "failed"
    assert result.failed_at_node is None


# --- build_resume_command ----------------------------------------------


def _interrupted_snapshot(invoice: dict, flags: list[str]) -> StateSnapshot:
    return StateSnapshot(
        values={},
        next=("human_review",),
        config={},
        metadata=None,
        created_at="2026-01-01T00:00:00Z",
        parent_config=None,
        tasks=(),
        interrupts=(Interrupt(value={"invoice": invoice, "flags": flags}),),
    )


def test_build_resume_command_empty_patch_means_accept_as_is():
    snapshot = _interrupted_snapshot(GOOD_INVOICE, ["some flag"])
    command = build_resume_command(snapshot, InvoiceCorrections())
    assert isinstance(command, Command)
    assert command.resume == {"edited_invoice": None}


def test_build_resume_command_merges_patch_without_wiping_other_fields():
    snapshot = _interrupted_snapshot(GOOD_INVOICE, ["Subtotal + tax = 10.00 but total is 12.00"])
    command = build_resume_command(snapshot, InvoiceCorrections(total=10.0))
    edited = command.resume["edited_invoice"]
    assert edited["total"] == 10.0
    assert edited["vendor_name"] == "Acme Corp"
    # The failure mode this guards against: a naive pass-through resume
    # that let line_items go missing would make db.insert_invoice run an
    # unfiltered delete, wiping every stored line item for the invoice.
    assert edited["line_items"] == GOOD_INVOICE["line_items"]


def test_build_resume_command_rejects_correction_that_nulls_a_required_field():
    snapshot = _interrupted_snapshot(GOOD_INVOICE, ["flag"])
    corrections = InvoiceCorrections.model_validate({"vendor_name": None})
    with pytest.raises(InvalidInvoice):
        build_resume_command(snapshot, corrections)


# --- GraphService: status-derived resume routing -------------------------


def test_graph_service_full_flow_needs_review_then_resume_completes():
    graph = _build_fixture_graph(interrupt_flags=["Subtotal + tax = 10.00 but total is 12.00"])
    service = GraphService(graph)
    thread_id = "t-flow"

    # start_run() drives the real invoice_agent-style initial state
    # (file_path, doc_type, ...), but the fixture graph's state shape is
    # simpler (no file_path) - invoke directly via the same config helper
    # GraphService uses internally, so this exercises resume_run() (the
    # thing under test) without needing a real PDF/router/extractor.
    from invoice_agent.graph import build_invoke_config

    graph.invoke(_initial_state(), config=build_invoke_config(thread_id))

    result = service.resume_run(thread_id, InvoiceCorrections(total=10.0))
    assert result["status"] == "completed"


def test_graph_service_resume_unknown_thread_raises_not_found():
    service = GraphService(_build_fixture_graph())
    with pytest.raises(ThreadNotFound):
        service.resume_run("nope", InvoiceCorrections())


def test_graph_service_resume_completed_thread_raises_conflict():
    from invoice_agent.graph import build_invoke_config

    graph = _build_fixture_graph()
    service = GraphService(graph)
    thread_id = "t-done"
    graph.invoke(_initial_state(), config=build_invoke_config(thread_id))

    with pytest.raises(ThreadConflict):
        service.resume_run(thread_id, InvoiceCorrections())


def test_graph_service_resume_failed_thread_with_corrections_raises_retry_only():
    from invoice_agent.graph import build_invoke_config

    fail = {"on": True}
    graph = _build_fixture_graph(fail_output=fail)
    service = GraphService(graph)
    thread_id = "t-failed-retry"
    with pytest.raises(RuntimeError):
        graph.invoke(_initial_state(), config=build_invoke_config(thread_id))

    with pytest.raises(ThreadFailedRetryOnly):
        service.resume_run(thread_id, InvoiceCorrections(total=5.0))


def test_graph_service_resume_failed_thread_empty_body_retries():
    from invoice_agent.graph import build_invoke_config

    fail = {"on": True}
    graph = _build_fixture_graph(fail_output=fail)
    service = GraphService(graph)
    thread_id = "t-failed-empty-retry"
    with pytest.raises(RuntimeError):
        graph.invoke(_initial_state(), config=build_invoke_config(thread_id))

    fail["on"] = False
    result = service.resume_run(thread_id, InvoiceCorrections())
    assert result["status"] == "completed"


def test_graph_service_resume_processing_thread_raises_busy(monkeypatch):
    import app.service as service_module

    graph = _build_fixture_graph()
    service = GraphService(graph)

    fake_processing = service_module.DerivedStatus(status="processing", current_node="output")
    monkeypatch.setattr(service_module, "derive_status", lambda snapshot: fake_processing)

    with pytest.raises(ThreadBusy):
        service.resume_run("whatever", InvoiceCorrections())


# --- GraphService: concurrency guards -------------------------------------


def test_graph_service_same_thread_lock_held_raises_busy():
    graph = _build_fixture_graph(interrupt_flags=["flag"])
    service = GraphService(graph)
    thread_id = "t-locked"

    lock = service._lock_for(thread_id)
    lock.acquire()
    try:
        with pytest.raises(ThreadBusy):
            service.start_run(thread_id, "unused.pdf")
    finally:
        lock.release()


def test_graph_service_semaphore_saturated_on_real_needs_review_thread(monkeypatch):
    from invoice_agent.graph import build_invoke_config

    import app.service as service_module

    monkeypatch.setattr(service_module, "SEMAPHORE_ACQUIRE_TIMEOUT", 0.05)
    graph = _build_fixture_graph(interrupt_flags=["flag"])
    service = GraphService(graph, max_concurrency=1)
    thread_id = "t-saturated"
    graph.invoke(_initial_state(), config=build_invoke_config(thread_id))

    service._semaphore.acquire()  # hold the only permit
    with pytest.raises(ServerBusy):
        service.resume_run(thread_id, InvoiceCorrections())


# --- GraphService: list_runs -----------------------------------------------


def test_graph_service_list_runs_dedupes_by_thread_id():
    from invoice_agent.graph import build_invoke_config

    graph = _build_fixture_graph()
    service = GraphService(graph)
    for i in range(3):
        graph.invoke(_initial_state(), config=build_invoke_config(f"t-list-{i}"))

    rows = service.list_runs(limit=20)
    thread_ids = {row["thread_id"] for row in rows}
    assert {"t-list-0", "t-list-1", "t-list-2"} <= thread_ids
    assert all(row["status"] == "completed" for row in rows if row["thread_id"].startswith("t-list-"))


def test_graph_service_list_runs_respects_limit():
    from invoice_agent.graph import build_invoke_config

    graph = _build_fixture_graph()
    service = GraphService(graph)
    for i in range(5):
        graph.invoke(_initial_state(), config=build_invoke_config(f"t-limit-{i}"))

    rows = service.list_runs(limit=2)
    assert len(rows) == 2
