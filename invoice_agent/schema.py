"""Pydantic v2 data contracts shared between agents.

These are the structured-output schema Claude fills in during extraction
(Phase 1) and the contract every downstream node (validator, output) relies
on. Kept intentionally small: well under the JSON-Schema limits for
structured outputs (max 24 optional fields, max 16 union-typed fields).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """A single billed line on an invoice."""

    description: str = Field(description="What was billed, verbatim from the invoice.")
    quantity: float = Field(description="Quantity billed. Use 1 if not itemized.")
    unit_price: float = Field(description="Price per unit, in the invoice's currency.")
    amount: float = Field(description="Line total (quantity * unit_price), as printed.")


class Invoice(BaseModel):
    """A structured invoice, extracted directly from a source PDF."""

    invoice_number: str = Field(description="The invoice's unique identifier/number.")
    invoice_date: str = Field(description="Date the invoice was issued, as ISO 8601 YYYY-MM-DD.")
    vendor_name: str = Field(description="The company/person issuing the invoice (the seller).")
    bill_to: str = Field(description="The company/person being billed (the buyer/recipient).")
    line_items: list[LineItem] = Field(description="Every billed line item on the invoice.")
    subtotal: float = Field(description="Sum of all line items before tax.")
    tax: float = Field(description="Total tax amount. Use 0 if none is stated.")
    total: float = Field(description="Grand total due (subtotal + tax).")
    due_date: str | None = Field(
        default=None, description="Payment due date, as ISO 8601 YYYY-MM-DD, if stated."
    )
    currency: str = Field(description="ISO 4217 currency code, e.g. USD, EUR, GBP.")
