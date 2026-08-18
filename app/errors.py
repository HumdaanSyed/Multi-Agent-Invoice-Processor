"""API-only exception hierarchy and its translation to HTTP responses.

Deliberately NOT reusing invoice_agent's RuntimeError/ValueError convention
as the source of HTTP status codes. Two reasons: `pydantic.ValidationError`
is itself a `ValueError` subclass, so a blanket ValueError->400 mapping would
report a server-side data problem (a corrupted checkpointed invoice) as a
client mistake; and the domain layer's RuntimeError messages are written for
a developer at a terminal and deliberately embed operational detail
(vendor_name, invoice_number, Storage paths, `.env` setup instructions,
see invoice_agent/db.py and invoice_agent/graph.py's output()) that must not
cross an unauthenticated HTTP boundary.

`ApiError` is raised explicitly at the call site in app/service.py or
app/routes.py, where the meaning of a failure is actually known - this
module only defines the hierarchy and wires up the three handlers that turn
it (plus FastAPI's own RequestValidationError, plus any unanticipated
exception) into the single `ErrorResponse` shape from app/models.py.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models import ErrorResponse

logger = logging.getLogger("app.errors")


class ApiError(Exception):
    """Base for every error this API raises deliberately. Carries the HTTP
    status to respond with, a stable machine-readable `code` for client
    branching, a human-safe `message`, and the `thread_id` if one was ever
    minted for this request - a run that failed after extraction already
    cost money, so thread_id is the only way to recover or retry it."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, thread_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.thread_id = thread_id


class ThreadNotFound(ApiError):
    status_code = 404
    code = "thread_not_found"


class ThreadConflict(ApiError):
    """Base for the three 409 cases - resume attempted on a thread that
    isn't currently interrupted, for one of three distinct reasons."""

    status_code = 409
    code = "thread_not_interrupted"


class ThreadBusy(ThreadConflict):
    """Either genuinely mid-flight, or the per-thread lock is held by a
    concurrent request against the same thread_id (see app/service.py)."""

    code = "thread_busy"


class ThreadFailedRetryOnly(ThreadConflict):
    """The thread failed in a node with no pending interrupt - there is
    nothing to apply a correction to. An empty-body resume (retry) is
    accepted; this is raised only when the body carries corrections."""

    code = "thread_failed_retry_only"


class UploadRejected(ApiError):
    status_code = 400
    code = "invalid_upload"


class EmptyUpload(UploadRejected):
    status_code = 400
    code = "empty_upload"


class UploadTooLarge(UploadRejected):
    status_code = 413
    code = "upload_too_large"


class UnsupportedMediaType(UploadRejected):
    status_code = 415
    code = "unsupported_media_type"


class UnprocessablePayload(ApiError):
    status_code = 422
    code = "invalid_request"


class InvalidInvoice(UnprocessablePayload):
    """A resume's merged invoice failed `Invoice.model_validate()` at the
    HTTP boundary - see app/service.py's build_resume_command()."""

    code = "invalid_invoice"


class PdfUnprocessable(UnprocessablePayload):
    """Anthropic rejected the PDF itself as malformed/unreadable, not a
    transport or auth problem."""

    code = "pdf_unprocessable"


class RetryableApiError(ApiError):
    """Base for errors that carry a Retry-After hint."""

    def __init__(self, message: str, *, thread_id: Optional[str] = None, retry_after: Optional[int] = None) -> None:
        super().__init__(message, thread_id=thread_id)
        self.retry_after = retry_after


class UpstreamRateLimited(RetryableApiError):
    status_code = 429
    code = "upstream_rate_limited"


class UpstreamUnavailable(RetryableApiError):
    status_code = 503
    code = "upstream_unavailable"


class UpstreamMisconfigured(UpstreamUnavailable):
    """Anthropic rejected our credentials/permissions. Reported as 503, not
    401/403 - it's our misconfiguration, and a 401 would tell the *client*
    to authenticate to us, which is meaningless here."""

    code = "upstream_misconfigured"


class PersistenceFailed(UpstreamUnavailable):
    """`output()` raised while writing to Supabase/Storage. The thread_id
    is always attached so the caller can retry via an empty-body resume."""

    code = "persistence_failed"


class ServiceNotConfigured(UpstreamUnavailable):
    """A required env var is missing or still a placeholder value."""

    code = "service_not_configured"


class ServerBusy(RetryableApiError):
    """The global concurrency semaphore is saturated."""

    status_code = 503
    code = "server_busy"


class RunFailed(ApiError):
    """Catch-all for a graph.invoke() exception not otherwise classified."""

    status_code = 500
    code = "internal_error"


def _error_json(
    status_code: int,
    error: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    detail: Optional[list] = None,
    headers: Optional[dict] = None,
) -> JSONResponse:
    body = ErrorResponse(error=error, message=message, thread_id=thread_id, detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    headers: dict[str, str] = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return _error_json(
        exc.status_code, exc.code, exc.message, thread_id=exc.thread_id, headers=headers or None
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default 422 body shape differs from ours; normalize it so
    # every 422 in this API - ours and pydantic's - looks the same to a
    # client. jsonable_encoder because exc.errors() can carry non-JSON
    # values (e.g. a ValueError instance) in a 'ctx' key.
    return _error_json(
        422, "invalid_request", "Request failed validation.", detail=jsonable_encoder(exc.errors())
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return _error_json(500, "internal_error", "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
