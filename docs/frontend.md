# Frontend — Streamlit demo UI

Phase 9 adds a clickable UI for the interrupt/resume flow that previously
only worked from a terminal (`scripts/run_graph.py`'s `input()` prompts) or
Swagger/`curl` (Phase 8). `frontend/app.py` talks to the FastAPI backend
over HTTP only, via `frontend/api_client.py` — it never imports
`invoice_agent.extract`, `invoice_agent.graph`, or anything that could
reach Anthropic directly, and it never reads `ANTHROPIC_API_KEY` or
`SUPABASE_*`. That's what actually satisfies the roadmap's "never put those
keys in the frontend" pitfall — not refusing to load `.env` at all (this
module calls `load_dotenv()` like every other entrypoint in the repo; it
just has no code path that ever touches those two variables).

## The flow

Upload a PDF (or pick one from the "...or try a sample" dropdown, sourced
from `data/eval/` — committed, so it works in a fresh checkout or a Phase
10 deploy; `data/samples/` is gitignored and only reachable via a real
upload). `POST /invoices` blocks under a spinner until the run interrupts
or completes. A `needs_review` result renders an editable form — header
fields as widgets, `line_items` as an `st.data_editor` table — that submits
corrections to the resume endpoint. A `completed` result shows the saved
record and a CSV download, generated client-side. A `failed` result offers
Retry. The sidebar ("Recent runs") lists the 10 most recent runs and is
itself a navigation tool: clicking any row loads that thread, whatever its
status, so a previously-flagged invoice can be revisited and finished
later without re-uploading.

## Why no polling loop

The roadmap's original instruction for this phase says "POST to the
backend; poll `GET /invoices/{thread_id}`." There is nothing to poll on the
happy path — Phase 8's `POST /invoices` and the resume endpoint both block
until the run interrupts or completes, so the response already carries the
terminal/interrupted state. A spinner around one blocking call is the
actual UX.

The one place polling-shaped logic survives is recovering from a client-
side timeout: `POST /invoices` mints its `thread_id` server-side and only
returns it in the response body, so a timeout means the client never
learns it. Rather than a `time.sleep` loop (which would freeze the whole
Streamlit session and can't be cancelled), `_submit_upload()` snapshots
`GET /invoices`' thread list before the call and diffs it after a timeout —
the one new thread_id that appears is the run that timed out, and it's
loaded directly.

## Why the sidebar reads the checkpointer, not Supabase

The roadmap also says "a sidebar listing the last 10 invoices from
Supabase." `GET /invoices` reads the LangGraph checkpointer instead
(`app/service.py`'s `list_runs()`), which is better here for two reasons:
it surfaces `needs_review`/`failed`/`processing` runs, not just `completed`
ones — exactly the runs a reviewer would want to jump back into — and it
needs zero Supabase credentials in the frontend, which the checkpointer-
backed sidebar gets for free.

## The no-override invariant

There is deliberately no "accept as extracted" button on the `needs_review`
screen. `invoice_agent/graph.py`'s `human_review -> validator` edge is
unconditional: an empty-body resume sends `{"edited_invoice": None}`, the
invoice comes back unchanged, the validator re-runs on the *identical*
data, produces the *identical* flags, and re-interrupts. Verified live —
submitting a flagged invoice with no changes lands back on the same
`needs_review` screen with the same flags, not a silent success. The only
way out is an edit that clears every flag. A caption under the flags says
so explicitly, so this reads as a design decision, not a bug: nothing
reaches the database without passing validation on its final, corrected
form (see `REVIEW.md`'s deterministic-validation invariant).

One consequence worth knowing: a genuine duplicate can never clear its
`Possible duplicate:` flag through editing anything *except*
`invoice_number`/`vendor_name` — there's no override, and there shouldn't
be one. If a demo re-uploads the same sample twice, that's expected, not a
bug — correct the invoice number to move past it.

## Setup

1. No new credentials — `frontend/api_client.py` only needs `BACKEND_URL`
   (optional, defaults to `http://127.0.0.1:8000`; see `.env.example`).
2. Run the backend, then the frontend, in separate terminals:
   ```bash
   uvicorn app.main:app --reload
   streamlit run frontend/app.py
   ```
3. Open the URL Streamlit prints (`http://localhost:8501` by default).
   Confirm the sidebar shows "Backend ready" — if it shows "degraded" or
   "unreachable", check the backend terminal and `BACKEND_URL`.
4. Verify: pick a sample from the dropdown, click "Process invoice", and
   either see `completed` directly or a `needs_review` screen with real
   flags — the extraction is real (calls Anthropic), so this takes 15–40s.
   If flagged only for a duplicate (likely, on a repeat run), correct the
   invoice number and save to confirm the full resume loop reaches
   `completed`.

## Known limitations

- **No non-invoice sample is committed.** `data/eval/`'s 20 files are all
  clean invoices (used for Phase 6's eval harness); the `skipped` status
  (a receipt or other document) has no reproducer via the sample dropdown
  in a fresh checkout — only through a real upload of a non-invoice PDF.
- **`invoice_to_csv_bytes()` imports `invoice_agent.db`** for its
  `CSV_FIELDS` constant (the single source of truth also used by the
  server-side CSV export), which pulls the `supabase` SDK into the
  frontend process. Harmless today; if Phase 10 ever wants a minimal
  frontend-only image, that's the moment to extract the field-list
  constants into a credential-free module — not done preemptively here.
- **Streamlit's script runner prepends this file's own directory to
  `sys.path`**, which — because this file is named `app.py`, same as the
  top-level `app/` backend package — would otherwise shadow `import app`
  and break every `from app.models import ...`. Worked around at the top
  of `frontend/app.py` (see the comment there); this is a one-time,
  well-contained fix, not something later code needs to think about.
- **No auth, no rate limiting** — matches the backend's own posture
  (`docs/api.md`); this is a demo, not a hardened deployment.
