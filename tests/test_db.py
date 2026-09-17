"""Tests for invoice_agent.db. insert_invoice/is_duplicate/upload_pdf all
call get_client() and need a real client - exercised manually (see
docs/ROADMAP.md's Phase 4 done-criteria), not here. insert_export_row/
iter_export_rows/export_status also call get_client(), but their row-
building/pagination/query logic is exercised below against FakeSupabaseClient
(a minimal stand-in for the query builder) rather than only via the
route-level mocks in tests/test_api_export.py, which monkeypatch these
functions themselves and so never run their real bodies.
"""

import invoice_agent.db as db
from invoice_agent.db import CSV_FIELDS, LEDGER_CSV_FIELDS, export_ledger_row_to_csv_rows, invoice_csv_rows

INVOICE = {
    "invoice_number": "INV-1",
    "invoice_date": "2026-07-01",
    "vendor_name": "Acme Co",
    "bill_to": "Someone",
    "line_items": [
        {"description": "Widget", "quantity": 2, "unit_price": 5.0, "amount": 10.0},
        {"description": "Gadget", "quantity": 1, "unit_price": 3.0, "amount": 3.0},
    ],
    "subtotal": 13.0,
    "tax": 1.04,
    "total": 14.04,
    "due_date": "2026-07-31",
    "currency": "USD",
}

# A ledger row, once fetched back from Supabase, has every key a plain
# invoice dict has (insert_export_row writes exactly _HEADER_FIELDS +
# line_items) plus its own id/written_at/thread_id columns.
LEDGER_ROW = {
    "id": 1,
    "written_at": "2026-07-02T10:00:00+00:00",
    "thread_id": "t-abc",
    **INVOICE,
}


def test_ledger_csv_fields_is_provenance_plus_csv_fields():
    """LEDGER_CSV_FIELDS must stay derived from CSV_FIELDS, not a separately
    hand-typed list - see export_ledger_row_to_csv_rows's docstring."""
    assert LEDGER_CSV_FIELDS == ["written_at", "thread_id"] + CSV_FIELDS


def test_export_ledger_row_to_csv_rows_one_row_per_line_item():
    rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    assert len(rows) == 2
    assert rows[0]["invoice_number"] == "INV-1"
    assert rows[0]["line_item_description"] == "Widget"
    assert rows[1]["line_item_description"] == "Gadget"


def test_export_ledger_row_to_csv_rows_adds_provenance_to_every_row():
    rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    for row in rows:
        assert row["written_at"] == "2026-07-02T10:00:00+00:00"
        assert row["thread_id"] == "t-abc"


def test_export_ledger_row_to_csv_rows_matches_invoice_csv_rows_shape():
    """The ledger's CSV output can't silently diverge from the frontend's
    per-invoice CSV download - both must build the same per-line-item
    fields from invoice_csv_rows()."""
    ledger_rows = export_ledger_row_to_csv_rows(LEDGER_ROW)
    plain_rows = invoice_csv_rows(LEDGER_ROW)

    assert len(ledger_rows) == len(plain_rows)
    for ledger_row, plain_row in zip(ledger_rows, plain_rows):
        for field in CSV_FIELDS:
            assert ledger_row[field] == plain_row[field]


class _FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    """Just enough of the Supabase query builder's chained-call surface to
    run insert_export_row/iter_export_rows/export_status's real bodies
    in-process: .table(...).insert(...)/.select(...).gt(...).order(...)
    .limit(...)/.desc-order-limit-1, all against a plain in-memory list."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._count_mode: str | None = None
        self._head = False
        self._order: list[tuple[str, bool]] = []
        self._gt: tuple[str, object] | None = None
        self._limit: int | None = None
        self._insert_row: dict | None = None

    def select(self, *_columns, count=None, head=False):
        self._count_mode = count
        self._head = head
        return self

    def insert(self, row: dict):
        self._insert_row = dict(row)
        return self

    def order(self, field: str, desc: bool = False):
        self._order.append((field, desc))
        return self

    def gt(self, field: str, value):
        self._gt = (field, value)
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def execute(self) -> _FakeResult:
        if self._insert_row is not None:
            row = {**self._insert_row, "id": len(self._rows) + 1}
            self._rows.append(row)
            return _FakeResult([row])

        rows = list(self._rows)
        if self._gt is not None:
            field, value = self._gt
            rows = [r for r in rows if r[field] > value]
        for field, desc in reversed(self._order):
            rows.sort(key=lambda r: r[field], reverse=desc)
        count = len(rows) if self._count_mode == "exact" else None
        if self._head:
            rows = []
        if self._limit is not None:
            rows = rows[: self._limit]
        return _FakeResult(rows, count=count)


class FakeSupabaseClient:
    """Minimal in-memory stand-in for the real Supabase client, so tests
    below exercise insert_export_row/iter_export_rows/export_status's
    actual bodies instead of monkeypatching those functions away."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self.tables.setdefault(name, []))


