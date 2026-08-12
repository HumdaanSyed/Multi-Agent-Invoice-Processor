"""Tests for the filesystem MCP server - patches INBOX_DIR/PROCESSED_DIR to
a tmp_path so nothing touches the repo's real data/inbox/."""

from pathlib import Path

import pytest

from invoice_agent.mcp_servers import filesystem_invoices as fs


@pytest.fixture(autouse=True)
def _isolated_inbox(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    processed = inbox / "processed"
    monkeypatch.setattr(fs, "INBOX_DIR", inbox)
    monkeypatch.setattr(fs, "PROCESSED_DIR", processed)
    monkeypatch.setattr(fs, "REPO_ROOT", tmp_path)
    return inbox


def _write_pdf(inbox: Path, name: str, content: bytes = b"%PDF fake") -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_bytes(content)
    return path


# --- list_pending_invoices ---------------------------------------------------


def test_list_pending_invoices_empty_inbox(_isolated_inbox):
    assert fs.list_pending_invoices() == []


def test_list_pending_invoices_finds_pdf(_isolated_inbox):
    _write_pdf(_isolated_inbox, "invoice.pdf")
    results = fs.list_pending_invoices()
    assert len(results) == 1
    assert results[0]["id"] == "invoice.pdf"
    assert results[0]["attachment_filenames"] == ["invoice.pdf"]


def test_list_pending_invoices_case_insensitive_extension(_isolated_inbox):
    """Regression guard: glob("*.pdf") was case-sensitive, missing
    uppercase extensions that gmail_imap.py's equivalent check handles."""
    _write_pdf(_isolated_inbox, "Invoice.PDF")
    results = fs.list_pending_invoices()
    assert [r["id"] for r in results] == ["Invoice.PDF"]


def test_list_pending_invoices_ignores_non_pdf(_isolated_inbox):
    _write_pdf(_isolated_inbox, "notes.txt")
    assert fs.list_pending_invoices() == []


def test_list_pending_invoices_respects_limit(_isolated_inbox):
    for i in range(5):
        _write_pdf(_isolated_inbox, f"invoice{i}.pdf")
    assert len(fs.list_pending_invoices(limit=2)) == 2


# --- download_invoice_pdfs ---------------------------------------------------


def test_download_invoice_pdfs_copies_file(_isolated_inbox, tmp_path):
    _write_pdf(_isolated_inbox, "invoice.pdf", b"real content")
    dest_dir = tmp_path / "attachments"

    paths = fs.download_invoice_pdfs("invoice.pdf", dest_dir=str(dest_dir))

    assert len(paths) == 1
    assert Path(paths[0]).read_bytes() == b"real content"
    assert Path(paths[0]).parent == dest_dir


def test_download_invoice_pdfs_unknown_id_returns_empty(_isolated_inbox, tmp_path):
    assert fs.download_invoice_pdfs("missing.pdf", dest_dir=str(tmp_path / "attachments")) == []


def test_download_invoice_pdfs_rejects_path_traversal(_isolated_inbox, tmp_path):
    """Regression guard for the path-traversal bug: an id containing '..'
    must never resolve outside INBOX_DIR."""
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=super-secret")
    dest_dir = tmp_path / "attachments"

    result = fs.download_invoice_pdfs("../secret.env", dest_dir=str(dest_dir))

    assert result == []
    assert not (dest_dir / "secret.env").exists()


def test_download_invoice_pdfs_different_content_same_name_dont_collide(_isolated_inbox, tmp_path):
    """Two different source PDFs sharing a filename across separate calls
    must not overwrite each other's copy in dest_dir."""
    dest_dir = tmp_path / "attachments"

    _write_pdf(_isolated_inbox, "invoice.pdf", b"vendor A content")
    [path_a] = fs.download_invoice_pdfs("invoice.pdf", dest_dir=str(dest_dir))

    _write_pdf(_isolated_inbox, "invoice.pdf", b"vendor B content")
    [path_b] = fs.download_invoice_pdfs("invoice.pdf", dest_dir=str(dest_dir))

    assert path_a != path_b
    assert Path(path_a).read_bytes() == b"vendor A content"
    assert Path(path_b).read_bytes() == b"vendor B content"


# --- mark_processed -----------------------------------------------------------


def test_mark_processed_moves_file(_isolated_inbox):
    _write_pdf(_isolated_inbox, "invoice.pdf")

    assert fs.mark_processed("invoice.pdf") is True
    assert not (_isolated_inbox / "invoice.pdf").exists()
    assert any(fs.PROCESSED_DIR.glob("*invoice.pdf"))


def test_mark_processed_unknown_id_returns_false(_isolated_inbox):
    assert fs.mark_processed("missing.pdf") is False


def test_mark_processed_rejects_path_traversal(_isolated_inbox, tmp_path):
    """Regression guard for the path-traversal bug: an id containing '..'
    must never move a file from outside INBOX_DIR."""
    victim = tmp_path / "pyproject.toml"
    victim.write_text("[project]\nname = \"invoice-agent\"")

    result = fs.mark_processed("../pyproject.toml")

    assert result is False
    assert victim.exists()
