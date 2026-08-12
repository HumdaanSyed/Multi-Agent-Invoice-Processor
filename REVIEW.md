# Review guidance

Tuning for automated code review (`/code-review`, ultrareview) on this repo.
Goal: high-signal reviews that catch the bug classes this project actually
has, and stop re-litigating settled design choices or nit-level style.

## Priority order

1. **Deterministic-validation invariants** (see below) — this is the one
   thing this project cannot get wrong and still call itself "production-grade."
2. **Data integrity across Supabase writes** — partial failures, non-atomic
   multi-step operations, anything that can silently corrupt or lose rows.
3. **Security / data handling** — secrets, service-key scope, anything that
   touches `.env`, credentials, or PII (`bill_to`, vendor names).
4. **LangGraph correctness** — state mutation, node side effects, routing,
   checkpointer behavior.
5. Everything else (naming, style, minor efficiency) — low priority, only
   worth a comment if it's cheap to fix and genuinely reduces risk.

## Skip entirely

- Style/formatting nits with no behavioral effect.
- Suggesting the extraction/validation math should be "smarter" or
  LLM-assisted — see the invariant below, this is intentional.
- Flagging sequential vs. parallel network calls in `output()` as an
  efficiency issue unless it's causing a real correctness problem (ordering
  matters here more than latency for this project's scale).
- Suggesting a full ORM/migration framework, ACID transactions via a
  Postgres RPC, or similar heavyweight fixes — this is a portfolio project on
  Supabase's free tier; propose the smallest change that removes silent data
  loss, not the "correct" enterprise architecture.
- Re-flagging anything in "known intentional patterns" below.

## Deterministic-validation invariant (core to this project)

Per `CLAUDE.md`: business-rule validation (math, dates, duplicates) is
**plain Python only, never delegated to an LLM**, and every invoice that
reaches persistence must have actually passed `validate_invoice()` on its
*final* form — not the form it had before a human correction.

Flag, at high severity, any change where:
- Data reaches `db.insert_invoice` / `db.export_invoice_csv` (or any future
  persistence call) without having gone through `validator` immediately
  before it, on the exact data being persisted.
- A human-in-the-loop correction (`human_review`'s `edited_invoice`) can
  reach persistence without re-running validation — including the duplicate
  check, which depends on `vendor_name`/`invoice_number` and must be
  re-checked if those fields are editable.
- Validation logic (math tolerance, date parsing, duplicate detection)
  moves into a prompt or an LLM call instead of staying plain Python.

This class of bug bit us twice already (Phase 3 and Phase 4 reviews): an
`output`/persistence step reachable from `human_review` without looping back
through `validator` first. If you see that shape again, it's a repeat, not a
new finding to explain from scratch — cite this file.

## Recurring bug patterns already found here

These exact shapes have shown up in prior reviews of this repo. Treat a new
instance of any of these as high-confidence, not merely plausible:

- **Unconditional persistence after a corrective/retry path.** A node or
  function reachable after "the human/system tried to fix it" that skips
  straight to a write instead of re-validating the final data.
- **Non-transactional multi-step Supabase writes** (e.g. delete-then-insert,
  upload-then-upsert) with no ordering that fails safe. Prefer "insert new
  before deleting old" (worst case: duplicates, recoverable) over "delete
  old before inserting new" (worst case: silent zero-rows data loss).
- **Unchecked external API responses** — indexing `result.data[0]` or
  similar without checking the response is non-empty. Supabase/PostgREST in
  particular can return an empty `data` under RLS/`return=minimal`
  conditions that are easy to miss in dev.
- **Bare `os.environ[...]` / raw exceptions on missing config** instead of a
  clear, actionable error message. `scripts/smoke_test.py`'s pattern (check,
  then raise/print a specific "set X in .env" message) is the convention to
  match.
- **Import-time side effects.** Module import must not touch the
  filesystem, open a DB/network connection, or do other I/O — compile
  graphs/clients lazily (see `get_graph()` in `invoice_agent/graph.py`).
