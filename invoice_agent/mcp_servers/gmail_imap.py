"""Local, self-hosted Gmail MCP server - IMAP + App Password, not OAuth.

Why: this repo doesn't depend on a third-party package with read access to
your inbox, and it avoids the Google Cloud OAuth consent-screen setup. The
tradeoff is IMAP is a plainer protocol than the Gmail API, so this server
does its own MIME parsing.

Scope is deliberately narrow and read-mostly: the only mailbox mutation is
setting the \\Seen flag in `mark_processed` (never delete, never send).
Every IMAP fetch uses BODY.PEEK so *reading* a message never marks it seen
as a side effect - only `mark_processed` does that, explicitly.

Setup: enable 2FA on the Gmail account, generate an App Password at
https://myaccount.google.com/apppasswords, then set GMAIL_ADDRESS and
GMAIL_APP_PASSWORD in .env. See docs/mcp_setup.md for the full walkthrough
and how to test this server standalone with the MCP Inspector before wiring
it into ingestion.

Runs over stdio - spawned as a subprocess by MultiServerMCPClient (or the
MCP Inspector). The only network call this process makes is IMAPS to Gmail.
"""

from __future__ import annotations

import email
import imaplib
import os
from email.message import Message
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

mcp = FastMCP("gmail-imap")


def _connect() -> imaplib.IMAP4_SSL:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set. See docs/mcp_setup.md."
        )
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(address, app_password)
    conn.select("INBOX")
    return conn


def _pdf_attachment_filenames(msg: Message) -> list[str]:
    filenames = []
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        filename = part.get_filename()
        if filename and filename.lower().endswith(".pdf"):
            filenames.append(filename)
    return filenames


def _fetch_message(conn: imaplib.IMAP4_SSL, uid: bytes) -> Message | None:
    """Fetch a message body without marking it \\Seen (BODY.PEEK)."""
    status, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
    if status != "OK" or not msg_data or msg_data[0] is None:
        return None
    raw = msg_data[0][1]
    return email.message_from_bytes(raw)


@mcp.tool()
def list_pending_invoices(limit: int = 10) -> list[dict]:
    """List unread inbox emails that have at least one PDF attachment.

    Read-only: fetches use BODY.PEEK, so this call never marks messages as
    read. Each result has "id" (the IMAP UID), "source_description"
    (subject + sender), and "attachment_filenames".

    Filters server-side via Gmail's X-GM-RAW search extension
    (is:unread has:attachment filename:pdf) before fetching any message
    bodies. Fetching every unseen message's full body just to inspect its
    MIME parts client-side doesn't scale - on an inbox with a large unread
    backlog (thousands of promos/notifications sitting unread for years is
    common) that naive scan can take many minutes and blow past any
    caller's timeout, even though almost none of those messages are
    invoices.
    """
    conn = _connect()
    try:
        gmail_query = "is:unread has:attachment filename:pdf"
        status, data = conn.search(None, "X-GM-RAW", f'"{gmail_query}"')
        if status != "OK":
            raise RuntimeError(f"Gmail search (X-GM-RAW) failed: {data}")
        if not data or not data[0]:
            return []
        uids = data[0].split()

        results = []
        for uid in uids:
            msg = _fetch_message(conn, uid)
            if msg is None:
                continue
            # Gmail's search is a best-effort filter (substring match on
            # filename, not a strict content-type check) - confirm
            # client-side. This candidate set is small (already filtered
            # server-side), so the per-message full-body fetch is cheap here.
            pdf_names = _pdf_attachment_filenames(msg)
            if not pdf_names:
                continue
            results.append(
                {
                    "id": uid.decode(),
                    "source_description": f"{msg.get('Subject', '(no subject)')} - from {msg.get('From', '?')}",
                    "attachment_filenames": pdf_names,
                }
            )
            if len(results) >= limit:
                break
        return results
    finally:
        conn.logout()


@mcp.tool()
def download_invoice_pdfs(id: str, dest_dir: str = "./attachments") -> list[str]:
    """Download every PDF attachment on the given email UID to dest_dir.

    Read-only w.r.t. the mailbox (BODY.PEEK); does not mark as read. Returns
    the local file paths written.
    """
    conn = _connect()
    try:
        msg = _fetch_message(conn, id.encode())
        if msg is None:
            return []

        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)

        written = []
        for part in msg.walk():
            if part.get_content_disposition() != "attachment":
                continue
            filename = part.get_filename()
            if not filename or not filename.lower().endswith(".pdf"):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            # Prefix with the UID so two attachments named identically
            # (e.g. "invoice.pdf" from different senders) never collide.
            out_path = dest / f"{id}_{filename}"
            out_path.write_bytes(payload)
            written.append(str(out_path))
        return written
    finally:
        conn.logout()


@mcp.tool()
def mark_processed(id: str) -> bool:
    """Mark the given email UID as read (\\Seen) so future ingestion runs
    won't list it again. This is the only mailbox mutation this server does."""
    conn = _connect()
    try:
        status, _ = conn.store(id.encode(), "+FLAGS", "\\Seen")
        return status == "OK"
    finally:
        conn.logout()


if __name__ == "__main__":
    mcp.run(transport="stdio")
