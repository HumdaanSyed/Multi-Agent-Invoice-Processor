"""HTTP endpoints - thin: validate input, delegate to app.service /
app.uploads, translate the result into a response model. All business logic
(status derivation, resume semantics, concurrency control) lives in
app/service.py; none of it is duplicated here.

Every handler that touches the graph is a sync `def`, not `async def` -
`SqliteSaver` has no working async methods (see app/service.py's module
docstring), so `await graph.ainvoke(...)`/`aget_state(...)` would fail
outright. FastAPI runs sync handlers in a threadpool, which keeps the event
loop free for concurrent `/health` and `/docs` requests while a blocking
`graph.invoke()` runs. `GET /health` is the one exception: zero I/O, no
reason not to stay on the event loop.

Route paths here are mirrored by hand in deploy/ec2.md's nginx `location`
regex (browser and backend share one origin there) - add or rename a route
and update that block too.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.errors import (
    ApiError,
    PdfUnprocessable,
    PersistenceFailed,
    RunFailed,
    ThreadNotFound,
    UnprocessablePayload,
    UpstreamMisconfigured,
    UpstreamRateLimited,
    UpstreamUnavailable,
)
from app.health import check_readiness
from app.models import (
    ExportStatusResponse,
    HealthResponse,
    ReadinessResponse,
    ResumeRequest,
    RunListResponse,
    RunResponse,
    RunSummary,
    RunTicket,
)
from app.service import DerivedStatus, GraphService, derive_status
from app.uploads import save_upload, sweep_old_uploads
from invoice_agent import db, events, tracing

router = APIRouter()
logger = logging.getLogger("app.routes")


def _service(request: Request) -> GraphService:
    return request.app.state.graph_service


def _to_run_response(thread_id: str, derived: DerivedStatus) -> RunResponse:
    return RunResponse(
        thread_id=thread_id,
        status=derived.status,
        doc_type=derived.doc_type,
        invoice=derived.invoice,
        validation=derived.validation,
        flags=derived.flags,
        current_node=derived.current_node,
        failed_at_node=derived.failed_at_node,
        # Unlike pdf_url/trace_url (GET-only, see get_invoice_run - both
        # need an external I/O call this shared builder deliberately never
        # makes), ledger_status is plain state already sitting in
        # GraphState with no extra work to attach - safe to include on
        # every endpoint that reaches "completed", not just GET, so a run
        # watched live to completion knows its own ledger outcome without
        # waiting on the SSE "ledger" event or a follow-up fetch.
        ledger_status=derived.ledger_status,
    )


def _current_status(service: GraphService, thread_id: str) -> DerivedStatus:
    """Status via get_state()/derive_status() after an invoke, rather than
    trusting graph.invoke()'s own return value - so POST, GET, and resume
    all build their response through the exact same derive_status() path
    instead of two subtly-different ones that could drift apart.

    `pop_cached_status` short-circuits the common case: `_publish_run_end`
    (app/service.py) already ran this exact computation moments earlier,
    inside the same invoke() call, to decide what to publish - reusing it
    here skips a second checkpoint read + full state deserialization on
    every request. A cache miss (nothing published, or a GET with no prior
    invoke this request) falls back to the original fresh read."""
    cached = service.pop_cached_status(thread_id)
    if cached is not None:
        return cached
    snapshot = service.get_snapshot(thread_id)
    derived = derive_status(snapshot)
    assert derived is not None  # a thread we just invoked always exists
    return derived


def _failed_node(service: GraphService, thread_id: str) -> str | None:
    """Best-effort lookup of which node's failure the checkpointer just
    recorded, for _translate_graph_exception's RuntimeError classification.
    LangGraph persists a failed task's error before the exception ever
    reaches the caller (verified in app/service.py's derive_status()
    docstring), so this is available immediately after a graph.invoke()
    raises. Returns None if the checkpointer itself can't be read - a
    diagnostic aid only, must not mask the real exception being handled."""
    try:
        derived = derive_status(service.get_snapshot(thread_id))
    except Exception:  # noqa: BLE001 - diagnostic aid only, never the primary error path
        return None
    return derived.failed_at_node if derived is not None else None


def _translate_graph_exception(exc: Exception, thread_id: str, *, failed_node: str | None) -> ApiError:
    """Map an exception raised out of graph.invoke() to the API's error
    hierarchy, logging the original exception first.

    RunResponse's docstring (app/models.py) promises "full detail goes to
    the server log, keyed by thread_id" for a failed run - this is where
    that actually happens. Every ApiError subclass's own handler in
    app/errors.py deliberately does NOT log (translating an error there
    doesn't know yet whether it's worth a log line at all - a 404 or a 422
    isn't); this is the one place a *graph* failure is turned into a safe,
    generic response, so it's the right place to keep the real exception
    (with its `__cause__`/traceback, and everything output()'s RuntimeError
    embeds - vendor_name, invoice_number, storage path) somewhere an
    operator can actually find it, rather than only in the checkpointer's
    `tasks[].error` column.

    `failed_node` disambiguates a genuine output() persistence failure from
    a RuntimeError raised earlier in the graph - both look identical by
    type alone. Concretely: validator() calls db.is_duplicate ->
    db.get_client(), which raises a plain RuntimeError if Supabase isn't
    configured - and that happens before output() ever runs. Anthropic
    exceptions propagate raw (neither router() nor extract_invoice()
    catches anything), so those are matched by type below; only the
    RuntimeError case needs this extra check.
    """
    logger.exception("Graph run failed (thread_id=%s, failed_node=%s)", thread_id, failed_node)

    if isinstance(exc, RateLimitError):
        retry_after = None
        try:
            retry_after = int(exc.response.headers.get("retry-after"))
        except (TypeError, ValueError, AttributeError):
            pass
        return UpstreamRateLimited(
            "The extraction service is rate-limited - try again shortly.",
            thread_id=thread_id,
            retry_after=retry_after,
        )
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        # 503, not 401/403 - it's our misconfiguration, and a 401 would
        # tell the *client* to authenticate to us, which is meaningless.
        return UpstreamMisconfigured("Extraction service is not configured correctly.", thread_id=thread_id)
    if isinstance(exc, BadRequestError):
        return PdfUnprocessable("The uploaded PDF could not be processed.", thread_id=thread_id)
    if isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError, APIStatusError)):
        return UpstreamUnavailable(
            "The extraction service is temporarily unavailable - try again shortly.", thread_id=thread_id
        )
    if isinstance(exc, RuntimeError) and failed_node == "output":
        return PersistenceFailed("Saving the processed invoice failed.", thread_id=thread_id)
    return RunFailed("An unexpected error occurred while processing this run.", thread_id=thread_id)


@router.post("/invoices/reserve", response_model=RunTicket)
def reserve_invoice_run(request: Request) -> RunTicket:
    """Mint a thread_id and open its SSE channel *before* any upload or
    graph work exists (Phase 8.5).

    POST /invoices is a blocking call - by the time it returns, the run
    (and every field/check event it would ever publish) has already
    happened. A client that wants to watch a run live has to know the
    thread_id, and have a stream already open, *before* that POST -
    which is exactly what this endpoint is for. Pass the returned
    thread_id back on POST /invoices to use it, or ignore it entirely and
    POST without one (see that handler) - reserving is optional, not
    required, for a client that doesn't care about live streaming.
    """
    service = _service(request)
    thread_id = service.reserve()
    return RunTicket(thread_id=thread_id, stream_url=f"/invoices/{thread_id}/stream")


@router.post("/invoices", response_model=RunResponse)
def create_invoice_run(
    request: Request, file: UploadFile = File(...), thread_id: str | None = Form(None)
) -> RunResponse:
    service = _service(request)
    upload_dir = request.app.state.upload_dir
    sweep_old_uploads(upload_dir)

    if thread_id is not None:
        # A reserved id, from POST /invoices/reserve - not client-invented.
        # save_upload() builds a filesystem path from thread_id, so this
        # must be a value this server minted, never an arbitrary string a
        # caller supplies (see app/uploads.py's docstring on untrusted
        # filenames/ids as path components). A reserved-but-unknown-to-the-
        # checkpointer id satisfies both checks; anything else - a made-up
        # string, or an id that's already a real run - is rejected before
        # any file touches disk.
        if not events.has_channel(thread_id) or derive_status(service.get_snapshot(thread_id)) is not None:
            raise UnprocessablePayload(
                f"thread_id={thread_id!r} was not reserved via POST /invoices/reserve, "
                "or has already been used."
            )
    else:
        thread_id = str(uuid.uuid4())

    pdf_path = save_upload(file, thread_id=thread_id, upload_dir=upload_dir)

    try:
        service.start_run(thread_id, str(pdf_path))
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate every graph failure into the API's shape
        raise _translate_graph_exception(exc, thread_id, failed_node=_failed_node(service, thread_id)) from exc

    return _to_run_response(thread_id, _current_status(service, thread_id))


@router.get("/invoices/{thread_id}/stream")
async def stream_invoice_run(thread_id: str, request: Request) -> StreamingResponse:
    """SSE feed of a run's progress - field-by-field extraction events,
    per-check validation events, stage changes, and a terminal event.

    Async (every other graph-touching handler in this module is sync, on
    purpose - see this module's docstring) because it awaits an
    asyncio.Queue for as long as the connection is open; it never touches
    the graph or the checkpointer directly except via run_in_threadpool,
    since a blocking SQLite read on the event loop would stall every other
    concurrent request.

    Three cases, all documented in docs/api.md:
      - No channel, and the checkpointer has never heard of thread_id ->
        404, same ThreadNotFound as GET /invoices/{thread_id}.
      - No channel, but the run already happened (completed, or the
        channel was swept) -> a one-shot stream: a single `snapshot`
        event carrying the same status GET /invoices/{thread_id} would
        return, then `done`.
      - A channel is open -> replay its history (so a late subscriber
        sees everything, and `Last-Event-ID` reconnects pick up where
        they left off), then live events, until `run_end`/`interrupted`
        closes the connection.
    """
    service = _service(request)

    def _snapshot_events(derived: DerivedStatus) -> list[events.Event]:
        response = _to_run_response(thread_id, derived)
        return [
            events.Event(type="snapshot", thread_id=thread_id, seq=0, payload=response.model_dump(mode="json")),
            events.Event(type="done", thread_id=thread_id, seq=1, payload={}),
        ]

    async def _async_snapshot_chunks(derived: DerivedStatus):
        for event in _snapshot_events(derived):
            yield event.to_sse()

    async def _channel_stream(last_event_id: int | None):
        # is_current (from events.subscribe()) is False only for a backlog
        # item something newer already supersedes - see that function's
        # docstring for why a resumed run needs this distinction. Closing
        # only on a *current* terminal event is what keeps a stale
        # historical interrupt from truncating the reply before a resume's
        # own events are sent.
        try:
            async for item, is_current in events.subscribe(thread_id, last_event_id=last_event_id):
                if item is None:
                    yield ": keep-alive\n\n"
                    continue
                yield item.to_sse()
                if is_current and item.type in events.TERMINAL_EVENT_TYPES:
                    return
        except events.ChannelClosed:
            # Raced: the channel closed between has_channel() below and
            # attaching here (the run just finished). Fall back to the
            # same snapshot a fresh request would get.
            derived = await run_in_threadpool(_get_derived_or_none, service, thread_id)
            if derived is None:
                # By this point the 200 status and text/event-stream
                # headers are already committed (StreamingResponse sends
                # them before ever pulling from this generator) - raising
                # ThreadNotFound here would try to send a 404 over an
                # already-started response, producing a broken stream
                # instead of a clean error. Degrade to a well-formed SSE
                # frame and a clean close instead of an HTTP-level error;
                # this exact case (the channel vanishing between the
                # has_channel() check below and subscribe() attaching -
                # e.g. a concurrent reserve's sweep_stale() reaping a
                # never-started reservation) is rare, but a client should
                # still get *something* parseable over the connection it
                # was handed a 200 for.
                error = events.Event(
                    type="error",
                    thread_id=thread_id,
                    seq=0,
                    payload={"code": "thread_not_found", "message": f"No run found for thread_id={thread_id!r}."},
                )
                yield error.to_sse()
                return
            async for chunk in _async_snapshot_chunks(derived):
                yield chunk

    # Resolved *before* constructing StreamingResponse - once that response
    # exists, Starlette sends the 200 status line immediately, before ever
    # pulling from the generator, so a 404 raised from inside the generator
    # would arrive over an already-started response instead of a clean
    # JSON error. Everything that can 404 is decided here, synchronously.
    if not events.has_channel(thread_id):
        derived = await run_in_threadpool(_get_derived_or_none, service, thread_id)
        if derived is None:
            raise ThreadNotFound(f"No run found for thread_id={thread_id!r}.", thread_id=thread_id)
        generator = _async_snapshot_chunks(derived)
    else:
        # The header is what a browser's own EventSource auto-reconnect
        # sends - but this app never relies on that (it always opens a
        # fresh connection itself, e.g. after a resume, rather than letting
        # the native reconnect fire), and EventSource gives no way to set a
        # custom header on a manually-constructed connection. The query
        # param is the standard workaround, so a client that tracks its own
        # last-seen id (web/hooks/use-run.ts) can still avoid replaying
        # history it already has.
        raw_last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        try:
            last_event_id = int(raw_last_event_id) if raw_last_event_id is not None else None
        except ValueError:
            last_event_id = None
        generator = _channel_stream(last_event_id)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # defeats reverse-proxy buffering (e.g. Railway's)
        },
    )


def _get_derived_or_none(service: GraphService, thread_id: str) -> DerivedStatus | None:
    return derive_status(service.get_snapshot(thread_id))


@router.get("/invoices/{thread_id}", response_model=RunResponse)
def get_invoice_run(thread_id: str, request: Request) -> RunResponse:
    """The one place `pdf_url`/`trace_url` get populated (see RunResponse's
    docstring) - both are best-effort convenience links for the frontend's
    read-only detail view (docs/FRONTEND_PLAN.md's Phase 9D), so a request
    for a run that hasn't reached `completed` yet simply gets neither, with
    no extra I/O attempted. `get_pdf_signed_url` caches its own result, so
    this isn't a fresh Supabase Storage call on every request.

    `derived.pdf_storage_path` is only ever set for a checkpoint that ran
    through the post-Phase-9D `output()` node (`invoice_agent/graph.py`) -
    a run completed before that falls back to `db.get_invoice_pdf_storage_path`,
    which reads the same column straight off the `invoices` table row
    (written by every `output()` that ever existed, old or new), so a
    historical invoice's "source PDF" link isn't permanently missing just
    because its checkpoint predates this column.
    """
    service = _service(request)
    snapshot = service.get_snapshot(thread_id)
    derived = derive_status(snapshot)
    if derived is None:
        raise ThreadNotFound(f"No run found for thread_id={thread_id!r}.", thread_id=thread_id)
    response = _to_run_response(thread_id, derived)
    if derived.status == "completed":
        pdf_storage_path = derived.pdf_storage_path
        if not pdf_storage_path and derived.invoice:
            pdf_storage_path = db.get_invoice_pdf_storage_path(
                derived.invoice.get("vendor_name", ""), derived.invoice.get("invoice_number", "")
            )
        if pdf_storage_path:
            response.pdf_url = db.get_pdf_signed_url(pdf_storage_path)
        response.trace_url = tracing.trace_url(thread_id)
    return response


@router.post("/invoices/{thread_id}/resume", response_model=RunResponse)
def resume_invoice_run(thread_id: str, body: ResumeRequest, request: Request) -> RunResponse:
    service = _service(request)
    try:
        service.resume_run(thread_id, body.corrections)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _translate_graph_exception(exc, thread_id, failed_node=_failed_node(service, thread_id)) from exc

    return _to_run_response(thread_id, _current_status(service, thread_id))


@router.get("/invoices", response_model=RunListResponse)
def list_invoice_runs(request: Request, limit: int = Query(20, ge=1, le=100)) -> RunListResponse:
    service = _service(request)
    rows = service.list_runs(limit=limit)
    return RunListResponse(runs=[RunSummary(**row) for row in rows])


def _stream_csv_rows(rows, writer: "csv.DictWriter", buffer: io.StringIO, error_row: dict):
    """Shared shape for streaming DictWriter rows as they're produced, with
    a failure mid-iteration caught and reported in-band rather than left to
    abort the response. Reuse this for the next Supabase-backed CSV export
    instead of re-copying the try/except.

    By the time any caller of this generator is pulling from it, the 200
    status and `text/csv` headers are already committed (StreamingResponse
    sends them before ever pulling the first chunk - same reasoning as
    stream_invoice_run's `ChannelClosed` handling for the SSE side of this
    problem, documented in REVIEW.md), so a mid-stream failure can only be
    caught and reported in-band - it can never become a clean HTTP error.
    `error_row` must already be shaped as a valid row for `writer` (every
    fieldname present) - not a raw string - so it round-trips through
    ordinary CSV readers as one extra, clearly-flagged row instead of a
    "#"-prefixed comment line, which RFC 4180 has no concept of and which
    pandas/csv.DictReader would otherwise silently misparse as a malformed
    data row.
    """
    try:
        for row in rows:
            writer.writerow(row)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
    except Exception:
        logger.exception("CSV export failed partway through streaming")
        writer.writerow(error_row)
        yield buffer.getvalue()


def _export_csv_chunks():
    """Sync generator, streamed via StreamingResponse's automatic
    threadpool wrapping (Starlette wraps any non-async-iterable content
    with `iterate_in_threadpool`) - consistent with every other Supabase-
    touching handler in this module being a plain sync `def`. Builds one
    CSV line per DictWriter call via a reused StringIO buffer, so the
    ledger is never held in memory as a single string (the 1GB-RAM
    constraint this project targets) no matter how many rows it has -
    `db.iter_export_rows()` itself pages through Supabase rather than
    fetching everything in one call.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=db.LEDGER_CSV_FIELDS)

    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    def _ledger_csv_rows():
        for ledger_row in db.iter_export_rows():
            yield from db.export_ledger_row_to_csv_rows(ledger_row)

    error_row = dict.fromkeys(db.LEDGER_CSV_FIELDS, "")
    error_row[db.LEDGER_CSV_FIELDS[0]] = "ERROR: export truncated - see server logs"
    yield from _stream_csv_rows(_ledger_csv_rows(), writer, buffer, error_row)


@router.get("/export.csv")
def download_export_csv() -> StreamingResponse:
    """The full export ledger, ordered oldest-first, as a CSV download -
    every successful invoice write ever persisted, including every
    corrected re-run as its own row (the ledger is append-only, never
    deduplicated - see REVIEW.md)."""
    return StreamingResponse(
        _export_csv_chunks(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )


@router.get("/export/status", response_model=ExportStatusResponse)
def export_status() -> ExportStatusResponse:
    """Row count and freshness of the export ledger, for a persistent
    "N invoices exported" UI element that doesn't need to download the
    whole CSV just to show that."""
    return ExportStatusResponse(**db.export_status())


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Static liveness - zero I/O. What Phase 10's Docker HEALTHCHECK hits;
    see app/health.py's module docstring for why this must never make a
    network call."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadinessResponse)
def readiness(request: Request, response: Response, deep: bool = Query(False)) -> ReadinessResponse:
    service = _service(request)
    result = check_readiness(service, deep=deep)
    if result.status != "ok":
        response.status_code = 503
    return result