- **Unbounded serial network fetch over an unfiltered result set.**
  Iterating every result of a broad search/list call and doing a heavy
  per-item network fetch just to filter client-side, instead of pushing the
  filter into the query itself. Bit us in
  `invoice_agent/mcp_servers/gmail_imap.py`'s `list_pending_invoices`:
  IMAP-searched all `UNSEEN` messages (1,147 on a real account) and did a
  full `BODY.PEEK[]` fetch on each one just to check for a PDF attachment,
  instead of filtering server-side first (Gmail's `X-GM-RAW` search
  extension) — ~25 min worst case instead of ~8s. Any future "list X, then
  inspect each one in detail to decide if it matches" loop against a remote
  API is a candidate for this, regardless of which API it is.
- **Filename-only uniqueness assumptions** for anything derived from
  user-supplied or ingested files (e.g. Storage paths) — two different
  source documents can share a filename.
- **Session-scoped identifiers treated as stable across separate calls.**
  IMAP message sequence numbers (as opposed to UIDs) are only valid within
  the connection/session that produced them — bit us in
  `gmail_imap.py`, where `list_pending_invoices` returned a sequence number
  that a later `download_invoice_pdfs`/`mark_processed` call (a fresh
  `_connect()` session) could silently resolve to a different message if
  the mailbox changed in between. Fixed via `conn.uid(...)` throughout. Any
  identifier handed back across two separate connections/sessions to an
  external system is a candidate for this — check whether the protocol
  guarantees that identifier survives a new session, or only a stable ID
  (like a UID, not an index/offset/sequence number) does.
- **Untrusted filenames/ids used directly as local path components.** Any
  value that crosses a trust boundary (a MIME attachment filename from an
  inbound email, an MCP tool's caller-supplied `id` argument) and gets
  concatenated into a filesystem path without reducing it to just its
  basename (`Path(x).name`) is a path-traversal candidate — a value like
  `"../../.env"` resolves outside the intended directory. Bit us in both
  `gmail_imap.py`'s `download_invoice_pdfs` (MIME filename) and
  `filesystem_invoices.py`'s `download_invoice_pdfs`/`mark_processed`
  (MCP tool `id` argument, independently callable e.g. via the MCP
  Inspector — not restricted to values the server itself produced).
- **Overly strict matching on an external system's loosely-specified data.**
  Real-world MIME/email producers vary more than the "happy path" a first
  implementation assumes — `Content-Disposition` is sometimes absent or
  `inline` rather than `attachment`, filenames are sometimes RFC 2047
  encoded-words, extensions vary in case. Matching on the narrowest signal
  (an exact disposition string, a case-sensitive suffix) instead of the
  loosest correct one (does this part have a decoded, `.pdf`-suffixed
  filename at all?) silently drops real data with no error. When reviewing
  code that parses/filters real-world external input (email, uploaded
  files, third-party API responses), ask what the loosest correct
  matching rule is, not just whether today's happy-path test passes.

## Known intentional patterns — do not re-flag

- `exports/invoices.csv` is an **append-only audit log**, not deduplicated
  like the `invoices` table. Re-running/correcting the same invoice
  intentionally adds another CSV row. This is a feature, not a duplicate-data
  bug.
- `validate_invoice()`'s `duplicate_checker` parameter defaults to `None`
  (no-op) so unit tests stay offline and deterministic. Production wiring to
  `db.is_duplicate` happens in `graph.py`'s `validator` node, and that wiring
  is covered by `test_validator_wires_db_is_duplicate` in `tests/test_graph.py`
  — don't re-flag the default as "duplicate checking is broken/disabled"
  without checking whether the call site wires it.
- `GraphState` carries `invoice`/`validation` as plain `dict`, not the
  `Invoice` Pydantic model, because LangGraph state is a `TypedDict` that
  must merge partial updates and survive checkpointing/serialization. This
  is a deliberate exception to the "Pydantic models, not raw dicts"
  convention, scoped to graph state only — extraction/validation still
  produce and consume real `Invoice` objects.
- `insert_invoice`'s line-item replacement is "insert new, then delete old
  excluding the new IDs" rather than a real transaction — see "recurring bug
  patterns" above for why the order matters. A full atomic fix (Postgres RPC)
  is out of scope until there's evidence this project needs it.

## What "done" looks like for a finding here

A finding is worth reporting if it names a concrete input/state that
produces wrong data in Supabase, a crash with no actionable message, or a
security/secrets exposure. If the failure scenario is "this could be
cleaner" with no behavioral consequence, it belongs in a simplification pass,
not a bug review.
