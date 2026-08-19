"""Streamlit demo frontend for the invoice-processing pipeline (Phase 9).

Talks to the FastAPI backend (Phase 8, `app/`) over HTTP only, via
`frontend/api_client.py` - never imports `invoice_agent.extract`,
`invoice_agent.graph`, or anything that could reach Anthropic directly.
This module (and everything it imports) never reads `ANTHROPIC_API_KEY` or
`SUPABASE_*` - see `docs/frontend.md` for why that, not refusing to load
`.env` at all, is what actually satisfies the roadmap's "never put those
keys in the frontend" pitfall.

Two things about the backend shape this file, both verified against the
running code rather than assumed from the roadmap's original wording:

  - `POST /invoices` and the resume endpoint both BLOCK until the run
    interrupts or completes - there is no "processing" state to poll on
    the happy path. A spinner around one blocking call is the actual UX.
  - There is no "accept as extracted" action on a flagged invoice.
    `invoice_agent/graph.py`'s `human_review -> validator` edge is
    unconditional, so an empty-body resume on a `needs_review` thread
    just re-runs the validator on the SAME data and re-interrupts with
    the SAME flags. The only way out of `needs_review` is an edit that
    clears every flag - see `frontend.api_client.submit_corrections`'s
    docstring.

Session-state architecture (see `docs/frontend.md` for the reasoning):
  - Every action handler calls the API, writes the result into
    `st.session_state`, then `st.rerun()`s - render functions never make
    network calls of their own.
  - Widget keys are namespaced by a `form_nonce` that bumps every time a
    new run result is stored, so a re-interrupt's fresh invoice data
    can't be shadowed by stale widget state left under the same key.
  - Resets ("New invoice") happen in an `on_click=` callback, never by
    calling `st.session_state.clear()` mid-script-run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Streamlit's script runner prepends this file's OWN directory
# (`frontend/`) to the front of sys.path before exec'ing it
# (streamlit.runtime.scriptrunner.exec_code.modified_sys_path). Since this
# file is named `app.py`, that makes a bare `import app` resolve to THIS
# FILE instead of the top-level `app/` package (FastAPI backend, Phase 8) -
# `frontend/api_client.py`'s `from app.models import ...` would otherwise
# fail with a circular-import error. Confirmed this is Streamlit-runner-
# specific (a plain `pytest` run is unaffected - nothing else in this repo
# does this sys.path trick), so the fix belongs here, not in api_client.py/
# forms.py, and must run before any `frontend.*` import below.
REPO_ROOT = Path(__file__).resolve().parent.parent
_repo_root_str = str(REPO_ROOT)
# Must land at index 0 specifically, not merely "somewhere in sys.path" -
# the editable install's .pth file already puts REPO_ROOT on sys.path, but
# at a lower-priority position than where Streamlit inserts `frontend/`
# (index 0), so a plain "if not already present" guard is not enough:
# `frontend/` would still be found first. Remove-then-reinsert guarantees
# REPO_ROOT actually outranks it.
if _repo_root_str in sys.path:
    sys.path.remove(_repo_root_str)
sys.path.insert(0, _repo_root_str)

import streamlit as st  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from frontend.api_client import (  # noqa: E402
    BackendError,
    backend_url,
    create_run,
    get_run,
    list_runs,
    readiness,
    retry_run,
    submit_corrections,
)
from frontend.forms import (  # noqa: E402
    build_corrections,
    invoice_to_csv_bytes,
    line_items_to_rows,
    parse_iso_date,
    rows_to_line_items,
)

load_dotenv()

SAMPLE_DIR = REPO_ROOT / "data" / "eval"  # committed, deploy-safe demo files

st.set_page_config(page_title="Invoice Agent", page_icon="\U0001f9fe", layout="wide")


# --- session state -----------------------------------------------------


def init_state() -> None:
    st.session_state.setdefault("active_run", None)  # a RunResponse, or None
    st.session_state.setdefault("run_error", None)  # dict, see set_error()
    st.session_state.setdefault("processed_file_id", None)
    st.session_state.setdefault("form_nonce", 0)
    st.session_state.setdefault("uploader_nonce", 0)


def wkey(name: str) -> str:
    """Every editable-form widget key goes through this. See the module
    docstring - a fixed key would keep showing a stale value across a
    re-interrupt, since a widget's `value=` argument is only honored the
    run its key is first created."""
    return f"{st.session_state.form_nonce}:{name}"


def set_run(run) -> None:
    st.session_state.active_run = run
    st.session_state.run_error = None
    st.session_state.form_nonce += 1
    # Invalidate the sidebar's cached run list so whatever just happened
    # (a new upload, a resume, a retry) shows up immediately instead of
    # waiting out the cache TTL below.
    _cached_list_runs.clear()


def set_error(thread_id: Optional[str], err: BackendError) -> None:
    st.session_state.run_error = {
        "thread_id": thread_id or err.thread_id,
        "code": err.code,
        "message": err.message,
        "retry_after": err.retry_after,
    }
    st.session_state.active_run = None


def _reset_for_new_invoice() -> None:
    st.session_state.active_run = None
    st.session_state.run_error = None
    st.session_state.processed_file_id = None
    st.session_state.uploader_nonce += 1
    st.session_state.form_nonce += 1


def _safe_list_runs(limit: int = 20) -> list:
    try:
        return list_runs(limit=limit)
    except BackendError:
        return []


# render_sidebar() runs on EVERY script rerun (any widget interaction
# anywhere on the page, not just sidebar ones) - cache both backend calls
# it makes for a few seconds so an unrelated click doesn't cost 2 extra
# HTTP round-trips. Neither `readiness()` nor `_safe_list_runs()` (a
# failure returns `[]`, not an exception) get cached forever: st.cache_data
# doesn't cache a call that raises, and `set_run()` explicitly clears the
# list-runs cache so a just-completed action is never hidden by staleness.
_SIDEBAR_CACHE_TTL = 5  # seconds


@st.cache_data(ttl=_SIDEBAR_CACHE_TTL, show_spinner=False)
def _cached_readiness():
    return readiness()


@st.cache_data(ttl=_SIDEBAR_CACHE_TTL, show_spinner=False)
def _cached_list_runs(limit: int = 10) -> list:
    return _safe_list_runs(limit=limit)


# --- sidebar -------------------------------------------------------------


_STATUS_EMOJI = {
    "completed": "✅",
    "needs_review": "⚠️",
    "failed": "❌",
    "skipped": "⏭️",
    "processing": "⏳",
}


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Invoice Agent")
        _render_readiness_chip()

        st.divider()
        st.subheader("Recent runs")
        st.caption("From the run checkpointer, not Supabase - includes flagged and failed runs too.")
        runs = _cached_list_runs(limit=10)
        if not runs:
            st.caption("No runs yet.")
        for summary in runs:
            emoji = _STATUS_EMOJI.get(summary.status, "•")
            label = f"{emoji} {summary.thread_id[:8]} · {summary.status}"
            if st.button(label, key=f"sidebar_{summary.thread_id}", use_container_width=True):
                _load_thread(summary.thread_id)

        st.divider()
        st.button("New invoice", on_click=_reset_for_new_invoice, use_container_width=True)


def _render_readiness_chip() -> None:
    try:
        result = _cached_readiness()
    except BackendError:
        st.error(f"Backend unreachable ({backend_url()})")
        return
    if result.status == "ok":
        st.success("Backend ready")
    else:
        # "langfuse" is deliberately excluded: app/health.py's own
        # _REQUIRED_CHECKS never gates `status` on it (tracing is
        # optional), so listing it here would blame an unconfigured-by-
        # design service for a degraded status it had nothing to do with.
        missing = [
            name for name, check in result.checks.items() if not check.configured and name != "langfuse"
        ]
        st.warning(f"Backend degraded: {', '.join(missing) or 'unknown'}")


def _load_thread(thread_id: str) -> None:
    try:
        run = get_run(thread_id)
    except BackendError as err:
        set_error(thread_id, err)
    else:
        set_run(run)
    st.rerun()


# --- upload ----------------------------------------------------------------


def render_upload() -> None:
    st.subheader("Process an invoice")

    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader(
            "Upload a PDF invoice", type=["pdf"], key=f"uploader_{st.session_state.uploader_nonce}"
        )
    with col2:
        sample_paths = sorted(SAMPLE_DIR.glob("*.pdf")) if SAMPLE_DIR.exists() else []
        sample_choice = st.selectbox(
            "...or try a sample",
            options=["(none)"] + [p.name for p in sample_paths],
            key=f"sample_{st.session_state.uploader_nonce}",
        )

    if uploaded is not None:
        marker, filename, pdf_bytes = uploaded.file_id, uploaded.name, uploaded.getvalue()
    elif sample_choice != "(none)":
        path = SAMPLE_DIR / sample_choice
        marker, filename, pdf_bytes = f"sample:{sample_choice}", sample_choice, path.read_bytes()
    else:
        return

    if not st.button("Process invoice", type="primary"):
        return

    if st.session_state.processed_file_id == marker:
        st.info("Already processed - pick a different file, or click 'New invoice'.")
        return

    _submit_upload(marker, filename, pdf_bytes)


def _submit_upload(marker: str, filename: str, pdf_bytes: bytes) -> None:
    # A client-side timeout (POST /invoices mints its thread_id server-side
    # and only returns it in the response body, which a timeout means we
    # never got) is deliberately NOT auto-recovered by diffing GET
    # /invoices before/after: on a shared deployment, a second upload
    # (another tab, another visitor) completing in the same window would
    # make that diff resolve to exactly one new thread_id that isn't this
    # user's own, silently showing someone else's extracted invoice (PII).
    # render_error()'s client_timeout branch already tells the user to
    # check "Recent runs" themselves instead - slower, but correct.
    #
    # processed_file_id is likewise only set on success: marking a file
    # "processed" after a failed attempt (transient or not) would block
    # the user from simply retrying the same file without an unrelated
    # workaround ("New invoice" or picking a different file).
    with st.spinner(f"Extracting {filename}… this can take a while for a large/scanned PDF."):
        try:
            run = create_run(pdf_bytes, filename)
        except BackendError as err:
            set_error(None, err)
        else:
            st.session_state.processed_file_id = marker
            set_run(run)
    st.rerun()


# --- status-dispatch rendering ---------------------------------------------


def render_run(run) -> None:
    st.caption(f"thread_id: `{run.thread_id}`")
    handler = {
        "needs_review": render_needs_review,
        "completed": render_completed,
        "failed": render_failed,
        "skipped": render_skipped,
        "processing": render_processing,
    }.get(run.status)
    if handler is None:
        st.error(f"Unrecognized status {run.status!r} from the backend.")
        return
    handler(run)


def _render_invoice_summary(invoice) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Vendor", invoice.vendor_name)
    col2.metric("Total", f"{invoice.total:,.2f} {invoice.currency}")
    col3.metric("Invoice #", invoice.invoice_number)
    due = f" · **Due:** {invoice.due_date}" if invoice.due_date else ""
    st.write(f"**Bill to:** {invoice.bill_to}")
    st.write(f"**Invoice date:** {invoice.invoice_date}{due}")
    st.dataframe(
        line_items_to_rows([item.model_dump() for item in invoice.line_items]),
        use_container_width=True,
        hide_index=True,
    )


def _date_field(label: str, value: Optional[str], key: str, *, optional: bool = False) -> Optional[str]:
    """Renders `st.date_input` when `value` parses as ISO 8601, otherwise
    falls back to a plain text input - `validate_invoice()` can flag a
    non-ISO date, so the interrupt payload may legitimately contain one,
    and `st.date_input` cannot accept a non-date string at all. Returns
    an ISO date string (or None), ready for `build_corrections()`."""
    parsed = parse_iso_date(value)
    if parsed is not None or (optional and not value):
        # `not value` (not `value is None`) so an empty string is treated
        # the same as a missing value - some extraction/edit paths can
        # legitimately produce "" instead of null for an unset optional
        # field, and that's not a validation error to show the user.
        widget_value = st.date_input(label, value=parsed, key=key)
        return widget_value.isoformat() if widget_value else None
    text_value = st.text_input(f"{label} (not a recognized date – please fix)", value=value or "", key=key)
    return text_value or None


def render_needs_review(run) -> None:
    st.warning("This invoice needs review before it can be saved.")
    for flag in run.flags or []:
        st.error(flag)
    st.caption(
        "Every flag above must clear before this saves — there's no override. "
        "That's deliberate: nothing reaches the database without passing validation "
        "on its final, corrected form."
    )

    invoice = run.invoice
    with st.form(key=wkey("review_form")):
        col1, col2 = st.columns(2)
        with col1:
            invoice_number = st.text_input("Invoice number", value=invoice.invoice_number, key=wkey("invoice_number"))
            vendor_name = st.text_input("Vendor", value=invoice.vendor_name, key=wkey("vendor_name"))
            bill_to = st.text_input("Bill to", value=invoice.bill_to, key=wkey("bill_to"))
            currency = st.text_input("Currency", value=invoice.currency, key=wkey("currency"))
        with col2:
            invoice_date_str = _date_field("Invoice date", invoice.invoice_date, wkey("invoice_date"))
            due_date_str = _date_field(
                "Due date (optional)", invoice.due_date, wkey("due_date"), optional=True
            )
            subtotal = st.number_input(
                "Subtotal", value=float(invoice.subtotal), format="%.2f", key=wkey("subtotal")
            )
            tax = st.number_input("Tax", value=float(invoice.tax), format="%.2f", key=wkey("tax"))
            total = st.number_input("Total", value=float(invoice.total), format="%.2f", key=wkey("total"))

        st.markdown("**Line items**")
        rows = st.data_editor(
            line_items_to_rows([item.model_dump() for item in invoice.line_items]),
            num_rows="dynamic",
            use_container_width=True,
            key=wkey("line_items"),
            column_config={
                "description": st.column_config.TextColumn("Description", required=True),
                "quantity": st.column_config.NumberColumn("Qty"),
                "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
                "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            },
        )

        submitted = st.form_submit_button("Save corrections & continue", type="primary")

    if not submitted:
        return

    # Invoice/InvoiceCorrections type these as plain `str` with no
    # min_length, and nothing downstream (the merge/validate step,
    # validate_invoice()'s business rules, db.insert_invoice) rejects an
    # empty string either - unlike line_items just below, these need their
    # own check here or a blanked required field would persist to Supabase.
    required_fields = {
        "Invoice number": invoice_number,
        "Vendor": vendor_name,
        "Bill to": bill_to,
        "Currency": currency,
    }
    blank = [label for label, value in required_fields.items() if not value.strip()]
    if blank:
        st.error(f"{', '.join(blank)} cannot be blank.")
        return

    try:
        line_items = rows_to_line_items(rows)
    except ValueError as exc:
        st.error(str(exc))
        return

    corrections = build_corrections(
        invoice_number=invoice_number,
        invoice_date=invoice_date_str,
        vendor_name=vendor_name,
        bill_to=bill_to,
        line_items=line_items,
        subtotal=subtotal,
        tax=tax,
        total=total,
        due_date=due_date_str,
        currency=currency,
    )

    with st.spinner("Re-validating…"):
        try:
            new_run = submit_corrections(run.thread_id, corrections)
        except BackendError as err:
            set_error(run.thread_id, err)
        else:
            set_run(new_run)
    st.rerun()


def render_completed(run) -> None:
    st.success("Saved — this invoice passed validation and was persisted.")
    invoice = run.invoice
    _render_invoice_summary(invoice)

    if run.validation:
        with st.expander("Validation result"):
            st.json(run.validation)

    csv_bytes = invoice_to_csv_bytes(invoice.model_dump(mode="json"))
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{invoice.invoice_number}.csv",
        mime="text/csv",
    )


def render_failed(run) -> None:
    node = run.failed_at_node or "an earlier step"
    st.error(f"This run failed at **{node}**. No invoice data is available to show.")
    if st.button("Retry", type="primary"):
        with st.spinner("Retrying…"):
            try:
                new_run = retry_run(run.thread_id)
            except BackendError as err:
                set_error(run.thread_id, err)
            else:
                set_run(new_run)
        st.rerun()


def render_skipped(run) -> None:
    st.info(f"This document was classified as **{run.doc_type or 'not an invoice'}** — nothing to review.")


def render_processing(run) -> None:
    st.info(
        f"Still processing (at **{run.current_node or 'unknown'}**). This is unusual for a "
        "blocking API — refresh to check again."
    )
    if st.button("Refresh status"):
        try:
            new_run = get_run(run.thread_id)
        except BackendError as err:
            set_error(run.thread_id, err)
        else:
            set_run(new_run)
        st.rerun()


# --- error rendering ------------------------------------------------------


_ERROR_COPY = {
    "thread_not_found": "That run couldn't be found.",
    "thread_not_interrupted": "That run has already finished and can't be resumed.",
    "thread_busy": "This run is already being processed — wait a moment and try again.",
    "thread_failed_retry_only": "This run failed and isn't paused for review — retry instead of editing.",
    "unsupported_media_type": "That file doesn't look like a PDF.",
    "empty_upload": "That file is empty.",
    "upload_too_large": "That file is too large.",
    "invalid_request": "That correction couldn't be understood.",
    "invalid_invoice": "The corrected invoice isn't valid — check the fields and try again.",
    "pdf_unprocessable": "The extraction service couldn't read that PDF.",
    "upstream_rate_limited": "The extraction service is rate-limited — try again shortly.",
    "upstream_unavailable": "The extraction service is temporarily unavailable.",
    "upstream_misconfigured": "The backend isn't fully configured (check its Anthropic setup).",
    "persistence_failed": "Saving the processed invoice failed.",
    "service_not_configured": "A required backend service isn't configured.",
    "server_busy": "The server is at capacity — try again shortly.",
    "internal_error": "Something went wrong on the backend.",
    "client_timeout": "The backend didn't respond in time — it may still be processing.",
    "backend_unreachable": "Can't reach the backend.",
}

# Slugs where a checkpoint may actually exist - i.e. the graph started
# running before the failure. Upload-validation errors (bad file, too
# large, empty) never mint a thread_id worth checking on. thread_busy is
# included even though it isn't a graph *failure* - a concurrent-lock
# conflict is the single most likely-to-self-resolve case, and ThreadBusy
# always carries the thread_id of a real, existing checkpoint.
_RECOVERABLE_CODES = {
    "internal_error",
    "persistence_failed",
    "upstream_unavailable",
    "upstream_misconfigured",
    "upstream_rate_limited",
    "pdf_unprocessable",
    "service_not_configured",
    "thread_busy",
}


def render_error(err: dict) -> None:
    message = _ERROR_COPY.get(err["code"], err["message"])
    st.error(message)

    if err["code"] == "backend_unreachable":
        st.caption(f"BACKEND_URL = {backend_url()}")
        return
    if err["code"] == "client_timeout":
        st.caption("Check “Recent runs” in the sidebar — the upload may have completed anyway.")
        return
    if err.get("retry_after") is not None:
        # Not `if err.get("retry_after"):` - retry_after=0 ("retry
        # immediately") is a legitimate value and must not be treated the
        # same as "not provided" just because 0 is falsy.
        st.caption(f"Retry after {err['retry_after']}s.")

    thread_id = err.get("thread_id")
    if thread_id and err["code"] in _RECOVERABLE_CODES:
        st.caption(f"thread_id: `{thread_id}`")
        if st.button("Check status"):
            try:
                run = get_run(thread_id)
            except BackendError as check_err:
                set_error(thread_id, check_err)
            else:
                set_run(run)
            st.rerun()


# --- main --------------------------------------------------------------


def main() -> None:
    init_state()
    render_sidebar()

    st.title("Invoice Agent")

    if st.session_state.run_error:
        render_error(st.session_state.run_error)
        st.divider()

    if st.session_state.active_run:
        render_run(st.session_state.active_run)
    else:
        render_upload()


main()
