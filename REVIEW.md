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
- **Unguarded optional-integration SDK calls in a path that must never
  hard-fail.** Any call into a third-party SDK for a feature that is
  explicitly optional/degrade-to-no-op (tracing, metrics, logging-as-a-
  service) needs its own `try/except` — not just a top-level
  `tracing_enabled()`-style flag check — because the SDK can still throw
  once actually invoked (bad credentials, unreachable host, wrong region).
  Two distinct failure points, both bit us in `invoice_agent/tracing.py`
  (Phase 7 review): an **entry-time** failure (constructing a handler/
  starting an observation) must not prevent the wrapped business call from
  running at all; an **exit-time** failure (a post-success `flush()` or a
  `.update()` on an already-open span) must not convert work that already
  completed successfully into a reported failure. Check both ends of any
  new optional-integration call, not just the one that's easiest to guard.
- **Dependency version bound looser than the API surface actually used.**
  `pyproject.toml` pinning a package to `>=X.Y.0` when the code calls APIs
  that only exist from a later *major* version onward — bit us with
  `langfuse>=2.53.0` while using v3+-only entry points
  (`langfuse.langchain.CallbackHandler`, `get_client().start_as_current_observation`)
  that don't exist in v2 (Phase 7 review). The lockfile hides this because
  it already resolved a compatible version; a fresh install without the
  lockfile (CI cache miss, a new contributor, Docker rebuild) can resolve
  the oldest version satisfying the bound and fail at import or call time.
  When a diff starts using APIs introduced in a specific major version,
  check the dependency's lower bound requires at least that major, not
  just "some version that happens to work with what's currently locked."
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
- **An identifier silently doing double duty across two systems.** A value
  minted for one purpose (LangGraph's `thread_id`, a checkpoint key) gets
  reused as the uniqueness key for an unrelated second system (Langfuse
  trace identity, derived via `create_trace_id(seed=thread_id)`) with no
  enforcement that the second system's uniqueness requirement actually
  holds. Currently safe in `invoice_agent/tracing.py` because every caller
  mints a fresh `uuid.uuid4()` — but nothing stops a future caller from
  reusing a `thread_id` (e.g. a retry-with-same-thread pattern) and
  silently merging two unrelated runs into one trace. Lower severity than
  the other patterns here — usually a doc-comment fix, not a functional
  one — but worth a check whenever a diff adds a new caller of an
  identifier that already has an established uniqueness contract for one
  purpose.
- **Exception classification by type alone, ignoring which call site
  raised it.** An error-translation layer maps one exception type (e.g.
  `RuntimeError`) to one user-facing category via `isinstance()`, but the
  same type is raised by multiple, semantically different call sites
  deeper in the stack. Bit us in `app/routes.py`'s
  `_translate_graph_exception` (Phase 8 ultrareview): `output()`'s
  Supabase/Storage failures and `db.get_client()`'s missing-config failure
  (reached from `validator()` via `db.is_duplicate`) are both a plain
  `RuntimeError`, but only the former means "we tried and failed to
  save" — the classifier mapped both to `persistence_failed`, reporting a
  config problem as if a save had been attempted. Fixed by checking which
  node's task actually recorded the error (`derive_status()`'s
  `failed_at_node`, already computed for the API's own status field)
  before trusting the exception's type alone. Any classifier built on
  `isinstance()` over a broad exception type is a candidate — ask whether
  every call site that can raise that type actually means the same thing.
- **A sanitized outward-facing error response with no server-side log of
  the real exception.** Returning a safe, generic message to the client is
  correct when it deliberately avoids leaking PII/internal detail (see the
  Security/PII priority above) — but only if the real exception is
  captured somewhere an operator can actually find it. `app/errors.py`'s
  per-`ApiError`-subclass handler didn't log at all; the only durable
  trace of a failure was the checkpointer's `tasks[].error` column, one
  SQL query away from useless in practice. `RunResponse`'s own docstring
  already promised "full detail goes to the server log, keyed by
  thread_id" — a promise nothing was checking against the code meant to
  fulfill it. Fixed with `logger.exception(...)` at the translation point.
  When reviewing an error-sanitization boundary, check that hiding detail
  from the client didn't also silently drop it from the server.
- **A script's own filename shadowing a same-named top-level package.**
  `frontend/app.py` broke every `from app.models import ...` inside
  `frontend/api_client.py` (Phase 9), because Streamlit's script runner
  prepends the *executed script's own directory* to `sys.path` before
  running it — so a bare `import app`, triggered from anywhere in the
  import chain, resolved to `frontend/app.py` itself instead of the
  top-level `app/` package, both sharing the name "app". The first fix
  attempt (`if str(REPO_ROOT) not in sys.path: sys.path.insert(0, ...)`)
  didn't work either: `REPO_ROOT` was already in `sys.path` via the
  editable install's `.pth` file, just at a lower-priority *position* than
  where the script runner had just inserted `frontend/` — so the "already
  present" guard skipped the insert, and `frontend/` kept winning the
  search. The real fix removes-then-reinserts to guarantee position 0, not
  just presence. General lesson, not just for this exact collision: when a
  framework mutates `sys.path` (or any ordered search path) ahead of your
  own code, checking *membership* is not the same as checking *priority* —
  and a same-named file/package one directory below another is always a
  candidate for this class of shadow, independent of Streamlit.
- **A keyed UI widget shown again with different underlying data, under
  the same fixed key.** Streamlit (and similar immediate-mode UI
  frameworks) only honors a widget's `value=` argument the first render its
  `key` exists in session state; every later render, session state wins
  and `value=` is silently ignored. `frontend/app.py`'s `needs_review`
  screen can legitimately re-render with a *different* invoice after a
  resume that only partially fixed the flags (the human_review -> validator
  edge is unconditional, so a partial fix re-interrupts with the edited
  data) — a fixed `key="total"` would have kept showing the pre-edit value
  on the second render. Fixed with a nonce namespacing every widget key,
  bumped whenever a new run result is stored (`wkey()`/`form_nonce` in
  `frontend/app.py`). Verified live: editing one field of a two-flag
  invoice and resubmitting correctly showed the edited value, not a stale
  one, on the re-interrupt. Any screen that can be re-shown with new data
  through the same code path — not just a page navigation — is a candidate
  for this if its widgets use static keys.
- **A client-side "exactly one new item appeared, so it must be mine"
  recovery heuristic.** `frontend/app.py`'s upload-timeout recovery
  (Phase 9 ultrareview) diffed `GET /invoices`' thread list before/after a
  client-side timeout and auto-loaded whichever single new `thread_id`
  appeared, on the assumption that a lost server-side `thread_id` could be
  safely re-identified this way. It can't: on any deployment with more
  than one concurrent user, a *different* upload completing in the same
  window makes the diff land on someone else's thread — the fix removed
  the auto-load entirely rather than trying to disambiguate further (there
  was nothing to disambiguate *with* — no client-supplied token, no
  filename match, nothing). Any "correlate my lost request with a list of
  server-side state by elimination" mechanism is a candidate for this —
  count-based or set-difference-based matching is only safe when the
  system is provably single-tenant at that moment, which a shared demo
  deployment isn't.
- **A "success" response body trusted without validation.** Every
  `api_client.py` function that parses a 2xx (or an explicitly-handled
  non-2xx like `readiness()`'s 200/503) response body called
  `response.json()` and `Model.model_validate(data)` with no try/except,
  while every caller only caught the library's own `BackendError` (Phase 9
  ultrareview, independently surfaced by three separate review angles - a
  strong signal this shape is easy to miss). A non-JSON body or a body
  that doesn't match the target Pydantic schema — both realistic on a
  version-skewed backend/frontend pair, or a proxy returning an
  unexpected-but-200 error page — raised an uncaught `ValueError`/
  `pydantic.ValidationError` that crashed the whole request instead of
  going through the library's own designed error path. Any HTTP client
  wrapper that defines a custom exception type for "the call failed" needs
  to ask, separately, "what happens if the call *succeeds* with a body
  that doesn't parse or doesn't validate" — that path needs the same
  wrapping, not just the non-2xx path.
- **Baked-in image metadata surviving a runtime command override.**
  Docker's `HEALTHCHECK` (also `ENTRYPOINT`, labels) is set once at image
  build time and applies to *every* container run from that image,
  regardless of a `docker run`/Compose `command:` override — only a
  matching `docker run --health-cmd` or Compose `healthcheck:` block
  actually replaces it. Bit us in `Dockerfile` (Phase 10 review):
  `docker-compose.yml`'s `frontend` service overrode `command:` to run
  Streamlit on 8501 but inherited the backend-oriented `HEALTHCHECK CMD
  curl localhost:8000/health` unchanged, so the container ran correctly
  but reported `unhealthy` forever — confirmed live via `docker inspect`'s
  health log. Fixed with an explicit `healthcheck:` override in every
  compose file/service that changes what the container actually serves.
  Any multi-purpose image (one `Dockerfile`, several `command:` overrides
  for different services) is a candidate — check that anything baked into
  image metadata besides the entrypoint got a matching per-service
  override, not just the command.
- **A deploy/setup doc's claim not matching what its own steps actually
  do.** `deploy/README.md` (Phase 10 review) asserted "one shared image,"
  but `docker-compose.yml` gave `backend`/`frontend` independent `build:
  .` blocks with no shared `image:` tag, so Compose silently built two
  full images — reproduced live via `docker compose build` showing two
  separately-tagged ~945MB images. Separately, `deploy/railway.md`'s intro
  claimed both services "pull the pre-built GHCR image," but its own
  numbered steps said "Deploy from GitHub repo" (Railway builds from
  source), directly contradicting the doc's stated premise. Both were
  design claims nobody had checked against the actual config/steps below
  them. Any doc that asserts a design property ("one image," "uses the
  pre-built artifact," "no state is lost") is a candidate for verification
  against the literal config it describes, not just a read-through for
  plausibility.
- **A markdown inline code span line-wrapped across two source lines.**
  CommonMark/GFM collapses a newline inside a backtick code span to a
  single space in the rendered output. `deploy/railway.md` (Phase 10
  review) wrote `` `CHECKPOINT_DB_PATH=/data/\n     checkpoints/graph.sqlite` ``
  across a line wrap for readability — GitHub renders (and a reader
  copy-pastes) `CHECKPOINT_DB_PATH=/data/ checkpoints/graph.sqlite`, a
  broken value with an injected space, defeating the exact setup step it
  was part of. Any doc with a long inline-code value (a path, a command, a
  URL) wrapped for line length is a candidate — keep values that will be
  copy-pasted verbatim on one physical line, even if that line runs long.

- **`chown -R` on a parent directory assumed to substitute for `mkdir` on
  a child that a volume will later mount over.** Docker only auto-creates
  a missing volume-mount-point directory *as root* if the image doesn't
  already have that path — copying an existing directory's ownership into
  a fresh named volume on first mount only happens when the directory
  already exists in the image at build time. Bit us fixing the very
  previous finding in this file (Phase 10 review, self-inflicted): the
  original `Dockerfile` had `mkdir -p checkpoints uploads exports && chown
  -R app:app checkpoints uploads exports`; replacing it with just `chown
  -R app:app /app` (to generalize beyond three hardcoded names) silently
  dropped the `mkdir`, so `checkpoints/`/`uploads/`/`exports/` no longer
  existed in the image for `chown` to act on — `docker compose up` then
  auto-created them as root-owned mount points and the non-root backend
  crashed with `sqlite3.OperationalError: unable to open database file`.
  Caught only by actually running `docker compose up` against fresh
  volumes, not by reading the Dockerfile. Any edit that generalizes a
  `mkdir && chown` pair into a broader `chown -R` needs to keep (or
  re-verify) the `mkdir` for every specific path something else — a
  volume mount, a bind mount — will later target, and needs a real
  `docker compose up` from clean state to catch a dropped one; ownership
  bugs at this layer don't show up in `docker compose build` or `uv run
  pytest`, only in an actual container boot against a fresh volume.

- **Dockerfile syntax that's valid on local `docker`/Buildx but rejected
  by a hosted platform's own builder.** `docker/dockerfile:1`'s
  `RUN --mount=type=cache,target=...` is valid with no `id=` on standard
  BuildKit (it infers one from the target path) — Railway's builder
  ("Metal") rejects it outright: `dockerfile invalid: flag
  '--mount=type=cache,target=/root/.cache/uv' is missing an id argument`.
  Bit us in `Dockerfile` (Phase 10 review): both `uv sync` cache-mount
  `RUN` lines built and ran fine in every local/CI verification pass, then
  failed on the very first real Railway deploy. Fixed by adding an
  explicit `id=uv-cache` (valid on both builders). Local `docker build`/
  `docker compose build`/a GitHub Actions Buildx job all share the same
  underlying BuildKit, so passing all three does not establish a
  Dockerfile is portable to a *different* builder implementation — a
  platform-specific builder (Railway, Google Cloud Build, etc.) is only
  actually verified by a real deploy through it, and that step can't be
  skipped just because every other builder accepted the same file.

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
