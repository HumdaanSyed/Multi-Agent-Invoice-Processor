# Multi-Agent Invoice Processor

**Turns a PDF invoice into a validated, database-ready record — with a human in the loop for anything that doesn't add up.**

A recruiter or client hands this system an invoice (or it pulls one from an inbox automatically). A small pipeline of specialized agents classifies it, extracts every field with a structured-output LLM call, runs deterministic business-rule checks (not another LLM call — plain arithmetic and date math), flags anything wrong for a human to fix, and writes the clean result to Postgres. Every run is traced end-to-end, the extraction accuracy is measured against a labeled eval set rather than asserted, and the whole thing ships as one Docker image with a live URL.

**[Live demo →](https://frontend-production-d7a5.up.railway.app)**

<p align="center">
  <!-- Add a screenshot/GIF here: docs/images/frontend-upload.png -->
  <!-- Verified live on 2026-08-20: sidebar shows "Backend ready", a
       "...or try a sample" dropdown of the 20 eval invoices next to the
       upload box, and processing eval_01 correctly landed on the
       needs-review screen (a real "possible duplicate" flag, since that
       sample had already been run once) with the flag text, an editable
       field-by-field form, and a Resume action - the human-in-the-loop
       path in the architecture diagram below, working end-to-end. -->
  <em>Screenshot / demo GIF placeholder — see docs/demo_script.md for the shot list</em>
</p>

## Why this exists

Most "AI invoice extraction" demos stop at "the LLM read the PDF." That's the easy 80%. The parts that actually matter for a system someone would trust with real money are the other 20%: *is the extracted total mathematically consistent, is this a duplicate, what happens when the model gets something wrong, and can someone verify any of this after the fact.* This project is built around those questions, not around the extraction call.

## How it works

```mermaid
flowchart TD
    subgraph Ingestion
        A1["Streamlit upload"]
        A2["MCP: filesystem watch"]
        A3["MCP: Gmail IMAP"]
    end

    A1 --> B
    A2 --> B
    A3 --> B

    B(["FastAPI backend<br/>POST /invoices"]) --> C

    subgraph Pipeline["LangGraph pipeline (invoice_agent/graph.py)"]
        direction TB
        C["Router<br/>classify: invoice / receipt / other"]
        D["Extractor<br/>Claude + Pydantic structured output"]
        E["Validator<br/>plain Python: math, dates, duplicates"]
        F{{"needs_review?"}}
        G["Human Review<br/>interrupt() — edit fields, resume"]
        H["Output<br/>upsert + upload + CSV export"]

        C -- "invoice" --> D
        C -- "receipt / other" --> END1(["end — routed out"])
        D --> E
        E --> F
        F -- "no" --> H
        F -- "yes" --> G
        G -- "re-validates every resume" --> E
    end

    H --> I[("Supabase<br/>Postgres + Storage")]
    H --> J["exports/invoices.csv"]

    Pipeline -.->|"every node + both raw Claude calls"| K["Langfuse<br/>trace per run"]

    style K fill:#00000000,stroke-dasharray: 5 5
```

Every run is a LangGraph `thread_id` checkpointed to SQLite, so a flagged invoice can sit "awaiting review" indefinitely and resume exactly where it left off — including across a backend restart, since state lives in SQLite, not memory.

## Feature list

- **PDF-native extraction** — Claude reads the PDF directly (as a `document` content block) with a Pydantic `output_format`; no separate OCR step for the common case, digital or scanned.
- **Deterministic validation, not vibes** — line items must sum to the stated subtotal, subtotal + tax must equal total (±$0.01), dates must be valid ISO 8601 with `due_date >= invoice_date`, and vendor+invoice-number pairs are checked against Supabase for duplicates. None of this is delegated to the LLM.
- **Human-in-the-loop review** — any flagged invoice interrupts the graph (`interrupt()`) with the extracted data and the exact flags that tripped; a human edits the offending fields in the Streamlit UI and resumes. The resume path re-runs the *same* validator, not a rubber stamp — a partial fix that leaves one flag standing re-interrupts instead of silently passing through.
- **Automated ingestion** — an MCP server pulls new invoices from a filesystem inbox or a Gmail account (IMAP + App Password) and runs each one through the same pipeline unattended.
- **Full observability** — every graph run is traced in Langfuse: the LangGraph node tree is auto-captured, and the two raw Claude calls (router, extractor) are recorded as generation spans with cost/latency, since they bypass `langchain_anthropic`'s own tracing hook.
- **Quantified accuracy, not a claim** — a 20-document eval harness scores field-level exact-match, micro/macro-F1, and a document-level "every field and every line item correct" metric, sliced by currency and render type (digital vs. scanned). See [Eval results](#eval-results) below.
- **REST API + web UI** — a FastAPI backend (upload, poll, resume, list, health/readiness) and a Streamlit frontend on top of it; either can be driven independently.
- **Containerized, CI-built, deployable** — one Docker image runs both services; GitHub Actions tests and publishes it to GHCR on every push; deploy guides cover Railway (primary) and a 1GB-RAM EC2 box (documented for AWS range).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph (LangChain 1.x) | Native `interrupt()`/checkpointing for human-in-the-loop is exactly this problem's shape — a graph that can pause mid-run and resume later, not just a linear chain. |
| Extraction model | Claude (`claude-sonnet-5`) | Structured outputs + native PDF understanding in a single call — no separate OCR/vision pipeline for the common case. |
| Data contracts | Pydantic v2 | Every inter-agent handoff is a typed model, not a dict with hopeful key names — the schema *is* the LLM's structured-output contract too. |
| Business rules | Plain Python | Math and date logic should be deterministic and testable, not another place the LLM can hallucinate. |
| Database + storage | Supabase (Postgres + Storage) | Managed Postgres with an object store for the source PDFs, one project, one set of credentials. |
| Ingestion | MCP (`langchain-mcp-adapters`) | Standardized tool-calling interface for "list/download/mark-processed" — swapping Gmail for another source is a new MCP server, not a rewrite. |
| Checkpointing | `SqliteSaver` | State survives an HTTP backend restart — `InMemorySaver` doesn't, and this system explicitly needs runs to outlive a single request. |
| Observability | Langfuse | Full trace per run, including the two raw Anthropic SDK calls that would otherwise be invisible to LangChain's own instrumentation. |
| Backend / frontend | FastAPI + Streamlit | A typed REST API for real integration, plus a UI a non-technical reviewer can actually use, without building a separate SPA. |
| Deploy | Docker → Railway (primary) / EC2 (documented) | One image, two start commands; Railway for the live link, EC2 documented to show the constrained-resource, "you build the box" side of deployment too. |

## Production signals

Things a from-scratch script usually doesn't have, that this does:

- **226 automated tests**, all offline (mocked at the HTTP/API boundary — no live credentials needed to run `pytest`), covering the graph, validation, the API layer, the MCP ingestion sources, and the frontend's own data-transformation logic.
- **A quantified eval harness** (`evals/`) with per-field precision/recall/F1, not just "it worked on the examples I tried."
- **Human-in-the-loop that actually re-validates.** A common shortcut is "human edited it, so trust it" — this system re-runs the full validator (math, dates, duplicate check) on every resume, so a partial or wrong correction is caught, not silently persisted.
- **Structured error handling on the API**, not bare 500s — a typed `ApiError` hierarchy distinguishes "the model is degraded," "the PDF is unprocessable," "persistence failed," etc., each mapped to the right HTTP status and a message safe to show a client, with the real exception still logged server-side.
- **Two health endpoints with different contracts** — `/health` does zero I/O (safe for a container `HEALTHCHECK` to hit every 30s without ever restarting a healthy container over a transient upstream blip); `/health/ready` actually checks config and, optionally, live service connectivity.
- **CI that tests before it ships** — GitHub Actions runs the full suite before building/pushing the Docker image; a failing test blocks the image from ever reaching GHCR.
- **Non-root containers, single-worker on constrained RAM, real deploy docs** — not "works on my machine," a documented path to a 1GB-RAM production box with the actual OOM pitfalls called out.

## Eval results

Full report: [`evals/report.md`](evals/report.md) · Run yourself: `python evals/run_eval.py`

| Metric | Score |
|---|---|
| Field-level exact-match accuracy | **100.0%** |
| Document-level exact match (every field + every line item) | **100.0%** |
| Micro-F1 / Macro-F1 | **1.000 / 1.000** |
| Consistency-check pass rate (subtotal + tax = total) | **100.0%** |
| Dataset | 20 synthetic invoices — 17 digital, 3 scanned; USD/EUR/GBP/JPY |

**Read this number correctly:** the eval set is synthetic (`scripts/generate_eval_set.py`), not hand-labeled real-world invoices — gold labels are exact by construction, which removes transcription error from the ground truth, but real-world layout diversity (handwriting, unusual templates, poor scans) is wider than one renderer can cover. Treat 100% as an upper bound on a controlled distribution, not a claim about arbitrary real invoices — that's exactly why the validator and human-review path exist downstream of extraction, instead of trusting the model output directly.

## Setup

**Prerequisites:** Python 3.12+, [`uv`](https://docs.astral.sh/uv/), an [Anthropic API key](https://console.anthropic.com/), a [Supabase](https://supabase.com/dashboard) project.

```bash
git clone https://github.com/HumdaanSyed/Multi-Agent-Invoice-Processor.git
cd Multi-Agent-Invoice-Processor
uv sync --locked
cp .env.example .env        # fill in ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY
python scripts/smoke_test.py   # verifies both service connections before anything else
```

Run the pipeline against one PDF:

```bash
python scripts/run_graph.py data/eval/eval_01_acme_cloud_hosting_llc.pdf
```

Or run the full stack (backend + frontend) locally:

```bash
uvicorn app.main:app --reload &
streamlit run frontend/app.py
```

Or run it containerized, no local Python setup at all:

```bash
docker compose up
# backend: http://localhost:8000 · frontend: http://localhost:8501
```

Run the tests (no credentials needed — everything's mocked at the connection seam):

```bash
uv run pytest
```

**Deeper docs:** [`docs/api.md`](docs/api.md) (REST API reference) · [`docs/frontend.md`](docs/frontend.md) (Streamlit architecture) · [`docs/mcp_setup.md`](docs/mcp_setup.md) (Gmail/filesystem ingestion) · [`docs/observability.md`](docs/observability.md) (Langfuse tracing) · [`deploy/railway.md`](deploy/railway.md) / [`deploy/ec2.md`](deploy/ec2.md) (deployment)

## Limitations & future work


- **No authentication.** Both the API and the Streamlit UI are open to anyone with the URL. A real deployment needs auth, rate limiting, and per-user data isolation before it touches anyone else's documents.
- **Single-model extraction.** Everything currently runs on `claude-sonnet-5`; routing simple invoices to a cheaper/faster model and hard scanned documents to a stronger one (mentioned as a design goal) isn't wired up yet — there's no signal in production to route on until this runs against a larger, messier real-world sample.
- **Gmail ingestion is local-only.** MCP's `stdio` transport can't run inside the deployed backend — pulling from Gmail today means running `python -m invoice_agent.ingest_mcp --source gmail` from a machine you control (a cron job, not automatic). An HTTP-transport MCP server would close this gap.
- **`exports/invoices.csv` doesn't survive a redeploy** on either cloud target — it's an append-only audit log with no persistent-volume wiring today (documented in both deploy guides, not hidden).
- **No retries/idempotency around the two live API calls** (router, extractor) beyond what the Anthropic SDK does internally — a transient failure mid-run surfaces as a failed run, not an automatic retry.
- **Eval set is synthetic, single-renderer.** See the caveat under [Eval results](#eval-results) — this measures whether the pipeline is internally consistent, not real-world extraction accuracy across arbitrary invoice layouts.
- **No PII handling.** Real invoices contain names, addresses, sometimes bank details — nothing here redacts, encrypts at rest beyond Supabase's defaults, or enforces retention limits.

---
