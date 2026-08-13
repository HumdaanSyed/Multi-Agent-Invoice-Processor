"""LangGraph orchestration: Router -> Extractor -> Validator -> (human review) -> Output.

State is a TypedDict; nodes return *partial* state updates only, which
LangGraph merges into the running state - never return the whole state
from a node.

Checkpointer is `SqliteSaver` (not `InMemorySaver`) because `interrupt()`
needs state to survive between the interrupting call and the resuming
call - those are two separate `graph.invoke()` calls tied together only by
`thread_id` via the checkpointer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated, Literal, Optional, TypedDict

from anthropic import Anthropic
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from invoice_agent import db
from invoice_agent.extract import MODEL as EXTRACT_MODEL
from invoice_agent.extract import extract_invoice
from invoice_agent.tracing import trace_callbacks, traced_generation
from invoice_agent.validate import validate_invoice

EXPORT_CSV_PATH = Path(__file__).resolve().parent.parent / "exports" / "invoices.csv"

DocType = Literal["invoice", "receipt", "other"]


class DocumentClassification(BaseModel):
    """Structured output for the router's document-type classification."""

    doc_type: DocType = Field(
        description=(
            "The document type: 'invoice' (a bill requesting payment for "
            "goods/services), 'receipt' (proof of a completed payment), "
            "or 'other' (anything else)."
        )
    )


class GraphState(TypedDict):
    file_path: str
    doc_type: str
    invoice: Optional[dict]
    validation: Optional[dict]
    status: str
    messages: Annotated[list, add_messages]


ROUTER_MODEL = "claude-sonnet-5"
ROUTER_MAX_TOKENS = 256
ROUTER_PROMPT = (
    "Classify this document as one of: invoice, receipt, or other. "
    "An invoice requests payment for goods/services rendered. A receipt "
    "confirms a payment already made. Anything else is 'other'."
)


def _load_pdf_b64(file_path: str) -> str:
    import base64
    from pathlib import Path

    return base64.standard_b64encode(Path(file_path).read_bytes()).decode("utf-8")


def router(state: GraphState) -> dict:
    """Classify the document type via a structured-output Claude call."""
    pdf_b64 = _load_pdf_b64(state["file_path"])

    client = Anthropic()
    with traced_generation(
        "router-classify", model=ROUTER_MODEL, input_data={"file_path": state["file_path"]}
    ) as gen:
        response = client.messages.parse(
            model=ROUTER_MODEL,
            max_tokens=ROUTER_MAX_TOKENS,
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
                        {"type": "text", "text": ROUTER_PROMPT},
                    ],
                }
            ],
            output_format=DocumentClassification,
        )
        classification = response.parsed_output
        gen.record(output=classification.model_dump(), usage=response.usage)

    return {
        "doc_type": classification.doc_type,
        "status": "classified" if classification.doc_type == "invoice" else "skipped",
    }


def extractor(state: GraphState) -> dict:
    """Extract structured invoice data via the Phase 1 function."""
    response = None

    def _capture_response(r):
        nonlocal response
        response = r

    with traced_generation(
        "extract-invoice", model=EXTRACT_MODEL, input_data={"file_path": state["file_path"]}
    ) as gen:
        invoice = extract_invoice(state["file_path"], on_response=_capture_response)
        gen.record(
            output=invoice.model_dump(mode="json"),
            usage=response.usage if response is not None else None,
        )
    return {
        "invoice": invoice.model_dump(mode="json"),
        "status": "extracted",
    }


def validator(state: GraphState) -> dict:
    """Run deterministic business-rule checks on the extracted invoice."""
    result = validate_invoice(state["invoice"], duplicate_checker=db.is_duplicate)
    return {
        "validation": result.model_dump(),
        "status": "needs_review" if result.needs_review else "validated",
    }


