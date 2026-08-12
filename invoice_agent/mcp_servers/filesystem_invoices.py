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

import hashlib
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INBOX_DIR = REPO_ROOT / "data" / "inbox"
PROCESSED_DIR = INBOX_DIR / "processed"

mcp = FastMCP("filesystem-invoices")


def _safe_inbox_path(id: str) -> Path | None:
    """Resolve `id` to a path inside INBOX_DIR, or None if it isn't one.

    `id` is a caller-supplied MCP tool argument, not guaranteed to be a
    value previously returned by list_pending_invoices (these tools are
    independently callable, e.g. via the MCP Inspector) - take only the
    basename so a traversal value like "../../.env" can't resolve outside
    data/inbox/.
    """
    safe_name = Path(id).name
    if not safe_name:
        return None
    return INBOX_DIR / safe_name


def _unique_name(path: Path) -> str:
    """Content-hash-prefixed filename, so two different source PDFs that
    happen to share a filename (e.g. "invoice.pdf" from different senders,
    across separate ingestion runs) never collide in dest_dir/processed/ -
    same convention as invoice_agent.db.upload_pdf."""
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{content_hash}_{path.name}"


@mcp.tool()
def list_pending_invoices(limit: int = 10) -> list[dict]:
    """List PDF files sitting directly in data/inbox/ (not yet processed)."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(
        p for p in INBOX_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )
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
    src = _safe_inbox_path(id)
    if src is None or not src.is_file():
        return []
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / _unique_name(src)
    shutil.copyfile(src, out_path)
    return [str(out_path)]


@mcp.tool()
def mark_processed(id: str) -> bool:
    """Move the given inbox PDF into data/inbox/processed/ so it won't be re-listed."""
    src = _safe_inbox_path(id)
    if src is None or not src.is_file():
        return False
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(PROCESSED_DIR / _unique_name(src)))
    return True


if __name__ == "__main__":
    mcp.run(transport="stdio")
