"""Supabase-backed persistence: invoices, line items, PDF storage, CSV export.

The service key used here is server-side only (loaded from env) - never ship
it to a frontend. See `db/schema.sql` for the table definitions this module
assumes exist.
"""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from typing import Optional

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

# Single source of truth for the CSV column list - export_invoice_csv builds
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


def export_invoice_csv(invoice: dict, out_path: str | Path) -> None:
    """Append one CSV row per line item for this invoice to `out_path`.

    Writes a header row only if the file doesn't already exist yet, so
    repeated calls across a session build up a running export.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists()

    header_values = {field: invoice.get(field) for field in _HEADER_FIELDS}

    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        line_items = invoice.get("line_items") or [{}]
        for item in line_items:
            row = dict(header_values)
            row.update(
                {f"line_item_{field}": item.get(field) for field in _LINE_ITEM_FIELDS}
            )
            writer.writerow(row)
