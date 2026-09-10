# Verity: Frontend Migration Plan (Streamlit to Next.js)

Companion document to `docs/ROADMAP.md`. This replaces the original Phase 9
(Streamlit demo UI) and inserts two backend phases before it.

**Product name: Verity.** It lives in exactly one place in the codebase
(`frontend/lib/config.ts`) so it can be changed with a one-line edit.

Same working rules as the roadmap: one branch per phase, always leave the
project runnable, update `CLAUDE.md`'s Current Phase line as you go, and add
any new bug shapes to `REVIEW.md` the day you hit them.

---

## Stack decision

| Layer | Choice | Why |
|---|---|---|
| Framework | **Next.js, App Router, TypeScript** | Most requested React framework in freelance listings. Native streaming support, which this project specifically needs. Server components keep the client bundle small. |
| Styling | **Tailwind CSS** | Fast to iterate, no CSS file sprawl, universally recognized by reviewers. |
| Components | **shadcn/ui** | Components are copied into your repo, not imported from a package, so you own and can restyle them. Avoids the "obviously a template" look. |
| Data fetching | **TanStack Query** | Handles polling, caching, and request state cleanly. Replaces hand-rolled `useEffect` fetch logic. |
| Streaming transport | **Server-Sent Events (SSE)** | One-directional server to client, which is exactly this use case. Simpler than WebSockets, works through standard HTTP. |

Rejected: plain React (you would rebuild routing and a server layer by hand),
Angular (strong in enterprise, much weaker signal for freelance work).

---

## Design system: warm paper

The whole visual idea is that Verity should feel like working on a clean
desk with good paper, not like a dashboard. Restrained, warm, typographic.
Almost no color except where color carries meaning.

### Color tokens

```
--bg            #FAF8F5   warm off-white page background
--surface       #FFFFFF   cards, panels, the "sheet of paper"
--border        #E8E2D9   warm hairline borders, never pure grey
--text          #1C1917   near-black, warm-toned
--text-muted    #78716C   labels, secondary info
--accent        #24405B   ink blue, like fountain pen. Primary actions, links
--accent-soft   #EDF1F5   accent tint for subtle backgrounds

Semantic (the ONLY other color in the app):
--ok            #15803D   check passed, validated, export written
--flag          #B45309   needs review, warning amber
--error         #B91C1C   check failed, error states
```

Rule to follow throughout: color communicates state, never decoration. A
button is ink blue because it is the primary action, a check is red because
it failed. Nothing is colored to look nice.

### Typography

Three roles, loaded from Google Fonts with real fallback stacks:

- **Headings and the Verity wordmark:** a serif. `Instrument Serif` or
  `Fraunces`. This is what creates the document feel.
- **UI and body:** a clean sans. `Inter` is fine and unobtrusive.
- **Numbers and identifiers:** a mono. `JetBrains Mono` or `IBM Plex Mono`,
  used for invoice numbers, amounts, dates, and thread IDs. This makes
  extracted data instantly scannable.

### Layout rules

- Generous whitespace. Minimum 24px padding inside cards, 48px between
  major sections.
- Max content width around 1100px, centered. Never full-bleed.
- Borders over shadows. If a shadow is used, keep it barely visible.
- Border radius: 6px to 8px. Not pill-shaped, not sharp.
- No illustrations, no icon soup, no gradients. Icons only where they
  replace a word (check pass/fail, status dots, upload).

---

## Screens

1. **Home / Upload.** Verity in serif, one line describing what it does, a
   drop zone. Recent runs below.
2. **Live run.** The document on the left. On the right, fields populating
   one by one as they are extracted, then the validation checks running
   underneath them, each turning green or red.
3. **Review.** Opens automatically when any check fails. Flagged fields are
   editable, failures explained in plain language, one action to approve.
4. **Detail.** A completed invoice, read-only, with links to its source PDF
   and its trace.

**Persistent export bar.** A slim footer present on every screen showing the
ledger row count, when it was last written to, and an Export CSV button.
Inside an active run it also shows that run's ledger status, which flips to
a green "Added to ledger" once the export row is committed.

---

## Two backend changes this requires

### 1. Streaming, and one honest limitation

Today `extract_invoice()` makes one blocking call and returns a finished
`Invoice`. To show fields appearing progressively, three things change:

1. **A streaming variant of the extractor.** Anthropic's streaming API emits
   partial JSON as the model generates. Accumulate those deltas, parse the
   partial JSON progressively, and fire a callback each time a new
   top-level field completes.
2. **An event bus.** The extractor and validator nodes run inside the
   LangGraph pipeline, so they need a way to push events out to whatever
   HTTP connection is watching. An in-memory registry mapping `thread_id`
   to an `asyncio.Queue` is enough here.
