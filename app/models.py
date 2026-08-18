"""Request/response Pydantic models for the FastAPI backend.

One status envelope (`RunResponse`) is shared by every endpoint that reports
on a run - POST /invoices, GET /invoices/{thread_id}, and
POST /invoices/{thread_id}/resume - so a client branches on `.status`
identically no matter which endpoint produced it. Which optional fields are
populated is determined by `status`; see `app/service.py`'s `derive_status()`
for the procedure that decides which branch applies.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from invoice_agent.schema import Invoice, LineItem

RunStatusLiteral = Literal["processing", "needs_review", "completed", "skipped", "failed"]


class InvoiceCorrections(BaseModel):
    """Sparse patch of Invoice fields a human may submit on resume.

    Every field defaults to "not provided" - `app/service.py` merges this
    with `model_dump(exclude_unset=True)`, so an omitted field keeps the
    checkpointed value while an explicit null overwrites it. `extra="forbid"`
    rejects unknown keys at the HTTP boundary, before they can reach the
    graph or (via `db.insert_invoice`) an unexpected Supabase column.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    vendor_name: Optional[str] = None
    bill_to: Optional[str] = None
    line_items: Optional[list[LineItem]] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None


class ResumeRequest(BaseModel):
    """Body of POST /invoices/{thread_id}/resume. An empty/default body
    (no corrections at all) means "accept as extracted" for an interrupted
    run, or "retry" for a run that failed in `output()` - see
    `app/service.py`'s `build_resume_command()`."""

    corrections: InvoiceCorrections = Field(default_factory=InvoiceCorrections)


class RunResponse(BaseModel):
    """POST /invoices, GET /invoices/{thread_id}, and the resume endpoint's
    response shape. Populated fields by `status`:
      - needs_review: invoice + flags
      - completed:    invoice + validation
      - skipped:      doc_type (router sent a receipt/other straight to END)
      - failed:       failed_at_node only - never the raw error message,
                       which embeds vendor_name/invoice_number/storage paths
                       (see REVIEW.md's PII priority). Full detail goes to
                       the server log, keyed by thread_id.
      - processing:   current_node (only observable if a GET races an
                       in-flight blocking POST from another client)
    """

    thread_id: str
    status: RunStatusLiteral
    doc_type: Optional[str] = None
    invoice: Optional[Invoice] = None
    validation: Optional[dict] = None
    flags: Optional[list[str]] = None
    current_node: Optional[str] = None
    failed_at_node: Optional[str] = None


class RunSummary(BaseModel):
    """One row of GET /invoices - lighter than RunResponse since listing
    doesn't need the full invoice payload per row."""

    thread_id: str
    status: RunStatusLiteral
    doc_type: Optional[str] = None
    created_at: Optional[str] = None


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class ErrorResponse(BaseModel):
    """The one error shape every endpoint returns - see app/errors.py.
    `thread_id` is set on every error raised after a thread_id was minted,
    so a client can recover/retry a run that failed mid-flight."""

    error: str
    message: str
    thread_id: Optional[str] = None
    detail: Optional[list[dict]] = None


class HealthResponse(BaseModel):
    """GET /health - static liveness, no fields worth reporting beyond ok."""

    status: Literal["ok"]


class ReadinessCheck(BaseModel):
    configured: bool
    detail: Optional[str] = None


class ReadinessResponse(BaseModel):
    """GET /health/ready - per-service config breakdown, see app/health.py."""

    status: Literal["ok", "degraded"]
    checks: dict[str, ReadinessCheck]
