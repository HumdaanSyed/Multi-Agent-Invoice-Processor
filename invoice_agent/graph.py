"""LangGraph orchestration: Router -> Extractor (-> Validator -> Output later).

State is a TypedDict; nodes return *partial* state updates only, which
LangGraph merges into the running state - never return the whole state
from a node.

Checkpointer is `InMemorySaver` for now (Phase 2). Phase 3 switches this to
`SqliteSaver` once `interrupt()`-based human review needs state to survive
across process boundaries.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict

from anthropic import Anthropic
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from invoice_agent.extract import extract_invoice

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

    return {
        "doc_type": classification.doc_type,
        "status": "classified" if classification.doc_type == "invoice" else "skipped",
    }


def extractor(state: GraphState) -> dict:
    """Extract structured invoice data via the Phase 1 function."""
    invoice = extract_invoice(state["file_path"])
    return {
        "invoice": invoice.model_dump(mode="json"),
        "status": "extracted",
    }


def route_after_classification(state: GraphState) -> str:
    """Conditional edge: only invoices proceed to extraction."""
    if state["doc_type"] == "invoice":
        return "extractor"
    return END


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("router", router)
    graph.add_node("extractor", extractor)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_classification,
        {"extractor": "extractor", END: END},
    )
    graph.add_edge("extractor", END)

    return graph.compile(checkpointer=InMemorySaver())


# Module-level compiled graph, ready to `.invoke()`.
graph = build_graph()