3. **An SSE endpoint.** `GET /invoices/{thread_id}/stream` subscribes to
   that queue and yields events until the run reaches a terminal state or
   interrupts.

**The honest limitation, which belongs in the README:** an in-memory event
bus only works with a single worker process. You already run a single
Uvicorn worker because of the 1GB memory constraint, so this is consistent
with the existing deployment, but it is a real ceiling. Scaling to multiple
workers would mean moving the bus to Redis pub/sub. Say this plainly rather
than discovering it in an interview.

**Two invariants this must not break:**

- **Streamed fields are display only.** The authoritative `Invoice` object
  is still the fully parsed, validated one. Streaming must never become a
  path by which unvalidated data reaches persistence. This is the core
  invariant in `REVIEW.md` and it applies here directly.
- **The non-streaming path must keep working.** The eval harness (Phase 6)
  and MCP ingestion (Phase 5) both call `extract_invoice()` and neither
  should be forced through a streaming code path. Add the streaming variant
  alongside the existing function, do not replace it.

### 2. The export ledger, replacing the local CSV file

Today the Output node appends to `exports/invoices.csv` on the container's
local disk. That file does not survive a redeploy, so it cannot back a
user-facing export button as-is.

**Replace it with an append-only `invoice_exports` table in Postgres, and
generate the CSV on demand.** Rationale:

- Preserves the append-only audit-log semantics that `REVIEW.md` documents
  as intentional (a corrected re-run adds a row, it does not overwrite one),
  which regenerating from the deduplicated `invoices` table would lose.
- Survives redeploys, which the local file does not.
- Avoids a real race. With `Semaphore(2)`, two invoices completing at once
  would both read-modify-write a single file or storage blob and one would
  clobber the other. Concurrent row inserts are safe by default.
- Export becomes a simple ordered SELECT streamed out as CSV, with no file
  to keep in sync.

There is exactly one ledger, shared across all invoices. No per-invoice
export files.

---

## Phase 8.5: Streaming backend (1 to 1.5 days)

**Goal:** the backend emits both field-level extraction events and
per-check validation events over SSE, with the existing non-streaming path
untouched.

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 8.5.
>
> Add streaming support without changing the existing `extract_invoice()`
> function or `validate_invoice()`'s return contract, which the eval harness,
> MCP ingestion, and existing tests depend on.
>
> 1. In `invoice_agent/extract.py`, add
>    `extract_invoice_streaming(pdf_path, on_field=None)`. Use the Anthropic
>    streaming API with the same document block and Pydantic output format.
>    Accumulate partial JSON deltas and invoke `on_field(field_name, value)`
>    each time a new top-level field completes. Return the same validated
>    `Invoice` object at the end, identical to the non-streaming function.
> 2. In `invoice_agent/validate.py`, refactor the internals so each rule
>    produces a structured result: a stable `check_id`, a human-readable
>    label, a passed boolean, and a detail message on failure. Add an
>    optional `on_check` callback fired per rule. `ValidationResult` must
>    keep its existing `passed` / `flags` / `needs_review` fields, with
>    `flags` derived from the failed checks, so nothing downstream and no
>    existing test changes.
> 3. Add `invoice_agent/events.py`: an in-memory registry mapping thread_id
>    to an asyncio.Queue, with `publish`, `subscribe`, and cleanup on
>    terminal state. Document in a module docstring that this is
>    single-worker only and would need Redis for horizontal scaling.
> 4. Wire the graph's extractor and validator nodes to publish events when a
>    thread has a subscriber: node stage changes, per-field extraction
>    events, and per-check validation events.
> 5. Add `GET /invoices/{thread_id}/stream` returning `text/event-stream`.
>    Close on terminal or interrupted state. Handle client disconnect
>    without leaking queues.
> 6. Enable CORS for the Next.js dev origin, configurable via env.
>
> Do not add artificial delays anywhere to make events easier to watch. The
> frontend handles pacing.
>
> Streamed values are display only. The object that reaches validation and
> persistence must still be the fully parsed, validated Invoice.

**Done when:** `curl -N http://localhost:8000/invoices/<id>/stream` prints
field events then check events as an invoice processes, and both
`pytest -q` and `python evals/run_eval.py` pass unchanged.

**Pitfalls:**
- Partial JSON is invalid JSON most of the time. Use a tolerant partial
  parser, do not naively call `json.loads` on every delta.
- Clean up queues on disconnect or you will leak memory on the 1GB box.
- Test that an interrupt mid-stream closes the SSE connection cleanly
  rather than hanging it open.
- Keep `check_id` values stable strings. The frontend maps them to labels
  and ordering, so renaming one silently breaks the UI.

---

## Phase 8.6: Export ledger (0.5 to 1 day)

