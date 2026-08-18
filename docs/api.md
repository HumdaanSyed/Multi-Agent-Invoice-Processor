# API — FastAPI backend

Phase 8 exposes the graph over HTTP, including the human-in-the-loop resume
flow that previously only worked from a terminal (`scripts/run_graph.py`'s
`input()` prompts). A `thread_id` is the API's core concept: LangGraph's
checkpointer makes a run a long-lived, resumable entity, so a client gets
one back from `POST /invoices` and uses it for every later call about that
same run — a flagged invoice doesn't need to be re-uploaded to correct it.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| `POST` | `/invoices` | Uploads a PDF, runs it through the graph, blocks until it interrupts or completes. |
| `GET` | `/invoices/{thread_id}` | Current status, read from the checkpointer — no execution. |
| `POST` | `/invoices/{thread_id}/resume` | Submits corrections (or an empty body) and continues the run. |
| `GET` | `/invoices` | The 20 most recent runs, newest first. |
| `GET` | `/health` | Static liveness — zero I/O. |
| `GET` | `/health/ready` | Per-service config check (Anthropic, Supabase, Langfuse, checkpointer). |

Every endpoint that reports on a run (`POST /invoices`, `GET
/invoices/{thread_id}`, and the resume endpoint) returns the same envelope:
`{thread_id, status, ...}`, where `status` is one of `processing`,
`needs_review`, `completed`, `skipped`, or `failed`. Which other fields are
populated depends on `status` — `invoice`/`flags` for `needs_review`,
`invoice`/`validation` for `completed`, `doc_type` for `skipped` (the router
sent a receipt/other document straight to the end), `failed_at_node` for
`failed`. A client branches on `status` the same way no matter which
endpoint produced it — see `app/service.py`'s `derive_status()` for the
exact rules, which are more precise than they look: `status` in the
underlying `GraphState` is stale in three of the five API-level states (most
notably right after a persistence failure, where it still reads whatever the
last *successful* node set), so the API derives its own status from the
checkpointer's snapshot rather than trusting that field directly.

## Why blocking, not 202 + polling

`POST /invoices` holds the connection open for the full extraction — 15–40s
typically — rather than returning immediately with a `202` and having the
client poll. Two things made this decision, not one: the roadmap's
acceptance criterion is a Swagger UI walkthrough, and Swagger can't poll a
background job; and on this project's single-worker deployment (see
`CLAUDE.md`'s 1GB-RAM pitfalls), a `BackgroundTasks` run would just as
easily get orphaned by a Railway redeploy mid-request, leaving a thread
wedged at `processing` forever with no failure recorded anywhere.

The tradeoff: a slow proxy can time out the HTTP response before the graph
finishes. This is deliberately not "fixed" so much as made safe to hit —
LangGraph's checkpointer means the run keeps going and its state is durable
even if the response never arrives, so a timed-out client loses nothing but
the response body. `GET /invoices` recovers the `thread_id` from the recent-
runs list, and `Anthropic(timeout=120.0)` (`invoice_agent/graph.py`,
`invoice_agent/extract.py`) bounds the worst case — the SDK's own default is
600s with 2 retries, which would otherwise let one request run for ~20
minutes.

## The resume contract

`POST /invoices/{thread_id}/resume` takes a **sparse patch** of invoice
fields (`{"corrections": {"total": 448.33}}`), not a full invoice. The patch
is merged over the invoice the human was actually shown, then validated as a
real `Invoice` *before* the graph is touched — not passed through as-is.
This matters for reasons that aren't obvious from the roadmap's original
wording ("accepts edited fields and resumes with `Command(resume=...)`"):

- Omitting `line_items` from a full-invoice payload would make
  `db.insert_invoice` run an unfiltered `delete().eq("invoice_id", ...)`,
  wiping every line item already stored for that invoice.
- A malformed merged invoice would raise `ValidationError` *inside* the
  validator node, wedging the thread as an opaque `500` instead of a clean
  `422` at the boundary.

All editable fields are accepted, not just `total` (which is all
`scripts/run_graph.py`'s CLI prompt allows) — the validator also raises date
and duplicate-vendor/invoice-number flags, and restricting corrections to
`total` would make those unresolvable.

**A genuine duplicate can never clear its flag**, and this is by design, not
a bug: `resume` always re-runs the full validator, including the duplicate
check, so a truly re-sent invoice re-interrupts indefinitely rather than
ever reaching `output()`. There's no `force`/override parameter — accepting
one would let a duplicate reach persistence without actually passing
validation on its final form, which is the one invariant this project
doesn't compromise on (see `REVIEW.md`). If the duplicate is a false
positive, correct `invoice_number` or `vendor_name` instead.

## Setup

1. No new credentials — the backend reuses `ANTHROPIC_API_KEY` and
   `SUPABASE_URL`/`SUPABASE_KEY` from `.env`. Optional overrides
   (`MAX_UPLOAD_BYTES`, `UPLOAD_DIR`, `CHECKPOINT_DB_PATH`,
   `GRAPH_MAX_CONCURRENCY`) are documented in `.env.example`; every one has
   a working default if unset.
2. Run it:
   ```bash
   uvicorn app.main:app --reload
   ```
3. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) — Swagger
   UI. Upload a PDF via `POST /invoices`, note the `thread_id` it returns,
   and resume it via `POST /invoices/{thread_id}/resume`.
4. Verify: `GET /health` returns `{"status": "ok"}` with no delay (no
   network call happens); `GET /health/ready` reports which services are
   actually configured. If a flagged invoice's only remaining flag starts
   with `"Possible duplicate:"`, that's expected the second time you upload
   the same sample PDF — see "The resume contract" above.

## Known limitations

- **Single worker, in-process locking.** The per-`thread_id` lock and
  global concurrency semaphore in `app/service.py`'s `GraphService` are
  `threading` primitives scoped to one process — correct for the single-
  Uvicorn-worker deployment this project targets (`CLAUDE.md`'s 1GB-RAM
  pitfall), not for a multi-worker or multi-instance one. A production
  version would need a Postgres checkpointer plus a real distributed lock.
- **`checkpoints/graph.sqlite` grows unbounded.** Nothing prunes old
  checkpoints. Fine for a demo's traffic; a real deployment would want a
  retention policy.
- **Uploaded PDFs are swept after 24h**, not kept indefinitely — retrying
  an empty-body resume against a run older than that fails with a clear
  `failed`/`router` (or `failed`/`output`) status rather than succeeding
  silently, since the source file is gone.
- **No auth, no rate limiting.** The demo is intentionally public. A
  `Semaphore(2)` bounds concurrent graph runs, which is a resource limit,
  not an access control.
