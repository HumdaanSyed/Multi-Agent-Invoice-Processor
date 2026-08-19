"""Pure form-data helpers for the Streamlit frontend - no Streamlit import,
no network. Kept separate from `frontend/app.py` specifically so this
logic (the actually bug-prone part: converting between `st.data_editor`'s
row shape and `Invoice.line_items`, building a corrections payload, and
generating a CSV) is unit-testable without a Streamlit runtime.
"""

from __future__ import annotations

import csv
import io
import math
from typing import Optional

from app.models import InvoiceCorrections
from invoice_agent import db
from invoice_agent.validate import parse_iso_date

__all__ = [
    "parse_iso_date",
    "line_items_to_rows",
    "rows_to_line_items",
    "build_corrections",
    "invoice_to_csv_bytes",
]

# parse_iso_date is re-exported from invoice_agent.validate (not
# reimplemented here) - it used to be a byte-for-byte duplicate of that
# module's date parser. frontend/app.py's `_date_field` relies on the same
# ISO-8601-or-None judgment `validate_invoice()` itself makes, so sharing
# one implementation means the two can't silently diverge on an edge case.


def line_items_to_rows(line_items: list[dict]) -> list[dict]:
    """`Invoice.line_items` -> the list-of-dicts shape `st.data_editor`
    renders. A direct pass-through today (the field names already match
    1:1) - kept as a named function so the Invoice -> editor direction is
    explicit and symmetric with `rows_to_line_items` below."""
    return [
        {
            "description": item.get("description", ""),
            "quantity": item.get("quantity"),
            "unit_price": item.get("unit_price"),
            "amount": item.get("amount"),
        }
        for item in line_items
    ]


def rows_to_line_items(rows: list[dict]) -> list[dict]:
    """The inverse of `line_items_to_rows`: `st.data_editor`'s returned
    rows -> a list of well-formed `LineItem` dicts, ready to go into a
    corrections payload. Two defensive steps matter here because
    `num_rows="dynamic"` lets a human add a row and leave it half-filled:

    - A row with no description AND no numeric values at all (an
      added-then-abandoned row) is silently dropped, rather than sent as
      a line item with missing fields that would fail `Invoice`
      validation server-side with a confusing error.
    - A non-finite number (NaN/inf) is rejected with a clear ValueError.
      Pydantic v2's `float` fields accept NaN by default, and both
      Python's `json` module and Starlette's request parser accept a
      bare `NaN` literal even though it isn't valid JSON - so a NaN left
      by an incompletely-cleared cell would otherwise sail straight
      through `Invoice.model_validate` and into Supabase undetected.

    Raises `ValueError` (with a 1-indexed, human-readable message) for
    any row that has a description but a missing/non-finite number, or a
    number but no description - both are genuine input errors, not
    something to silently coerce.
    """
    result: list[dict] = []
    for i, row in enumerate(rows):
        description = (row.get("description") or "").strip()
        quantity = row.get("quantity")
        unit_price = row.get("unit_price")
        amount = row.get("amount")

        if not description and quantity is None and unit_price is None and amount is None:
            continue  # an added-then-abandoned row

        for field_name, value in (("quantity", quantity), ("unit_price", unit_price), ("amount", amount)):
            if value is None:
                raise ValueError(f"Line item {i + 1}: {field_name} is required.")
            if not math.isfinite(value):
                raise ValueError(f"Line item {i + 1}: {field_name} must be a finite number.")

        if not description:
            raise ValueError(f"Line item {i + 1}: description is required.")

        result.append(
            {
                "description": description,
                "quantity": float(quantity),
                "unit_price": float(unit_price),
                "amount": float(amount),
            }
        )
    return result


def build_corrections(
    *,
    invoice_number: str,
    invoice_date: str,
    vendor_name: str,
    bill_to: str,
    line_items: list[dict],
    subtotal: float,
    tax: float,
    total: float,
    due_date: Optional[str],
    currency: str,
) -> dict:
    """Assembles a full `InvoiceCorrections`-shaped dict from every
    editable field's current widget value - sent in full on every resume,
    not as a diff against the original.

    This is sound (not merely convenient) because every field is always
    explicitly present: the server's `exclude_unset=True` merge never
    actually elides anything here, since nothing is ever left unset, so an
    untouched field's value is already byte-identical to what the server
    already has. Full-send is also the *safer* of the two options - it
    always includes `line_items`, so it can never trigger the line-item-
    wipe hazard a payload omitting that key would (see
    `invoice_agent.db.insert_invoice`'s docstring).

    Never use this to retry a `failed` thread - that needs a *literally*
    empty corrections body (`frontend.api_client.retry_run`), a completely
    different contract; see that function's docstring.

    Builds and validates a real `app.models.InvoiceCorrections` - not a
    hand-assembled dict - so a widget value that doesn't actually satisfy
    the contract (a bad type, in practice) fails here with a clear error
    instead of silently reaching the HTTP boundary as an untyped dict.
    """
    corrections = InvoiceCorrections(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        vendor_name=vendor_name,
        bill_to=bill_to,
        line_items=line_items,
        subtotal=subtotal,
        tax=tax,
        total=total,
        due_date=due_date,
        currency=currency,
    )
    return corrections.model_dump(mode="json")


def invoice_to_csv_bytes(invoice: dict) -> bytes:
    """A single-invoice CSV, generated client-side.

    The backend's own CSV export (`db.export_invoice_csv`) writes to a
    server-side file (`exports/invoices.csv`) a separately-deployed
    frontend container (Phase 10) has no filesystem access to, and it
    appends to a running multi-invoice log rather than producing a
    one-invoice download - a different job entirely. Row assembly itself
    is shared with `export_invoice_csv` via `db.invoice_csv_rows()`, so
    this download's schema can't silently drift from the server-side
    export's - only the "how/where to write it" differs.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=db.CSV_FIELDS)
    writer.writeheader()
    for row in db.invoice_csv_rows(invoice):
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")