**Goal:** replace the local CSV file with a durable append-only ledger and
expose it for download.

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 8.6.
>
> Replace the local `exports/invoices.csv` file with an append-only Postgres
> ledger.
>
> 1. Add an `invoice_exports` table to `db/schema.sql`: an auto-increment
>    id, a written_at timestamp, the invoice's thread_id, and one column per
>    exported invoice field. No unique constraint. This is deliberately
>    append-only, so a corrected re-run adds a new row rather than
>    overwriting.
> 2. In the Output node, insert the ledger row as part of the same write
>    path as the invoice upsert, after it succeeds. Publish a ledger event
>    to the event bus so the frontend can show it landed.
> 3. Add `GET /export.csv` streaming the full ledger ordered by written_at
>    as a CSV download with proper Content-Disposition. Stream it rather
>    than building the whole file in memory, given the 1GB constraint.
> 4. Add `GET /export/status` returning row count and last written_at, for
>    the persistent export bar.
> 5. Delete the local CSV writing code and the `exports/` directory
>    handling. Update README and REVIEW.md, which currently describes the
>    CSV file as an append-only audit log, to describe the table instead.
>
> Keep the append-only semantics. Do not deduplicate the ledger and do not
> derive it from the invoices table.

**Done when:** processing two invoices then re-running a corrected one
produces three ledger rows, `GET /export.csv` downloads all three, and the
ledger survives a container restart.

**Pitfall:** an invoice must never appear in the ledger unless it passed
validation and was persisted. The ledger insert belongs after the invoice
write succeeds, not before or in parallel.

---

## Phase 9A: Next.js scaffold and design system (0.5 day)

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 9A.
>
> Create a `frontend/` Next.js app (App Router, TypeScript, Tailwind) at the
> repo root. Do not delete the Streamlit app yet.
>
> 1. Set up the color tokens from the plan as CSS variables in globals.css
>    and wire them into the Tailwind theme. Support the dark scheme by
>    redefining tokens under prefers-color-scheme, keeping the warm feel.
> 2. Load the three fonts (serif for headings, sans for UI, mono for
>    numbers) via next/font with real fallback stacks.
> 3. Install shadcn/ui and restyle its base components to match these
>    tokens: borders over shadows, 6-8px radius, generous padding.
> 4. Build the app shell: centered max-width container, a minimal header
>    with "Verity" in serif, nothing else.
> 5. Create `lib/config.ts` exporting the product name as a single constant.
> 6. Add a typed API client in `lib/api.ts` covering all backend endpoints
>    including the stream and export routes, base URL from an env var.

**Done when:** `npm run dev` shows a styled empty shell and the API client
successfully calls `GET /health`.

---

## Phase 9B: Upload, live extraction, live validation (1.5 days)

**Goal:** the flagship screen. This is what sells the project.

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 9B.
>
> Build the upload and live run screens.
>
> 1. Home screen: "Verity" in serif, one line describing what it does, a
>    drag-and-drop upload zone with client-side file type validation.
> 2. On upload, POST to the backend, get a thread_id, route to the live run
>    view, and open the SSE connection.
> 3. Live run view, two columns. Left: the uploaded PDF. Right: an
>    extraction panel where every field starts as a skeleton and fills in as
>    its event arrives, using the mono font for numbers, dates, and
>    identifiers.
> 4. Below the fields, a validation panel. Each check appears as a row with
>    its label, showing a neutral pending state, then resolving to green
>    with a check mark if it passed or red with a cross and its failure
>    detail if it did not.
> 5. Important: validation runs in milliseconds, so all check events arrive
>    almost simultaneously. Stagger the reveal client-side, roughly 200ms
>    per check, so a viewer can actually read them resolving in sequence.
>    Do not add delays to the backend to achieve this.
> 6. Show the current pipeline stage (classifying, extracting, validating)
>    above the panels, driven by node events.
> 7. Animate arrivals subtly. A short fade or a skeleton dissolving into
>    text. Nothing bouncy.
> 8. When all checks resolve: if any failed, route to the review screen once
>    the stagger finishes. If all passed, show the ledger confirmation and
>    route to the detail view.
> 9. Handle the SSE connection dropping by falling back to polling
>    `GET /invoices/{thread_id}` so the UI still reaches a correct final
>    state.

**Done when:** uploading a clean invoice shows fields populating, then all
checks going green, then the ledger confirmation. Uploading a deliberately
broken one shows the failing check in red and opens the review screen.
Killing the connection mid-run still lands on the correct final screen.

**Pitfall:** do not let the UI imply a field is confirmed while it is still
streaming. Fields stay visibly provisional until validation completes.

---

