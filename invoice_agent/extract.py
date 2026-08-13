"""Single-call PDF -> structured Invoice extraction.

Feeds the raw PDF to Claude as a `document` content block combined with a
Pydantic `output_format` in one `messages.parse()` call. No separate OCR
step: Claude's native PDF path reads the embedded text layer plus the
rendered page image, so this handles most scanned/photographed invoices
too. Do NOT combine `citations` with structured outputs on this call -
that's a documented 400 error.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path
from typing import Any, Callable

from anthropic import Anthropic
from dotenv import load_dotenv

from invoice_agent.schema import Invoice

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048

EXTRACTION_PROMPT = (
    "Extract the invoice data as structured JSON. Use ISO 8601 YYYY-MM-DD "
    "for all dates. If a field is not present on the invoice, make your "
    "best reasonable inference from context rather than guessing wildly, "
    "and leave optional fields unset only if truly absent."
)


def extract_invoice(
    pdf_path: str | Path,
    model: str | None = None,
    on_response: Callable[[Any], None] | None = None,
) -> Invoice:
    """Extract a validated Invoice from a single PDF file.

    Args:
        pdf_path: Path to the invoice PDF.
        model: Override the default extraction model (`MODEL`). Exists so
            the eval harness (evals/run_eval.py) can score other models
            (e.g. Haiku for cost, Opus for hard scanned docs) without
            duplicating this function - see CLAUDE.md's model-routing note.
        on_response: Optional callback invoked with the raw parsed API
            response (before this function returns just `.parsed_output`).
            Exists so a caller (invoice_agent/graph.py's extractor node) can
            record token usage for tracing without this function needing to
            know anything about Langfuse - see invoice_agent/tracing.py.

    Returns:
        A validated `Invoice` Pydantic object.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"No such PDF: {pdf_path}")

    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    client = Anthropic()
    response = client.messages.parse(
        model=model or MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
        output_format=Invoice,
    )
    if on_response is not None:
        on_response(response)
    return response.parsed_output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract structured invoice data from a single PDF."
    )
    parser.add_argument("pdf_path", help="Path to the invoice PDF.")
    args = parser.parse_args()

    load_dotenv()

    invoice = extract_invoice(args.pdf_path)
    print(invoice.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
