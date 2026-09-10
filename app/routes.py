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
"""

from __future__ import annotations

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
from invoice_agent import events

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
    )


def _current_status(service: GraphService, thread_id: str) -> DerivedStatus:
    """Re-reads status via get_state()/derive_status() after an invoke,
    rather than trusting graph.invoke()'s own return value - so POST, GET,
    and resume all build their response through the exact same path instead
    of two subtly-different ones that could drift apart."""
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

    def _snapshot_chunks(derived: DerivedStatus):
        response = _to_run_response(thread_id, derived)
        yield f"id: 0\nevent: snapshot\ndata: {response.model_dump_json(exclude_none=True)}\n\n"
        yield "id: 1\nevent: done\ndata: {}\n\n"

    async def _async_snapshot_chunks(derived: DerivedStatus):
        for chunk in _snapshot_chunks(derived):
            yield chunk

    async def _channel_stream(last_event_id: int | None):
        # A resume re-enters at human_review -> validator (unconditional
        # edge), so a run that interrupted and was then resumed republishes
        # a fresh validation_start/checks/interrupted-or-run_end - leaving
        # an earlier "interrupted" sitting mid-history with more, newer
        # events after it. Only closing the connection on the *last*
        # replayed item (backlog_length()) - not on the first terminal-type
        # event encountered - is what keeps a stale historical interrupt
        # from truncating the reply before the resume's own events are
        # sent. Any live event (arriving after the count exceeds this) is
        # unambiguous and always closes on a terminal type, same as before.
        expected_backlog = events.backlog_length(thread_id, last_event_id)
        seen = 0
        try:
            async for item in events.subscribe(thread_id, last_event_id=last_event_id):
                if item is None:
                    yield ": keep-alive\n\n"
                    continue
                seen += 1
                yield item.to_sse()
                if item.type in events.TERMINAL_EVENT_TYPES and seen >= expected_backlog:
                    return
        except events.ChannelClosed:
            # Raced: the channel closed between has_channel() below and
            # attaching here (the run just finished). Fall back to the
            # same snapshot a fresh request would get - safe to raise
            # ThreadNotFound from inside this generator if that lookup
            # also comes back empty, since no bytes have been sent on
            # *this* path yet either (the caller only reaches here after
            # has_channel() was already True, i.e. streaming has begun,
            # but no chunk has been yielded on this specific branch).
            derived = await run_in_threadpool(_get_derived_or_none, service, thread_id)
            if derived is None:
                raise ThreadNotFound(f"No run found for thread_id={thread_id!r}.", thread_id=thread_id)
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
        raw_last_event_id = request.headers.get("last-event-id")
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
    service = _service(request)
    snapshot = service.get_snapshot(thread_id)
    derived = derive_status(snapshot)
    if derived is None:
        raise ThreadNotFound(f"No run found for thread_id={thread_id!r}.", thread_id=thread_id)
    return _to_run_response(thread_id, derived)


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
