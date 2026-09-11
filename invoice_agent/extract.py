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
from pydantic_core import from_json

from invoice_agent.schema import Invoice

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048

EXTRACTION_PROMPT = (
    "Extract the invoice data as structured JSON. Use ISO 8601 YYYY-MM-DD "
    "for all dates. If a field is not present on the invoice, make your "
    "best reasonable inference from context rather than guessing wildly, "
    "and leave optional fields unset only if truly absent."
)

_INVOICE_FIELD_NAMES = frozenset(Invoice.model_fields)


def _build_messages(pdf_b64: str) -> list[dict]:
    """Shared by extract_invoice and extract_invoice_streaming so the two
    can't drift - same document block, same prompt."""
    return [
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
    ]


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

    # timeout bounds the FastAPI backend's blocking POST /invoices (Phase 8)
    # - the SDK's default is 600s with 2 retries, so an unbounded client
    # here would give one request a ~20min worst case.
    client = Anthropic(timeout=120.0)
    response = client.messages.parse(
        model=model or MODEL,
        max_tokens=MAX_TOKENS,
        messages=_build_messages(pdf_b64),
        output_format=Invoice,
    )
    if on_response is not None:
        on_response(response)
    return response.parsed_output


def extract_invoice_streaming(
    pdf_path: str | Path,
    model: str | None = None,
    on_response: Callable[[Any], None] | None = None,
    on_field: Callable[[str, Any], None] | None = None,
) -> Invoice:
    """Extract a validated Invoice from a PDF, firing `on_field(name, value)`
    each time a top-level field's value can be confidently read out of the
    partial JSON as Claude streams its response (Phase 8.5).

    **This is a display-only preview channel.** The Invoice this function
    *returns* comes from `stream.get_final_message().parsed_output` - the
    same fully-parsed, schema-validated object `extract_invoice` returns,
    not anything reconstructed from the partial values handed to
    `on_field`. Nothing based on a streamed value is ever assigned into
    `GraphState["invoice"]` or reaches validation/persistence - see
    REVIEW.md's "streamed data reaching persistence" entry. A caller that
    only wants the final Invoice (the eval harness, MCP ingestion) should
    keep using `extract_invoice`; this function exists solely to feed a
    live UI while the same underlying call runs.

    How field completion is detected: Claude's structured-output stream
    delivers the JSON as plain text deltas (not `input_json_delta` - this
    isn't tool use), accumulated by the SDK into `.snapshot` on each text
    event. Every delta is parsed with `pydantic_core.from_json(...,
    allow_partial=True)`, which tolerates a truncated string. A top-level
    key is only announced once *stops* being the last key in that partial
    parse (dicts preserve insertion order) - a trailing value, especially
    a number, can still grow, so "the key is present" alone isn't
    sufficient evidence it's finished. This does not depend on the model
    emitting fields in `Invoice`'s declared order; it works for any order.
    `line_items` (a list) only ever fires once a later field (`subtotal`)
    starts, since a partial list can't be judged "done" mid-item.

    If parsing the partial JSON never produces a usable top-level key at
    all (an unexpected response shape), no `on_field` calls fire and the
    function still returns the correct Invoice from the final parsed
    message - the failure mode is "no live preview," never "wrong data."
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"No such PDF: {pdf_path}")

    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    client = Anthropic(timeout=120.0)
    emitted: list[str] = []
    # Every key but a partial parse's *last* key is provably done (a later
    # key only appears once the model has moved past this one), so once
    # every field but the structurally-last one is confirmed, no further
    # delta can announce anything new until the stream ends - the one
    # remaining field only ever comes from the final flush below. Skipping
    # the parse for the rest of the stream avoids re-scanning the whole
    # accumulated snapshot (from_json's cost grows with response length)
    # for zero payoff on every remaining delta.
    total_fields = len(_INVOICE_FIELD_NAMES)

    with client.messages.stream(
        model=model or MODEL,
        max_tokens=MAX_TOKENS,
        messages=_build_messages(pdf_b64),
        output_format=Invoice,
    ) as stream:
        for event in stream:
            if event.type != "text" or on_field is None or len(emitted) >= total_fields - 1:
                continue
            try:
                partial = from_json(event.snapshot, allow_partial=True)
            except ValueError:
                continue
            if not isinstance(partial, dict):
                continue
            keys = list(partial)
            # Every key but the last one is provably done - a later key
            # only appears once the model has moved past this one.
            for name in keys[:-1]:
                if name not in emitted and name in _INVOICE_FIELD_NAMES:
                    emitted.append(name)
                    on_field(name, partial[name])

        final = stream.get_final_message()

    if on_response is not None:
        on_response(final)

    invoice = final.parsed_output
    if invoice is None:
        raise ValueError(
            "Streaming extraction produced no parsed Invoice - the response "
            "may not have been valid structured output."
        )

    if on_field is not None:
        dumped = invoice.model_dump(mode="json")
        for name in Invoice.model_fields:
            if name not in emitted:
                on_field(name, dumped[name])

    return invoice


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract structured invoice data from a single PDF."
    )
    parser.add_argument("pdf_path", help="Path to the invoice PDF.")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Use the streaming extractor and print each field as it arrives (manual smoke check).",
    )
    args = parser.parse_args()

    load_dotenv()

    if args.stream:
        invoice = extract_invoice_streaming(
            args.pdf_path,
            on_field=lambda name, value: print(f"[field] {name} = {value!r}", file=sys.stderr),
        )
    else:
        invoice = extract_invoice(args.pdf_path)
    print(invoice.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
