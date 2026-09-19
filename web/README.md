# Verity (frontend)

The Next.js frontend for the Multi-Agent Invoice Processor: upload, a live
extraction/validation view, human review, a read-only detail screen, and a
persistent export bar. It replaced the original Streamlit demo UI
(`docs/FRONTEND_PLAN.md`). The directory is named `web/` rather than
`frontend/` — the plan's original name — because nothing needed the rename
enough to justify rewriting every path reference in the repo.

Stack: App Router, TypeScript, Tailwind CSS v4, shadcn/ui. See
`../docs/FRONTEND_PLAN.md` for the design system and the phase-by-phase plan.

## Getting started

Requires the backend running first (`uvicorn app.main:app --reload` from the
repo root - see `../docs/api.md`).

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). With no configuration
it talks to `http://127.0.0.1:8000`; point it elsewhere by copying
`.env.example` to `.env.local` (see below).

## Configuration

Backend URLs are read at **request time**, not build time, so one prebuilt
image serves any deployment (`lib/config.ts`, `app/layout.tsx`):

- `API_PUBLIC_URL` — what the *browser* calls (client fetches, the SSE
  stream, the CSV link). The backend's `CORS_ALLOW_ORIGINS` must include
  this app's origin unless both are served from one origin.
- `API_INTERNAL_URL` — what the Next.js *server* calls (the home page's
  recent-runs list). Only needed when it differs from `API_PUBLIC_URL`,
  e.g. `http://backend:8000` inside Docker Compose.

## Docker

`Dockerfile` is a multi-stage build on `output: "standalone"`; from the repo
root, `docker compose up` builds and runs it next to the backend on
[http://localhost:3000](http://localhost:3000). CI publishes it to GHCR as
`verity-web` (`.github/workflows/docker-build.yml`). See `../deploy/README.md`.

## Layout

- `app/` — routes (App Router): `/` (upload + recent runs), `/runs/[threadId]`
  (live view, review form, detail — one screen driven by run status)
- `components/ui/` — shadcn/ui components, restyled to the warm-paper tokens
- `hooks/use-run.ts` — SSE subscription, polling fallback, resume
- `lib/config.ts` — the product name and backend URLs, in one place
- `lib/types.ts` — data contracts mirroring `../app/models.py` and `../invoice_agent/schema.py`
- `lib/api.ts` — typed client for every backend endpoint, plus the SSE stream
