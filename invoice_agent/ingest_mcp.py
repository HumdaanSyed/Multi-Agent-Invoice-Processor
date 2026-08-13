"""Automated invoice ingestion via MCP.

Connects (stdio) to a source MCP server exposing list_pending_invoices /
download_invoice_pdfs / mark_processed (see invoice_agent/mcp_servers/),
downloads each PDF, and runs it through the full LangGraph pipeline
(Router -> Extractor -> Validator -> human review -> Output).

Two sources ship in this repo:
  --source filesystem  (default) invoice_agent/mcp_servers/filesystem_invoices.py
                        Drop PDFs in data/inbox/ - no credentials needed.
  --source gmail        invoice_agent/mcp_servers/gmail_imap.py
                        Real Gmail inbox over IMAP + App Password.
                        See docs/mcp_setup.md before using this.

A source item is only marked processed if every PDF from it reached a
terminal, non-interrupted state (completed/skipped), OR every remaining
flag on it is a "possible duplicate" flag (meaning it was already fully
persisted by an earlier run - most likely a crash between that run's
Output step and its mark_processed call - so there's nothing left to do
but stop re-fetching it). Any other flagged/failed invoice is left
unresolved and will be re-listed on the next run, rather than being
silently marked done while parked mid-review with nothing in Supabase yet.
This script does not do interactive review itself; resume a printed
thread_id separately with `Command(resume=...)`.

Usage:
  python -m invoice_agent.ingest_mcp --source filesystem
  python -m invoice_agent.ingest_mcp --source gmail --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

from invoice_agent.graph import build_invoke_config, get_graph
from invoice_agent.tracing import flush as flush_traces

REPO_ROOT = Path(__file__).resolve().parent.parent
ATTACHMENTS_DIR = REPO_ROOT / "attachments"
MCP_SERVERS_DIR = REPO_ROOT / "invoice_agent" / "mcp_servers"

SOURCES = {
    "filesystem": MCP_SERVERS_DIR / "filesystem_invoices.py",
    "gmail": MCP_SERVERS_DIR / "gmail_imap.py",
}


def _find_tool(tools, name: str):
    for tool in tools:
        if tool.name == name:
            return tool
    available = [t.name for t in tools]
    raise RuntimeError(f"MCP server did not expose a {name!r} tool (has: {available})")


def _extract_tool_result(raw):
    """Normalize an MCP tool call's result into a list of plain Python values.

    langchain-mcp-adapters returns a list of MCP content blocks
    (`{"type": "text", "text": ...}`), one block per item of whatever the
    server function returned - and the text encoding depends on the
    underlying type: JSON for a dict/bool, a raw (unquoted) string for a
    plain str. Try JSON first and fall back to the literal text. Use this
    for tools whose return type is itself a list (list_pending_invoices,
    download_invoice_pdfs); use `_extract_scalar_result` for a tool that
    returns a single scalar (mark_processed).
    """
    if not isinstance(raw, list):
        return raw
    values = []
    for block in raw:
        text = block["text"] if isinstance(block, dict) and "text" in block else block
        try:
            values.append(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            values.append(text)
    return values


def _extract_scalar_result(raw):
    """Like `_extract_tool_result`, but for a tool whose return type is a
    single scalar (mark_processed -> bool), not a list. MCP still wraps a
    scalar in a one-element content-block list; unwrap that here so the
    caller gets the actual bool instead of a list, which is truthy even
    when the underlying value is `False`."""
    values = _extract_tool_result(raw)
    return values[0] if values else None


def _is_duplicate_only(flags: list[str]) -> bool:
    """True if every flag on an interrupted invoice is a duplicate flag
    (see invoice_agent/validate.py's message format) - i.e. nothing else is
    wrong with it, it was just already persisted by an earlier run."""
    return bool(flags) and all(f.startswith("Possible duplicate:") for f in flags)


async def run_ingestion(source: str, limit: int) -> None:
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}, expected one of {sorted(SOURCES)}")

    load_dotenv()
    ATTACHMENTS_DIR.mkdir(exist_ok=True)

    client = MultiServerMCPClient(
        {
            source: {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(SOURCES[source])],
            }
        }
    )
    tools = await client.get_tools()
    list_tool = _find_tool(tools, "list_pending_invoices")
    download_tool = _find_tool(tools, "download_invoice_pdfs")
    mark_tool = _find_tool(tools, "mark_processed")

    invoices = _extract_tool_result(await list_tool.ainvoke({"limit": limit}))
    if not invoices:
        print(f"No pending invoices found via {source!r}.")
        return

    graph = get_graph()

    for entry in invoices:
        item_id = entry["id"]
        print(f"\n=== {entry.get('source_description', item_id)} (id={item_id}) ===")

        try:
            paths = _extract_tool_result(
                await download_tool.ainvoke({"id": item_id, "dest_dir": str(ATTACHMENTS_DIR)})
            )
        except Exception as exc:
            print(f"  FAILED to download: {exc}")
            print(f"  leaving {item_id!r} unprocessed (will be re-listed next run)")
            continue

        if not paths:
            print("  no PDF downloaded, leaving unprocessed (will be re-listed next run)")
            continue

        item_fully_done = True
        for pdf_path in paths:
            thread_id = str(uuid.uuid4())
            config = build_invoke_config(thread_id)
            print(f"  processing {pdf_path} (thread_id={thread_id})")
            try:
                result = graph.invoke(
                    {
                        "file_path": pdf_path,
                        "doc_type": "",
                        "invoice": None,
                        "validation": None,
                        "status": "pending",
                        "messages": [],
                    },
                    config=config,
                )
            except Exception as exc:
                print(f"    FAILED: {exc}")
                item_fully_done = False
                continue

            if result.get("__interrupt__"):
                flags = result["__interrupt__"][0].value.get("flags", [])
                if _is_duplicate_only(flags):
                    # Already fully persisted by an earlier run (most likely
                    # a crash between that run's Output step and its
                    # mark_processed call) - nothing to review, just stop
                    # re-fetching it. Nothing new is written here.
                    print(f"    already processed previously (duplicate): {flags}")
                else:
                    print(f"    needs review (thread_id={thread_id}): {flags}")
                    print(
                        "    left unresolved - resume with Command(resume=...) against "
                        "this thread_id, then re-run ingestion to mark it processed"
                    )
                    item_fully_done = False
            else:
                print(f"    status={result['status']} doc_type={result['doc_type']}")

        if item_fully_done:
            try:
                marked = _extract_scalar_result(await mark_tool.ainvoke({"id": item_id}))
            except Exception as exc:
                print(f"  FAILED to mark {item_id!r} processed: {exc}")
                continue
            if not marked:
                print(f"  WARNING: mark_processed returned falsy for {item_id!r} - it may be re-listed next run")
        else:
            print(f"  leaving {item_id!r} unprocessed (will be re-listed next run)")

    flush_traces()  # short-lived process - force-send any buffered trace data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull invoices via MCP and run them through the graph."
    )
    parser.add_argument("--source", choices=sorted(SOURCES), default="filesystem")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_ingestion(args.source, args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
