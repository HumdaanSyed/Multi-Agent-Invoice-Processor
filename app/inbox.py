"""Inbox endpoints - the backend acting as an MCP *client*.

Each call spawns the chosen MCP server (invoice_agent/mcp_servers/) as a
stdio subprocess inside this same container and invokes one of its three
tools: list_pending_invoices, download_invoice_pdfs, mark_processed. stdio
is local-only, which is fine here: the server runs on the same machine as
the backend. Same client setup and result parsing as the ingestion script
(invoice_agent/ingest_mcp.py), reused rather than copied.

The frontend turns a downloaded PDF into a normal run by fetching it from
GET /inbox/files/{name} and going through the usual reserve + upload flow,
so an inbox invoice gets the same live view, review and ledger as an upload.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, model_validator

from app.auth import require_owner
from app.errors import ThreadNotFound, UpstreamUnavailable
from app.uploads import sweep_old_uploads
from invoice_agent.ingest_mcp import SOURCES, _extract_scalar_result, _extract_tool_result, _find_tool

logger = logging.getLogger("app.inbox")

# The inbox reads the owner's mailbox with their App Password - owner only,
# on every route (download, mark and file serving included).
router = APIRouter(dependencies=[Depends(require_owner)])

# Each call spawns a subprocess and an IMAP login; cap how many run at once.
_mcp_slots = asyncio.Semaphore(2)

# fullmatch + ASCII: no trailing newline ("$" allows one), no non-ASCII digits/letters.
_GMAIL_UID = re.compile(r"\d{1,10}", re.ASCII)
_SAFE_PDF_NAME = re.compile(r"\w[\w .()-]{0,200}\.pdf", re.IGNORECASE | re.ASCII)

Source = Literal["gmail", "filesystem"]

# The MCP SDK hands a stdio server only a minimal environment (PATH, HOME...),
# and in Docker there's no .env file for the server's own load_dotenv() to
# read - so the credentials it needs are passed through explicitly.
_PASSTHROUGH_ENV = ("PATH", "HOME", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")


async def call_tool(source: Source, tool: str, args: dict[str, Any]) -> tuple[Any, int]:
    """Run one MCP tool call; returns (raw result, milliseconds taken)."""
    started = time.perf_counter()
    client = MultiServerMCPClient(
        {
            source: {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(SOURCES[source])],
                "env": {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ},
            }
        }
    )
    try:
        async with _mcp_slots:
            tools = await client.get_tools()
            result = await _find_tool(tools, tool).ainvoke(args)
    except Exception as exc:  # noqa: BLE001 - any MCP/server failure becomes one clean 503
        # Details (which can name env vars or IMAP responses) stay in the log.
        logger.warning("MCP %s (%s) failed: %s", tool, source, type(exc).__name__)
        raise UpstreamUnavailable(f"The {source} inbox isn't reachable right now.") from exc
    return result, round((time.perf_counter() - started) * 1000)


def _inbox_dir(request: Request) -> Path:
    path = request.app.state.upload_dir / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


class InboxItemRequest(BaseModel):
    source: Source = "gmail"
    id: str

    @model_validator(mode="after")
    def _check_id(self) -> "InboxItemRequest":
        # A Gmail id is one IMAP UID (never a set like "1:*"); a filesystem id
        # is a bare PDF filename (no path segments).
        pattern = _GMAIL_UID if self.source == "gmail" else _SAFE_PDF_NAME
        if not pattern.fullmatch(self.id):
            raise ValueError("invalid id for this source")
        return self


@router.get("/inbox")
async def list_inbox(request: Request, source: Source = "gmail", limit: int = Query(10, ge=1, le=25)) -> dict:
    sweep_old_uploads(_inbox_dir(request))
    raw, ms = await call_tool(source, "list_pending_invoices", {"limit": limit})
    return {"tool": "list_pending_invoices", "ms": ms, "items": _extract_tool_result(raw) or []}


@router.post("/inbox/download")
async def download_inbox_item(body: InboxItemRequest, request: Request) -> dict:
    dest = _inbox_dir(request)
    raw, ms = await call_tool(body.source, "download_invoice_pdfs", {"id": body.id, "dest_dir": str(dest)})
    names = [Path(p).name for p in _extract_tool_result(raw) or []]
    return {"tool": "download_invoice_pdfs", "ms": ms, "files": names}


@router.post("/inbox/mark")
async def mark_inbox_item(body: InboxItemRequest) -> dict:
    raw, ms = await call_tool(body.source, "mark_processed", {"id": body.id})
    return {"tool": "mark_processed", "ms": ms, "marked": bool(_extract_scalar_result(raw))}


@router.get("/inbox/files/{name}")
def get_inbox_file(name: str, request: Request) -> FileResponse:
    # Basename only, and only PDFs the download tool wrote into the inbox dir.
    path = _inbox_dir(request) / Path(name).name
    if path.name != name or path.suffix.lower() != ".pdf" or not path.is_file():
        raise ThreadNotFound(f"No downloaded PDF named {name!r}.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
