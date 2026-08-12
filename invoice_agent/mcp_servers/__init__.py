"""Standalone MCP servers for invoice ingestion.

Each module here is a runnable stdio MCP server (`python -m
invoice_agent.mcp_servers.<name>` or `python invoice_agent/mcp_servers/<name>.py`)
exposing the same 3-tool contract that `invoice_agent.ingest_mcp` expects:

  - list_pending_invoices(limit: int) -> list[dict]
      Each dict has at least "id" and "source_description".
  - download_invoice_pdfs(id: str, dest_dir: str) -> list[str]
      Local paths written.
  - mark_processed(id: str) -> bool
      Marks the source item so it isn't listed again.

That shared shape is what lets `ingest_mcp.py` treat any source (Gmail,
filesystem, or a future one) identically.
"""
