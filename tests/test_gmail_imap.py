"""Tests for the Gmail MCP server: pure MIME-parsing logic (no IMAP needed)
plus `list_pending_invoices` against a mocked IMAP connection (no live
Gmail needed - a real account is exercised manually, see docs/mcp_setup.md)."""

from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

from invoice_agent.mcp_servers.gmail_imap import (
    _decode_mime_words,
    _pdf_attachment_filenames,
    _pdf_attachments,
    list_pending_invoices,
)


def _message_with_attachments(*attachments: tuple[str, bytes, str]) -> EmailMessage:
    """attachments: (filename, content, maintype/subtype)"""
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg.set_content("body text")
    for filename, content, content_type in attachments:
        maintype, subtype = content_type.split("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg


def _message_with_disposition(filename: str, content: bytes, disposition: str | None) -> MIMEMultipart:
    """Build a PDF part with an explicit (or absent) Content-Disposition,
    unlike EmailMessage.add_attachment which always forces "attachment"."""
    msg = MIMEMultipart()
    msg["Subject"] = "Test"
    msg.attach(MIMEText("body text"))
    part = MIMEApplication(content, _subtype="pdf")
    if disposition is not None:
        part.add_header("Content-Disposition", disposition, filename=filename)
    else:
        del part["Content-Disposition"]
        part.set_param("name", filename, header="Content-Type")
    msg.attach(part)
    return msg


# --- _pdf_attachment_filenames / _pdf_attachments -------------------------


def test_no_attachments_returns_empty():
    msg = _message_with_attachments()
    assert _pdf_attachment_filenames(msg) == []


def test_single_pdf_attachment_detected():
    msg = _message_with_attachments(("invoice.pdf", b"%PDF-1.4 fake", "application/pdf"))
    assert _pdf_attachment_filenames(msg) == ["invoice.pdf"]


def test_non_pdf_attachment_ignored():
    msg = _message_with_attachments(("photo.png", b"fake png bytes", "image/png"))
    assert _pdf_attachment_filenames(msg) == []


def test_mixed_attachments_only_pdfs_returned():
    msg = _message_with_attachments(
        ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf"),
        ("photo.png", b"fake png bytes", "image/png"),
        ("receipt.pdf", b"%PDF-1.4 fake2", "application/pdf"),
    )
    assert _pdf_attachment_filenames(msg) == ["invoice.pdf", "receipt.pdf"]


def test_case_insensitive_extension():
    msg = _message_with_attachments(("Invoice.PDF", b"%PDF-1.4 fake", "application/pdf"))
    assert _pdf_attachment_filenames(msg) == ["Invoice.PDF"]


def test_pdf_attachments_returns_payload_bytes():
    msg = _message_with_attachments(("invoice.pdf", b"%PDF-1.4 fake", "application/pdf"))
    [(filename, payload)] = _pdf_attachments(msg)
    assert filename == "invoice.pdf"
    assert payload == b"%PDF-1.4 fake"


def test_inline_disposition_pdf_detected():
    """Many billing/ERP mailers use Content-Disposition: inline, not
    'attachment' - must still be found."""
    msg = _message_with_disposition("inline.pdf", b"%PDF fake", disposition="inline")
    assert _pdf_attachment_filenames(msg) == ["inline.pdf"]


def test_missing_disposition_header_pdf_detected():
    """Some mailers omit Content-Disposition entirely, relying on
    Content-Type's name= param - must still be found."""
    msg = _message_with_disposition("noheader.pdf", b"%PDF fake", disposition=None)
    assert _pdf_attachment_filenames(msg) == ["noheader.pdf"]


def test_rfc2047_encoded_filename_decoded_and_detected():
    """A MIME encoded-word filename (e.g. non-ASCII vendor name) must be
    decoded before the .pdf suffix check, or it's silently dropped."""
    encoded = "=?UTF-8?B?ZmFjdHVyZS5wZGY=?="  # "facture.pdf"
    msg = _message_with_disposition(encoded, b"%PDF fake", disposition="attachment")
    assert _pdf_attachment_filenames(msg) == ["facture.pdf"]


# --- _decode_mime_words -----------------------------------------------------


def test_decode_mime_words_plain_ascii_unchanged():
    assert _decode_mime_words("plain text") == "plain text"


def test_decode_mime_words_none_passthrough():
    assert _decode_mime_words(None) is None


def test_decode_mime_words_decodes_encoded_word():
    assert _decode_mime_words("=?UTF-8?B?ZmFjdHVyZQ==?=") == "facture"


# --- list_pending_invoices (mocked IMAP) -----------------------------------


def _fake_conn(search_status="OK", search_uids=b"", fetch_bodies=None):
    """A MagicMock standing in for imaplib.IMAP4_SSL, configured for
    conn.uid("search", ...) / conn.uid("fetch", ...) calls."""
    conn = MagicMock()

    def uid_side_effect(command, *args):
        if command == "search":
            return (search_status, [search_uids])
        if command == "fetch":
            requested_uid = args[0].decode()
            body = (fetch_bodies or {}).get(requested_uid)
            if body is None:
                return ("OK", [None])
            return ("OK", [(b"1 (BODY[])", body)])
        raise AssertionError(f"unexpected uid command: {command}")

    conn.uid.side_effect = uid_side_effect
    return conn


def _raw_message_bytes(filename: str) -> bytes:
    msg = _message_with_attachments((filename, b"%PDF fake", "application/pdf"))
    return msg.as_bytes()


@patch("invoice_agent.mcp_servers.gmail_imap._connect")
def test_list_pending_invoices_uses_uid_commands(mock_connect):
    """Regression guard for the sequence-number-vs-UID bug: search/fetch
    must go through conn.uid(...), not conn.search()/conn.fetch() directly."""
    conn = _fake_conn(search_uids=b"101", fetch_bodies={"101": _raw_message_bytes("invoice.pdf")})
    mock_connect.return_value = conn

    results = list_pending_invoices(limit=10)

    assert results == [
        {
            "id": "101",
            "source_description": "Test - from ?",
            "attachment_filenames": ["invoice.pdf"],
        }
    ]
    # Every mailbox call went through conn.uid(...), never conn.search/conn.fetch directly.
    assert conn.search.call_count == 0
    assert conn.fetch.call_count == 0
    conn.logout.assert_called_once()


@patch("invoice_agent.mcp_servers.gmail_imap._connect")
def test_list_pending_invoices_limit_zero_returns_empty(mock_connect):
    """Regression guard: limit=0 must return [], matching
    filesystem_invoices.py's pdfs[:limit] semantics, not 1 result."""
    conn = _fake_conn(search_uids=b"101", fetch_bodies={"101": _raw_message_bytes("invoice.pdf")})
    mock_connect.return_value = conn

    assert list_pending_invoices(limit=0) == []


@patch("invoice_agent.mcp_servers.gmail_imap._connect")
def test_list_pending_invoices_no_search_results(mock_connect):
    conn = _fake_conn(search_uids=b"")
    mock_connect.return_value = conn

    assert list_pending_invoices(limit=10) == []


@patch("invoice_agent.mcp_servers.gmail_imap._connect")
def test_list_pending_invoices_search_failure_raises(mock_connect):
    conn = _fake_conn(search_status="NO")
    mock_connect.return_value = conn

    try:
        list_pending_invoices(limit=10)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "X-GM-RAW" in str(exc)


@patch("invoice_agent.mcp_servers.gmail_imap._connect")
def test_list_pending_invoices_skips_non_pdf_messages(mock_connect):
    conn = _fake_conn(search_uids=b"201 202", fetch_bodies={"201": _raw_message_bytes("invoice.pdf")})
    # 202 has no matching fetch_bodies entry -> _fetch_message returns None -> skipped
    mock_connect.return_value = conn

    results = list_pending_invoices(limit=10)
    assert [r["id"] for r in results] == ["201"]
