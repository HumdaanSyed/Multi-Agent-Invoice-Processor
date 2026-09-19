# Railway — public deployment

Phase 10's primary deploy target. Two services — the FastAPI `backend` and
the Next.js `frontend` (Verity) — each running an image
`.github/workflows/docker-build.yml` publishes to GHCR
(`invoice-agent` and `verity-web`). Railway can also build the Dockerfiles
itself if you'd rather not wait on CI, but pulling the pre-built images is
faster and matches what `deploy/ec2.md` does too, so a change to the app
behaves identically on both targets. (If you do build directly on Railway:
the root `Dockerfile` deliberately has no BuildKit cache mounts — Railway's
builder requires their id hardcoded per-service, which doesn't work for a
Dockerfile shared across services, so builds there are correct but slower
than they'd be with a cache. See `REVIEW.md` for the full story.)

## Why Railway over building this by hand

Railway gives every service HTTPS on a generated domain with zero
certificate setup, and its private networking carries the Next.js server's
own requests to the backend without touching the public internet. (The
browser also calls the backend directly — live SSE stream, CSV download —
so the backend does need a public domain of its own. **That is a real
change from the Streamlit setup, where the backend had no public address
at all** — see "Security: the backend is public" below.) The tradeoff, and
the reason
`deploy/ec2.md` exists too: Railway requires a payment method on file even
for trial usage, and you're trusting their platform with the deploy
instead of a box you fully control.

## Setup

1. **Account** — [railway.app](https://railway.app), sign in with GitHub.
   Railway asks for a payment method before deploying anything — this is
   Railway's own requirement, not optional even on a trial.
2. **New Project → Deploy an existing image** → enter
   `ghcr.io/<owner>/invoice-agent:latest` (lowercased owner, e.g.
   `humdaansyed`). This needs `.github/workflows/docker-build.yml` to have
   already published the image at least once, and needs the GHCR package
   to be pullable — do `deploy/ec2.md`'s step 6 (make the package public,
   or hand Railway registry credentials) first if you haven't, for both
   packages. Rename the service `backend` (Settings → General). Deploying
   from the image directly — not "Deploy from GitHub repo" — is what
   actually gets you the pre-built image instead of Railway rebuilding the
   `Dockerfile` itself on every deploy.
3. **Add a second service** to the same project (`+ New` → `Docker Image`
   → `ghcr.io/<owner>/verity-web:latest`). Rename it `frontend`.
4. **Generate both public domains first** (each service → Settings →
   Networking → Generate Domain), because each one's settings below refer
   to the other's URL. Target port `8000` for `backend`, `3000` for
   `frontend` — set explicitly. The backend's `CMD` hardcodes 8000 and
   ignores Railway's injected `$PORT`, but the frontend (Next.js
   `server.js`) *does* read `PORT`, which the image defaults to 3000; so
   also add the variable `PORT=3000` to the `frontend` service, or Railway
   may inject a different value and the target port above stops matching
   (a 502). Note `backend`'s internal address too:
   `backend.railway.internal`, reachable only from services in this
   project.
5. **Configure `backend`** (Settings tab):
   - **Deploy → Custom Start Command**: leave blank — the Dockerfile's
     default `CMD` (uvicorn on port 8000) is exactly what you want.
   - **Variables**: add `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`
     (required), and optionally `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
     `LANGFUSE_HOST` (tracing is genuinely optional — the app runs
     untraced if these are unset; add `LANGFUSE_PROJECT_ID` too if you want
     the detail view's "View trace" link). Also set, each on one line:
     - `CORS_ALLOW_ORIGINS=https://<frontend-domain>` — the browser calls
       the backend directly, so it must allow the frontend's origin
       (scheme + host, no trailing slash).
     - `CHECKPOINT_DB_PATH=/data/checkpoints/graph.sqlite`
     - `UPLOAD_DIR=/data/uploads`
6. **Add a Volume to `backend`** (Settings → Volumes → New Volume), mount
   path `/data`. Railway volumes are one-per-service, so both
   `checkpoints/` and `uploads/` share it via the env vars set in step 5 —
   without this, both are wiped on every redeploy (Railway's filesystem is
   otherwise ephemeral). The image pre-creates and chowns `/data` to the
   non-root `app` user (`Dockerfile`) specifically so a fresh volume
   mounted here inherits writable ownership instead of coming up
   root-owned. If the backend still crash-loops with a `PermissionError`
   on `/data/checkpoints` right after adding the volume, that inheritance
   didn't happen (platform-dependent) — the one-time fix is
   `railway run --service backend -- chown -R app:app /data`.