def test_insert_export_row_writes_header_fields_thread_id_and_line_items(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)

    result = db.insert_export_row("t-1", INVOICE)

    assert result["invoice_number"] == "INV-1"
    assert result["thread_id"] == "t-1"
    assert result["line_items"] == INVOICE["line_items"]
    assert fake.tables["invoice_exports"] == [result]


def test_iter_export_rows_pages_through_all_rows_oldest_first(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)
    for thread_id in ("t-1", "t-2", "t-3"):
        db.insert_export_row(thread_id, INVOICE)

    # page_size=1 forces iter_export_rows to make three separate keyset
    # round trips rather than fetching everything in one call.
    rows = list(db.iter_export_rows(page_size=1))

    assert [r["thread_id"] for r in rows] == ["t-1", "t-2", "t-3"]
    assert [r["id"] for r in rows] == [1, 2, 3]


def test_iter_export_rows_empty_ledger_yields_nothing(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)

    assert list(db.iter_export_rows()) == []


def test_export_status_reports_row_count_and_most_recent_written_at(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)
    # written_at is a DB-assigned default (see db/schema.sql) - insert_export_row
    # never sets it itself, so seed the fake table directly to simulate that.
    fake.tables["invoice_exports"] = [
        {"id": 1, "written_at": "2026-07-01T00:00:00+00:00", "thread_id": "t-1"},
        {"id": 2, "written_at": "2026-07-02T00:00:00+00:00", "thread_id": "t-2"},
    ]

    status = db.export_status()

    assert status["row_count"] == 2
    assert status["last_written_at"] == "2026-07-02T00:00:00+00:00"


def test_export_status_empty_ledger_reports_zero_and_none(monkeypatch):
    fake = FakeSupabaseClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)

    status = db.export_status()

    assert status == {"row_count": 0, "last_written_at": None}


# --- get_pdf_signed_url (Phase 9D's detail-view "source PDF" link) --------


class _FakeStorageBucket:
    def __init__(self, signed_url: str | None = None, raises: bool = False):
        self._signed_url = signed_url
        self._raises = raises
        self.requested_path: str | None = None
        self.requested_expires_in: int | None = None

    def create_signed_url(self, path: str, expires_in: int):
        if self._raises:
            raise RuntimeError("storage unreachable")
        self.requested_path = path
        self.requested_expires_in = expires_in
        return {"signedURL": self._signed_url, "signedUrl": self._signed_url}


class _FakeStorageClient:
    def __init__(self, bucket: _FakeStorageBucket):
        self._bucket = bucket

    def from_(self, bucket_name: str):
        assert bucket_name == db.PDF_BUCKET
        return self._bucket


class _FakeClientWithStorage:
    def __init__(self, bucket: _FakeStorageBucket):
        self.storage = _FakeStorageClient(bucket)


def test_get_pdf_signed_url_returns_the_signed_url(monkeypatch):
    bucket = _FakeStorageBucket(signed_url="https://supabase.example/invoices/x.pdf?token=abc")
    monkeypatch.setattr(db, "get_client", lambda: _FakeClientWithStorage(bucket))

    url = db.get_pdf_signed_url("invoices/x.pdf", expires_in=120)

    assert url == "https://supabase.example/invoices/x.pdf?token=abc"
    assert bucket.requested_path == "invoices/x.pdf"
    assert bucket.requested_expires_in == 120


def test_get_pdf_signed_url_degrades_to_none_on_failure(monkeypatch):
    """A Storage hiccup must not break the detail view around a completed
    invoice that already persisted fine - see get_pdf_signed_url's
    docstring."""
    bucket = _FakeStorageBucket(raises=True)
    monkeypatch.setattr(db, "get_client", lambda: _FakeClientWithStorage(bucket))

    assert db.get_pdf_signed_url("invoices/x.pdf") is None
