"""Run the full LangGraph pipeline (Phase 2: Router -> Extractor) on a PDF.

Usage: python scripts/run_graph.py <pdf_path>
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_agent.graph import graph  # noqa: E402


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

    initial_state = {
        "file_path": pdf_path,
        "doc_type": "",
        "invoice": None,
        "validation": None,
        "status": "pending",
        "messages": [],
    }

    result = graph.invoke(initial_state, config=config)

    print(f"thread_id: {thread_id}")
    print(f"doc_type:  {result['doc_type']}")
    print(f"status:    {result['status']}")
    print()
    if result["invoice"] is not None:
        print("invoice:")
        print(json.dumps(result["invoice"], indent=2))
    else:
        print("invoice: (none - not extracted)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
