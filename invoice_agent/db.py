"""Supabase-backed persistence: invoices, line items, PDF storage, the
export ledger.

The service key used here is server-side only (loaded from env) - never ship
it to a frontend. See `db/schema.sql` for the table definitions this module
assumes exist.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterator, Optional

from supabase import Client, create_client

PDF_BUCKET = "invoice-pdfs"

_HEADER_FIELDS = [
    "invoice_number",
    "invoice_date",
    "vendor_name",
    "bill_to",
    "subtotal",
    "tax",
    "total",
    "due_date",
    "currency",
]
_LINE_ITEM_FIELDS = ["description", "quantity", "unit_price", "amount"]

# Single source of truth for the CSV column list - invoice_csv_rows() builds
# every row from the same two field-name lists above, so the header and the
# row data can't drift out of sync with each other.
CSV_FIELDS = _HEADER_FIELDS + [f"line_item_{f}" for f in _LINE_ITEM_FIELDS]

_client: Optional[Client] = None


def get_client() -> Client:
    """Lazily construct (and cache) the Supabase client from env vars."""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL / SUPABASE_KEY are not set. Copy .env.example to "
                ".env and fill in your Supabase project's URL and key."
            )
        _client = create_client(url, key)
    return _client


def is_duplicate(vendor_name: str, invoice_number: str) -> bool:
    """True if an invoice with this (vendor_name, invoice_number) is already stored."""
    response = (
        get_client()
        .table("invoices")
        .select("id")
        .eq("vendor_name", vendor_name)
        .eq("invoice_number", invoice_number)
        .limit(1)
        .execute()
    )
    return len(response.data) > 0


def insert_invoice(invoice: dict) -> dict:
    """Upsert the invoice header, then replace its line items.

    Upsert key is (vendor_name, invoice_number) - see the unique constraint
    in `db/schema.sql`. Line items aren't diffed - they're fully replaced -
    but new rows are inserted *before* old ones are deleted, so a failure
    between the two steps leaves the previous line items intact (at worst,
    duplicated) rather than dropping an invoice to zero line items.
    """
    client = get_client()
    line_items = invoice.get("line_items", [])
    invoice_row = {k: v for k, v in invoice.items() if k != "line_items"}

    result = (
        client.table("invoices")
        .upsert(invoice_row, on_conflict="vendor_name,invoice_number")
        .execute()
    )
    if not result.data:
        raise RuntimeError(
            "insert_invoice: upsert returned no rows - check that the Supabase "
            "service key's RLS policy allows reading back the row it just wrote"
        )
    inserted = result.data[0]
    invoice_id = inserted["id"]

    new_ids: list = []
    if line_items:
        rows = [{**item, "invoice_id": invoice_id} for item in line_items]
        insert_result = client.table("line_items").insert(rows).execute()
        new_ids = [row["id"] for row in insert_result.data]

    delete_query = client.table("line_items").delete().eq("invoice_id", invoice_id)
    if new_ids:
        delete_query = delete_query.not_.in_("id", new_ids)
    delete_query.execute()

    return inserted


def upload_pdf(path: str | Path) -> str:
    """Upload the source PDF to Supabase Storage. Returns its storage path.

    The path is prefixed with a content hash rather than using the local
    filename alone - two different invoices saved locally under the same
    generic filename (e.g. "invoice.pdf") would otherwise silently overwrite
    each other in Storage.
    """
    path = Path(path)
    content = path.read_bytes()
    content_hash = hashlib.sha256(content).hexdigest()[:16]
    storage_path = f"invoices/{content_hash}_{path.name}"
    get_client().storage.from_(PDF_BUCKET).upload(
        storage_path,
        content,
        {"content-type": "application/pdf", "upsert": "true"},
    )
    return storage_path


def invoice_csv_rows(invoice: dict) -> list[dict]:
    """One CSV row dict per line item, keyed by `CSV_FIELDS` - the shared
    row-assembly logic behind both `export_ledger_row_to_csv_rows` below
    (the server-side export ledger's own CSV download) and the frontend's
    per-invoice CSV download (`frontend/forms.py`'s `invoice_to_csv_bytes`),
    so the outputs can't silently diverge in row shape. `line_items or
    [{}]` so a zero-line-item invoice still emits one row."""
    header_values = {field: invoice.get(field) for field in _HEADER_FIELDS}
    line_items = invoice.get("line_items") or [{}]
    rows = []
    for item in line_items:
        row = dict(header_values)
        row.update({f"line_item_{field}": item.get(field) for field in _LINE_ITEM_FIELDS})
        rows.append(row)
    return rows


# The ledger's own CSV shape - written_at/thread_id first (provenance a
# plain per-invoice dump never had), then the same per-line-item columns
# CSV_FIELDS already defines. Single source of truth for the provenance
# columns too - export_ledger_row_to_csv_rows() below builds its
# provenance dict from this same list, so the two can't drift apart.
_LEDGER_PROVENANCE_FIELDS = ["written_at", "thread_id"]
LEDGER_CSV_FIELDS = _LEDGER_PROVENANCE_FIELDS + CSV_FIELDS


def insert_export_row(thread_id: str, invoice: dict) -> dict:
    """Append one row to the `invoice_exports` ledger (Phase 8.6).

    Deliberately an INSERT, never an upsert - there is no unique
    constraint on this table (see `db/schema.sql`), so a corrected re-run
    of the same invoice adds a *new* row rather than overwriting the
    earlier one. Must only be called after `insert_invoice` has already
    succeeded for this same write - see `invoice_agent/graph.py`'s
    `output()` and REVIEW.md's "ledger rows written without a successful
    invoice write" entry.
    """
    row = {field: invoice.get(field) for field in _HEADER_FIELDS}
    row["thread_id"] = thread_id
    row["line_items"] = invoice.get("line_items", [])
    result = get_client().table("invoice_exports").insert(row).execute()
    if not result.data:
        raise RuntimeError("insert_export_row: insert returned no rows")
    return result.data[0]


def iter_export_rows(page_size: int = 500) -> Iterator[dict]:
    """Yield every `invoice_exports` row, oldest first, paginated so
    `GET /export.csv` never has to hold the whole ledger in memory at
    once (the 1GB-RAM constraint this project targets) and never silently
    truncates at PostgREST's default row cap the way one unpaginated
    `select("*")` would on a large ledger.

    Paginates by keyset on `id` (the identity primary key - unique and
    assigned in commit order) rather than offset/range on `written_at`.
    `written_at` is assigned at statement-execution time, not commit time,
    so two concurrent `insert_export_row` calls can tie or commit out of
    that order; combined with offset/range pagination (which re-derives
    "page N" by counting from row zero on every call), a row inserted
    while a long-lived `GET /export.csv` download is still in progress
    would shift every later offset and cause a row to be skipped or
    duplicated in the stream. A keyset cursor has no such drift: each page
    only asks for ids strictly greater than the last one seen, so rows
    inserted concurrently elsewhere in the table never move already-issued
    pages around.
    """
    client = get_client()
    last_id = 0
    while True:
        response = (
            client.table("invoice_exports")
            .select("*")
            .gt("id", last_id)
            .order("id")
            .limit(page_size)
            .execute()
        )
        rows = response.data
        if not rows:
            return
        yield from rows
        if len(rows) < page_size:
            return
        last_id = rows[-1]["id"]


def export_ledger_row_to_csv_rows(ledger_row: dict) -> list[dict]:
    """One CSV row per line item for a single ledger row, prefixed with
    its `written_at`/`thread_id` - reuses `invoice_csv_rows()` for the
    per-line-item flattening (a ledger row already has every key that
    function reads: the header fields plus `line_items`), so the two
    outputs can't drift apart in row shape."""
    provenance = {field: ledger_row.get(field) for field in _LEDGER_PROVENANCE_FIELDS}
    return [{**provenance, **row} for row in invoice_csv_rows(ledger_row)]


def export_status() -> dict[str, Any]:
    """Row count and the most recent `written_at`, for the persistent
    export bar (`GET /export/status`)."""
    client = get_client()
    # head=True turns this into a HEAD request - PostgREST still returns the
    # exact count via Content-Range, but without it a plain GET would return
    # every matching row's `id` in the body just to have it discarded below.
    count_response = client.table("invoice_exports").select("id", count="exact", head=True).execute()
    row_count = count_response.count or 0

    last_response = (
        client.table("invoice_exports").select("written_at").order("written_at", desc=True).limit(1).execute()
    )
    last_written_at = last_response.data[0]["written_at"] if last_response.data else None
    return {"row_count": row_count, "last_written_at": last_written_at}
