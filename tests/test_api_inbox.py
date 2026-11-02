"""Tests for app/inbox.py - the MCP-client endpoints. The MCP server
subprocess itself is faked at the `call_tool` / `MultiServerMCPClient` seam,
so nothing touches Gmail or the real data/inbox/ folder."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

import app.inbox as inbox
from app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    with TestClient(create_app(checkpointer=saver, load_env=False)) as c:
        yield c


def _fake_call_tool(results: dict, calls: list):
    async def fake(source, tool, args):
        calls.append((source, tool, args))
        return results[tool], 12

    return fake


def test_list_inbox_returns_parsed_items(client, monkeypatch):
    calls = []
    item = {"id": "101", "source_description": "Invoice - from a@b.c", "attachment_filenames": ["inv.pdf"]}
    monkeypatch.setattr(inbox, "call_tool", _fake_call_tool({"list_pending_invoices": [{"type": "text", "text": '{"id": "101", "source_description": "Invoice - from a@b.c", "attachment_filenames": ["inv.pdf"]}'}]}, calls))

    body = client.get("/inbox", params={"source": "gmail", "limit": 5}).json()

    assert body == {"tool": "list_pending_invoices", "ms": 12, "items": [item]}
    assert calls == [("gmail", "list_pending_invoices", {"limit": 5})]


def test_download_returns_basenames_and_targets_inbox_dir(client, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(inbox, "call_tool", _fake_call_tool({"download_invoice_pdfs": [{"type": "text", "text": f"{tmp_path}/inbox/101_inv.pdf"}]}, calls))

    body = client.post("/inbox/download", json={"source": "gmail", "id": "101"}).json()

    assert body["files"] == ["101_inv.pdf"]
    assert calls[0][2] == {"id": "101", "dest_dir": str(tmp_path / "inbox")}


def test_mark_returns_bool(client, monkeypatch):
    monkeypatch.setattr(inbox, "call_tool", _fake_call_tool({"mark_processed": [{"type": "text", "text": "false"}]}, []))
    assert client.post("/inbox/mark", json={"source": "filesystem", "id": "x.pdf"}).json()["marked"] is False


def test_mcp_failure_is_a_clean_503(client, monkeypatch):
    class Boom:
        def __init__(self, *_):
            pass

        async def get_tools(self):
            raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set")

    monkeypatch.setattr(inbox, "MultiServerMCPClient", Boom)

    response = client.get("/inbox")

    assert response.status_code == 503
    assert response.json()["error"] == "upstream_unavailable"
    assert "GMAIL_ADDRESS" in response.json()["message"]


def test_unknown_source_is_rejected(client):
    assert client.get("/inbox", params={"source": "dropbox"}).status_code == 422


def test_serves_downloaded_pdf_only(client, tmp_path):
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / "101_inv.pdf").write_bytes(b"%PDF-1.4 x")
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 other user's upload")
    (tmp_path / "inbox" / "notes.txt").write_text("x")

    ok = client.get("/inbox/files/101_inv.pdf")
    assert ok.status_code == 200 and ok.content == b"%PDF-1.4 x"
    assert client.get("/inbox/files/..%2Fsecret.pdf").status_code == 404
    assert client.get("/inbox/files/notes.txt").status_code == 404
    assert client.get("/inbox/files/missing.pdf").status_code == 404
