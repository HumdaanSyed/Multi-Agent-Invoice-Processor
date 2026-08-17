# Observability — Langfuse tracing

Phase 7 adds tracing so a graph run is a debuggable, inspectable trace
instead of a black box: which node ran, what the LLM saw and returned,
how many tokens it cost, how long each step took, and — for a flagged
invoice — the full pause/resume cycle in one place.

## What gets traced

Every `graph.invoke()` call produces one Langfuse trace named `LangGraph`
containing:

- **The full node tree** — `router → extractor → validator → (human_review
  ↔ validator loop) → output`, plus LangGraph's own conditional-edge
  functions (`route_after_classification`, `route_after_validation`) as
  nested spans. This is captured automatically: LangGraph node execution
  goes through LangChain's `Runnable` protocol, so passing a
  `langfuse.langchain.CallbackHandler` in `graph.invoke()`'s `config`
  traces the whole node tree with zero per-node code.
- **Token usage and cost for both Claude calls** (`router`'s classification
  call and `extractor`'s extraction call) — model, input/output tokens,
  and Langfuse's auto-computed cost from its model pricing table. This
  part is *not* automatic: `invoice_agent/extract.py` and `graph.py`'s
  `router` call the raw `anthropic` SDK directly (`client.messages.parse`),
  not `langchain_anthropic`, because the raw SDK is what makes the
  `document` content block + `output_format=Invoice` structured-extraction
  pattern from Phase 1 work in one call. That bypasses LangChain's
  callback propagation entirely, so `invoice_agent/tracing.py`'s
  `traced_generation()` context manager manually records each call as a
  Langfuse "generation" observation, nested under the current node's span.
  See that module's docstring for the full reasoning.

## Why Langfuse over LangSmith

LangSmith is the more turnkey option if you're all-in on LangChain, but
LangSmith cut its old free tier, and Langfuse is MIT-licensed,
self-hostable (Docker Compose, though see the self-hosting note below),
framework-agnostic (built on OpenTelemetry — the `document`/raw-SDK calls
here integrate the same way a non-LangChain call from any other framework
would), and roughly an order of magnitude cheaper than LangSmith at
production scale. Langfuse Cloud's free tier (50K units/month) comfortably
covers this project's usage.

## Setup

1. Create a Langfuse Cloud project — [cloud.langfuse.com](https://cloud.langfuse.com) or
   [us.cloud.langfuse.com](https://us.cloud.langfuse.com) depending on which region you want your data in.
2. Project Settings → API Keys → create a new key pair.
3. Add to `.env`:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com   # or https://us.cloud.langfuse.com
   ```
   `LANGFUSE_HOST` defaults to the EU host if unset — set it explicitly if
   your project is on the US region, or traces will silently go to the
   wrong region's API and never show up.
4. Run any invoice through the graph (`python scripts/run_graph.py <pdf>` or
   `python -m invoice_agent.ingest_mcp --source filesystem`) and check the
   Langfuse dashboard's Traces tab.

**Tracing is optional.** If `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`
aren't set, `invoice_agent/tracing.py`'s functions all degrade to no-ops —
`graph.invoke()` runs exactly the same, just untraced. Tracing is never a
hard requirement for the pipeline to work, matching this project's
existing pattern for optional integrations (e.g. `db.is_duplicate`
defaulting to skipped when no `duplicate_checker` is wired in).

## Interrupt/resume shows as one trace, not two

`interrupt()`/`Command(resume=...)` means a flagged invoice's "before" and
"after" halves are two separate `graph.invoke()` calls (see
`invoice_agent/graph.py`'s module docstring). Naively building a fresh
`CallbackHandler()` for each call would produce two disconnected traces for
what's really one logical run. Instead, `trace_callbacks(thread_id)` derives
a Langfuse trace ID deterministically from the graph's own `thread_id`
(`langfuse.create_trace_id(seed=thread_id)` — same seed, same ID) and passes
it as the handler's `trace_context`. Both `graph.invoke()` calls for the
same `thread_id` land in the same trace, so a flagged invoice's full
lifecycle — extraction, the flag, the pause, the correction, re-validation,
persistence — reads as one trace instead of two fragments. Verified: a
deliberately-broken invoice that required two review rounds (a math
correction, then a duplicate-flag round) produced one 19-observation trace
spanning both `graph.invoke()` calls.

## Known limitations

- Only the two direct Anthropic calls (router, extractor) are traced as
  generations. If a future phase adds another raw-SDK LLM call, it needs
  its own `traced_generation()` wrapper — it won't be picked up
  automatically the way a `langchain_anthropic` call would be.
- Self-hosting Langfuse v3 needs Postgres + ClickHouse + Redis + an
  S3-compatible store — meaningfully more infrastructure than this
  project's other pieces. Cloud free tier is the low-risk path and what
  this project uses; self-hosting is documented by Langfuse but not set up
  here.
- Traces are sent asynchronously in the background; short-lived scripts
  (`scripts/run_graph.py`, `invoice_agent/ingest_mcp.py`) call
  `invoice_agent.tracing.flush()` before exiting so buffered trace data
  isn't lost when the process ends. A future long-running FastAPI backend
  (Phase 8) won't need this — the process stays alive, so the background
  sender has time to flush on its own.
