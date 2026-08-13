# CLAUDE.md

## Project Overview
A production-grade multi-agent invoice processing system, built as a portfolio/resume piece and Fiverr showcase. Pipeline: a user (or an automated inbox pull) provides a PDF invoice, agents classify it, extract structured data, validate it against business rules with human-in-the-loop review for flagged cases, and write clean results to a database. Target audience for the finished project: recruiters reviewing GitHub, and Fiverr clients wanting document automation.

Goal for every session: keep the system runnable end-to-end. Never leave it in a broken state between phases.

## Tech Stack
- Python 3.12, dependencies managed with `uv`
- LangGraph (LangChain 1.x) for multi-agent orchestration
- Anthropic API, `claude-sonnet-5` as the primary extraction model (Haiku 4.5 for simple/high-volume cases, Opus 5 for hard scanned documents, routed based on eval results)
- Pydantic v2 for all structured data contracts between agents
- Supabase (Postgres + Storage) as the database and file store
- `langchain-mcp-adapters` + an MCP server (Gmail attachment puller, filesystem fallback) for automated ingestion
- Langfuse for tracing/observability
- FastAPI + Uvicorn backend
- Streamlit for the demo frontend
- Docker, deployed to Railway (primary demo) and documented for AWS EC2 free tier

## Architecture
Graph: `Router -> Extractor -> Validator -> (interrupt if flagged) -> Output`

- **Router**: classifies the document (invoice / receipt / other), routes accordingly
- **Extractor**: calls Claude with the PDF as a `document` content block + Pydantic `output_format` in a single call (no separate OCR step needed for most documents)
- **Validator**: deterministic Python checks only, not LLM-based (line items sum to subtotal, subtotal+tax=total, valid dates, duplicate check against Supabase). Raises flags and triggers `interrupt()` for human review when needed
- **Output**: upserts to Supabase, uploads source PDF to Storage, exports CSV

State is a `TypedDict`: `file_path`, `doc_type`, `invoice`, `validation`, `status`, `messages`. Nodes return partial state updates only. Checkpointer is `SqliteSaver` (never `InMemorySaver` once the FastAPI backend exists, state must survive across requests).

Every `graph.invoke()` is traced via `invoice_agent/tracing.py` (optional — no-op without `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`). LangGraph's node tree is auto-captured by the `langfuse.langchain.CallbackHandler` passed in `config`; the two raw-`anthropic`-SDK calls (router, extractor) are manually recorded as generation spans since they bypass `langchain_anthropic`. See `docs/observability.md`.

## Conventions
- All inter-agent data contracts are Pydantic models, not raw dicts
- Business-rule validation (math, dates, duplicates) is plain Python, never delegated to the LLM
- Secrets live in `.env`, never committed (`.env.example` documents required vars)
- Tests in `tests/`, run with `pytest`
- Every phase should end in a runnable, demoable state, don't move on if the previous phase is half-working

## Commands
- `python scripts/smoke_test.py` — verify all service connections (Anthropic, Supabase)
- `python -m invoice_agent.extract <pdf_path>` — test single-PDF extraction
- `python scripts/run_graph.py <pdf_path>` — run the full LangGraph pipeline
- `python -m invoice_agent.ingest_mcp --source filesystem|gmail` — pull invoices via MCP and run each through the graph (see `docs/mcp_setup.md`)
- `python evals/run_eval.py` — run the eval harness, outputs `evals/report.md`
- `uvicorn app.main:app --reload` — run backend locally
- `streamlit run frontend/app.py` — run frontend locally
- `docker compose up` — run full stack in containers

## Current Phase
Phase 8: FastAPI backend

## Roadmap
The full 11-phase build plan lives in `docs/ROADMAP.md` (not auto-loaded here, it's long). At the start of a session working on a new phase, say: "Read docs/ROADMAP.md, we're on Phase 8, implement it."

## Known Pitfalls (don't relearn these)
- Never combine `citations` with structured outputs on the same Claude API call, it errors
- Structured output schemas: max 24 optional fields, max 16 union-typed fields
- `InMemorySaver` loses all state between HTTP requests, must use `SqliteSaver` or Postgres once the backend exists
- Don't build Docker images on a t3.micro EC2 box, it OOMs (exit code 137). Build locally or in CI and pull the image instead
- Run a single Uvicorn worker on 1GB RAM instances, multiple workers will OOM
- Supabase free-tier projects pause after 7 days of inactivity, reactivate before demos
- MCP `stdio` transport is local-machine only, a deployed backend needs an HTTP-transport MCP server or a separate local/cron ingestion job
- `LANGFUSE_HOST` defaults to the EU Langfuse Cloud host if unset - a project on the US region needs it set explicitly, or traces silently go to the wrong region and never appear
- Short-lived scripts must call `invoice_agent.tracing.flush()` before exiting (traces send asynchronously in the background) - a long-running process like the future FastAPI backend won't need this

## Working Style
- Follow the phase order in the project roadmap, don't skip ahead to later phases before earlier ones are solid
- For ambiguous design decisions (schema shape, error handling approach), propose an option and proceed rather than stalling on a question, unless the choice is hard to reverse
- After any code change, run the relevant smoke test or script before considering the task done
- Keep commits scoped to one phase or fix at a time
