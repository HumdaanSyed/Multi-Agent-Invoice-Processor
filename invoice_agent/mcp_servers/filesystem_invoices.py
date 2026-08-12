"""Filesystem MCP server - a credential-free fallback/test double for
gmail_imap.py, exposing the identical 3-tool contract.

Watches data/inbox/ for PDFs. "Downloading" copies the file into the
destination directory; "marking processed" moves it into
data/inbox/processed/ so it won't be listed again. Useful for testing
invoice_agent.ingest_mcp end-to-end without any Gmail setup, and as the
documented fallback if Gmail/IMAP setup isn't worth the time right now (see
docs/mcp_setup.md).

Runs over stdio, same as gmail_imap.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INBOX_DIR = REPO_ROOT / "data" / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"

mcp = FastMCP("filesystem-invoices")


@mcp.tool()
def list_pending_invoices(limit: int = 10) -> list[dict]:
    """List PDF files sitting directly in data/inbox/ (not yet processed)."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(p for p in INBOX_DIR.glob("*.pdf") if p.is_file())
    return [
        {
            "id": p.name,
            "source_description": str(p.relative_to(REPO_ROOT)),
            "attachment_filenames": [p.name],
        }
        for p in pdfs[:limit]
    ]


@mcp.tool()
def download_invoice_pdfs(id: str, dest_dir: str = "./attachments") -> list[str]:
    """Copy the given inbox PDF into dest_dir. Returns the new local path(s)."""
    src = INBOX_DIR / id
    if not src.is_file():
        return []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / id
    shutil.copyfile(src, out_path)
    return [str(out_path)]


@mcp.tool()
def mark_processed(id: str) -> bool:
    """Move the given inbox PDF into data/inbox/processed/ so it won't be re-listed."""
    src = INBOX_DIR / id
    if not src.is_file():
        return False
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(PROCESSED_DIR / id))
    return True


if __name__ == "__main__":
    mcp.run(transport="stdio")