7. **Configure `frontend`** (Settings tab → Variables). Read at runtime,
   so the same pre-built image works here, on EC2, and locally — nothing
   deployment-specific is baked in:
   - `API_PUBLIC_URL=https://<backend-domain>` — what the *browser* calls.
   - `API_INTERNAL_URL=http://backend.railway.internal:8000` — what the
     Next.js *server* calls (private networking, faster than routing out
     through the public domain and back).
   No other variables: the frontend never needs `ANTHROPIC_API_KEY` or
   `SUPABASE_*`, so don't give it any.
8. **Verify**: open `frontend`'s generated domain. The "Recent runs" list
   should load (that's the frontend's server reaching the backend over
   private networking) and the footer should show the ledger row count
   (that's your browser reaching the backend directly, through CORS).
   Upload any invoice PDF (the repo's `data/eval/` has 20 synthetic ones —
   they are not shipped in the images, and Verity has no built-in sample
   picker, so use a checkout's copy) — a live extraction, then the
   validation checks, confirms the whole path, SSE stream included.
   If "Recent runs" is empty and the page says the backend is unreachable
   while the footer works, the frontend's server can't reach
   `backend.railway.internal`: Railway's private network can be IPv6, and
   the backend's default `uvicorn --host 0.0.0.0` listens on IPv4 only.
   Set the backend's Custom Start Command to
   `uvicorn app.main:app --host :: --port 8000` (unverified against
   Railway's current defaults - only needed if you hit this).

## Security: the backend is public

With the Streamlit UI the backend was reachable only over Railway's
private network. Now its domain is public, and **CORS does not protect
it**: CORS is enforced by browsers, so `curl`, scripts, and bots ignore it
entirely. There is still no authentication and no per-client rate limit
(README, "Limitations"). Anyone who finds the backend's domain can:

- `POST /invoices` PDFs in a loop — every one is a paid Claude call
  against *your* `ANTHROPIC_API_KEY`, throttled only by the 20MB upload cap
  and a 2-run concurrency limit (which one caller can also hold to starve
  real visitors);
- read every run via `GET /invoices` and download the whole ledger from
  `GET /export.csv`;
- browse `/docs` and `/openapi.json`.

Before sharing the link widely: set a monthly spend limit in the Anthropic
console and turn **auto-reload off**, keep real (non-synthetic) invoices out
of the deployment, and consider putting the frontend and backend behind
Cloudflare Access or basic auth. Adding real authentication and rate
limiting is future work, not something this guide does for you.

## Migrating an existing Streamlit deployment

If you already deployed the old Streamlit frontend, redeploying it from the
new images alone will not work. For the existing `frontend` service:
clear **Custom Start Command** (`sh frontend/start.sh` no longer exists),
change the image to `ghcr.io/<owner>/verity-web:latest`, change the target
port from `8501` to `3000`, delete `BACKEND_URL`, and add
`API_PUBLIC_URL`, `API_INTERNAL_URL` and `PORT=3000` (step 7). For the
existing `backend` service: **generate a public domain** (it had none) and
add `CORS_ALLOW_ORIGINS` (step 5). Until then the old link keeps serving
the old version, or nothing.

## Known limitations

- **No automatic redeploy on a new image push.** Deploying from
  `ghcr.io/<owner>/invoice-agent:latest` and `.../verity-web:latest`
  (steps 2–3) pins to whatever `:latest` resolved to at deploy time — Railway doesn't poll external
  registries for new tags, so a later push to `main` doesn't reach either
  service until you manually hit Redeploy (or wire up a Railway API call
  in the CI workflow, not set up here). Same limitation `deploy/ec2.md`
  documents for the EC2 box.
- **One Railway Volume per service** — if you ever split `checkpoints/`
  and `uploads/` onto genuinely separate physical concerns, that needs
  two services or an external object store, not two volumes on one.
- **No staging environment** — Railway supports them; not set up here to
  keep this a single, easy-to-follow path for a portfolio deploy.
