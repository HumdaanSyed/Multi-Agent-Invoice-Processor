"""Thin, pure `requests` wrapper around the FastAPI backend (Phase 8, `app/`).

Zero Streamlit imports here, on purpose - this module is unit-testable by
mocking exactly one seam (`requests.request`), independent of any Streamlit
runtime. `frontend/app.py` is the only caller.

Every function returns a typed model from `app.models`/`invoice_agent.schema`
- the same Pydantic models the backend itself uses - rather than a raw
dict, so the frontend can't silently drift from the backend's actual
response shape.

This module never reads `ANTHROPIC_API_KEY` or `SUPABASE_*` - the frontend
talks to the backend over HTTP only (see `docs/frontend.md`).
"""

from __future__ import annotations

import os
from typing import Any, Optional, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from app.models import ReadinessResponse, RunListResponse, RunResponse, RunSummary

_ModelT = TypeVar("_ModelT", bound=BaseModel)

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"

# Anthropic's real worst case is ~12 minutes: Anthropic(timeout=120.0) in
# invoice_agent/graph.py and extract.py only overrides the *per-attempt*
# timeout, `max_retries` stays at the SDK default of 2 (3 attempts total),
# and two sequential calls (router, extractor) happen per run - 3 x 120s x
# 2 = up to 720s. Typical runs are 15-40s; this is the pathological tail.
# A generous but finite read timeout is a deliberate tradeoff, not an
# oversight - a client-side timeout is treated as "check the sidebar" by
# frontend/app.py, not as a failure, since the run is very likely still
# completing server-side.
UPLOAD_TIMEOUT = (10, 300)  # connect, read
RESUME_TIMEOUT = (10, 300)  # resume can also invoke the graph (retry, etc.)
READ_TIMEOUT = (5, 15)  # GET requests never invoke the graph