def human_review(state: GraphState) -> dict:
    """Surface flagged invoices to a human and apply their corrections.

    No side effects here (no DB writes, no file I/O) - this node may run
    more than once across a resume, and interrupts must stay pure.
    """
    validation = state["validation"]
    resume_value = interrupt(
        {
            "invoice": state["invoice"],
            "flags": validation["flags"],
        }
    )

    edited_invoice = resume_value.get("edited_invoice") if resume_value else None
    if edited_invoice is not None:
        return {"invoice": edited_invoice, "status": "reviewed"}
    return {"status": "reviewed"}


def output(state: GraphState) -> dict:
    """Persist the invoice: upload the source PDF, upsert to Supabase (with
    the resulting storage path), and append the CSV export."""
    invoice = state["invoice"]
    pdf_storage_path = None
    try:
        pdf_storage_path = db.upload_pdf(state["file_path"])
        db.insert_invoice({**invoice, "pdf_storage_path": pdf_storage_path})
        db.export_invoice_csv(invoice, EXPORT_CSV_PATH)
    except Exception as exc:
        raise RuntimeError(
            f"output failed persisting vendor={invoice.get('vendor_name')!r} "
            f"invoice_number={invoice.get('invoice_number')!r} "
            f"(pdf_storage_path={pdf_storage_path!r} - already uploaded if set): {exc}"
        ) from exc
    return {"status": "completed"}


def route_after_classification(state: GraphState) -> str:
    """Conditional edge: only invoices proceed to extraction."""
    if state["doc_type"] == "invoice":
        return "extractor"
    return END


def route_after_validation(state: GraphState) -> str:
    """Conditional edge: flagged invoices go to human review, clean ones straight to output."""
    if state["validation"]["needs_review"]:
        return "human_review"
    return "output"


CHECKPOINT_DB_PATH = Path(__file__).resolve().parent.parent / "checkpoints" / "graph.sqlite"


def _default_checkpointer() -> SqliteSaver:
    CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB_PATH), check_same_thread=False)
    return SqliteSaver(conn)


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    graph = StateGraph(GraphState)
    graph.add_node("router", router)
    graph.add_node("extractor", extractor)
    graph.add_node("validator", validator)
    graph.add_node("human_review", human_review)
    graph.add_node("output", output)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_classification,
        {"extractor": "extractor", END: END},
    )
    graph.add_edge("extractor", "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validation,
        {"human_review": "human_review", "output": "output"},
    )
    # Loop back through the validator (not straight to output) so a human
    # correction gets fully re-checked - math/dates, and the duplicate check
    # re-run against the possibly-edited vendor_name/invoice_number - before
    # anything is persisted. If it's still flagged, this re-interrupts.
    graph.add_edge("human_review", "validator")
    graph.add_edge("output", END)

    return graph.compile(checkpointer=checkpointer or _default_checkpointer())


_graph_singleton = None


def get_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Lazily build (and cache) the default compiled graph.

    Importing this module must stay side-effect-free - no filesystem writes,
    no open connections - so the graph is only compiled on first call, not
    at import time. Callers that want a fresh/custom checkpointer (tests,
    a future FastAPI app) should call `build_graph()` directly instead.
    """
    global _graph_singleton
    if checkpointer is not None:
        return build_graph(checkpointer)
    if _graph_singleton is None:
        _graph_singleton = build_graph()
    return _graph_singleton


def build_invoke_config(thread_id: str) -> dict:
    """The `config` dict every `graph.invoke()`/`graph.invoke(Command(resume=...))`
    call needs: the checkpointer's thread_id plus tracing callbacks (an
    empty list if tracing isn't configured). Shared here so
    scripts/run_graph.py, invoice_agent/ingest_mcp.py, and any future
    caller (e.g. Phase 8's FastAPI backend) build it identically instead
    of each re-implementing the same two-key dict."""
    return {"configurable": {"thread_id": thread_id}, "callbacks": trace_callbacks(thread_id)}
