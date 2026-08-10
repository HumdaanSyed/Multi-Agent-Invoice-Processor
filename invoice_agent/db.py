"""Supabase-backed persistence: invoices, line items, PDF storage, CSV export.

The service key used here is server-side only (loaded from env) - never ship
it to a frontend. See `db/schema.sql` for the table definitions this module
assumes exist.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Optional

from supabase import Client, create_client

PDF_BUCKET = "invoice-pdfs"

CSV_FIELDS = [
    "invoice_number",
    "invoice_date",
    "vendor_name",
    "bill_to",
    "subtotal",
    "tax",
    "total",
    "due_date",
    "currency",
    "line_item_description",
    "line_item_quantity",
    "line_item_unit_price",
    "line_item_amount",
]

_client: Optional[Client] = None


def get_client() -> Client:
    """Lazily construct (and cache) the Supabase client from env vars."""
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
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
    in `db/schema.sql`. Line items are deleted and re-inserted rather than
    diffed, which keeps this idempotent for re-runs/corrections without
    needing per-line-item identity.
    """
    client = get_client()
    line_items = invoice.get("line_items", [])
    invoice_row = {k: v for k, v in invoice.items() if k != "line_items"}

    result = (
        client.table("invoices")
        .upsert(invoice_row, on_conflict="vendor_name,invoice_number")
        .execute()
    )
    inserted = result.data[0]
    invoice_id = inserted["id"]

    client.table("line_items").delete().eq("invoice_id", invoice_id).execute()
    if line_items:
        rows = [{**item, "invoice_id": invoice_id} for item in line_items]
        client.table("line_items").insert(rows).execute()

    return inserted


def upload_pdf(path: str | Path) -> str:
    """Upload the source PDF to Supabase Storage. Returns its storage path."""
    path = Path(path)
    storage_path = f"invoices/{path.name}"
    with open(path, "rb") as f:
        get_client().storage.from_(PDF_BUCKET).upload(
            storage_path,
            f,
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

    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        line_items = invoice.get("line_items") or [{}]
        for item in line_items:
            writer.writerow(
                {
                    "invoice_number": invoice.get("invoice_number"),
                    "invoice_date": invoice.get("invoice_date"),
                    "vendor_name": invoice.get("vendor_name"),
                    "bill_to": invoice.get("bill_to"),
                    "subtotal": invoice.get("subtotal"),
                    "tax": invoice.get("tax"),
                    "total": invoice.get("total"),
                    "due_date": invoice.get("due_date"),
                    "currency": invoice.get("currency"),
                    "line_item_description": item.get("description"),
                    "line_item_quantity": item.get("quantity"),
                    "line_item_unit_price": item.get("unit_price"),
                    "line_item_amount": item.get("amount"),
                }
            )
