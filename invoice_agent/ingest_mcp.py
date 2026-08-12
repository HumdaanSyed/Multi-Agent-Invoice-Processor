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
terminal, non-interrupted state (completed/skipped) - a flagged invoice is
left unresolved and will be re-listed on the next run, rather than being
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

from invoice_agent.graph import get_graph

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
    """Normalize an MCP tool call's result into plain Python values.

    langchain-mcp-adapters returns a list of MCP content blocks
    (`{"type": "text", "text": ...}`), one block per item of whatever the
    server function returned - and the text encoding depends on the
    underlying type: JSON for a dict/bool, a raw (unquoted) string for a
    plain str. Try JSON first and fall back to the literal text, which
    handles all three tools here (list_pending_invoices -> list[dict],
    download_invoice_pdfs -> list[str], mark_processed -> bool) uniformly.
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

        paths = _extract_tool_result(
            await download_tool.ainvoke({"id": item_id, "dest_dir": str(ATTACHMENTS_DIR)})
        )
        if not paths:
            print("  no PDF downloaded, skipping")
            continue

        item_fully_done = True
        for pdf_path in paths:
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
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
                print(f"    needs review (thread_id={thread_id}): {flags}")
                print(
                    "    left unresolved - resume with Command(resume=...) against "
                    "this thread_id, then re-run ingestion to mark it processed"
                )
                item_fully_done = False
            else:
                print(f"    status={result['status']} doc_type={result['doc_type']}")

        if item_fully_done:
            await mark_tool.ainvoke({"id": item_id})
        else:
            print(f"  leaving {item_id!r} unprocessed (will be re-listed next run)")


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
