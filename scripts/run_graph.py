"""Run the full LangGraph pipeline (Router -> Extractor -> Validator ->
human review) on a PDF, demonstrating the interrupt/resume flow.

Usage:
  python scripts/run_graph.py <pdf_path>

If the invoice is flagged for review, this script prints the interrupt
payload (invoice + flags) and prompts for a corrected `total` on the
terminal, then resumes the graph with `Command(resume=...)`. A clean
invoice runs straight through with no prompt.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_agent.graph import graph  # noqa: E402


def _print_invoice(invoice: dict | None) -> None:
    if invoice is None:
        print("invoice: (none - not extracted)")
        return
    print("invoice:")
    print(json.dumps(invoice, indent=2))


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/run_graph.py <pdf_path>")
        return 2

    pdf_path = sys.argv[1]
    if not Path(pdf_path).exists():
        print(f"No such PDF: {pdf_path}")
        return 1

    load_dotenv()

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    print(f"thread_id: {thread_id}")

    initial_state = {
        "file_path": pdf_path,
        "doc_type": "",
        "invoice": None,
        "validation": None,
        "status": "pending",
        "messages": [],
    }

    result = graph.invoke(initial_state, config=config)

    if result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        print("\n--- PAUSED FOR HUMAN REVIEW ---")
        print("flags:")
        for flag in payload["flags"]:
            print(f"  - {flag}")
        print("\nextracted invoice:")
        print(json.dumps(payload["invoice"], indent=2))

        current_total = payload["invoice"]["total"]
        raw = input(
            f"\nEnter corrected total (blank to accept {current_total} as-is): "
        ).strip()

        if raw:
            edited_invoice = {**payload["invoice"], "total": float(raw)}
            resume_value = {"edited_invoice": edited_invoice}
        else:
            resume_value = {"edited_invoice": None}

        result = graph.invoke(Command(resume=resume_value), config=config)

    print(f"\ndoc_type: {result['doc_type']}")
    print(f"status:   {result['status']}")
    if result.get("validation"):
        print(f"flags:    {result['validation']['flags']}")
    print()
    _print_invoice(result["invoice"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
