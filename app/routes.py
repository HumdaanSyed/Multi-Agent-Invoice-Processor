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
from fastapi import APIRouter, File, Query, Request, Response, UploadFile

from app.errors import (
    ApiError,
    PdfUnprocessable,
    PersistenceFailed,
    RunFailed,
    ThreadNotFound,
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
)
from app.service import DerivedStatus, GraphService, derive_status
from app.uploads import save_upload, sweep_old_uploads

router = APIRouter()


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


def _translate_graph_exception(exc: Exception, thread_id: str) -> ApiError:
    """Map an exception raised out of graph.invoke() to the API's error
    hierarchy. Anthropic exceptions propagate raw - neither router() nor
    extract_invoice() catches anything. A Supabase/Storage failure inside
    output() surfaces as a RuntimeError whose message embeds vendor_name,
    invoice_number, and a storage path - rich detail for the server log,
    never for the response body (see app/errors.py's module docstring)."""
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
    if isinstance(exc, RuntimeError):
        return PersistenceFailed("Saving the processed invoice failed.", thread_id=thread_id)
    return RunFailed("An unexpected error occurred while processing this run.", thread_id=thread_id)


@router.post("/invoices", response_model=RunResponse)
def create_invoice_run(request: Request, file: UploadFile = File(...)) -> RunResponse:
    service = _service(request)
    upload_dir = request.app.state.upload_dir
    sweep_old_uploads(upload_dir)

    thread_id = str(uuid.uuid4())
    pdf_path = save_upload(file, thread_id=thread_id, upload_dir=upload_dir)

    try:
        service.start_run(thread_id, str(pdf_path))
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate every graph failure into the API's shape
        raise _translate_graph_exception(exc, thread_id) from exc

    return _to_run_response(thread_id, _current_status(service, thread_id))


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
        raise _translate_graph_exception(exc, thread_id) from exc

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
