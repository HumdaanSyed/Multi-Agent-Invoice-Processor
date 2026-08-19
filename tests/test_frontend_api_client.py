"""Tests for frontend.api_client - offline, no real network, no Streamlit.

The one seam every function funnels through is `requests.request`, so
every test here patches exactly that (module attribute, matching the
repo's established "patch at the connection seam" convention - see
tests/test_gmail_imap.py's `_connect` patches)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import ReadinessResponse, RunListResponse, RunResponse
from frontend.api_client import (
    BackendError,
    backend_url,
    create_run,
    get_run,
    list_runs,
    readiness,
    retry_run,
    submit_corrections,
)

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


class _FakeResponse:
    def __init__(self, status_code: int, json_body=None, headers=None, json_raises: bool = False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.headers = headers or {}
        self._json_body = json_body
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not JSON")
        return self._json_body


@pytest.fixture(autouse=True)
def _default_backend_url(monkeypatch):
    monkeypatch.delenv("BACKEND_URL", raising=False)


def test_backend_url_default_and_override(monkeypatch):
    assert backend_url() == "http://127.0.0.1:8000"
    monkeypatch.setenv("BACKEND_URL", "http://backend:9000/")
    assert backend_url() == "http://backend:9000"  # trailing slash stripped


# --- create_run / get_run --------------------------------------------------


def test_create_run_success():
    body = {"thread_id": "t1", "status": "completed", "invoice": GOOD_INVOICE, "validation": {"passed": True}}
    with patch("requests.request", return_value=_FakeResponse(200, body)) as mock_request:
        result = create_run(b"%PDF-1.4\n...", "invoice.pdf")
    assert isinstance(result, RunResponse)
    assert result.status == "completed"
    # multipart field name must be "file", matching app/routes.py's UploadFile param name.
    _, kwargs = mock_request.call_args
    assert "file" in kwargs["files"]
    assert kwargs["files"]["file"][0] == "invoice.pdf"


def test_get_run_unknown_thread_raises_backend_error():
    body = {"error": "thread_not_found", "message": "No run found.", "thread_id": "nope"}
    with patch("requests.request", return_value=_FakeResponse(404, body)):
        with pytest.raises(BackendError) as excinfo:
            get_run("nope")
    assert excinfo.value.code == "thread_not_found"
    assert excinfo.value.status_code == 404
    assert excinfo.value.thread_id == "nope"


# --- submit_corrections / retry_run -----------------------------------------


def test_submit_corrections_rejects_empty_dict_without_a_request():
    with patch("requests.request") as mock_request:
        with pytest.raises(ValueError):
            submit_corrections("t1", {})
    mock_request.assert_not_called()


def test_submit_corrections_sends_the_given_corrections():
    body = {"thread_id": "t1", "status": "completed", "invoice": GOOD_INVOICE, "validation": {"passed": True}}
    with patch("requests.request", return_value=_FakeResponse(200, body)) as mock_request:
        submit_corrections("t1", {"total": 12.0})
    _, kwargs = mock_request.call_args
    assert kwargs["json"] == {"corrections": {"total": 12.0}}


def test_retry_run_sends_exactly_empty_corrections():
    """Guards the A3 contract: retrying a failed thread must send a
    LITERALLY empty corrections body, or app/service.py's resume_run
    raises 409 thread_failed_retry_only. This is the one test that would
    catch retry_run() accidentally reusing submit_corrections()'s path."""
    body = {"thread_id": "t1", "status": "completed", "invoice": GOOD_INVOICE, "validation": {"passed": True}}
    with patch("requests.request", return_value=_FakeResponse(200, body)) as mock_request:
        retry_run("t1")
    _, kwargs = mock_request.call_args
    assert kwargs["json"] == {"corrections": {}}


def test_resume_on_failed_thread_with_corrections_raises_retry_only():
    body = {
        "error": "thread_failed_retry_only",
        "message": "This run already failed and is not paused for review.",
        "thread_id": "t1",
    }
    with patch("requests.request", return_value=_FakeResponse(409, body)):
        with pytest.raises(BackendError) as excinfo:
            submit_corrections("t1", {"total": 1.0})
    assert excinfo.value.code == "thread_failed_retry_only"


# --- list_runs / readiness ---------------------------------------------


