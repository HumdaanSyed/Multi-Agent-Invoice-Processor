# Verity (frontend)

The Next.js frontend for the Multi-Agent Invoice Processor, replacing the
Streamlit demo UI (`../frontend/`) per `../docs/FRONTEND_PLAN.md`. Lives at
`web/`, not `frontend/`, until Phase 9E deletes the Streamlit app and this
directory takes its name.

Stack: App Router, TypeScript, Tailwind CSS v4, shadcn/ui. See
`../docs/FRONTEND_PLAN.md` for the design system and the phase-by-phase plan.

## Getting started

Requires the backend running first (`uvicorn app.main:app --reload` from the
repo root - see `../docs/api.md`).

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Point at a different
backend by copying `.env.example` to `.env.local` and setting
`NEXT_PUBLIC_API_BASE_URL`.

## Layout

- `app/` — routes (App Router)
- `components/ui/` — shadcn/ui components, restyled to the warm-paper tokens
- `lib/config.ts` — the product name, in exactly one place
- `lib/types.ts` — data contracts mirroring `../app/models.py` and `../invoice_agent/schema.py`
- `lib/api.ts` — typed client for every backend endpoint, plus the SSE stream