## Phase 9C: Human review (0.5 day)

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 9C.
>
> Build the review screen, reached automatically when any validation check
> fails.
>
> 1. Show the source PDF alongside the extracted fields.
> 2. Carry the failed checks over from the live view, keeping them visible
>    in red with their failure detail, so the reviewer sees exactly what
>    went wrong without re-reading the whole invoice.
> 3. Fields implicated by a failed check get the amber treatment and are
>    editable. Other fields are editable behind an "edit all" toggle.
> 4. Explain failures in plain language, not raw backend flag strings.
> 5. One primary action: Approve and Save, POSTing to the resume endpoint.
> 6. On resume, re-run the same live check display. If the correction still
>    fails, show the new results in place rather than navigating away or
>    silently accepting.
> 7. Disable the action while a resume is in flight so a double click cannot
>    fire two resumes.

**Done when:** a broken invoice reaches this screen, a partial fix visibly
re-fails the relevant check, and a full fix passes, writes to the ledger,
and routes to detail.

---

## Phase 9D: Dashboard, detail, export bar (1 day)

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 9D.
>
> 1. On the home screen below the upload zone, list recent runs from the
>    backend list endpoint. Include every state, not just completed, since
>    needs_review and failed are the states this demo exists to show. Status
>    is a small colored dot plus a word, no badges.
> 2. Build the read-only detail view: all fields, line items in a clean
>    table, links to the source PDF and the trace, thread_id in mono.
> 3. Build the persistent export bar as a slim footer on every screen. It
>    shows the ledger row count and last-written time from
>    `GET /export/status`, plus an Export CSV button hitting
>    `GET /export.csv`. Inside an active run it additionally shows that
>    run's ledger status: pending while processing, then green "Added to
>    ledger" when the ledger event arrives.
> 4. Make clear in the bar's copy that this is one shared ledger of every
>    processed invoice, not a per-invoice download.
> 5. Add empty and error states in the same restrained style. An empty
>    ledger says something useful, not "no data".

**Done when:** the full loop works from the browser with no Streamlit
involved, and the export button downloads a CSV containing every processed
invoice.

---

## Phase 9E: Retire Streamlit (0.5 day)

**Claude Code instruction:**

> Read docs/FRONTEND_PLAN.md. We're on Phase 9E.
>
> 1. Delete the Streamlit app and its dependency.
> 2. Update docker-compose.yml to build and run the Next.js frontend. Use a
>    multi-stage build with Next.js standalone output to keep the image
>    small, remembering the deployment target is memory constrained.
> 3. Update README setup instructions, the architecture diagram, and any
>    screenshots referencing Streamlit. Use the name Verity throughout.
> 4. Update CLAUDE.md's tech stack section.
> 5. Add README notes on the two known limitations: the in-memory event bus
>    is single-worker only, and the ledger is append-only by design.

**Done when:** `docker compose up` serves the Next.js frontend and a fresh
clone can follow the README successfully.

---

## Review guidance additions

Add these to `REVIEW.md` when you start Phase 8.5:

- **Streamed data reaching persistence.** Any path where a partially
  streamed field could be written to the database instead of the validated
  Invoice object. High severity, same class as the existing validation
  invariant.
- **Ledger rows written without a successful invoice write.** An
  `invoice_exports` insert that can land when the invoice upsert failed, or
  that runs before it, producing an audit log that disagrees with the
  invoices table.
- **Leaked event queues.** A subscriber queue not cleaned up on client
  disconnect, terminal state, or error. Memory leak on a 1GB box.
- **The non-streaming path breaking.** Any change to `extract_invoice()` or
  to `ValidationResult`'s existing fields that forces the eval harness, MCP
  ingestion, or existing tests through the new code paths.
- **Artificial backend delays.** Any sleep or pacing added to the pipeline
  to make the frontend animation look better. Pacing belongs in the client.

Also update the existing "known intentional patterns" entry that describes
`exports/invoices.csv`, since that file no longer exists after Phase 8.6.

---

## Model guidance

- **Phase 8.5:** `opusplan`. The event bus, its lifecycle, and how streaming
  interacts with interrupts are genuinely ambiguous design decisions.
- **Phase 8.6:** Sonnet. Schema plus endpoint work with a clear shape.
- **Phase 9A, 9B, 9D, 9E:** Sonnet.
- **Phase 9C:** Sonnet, reviewed carefully. It touches the resume flow,
  which is the highest-risk logic in the system.

Run `/code-review high` after 8.5, 8.6, and 9C.

---

## Scope warning

This is roughly 5 to 6 days on top of an already long build. If time gets
tight, the phases that matter most for how Verity presents to a Fiverr buyer
are 8.5, 8.6, 9A, 9B, and 9E. The live extraction and validation view is the
thing that sells it. 9C and 9D can ship rougher.
