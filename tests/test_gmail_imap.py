"""Tests for the pure MIME-parsing logic in the Gmail MCP server - no live
IMAP connection needed (that part is exercised manually, see docs/mcp_setup.md)."""

from email.message import EmailMessage

from invoice_agent.mcp_servers.gmail_imap import _pdf_attachment_filenames


def _message_with_attachments(*attachments: tuple[str, bytes, str]) -> EmailMessage:
    """attachments: (filename, content, maintype/subtype)"""
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg.set_content("body text")
    for filename, content, content_type in attachments:
        maintype, subtype = content_type.split("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg


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