class BackendError(Exception):
    """Raised for every backend call that doesn't succeed - a non-2xx HTTP
    response, or the request never completing at all. `.code` is the
    backend's own error slug (see app/errors.py) for a real HTTP error, or
    one of `client_timeout`/`backend_unreachable` for a transport-level
    failure that never reached the backend at all - those two synthetic
    codes let frontend/app.py's error-copy map handle both cases the same
    way it handles every other slug."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: Optional[int] = None,
        thread_id: Optional[str] = None,
        detail: Optional[list] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.thread_id = thread_id
        self.detail = detail
        self.retry_after = retry_after


def backend_url() -> str:
    return os.environ.get("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def _send(method: str, path: str, *, timeout: Any, **kwargs: Any) -> requests.Response:
    """Transport-level seam: turns network failures into BackendError,
    leaves HTTP status interpretation to the caller. Most callers want any
    non-2xx to raise (via `_request()`); `readiness()` wants to treat one
    specific non-2xx status (503) as a valid payload instead of an error,
    so it calls this directly."""
    url = f"{backend_url()}{path}"
    try:
        return requests.request(method, url, timeout=timeout, **kwargs)
    except requests.ConnectTimeout as exc:
        # ConnectTimeout subclasses BOTH Timeout and ConnectionError - it
        # means the backend never even accepted a TCP connection (not just
        # "slow to respond"), so it must be checked before the plain
        # Timeout clause below, or it would be misclassified as
        # client_timeout instead of backend_unreachable.
        raise BackendError(
            f"Can't reach the backend at {backend_url()} - is it running?",
            code="backend_unreachable",
        ) from exc
    except requests.Timeout as exc:
        raise BackendError("The backend didn't respond in time.", code="client_timeout") from exc
    except requests.ConnectionError as exc:
        raise BackendError(
            f"Can't reach the backend at {backend_url()} - is it running?",
            code="backend_unreachable",
        ) from exc
    except requests.RequestException as exc:
        # Catches everything else in the requests exception hierarchy
        # (MissingSchema, InvalidURL, InvalidSchema, TooManyRedirects, ...)
        # that isn't a Timeout/ConnectionError - most plausibly a malformed
        # BACKEND_URL (e.g. missing the "http://" scheme).
        raise BackendError(
            f"Couldn't send the request to {backend_url()} - check BACKEND_URL is a valid "
            f"URL (including the scheme, e.g. http://): {exc}",
            code="backend_unreachable",
        ) from exc


def _raise_for_error(response: requests.Response) -> None:
    """Turn a non-2xx response into a BackendError, carrying the backend's
    own error envelope (app/errors.py's ErrorResponse shape) when present."""
    retry_after = None
    raw_retry_after = response.headers.get("Retry-After")
    if raw_retry_after is not None:
        try:
            retry_after = int(raw_retry_after)
        except ValueError:
            pass

    try:
        body = response.json()
    except ValueError:
        # A non-JSON error body (a proxy/gateway error page, say) means the
        # request never really reached our app's own error handlers.
        raise BackendError(
            f"The backend returned an unexpected {response.status_code} response.",
            code="upstream_unavailable",
            status_code=response.status_code,
            retry_after=retry_after,
        )

    raise BackendError(
        body.get("message", "The backend reported an error."),
        code=body.get("error", "internal_error"),
        status_code=response.status_code,
        thread_id=body.get("thread_id"),
        detail=body.get("detail"),
        retry_after=retry_after,
    )


def _decode_json(response: requests.Response) -> Any:
    """A "successful" (2xx, or 200/503 for readiness) response body that
    isn't valid JSON - a proxy/gateway error page slipping through with a
    misleadingly-ok status, say - must not crash the caller with an
    unhandled `requests.exceptions.JSONDecodeError` (a `ValueError`)."""
    try:
        return response.json()
    except ValueError as exc:
        raise BackendError(
            f"The backend returned an unexpected {response.status_code} response.",
            code="upstream_unavailable",
            status_code=response.status_code,
        ) from exc


def _validate(model: type[_ModelT], data: Any) -> _ModelT:
    """A "successful" response body that doesn't match the target model's
    schema (e.g. a version-skewed backend after a redeploy) must not crash
    the caller with an unhandled `pydantic.ValidationError` either - both
    are real possibilities distinct from the non-2xx path `_raise_for_error`
    already handles."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise BackendError(
            "The backend returned a response that didn't match the expected shape.",
            code="upstream_unavailable",
        ) from exc


def _request(method: str, path: str, *, timeout: Any, **kwargs: Any) -> dict:
    """The seam nearly every public function in this module funnels
    through - non-2xx always raises here."""
    response = _send(method, path, timeout=timeout, **kwargs)
    if not response.ok:
        _raise_for_error(response)
    return _decode_json(response)


def create_run(pdf_bytes: bytes, filename: str) -> RunResponse:
    """POST /invoices - blocks until the run interrupts or completes."""
    data = _request(
        "POST",
        "/invoices",
        timeout=UPLOAD_TIMEOUT,
        files={"file": (filename, pdf_bytes, "application/pdf")},
    )
    return _validate(RunResponse, data)


def get_run(thread_id: str) -> RunResponse:
    """GET /invoices/{thread_id} - reads current state, no execution."""
    data = _request("GET", f"/invoices/{thread_id}", timeout=READ_TIMEOUT)
    return _validate(RunResponse, data)


def submit_corrections(thread_id: str, corrections: dict) -> RunResponse:
    """POST /invoices/{thread_id}/resume with a non-empty corrections body.

    Deliberately asserts non-empty: an empty body on a `needs_review`
    thread does NOT "accept as extracted" (the human_review -> validator
    edge is unconditional, so it just re-interrupts with identical flags -
    see frontend/app.py), and an empty body on a `failed` thread means
    something else entirely (retry). Use `retry_run()` for that - never
    this function, which is why the two don't share an implementation.
    """
    if not corrections:
        raise ValueError(
            "submit_corrections() requires a non-empty corrections dict - use retry_run() to retry."
        )
    data = _request(
        "POST",
        f"/invoices/{thread_id}/resume",
        timeout=RESUME_TIMEOUT,
        json={"corrections": corrections},
    )
    return _validate(RunResponse, data)


def retry_run(thread_id: str) -> RunResponse:
    """POST /invoices/{thread_id}/resume with a LITERALLY empty corrections
    body - the only way to retry a `failed` thread (app/service.py's
    resume_run raises 409 thread_failed_retry_only for any non-empty body
    on a failed thread, since there's no pending interrupt to apply a
    correction to). Kept as its own function, never sharing a code path
    with submit_corrections(), so a future edit to one can't accidentally
    break the other's contract.
    """
    data = _request(
        "POST",
        f"/invoices/{thread_id}/resume",
        timeout=RESUME_TIMEOUT,
        json={"corrections": {}},
    )
    return _validate(RunResponse, data)


def list_runs(limit: int = 10) -> list[RunSummary]:
    """GET /invoices?limit=N - recent runs from the LangGraph checkpointer,
    NOT Supabase (see frontend/app.py's module docstring for why)."""
    data = _request("GET", "/invoices", timeout=READ_TIMEOUT, params={"limit": limit})
    return _validate(RunListResponse, data).runs


def readiness(*, deep: bool = False) -> ReadinessResponse:
    """GET /health/ready - treated as data, not an error path: the backend
    returns HTTP 503 when degraded, which `_request()` would otherwise
    raise as a BackendError. A "degraded" backend is exactly the thing
    this function exists to report, not something to alarm the caller
    with, so 503 is handled here alongside 200 rather than through
    `_request()`."""
    response = _send("GET", "/health/ready", timeout=READ_TIMEOUT, params={"deep": deep})
    if response.status_code not in (200, 503):
        _raise_for_error(response)
    return _validate(ReadinessResponse, _decode_json(response))
