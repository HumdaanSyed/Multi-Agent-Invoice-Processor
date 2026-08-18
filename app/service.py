"""Core service logic for the FastAPI backend: checkpointer ownership,
per-thread concurrency control, and the two functions that translate
between LangGraph's StateSnapshot/Command world and the API's status world.

`derive_status()` and `build_resume_command()` are pure functions - no I/O,
no locking - deliberately split out so they're unit-testable against a
hand-built StateSnapshot or an in-memory SqliteSaver, with no TestClient and
no network. See tests/test_api_service.py.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from pydantic import ValidationError

from app.errors import (
    InvalidInvoice,
    ServerBusy,
    ThreadBusy,
    ThreadConflict,
    ThreadFailedRetryOnly,
    ThreadNotFound,
)
from app.models import InvoiceCorrections
from invoice_agent.graph import build_invoke_config
from invoice_agent.schema import Invoice

DEFAULT_MAX_CONCURRENCY = 2
SEMAPHORE_ACQUIRE_TIMEOUT = 30  # seconds


@dataclass
class DerivedStatus:
    """The result of interpreting a StateSnapshot - everything RunResponse
    needs except thread_id, which the caller already has."""

    status: str
    doc_type: Optional[str] = None
    invoice: Optional[dict] = None
    validation: Optional[dict] = None
    flags: Optional[list[str]] = None
    current_node: Optional[str] = None
    failed_at_node: Optional[str] = None


def derive_status(snapshot: StateSnapshot) -> Optional[DerivedStatus]:
    """Map a StateSnapshot to the single API-level status.

    `values["status"]` (the GraphState field) is stale in three of the five
    reachable states, so it is consulted LAST and only once the run is
    terminal. Branch order is load-bearing - each comment below states the
    fact it depends on, verified against this repo's langgraph 1.2.10:

      - An unknown thread_id does not raise from get_state(); it returns
        values={}, next=(), interrupts=(), created_at=None. created_at is
        the only sound "unknown thread" discriminator - an empty values
        dict alone would also match a hypothetical empty terminal run.
      - A node that raised leaves a pending task whose `.error` is a
        persisted, non-None *string* (not an exception object) - readable
        even from a fresh checkpointer connection. At that point `next` is
        non-empty and `values["status"]` still reads whatever the
        *previous* successful node set (e.g. "validated" after the
        validator ran but before output() raised) - so a failure must be
        checked before the "still executing" branch, never derived from
        status.
      - `.interrupts` is a top-level StateSnapshot field (flattened across
        tasks). Read the invoice/flags payload from there, not from
        `values` - `human_review()` hasn't returned yet at an interrupt, so
        `values["status"]` happens to read "needs_review" (set by the
        validator) but that's incidental, not the source of truth.

    Returns None for an unknown thread_id - callers translate that to 404.
    """
    if snapshot.created_at is None:
        return None

    values = snapshot.values or {}

    failed = [task for task in snapshot.tasks if task.error is not None]
    if failed:
        return DerivedStatus(status="failed", failed_at_node=failed[0].name)

    if snapshot.interrupts:
        payload = snapshot.interrupts[0].value
        return DerivedStatus(
            status="needs_review",
            invoice=payload.get("invoice"),
            flags=payload.get("flags", []),
        )

    if snapshot.next:
        return DerivedStatus(status="processing", current_node=snapshot.next[0])

    status = values.get("status")
    if status == "completed":
        return DerivedStatus(
            status="completed",
            invoice=values.get("invoice"),
            validation=values.get("validation"),
        )
    if status == "skipped":
        # Router sent a receipt/other document straight to END - a
        # legitimate terminal state with no invoice at all.
        return DerivedStatus(status="skipped", doc_type=values.get("doc_type"))

    # Unreachable in the current graph topology: every path to END goes
    # through output() (-> "completed") or the router's skip edge
    # (-> "skipped"). Fail closed rather than inventing a success.
    return DerivedStatus(status="failed", failed_at_node=None)


def build_resume_command(snapshot: StateSnapshot, corrections: InvoiceCorrections) -> Command:
    """Turn a human's correction patch into the Command LangGraph expects.

    Merges the patch over the invoice the human was actually shown (the
    interrupt payload, not any other copy of it), then validates the
    result as a real `Invoice` *before* the graph is touched. Three
    concrete failures this prevents:
      - Omitting `line_items` would make `db.insert_invoice` run an
        unfiltered `delete().eq("invoice_id", ...)`, wiping every line
        item already stored for the invoice.
      - A malformed merged invoice would raise `ValidationError` inside
        the validator *node*, wedging the thread as an opaque 500 instead
        of a clean 422 at the boundary.
      - `extra="forbid"` on InvoiceCorrections keeps stray keys from ever
        reaching PostgREST as an unexpected column.

    Caller must have already confirmed `snapshot.interrupts` is non-empty -
    this function only handles the interrupted-thread resume path, not the
    failed-thread retry path (see GraphService.resume_run).
    """
    base_invoice = snapshot.interrupts[0].value["invoice"]
    patch = corrections.model_dump(exclude_unset=True)

    if not patch:
        # Matches scripts/run_graph.py's "blank input = accept as-is".
        return Command(resume={"edited_invoice": None})

    merged = {**base_invoice, **patch}
    try:
        canonical = Invoice.model_validate(merged)
    except ValidationError as exc:
        raise InvalidInvoice(f"Corrected invoice failed validation: {exc}") from exc

    return Command(resume={"edited_invoice": canonical.model_dump(mode="json")})


class GraphService:
    """Owns the compiled graph + checkpointer for the app's lifetime, and
    the concurrency controls around invoking it.

    Built in app/main.py's lifespan with `build_graph(saver)` - NEVER
    `get_graph()`. Two SqliteSaver instances over two connections have two
    different internal locks, which would reintroduce exactly the race the
    per-thread lock below exists to close.
    """

    def __init__(self, graph: CompiledStateGraph, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        self._graph = graph
        self._semaphore = threading.Semaphore(max_concurrency)
        self._thread_locks: dict[str, threading.Lock] = {}
        self._thread_locks_guard = threading.Lock()

    def _lock_for(self, thread_id: str) -> threading.Lock:
        # One Lock object per thread_id ever seen, kept for the process's
        # lifetime - a few hundred bytes each, acceptable for a portfolio
        # demo's traffic. A production version would evict locks for
        # terminal threads; not worth the complexity here.
        with self._thread_locks_guard:
            lock = self._thread_locks.get(thread_id)
            if lock is None:
                lock = threading.Lock()
                self._thread_locks[thread_id] = lock
            return lock

    @contextmanager
    def _guarded_invoke(self, thread_id: str):
        """Acquire the per-thread lock (non-blocking - 409 if held) and the
        global semaphore (blocking with a timeout - 503 if saturated)
        around one graph.invoke() call.

        SqliteSaver already serializes its own I/O safely under concurrent
        *distinct*-thread invokes (internal threading.Lock + WAL) - this
        lock is not compensating for that. It exists because concurrent
        invokes against the *same* thread_id were verified to clobber each
        other last-writer-wins with no error raised by LangGraph itself
        (e.g. a double-clicked "Approve & Save" in the Phase 9 frontend).
        """
        lock = self._lock_for(thread_id)
        if not lock.acquire(blocking=False):
            raise ThreadBusy(
                "This run is already being processed by another request.", thread_id=thread_id
            )
        try:
            if not self._semaphore.acquire(timeout=SEMAPHORE_ACQUIRE_TIMEOUT):
                raise ServerBusy(
                    "The server is at capacity - please retry shortly.",
                    thread_id=thread_id,
                    retry_after=30,
                )
            try:
                yield
            finally:
                self._semaphore.release()
        finally:
            lock.release()

    def get_snapshot(self, thread_id: str) -> StateSnapshot:
        return self._graph.get_state(build_invoke_config(thread_id))

    def start_run(self, thread_id: str, file_path: str) -> dict:
        initial_state = {
            "file_path": file_path,
            "doc_type": "",
            "invoice": None,
            "validation": None,
            "status": "pending",
            "messages": [],
        }
        with self._guarded_invoke(thread_id):
            return self._graph.invoke(initial_state, config=build_invoke_config(thread_id))

    def resume_run(self, thread_id: str, corrections: InvoiceCorrections) -> dict:
        snapshot = self.get_snapshot(thread_id)
        derived = derive_status(snapshot)
        if derived is None:
            raise ThreadNotFound(f"No run found for thread_id={thread_id!r}.", thread_id=thread_id)

        patch = corrections.model_dump(exclude_unset=True)

        if derived.status == "needs_review":
            command = build_resume_command(snapshot, corrections)
        elif derived.status == "failed":
            if patch:
                raise ThreadFailedRetryOnly(
                    "This run already failed and is not paused for review - retry with "
                    "an empty corrections body, or start a new run.",
                    thread_id=thread_id,
                )
            # invoke(None, config) re-executes the failed node from the top
            # without needing a Command at all - this is the retry path,
            # not a separate endpoint.
            command = None
        elif derived.status == "processing":
            raise ThreadBusy(
                f"This run is still processing (currently at {derived.current_node!r}).",
                thread_id=thread_id,
            )
        else:  # "completed" or "skipped"
            raise ThreadConflict(
                f"This run already finished (status={derived.status!r}) and cannot be resumed.",
                thread_id=thread_id,
            )

        with self._guarded_invoke(thread_id):
            return self._graph.invoke(command, config=build_invoke_config(thread_id))

    def list_runs(self, limit: int = 20, overfetch: int = 200) -> list[DerivedStatus | dict]:
        """Recent runs, newest first, deduped by thread_id.

        `checkpointer.list` yields one row per *checkpoint*, not per
        thread, so this over-fetches and keeps only the newest row seen per
        thread_id (checkpoints come back newest-first) before truncating.

        `SqliteSaver.list()` holds its internal lock for the entire span of
        the generator - its cursor() context manager acquires the lock
        before the `with` block that wraps `yield`, so the lock isn't
        released until the generator is exhausted or closed. Materialize it
        into a plain list *before* calling get_state() below: get_state()
        needs that same non-reentrant lock, so interleaving the two (as a
        single `for checkpoint_tuple in checkpointer.list(...): ...
        get_state(...)` loop) deadlocks the thread against itself -
        verified directly (test hung until this was fixed).
        """
        checkpoint_tuples = list(self._graph.checkpointer.list(None, limit=overfetch))

        rows: list[dict] = []
        seen: set[str] = set()
        for checkpoint_tuple in checkpoint_tuples:
            thread_id = checkpoint_tuple.config["configurable"]["thread_id"]
            if thread_id in seen:
                continue
            seen.add(thread_id)

            snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
            derived = derive_status(snapshot)
            if derived is None:
                continue
            rows.append(
                {
                    "thread_id": thread_id,
                    "status": derived.status,
                    "doc_type": derived.doc_type,
                    "created_at": snapshot.created_at,
                }
            )
            if len(rows) >= limit:
                break
        return rows