def test_list_runs_returns_run_summaries():
    body = {"runs": [{"thread_id": "t1", "status": "completed", "doc_type": None, "created_at": "2026-01-01T00:00:00Z"}]}
    with patch("requests.request", return_value=_FakeResponse(200, body)) as mock_request:
        runs = list_runs(limit=10)
    assert len(runs) == 1
    assert runs[0].thread_id == "t1"
    _, kwargs = mock_request.call_args
    assert kwargs["params"] == {"limit": 10}


def test_readiness_ok_returns_readiness_response():
    body = {"status": "ok", "checks": {"anthropic": {"configured": True, "detail": None}}}
    with patch("requests.request", return_value=_FakeResponse(200, body)):
        result = readiness()
    assert isinstance(result, ReadinessResponse)
    assert result.status == "ok"


def test_readiness_degraded_503_is_still_a_valid_result_not_an_error():
    """503 is data here, not a failure - readiness() exists specifically
    to report a degraded backend, so it must not raise for the one status
    code that means exactly that."""
    body = {"status": "degraded", "checks": {"anthropic": {"configured": False, "detail": "missing key"}}}
    with patch("requests.request", return_value=_FakeResponse(503, body)):
        result = readiness()
    assert result.status == "degraded"


def test_readiness_genuinely_unexpected_status_still_raises():
    with patch("requests.request", return_value=_FakeResponse(502, json_raises=True)):
        with pytest.raises(BackendError) as excinfo:
            readiness()
    assert excinfo.value.status_code == 502


# --- error/transport edge cases ---------------------------------------


def test_rate_limited_carries_retry_after():
    body = {"error": "upstream_rate_limited", "message": "Rate limited.", "thread_id": "t1"}
    headers = {"Retry-After": "30"}
    with patch("requests.request", return_value=_FakeResponse(429, body, headers=headers)):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.retry_after == 30


def test_non_json_error_body_maps_to_upstream_unavailable():
    with patch("requests.request", return_value=_FakeResponse(502, json_raises=True)):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "upstream_unavailable"
    assert excinfo.value.status_code == 502


def test_client_timeout_maps_to_client_timeout_code():
    import requests

    with patch("requests.request", side_effect=requests.exceptions.ReadTimeout("boom")):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "client_timeout"


def test_connection_error_maps_to_backend_unreachable():
    import requests

    with patch("requests.request", side_effect=requests.exceptions.ConnectionError("refused")):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "backend_unreachable"
    assert "127.0.0.1:8000" in excinfo.value.message


def test_connect_timeout_maps_to_backend_unreachable_not_client_timeout():
    """ConnectTimeout subclasses BOTH Timeout and ConnectionError - it means
    the backend was never reachable at all (not just slow), so it must be
    classified as backend_unreachable, not client_timeout."""
    import requests

    with patch("requests.request", side_effect=requests.exceptions.ConnectTimeout("refused")):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "backend_unreachable"


def test_missing_schema_error_maps_to_backend_unreachable():
    """A malformed BACKEND_URL (e.g. missing "http://") raises MissingSchema
    - a RequestException that's neither Timeout nor ConnectionError - and
    must still become a BackendError, not propagate raw."""
    import requests

    with patch("requests.request", side_effect=requests.exceptions.MissingSchema("no scheme")):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "backend_unreachable"


def test_success_status_with_non_json_body_raises_backend_error():
    """A 2xx response whose body isn't valid JSON must not crash the
    caller with an unhandled JSONDecodeError."""
    with patch("requests.request", return_value=_FakeResponse(200, json_raises=True)):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "upstream_unavailable"


def test_success_status_with_schema_mismatched_body_raises_backend_error():
    """A 2xx response whose body is valid JSON but doesn't satisfy
    RunResponse's schema (e.g. a version-skewed backend) must not crash the
    caller with an unhandled pydantic.ValidationError."""
    with patch("requests.request", return_value=_FakeResponse(200, {"unexpected": "shape"})):
        with pytest.raises(BackendError) as excinfo:
            get_run("t1")
    assert excinfo.value.code == "upstream_unavailable"


def test_readiness_200_with_non_json_body_raises_backend_error():
    """readiness() bypasses _request()'s auto-raise for 200/503, so its
    own JSON decode needs the same guard - covered separately from the
    generic _request() path above."""
    with patch("requests.request", return_value=_FakeResponse(200, json_raises=True)):
        with pytest.raises(BackendError):
            readiness()


def test_readiness_200_with_schema_mismatched_body_raises_backend_error():
    with patch("requests.request", return_value=_FakeResponse(200, {"unexpected": "shape"})):
        with pytest.raises(BackendError):
            readiness()
