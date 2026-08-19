# Railway — public deployment

Phase 10's primary deploy target. Two services from one GitHub repo, both
running the image `.github/workflows/docker-build.yml` publishes to GHCR —
Railway can also build the `Dockerfile` itself if you'd rather not wait on
CI, but pulling the pre-built image is faster and matches what
`deploy/ec2.md` does too, so a change to the app behaves identically on
both targets.

## Why Railway over building this by hand

Railway auto-detects the `Dockerfile`, gives every service HTTPS on a
generated domain with zero certificate setup, and its private networking
means the backend never needs a public IP at all — a smaller attack
surface than the EC2 path for the same app. The tradeoff, and the reason
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
   or hand Railway registry credentials) first if you haven't. Rename the
   service `backend` (Settings → General). Deploying from the image
   directly — not "Deploy from GitHub repo" — is what actually gets you
   the pre-built image instead of Railway rebuilding the `Dockerfile`
   itself on every deploy.
3. **Add a second service** to the same project (`+ New` → `Docker Image`
   → the same `ghcr.io/<owner>/invoice-agent:latest`). Rename it
   `frontend`.
4. **Configure `backend`** (Settings tab):
   - **Deploy → Custom Start Command**: leave blank — the Dockerfile's
     default `CMD` (uvicorn on port 8000) is exactly what you want.
   - **Networking**: do **not** generate a public domain. Note the
     internal address Railway shows once the service deploys —
     `backend.railway.internal`, reachable only from other services in
     this same project.
   - **Variables**: add `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`
     (required), and optionally `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
     `LANGFUSE_HOST` (tracing is genuinely optional — the app runs
     untraced if these are unset). Also set these two, each on one line —
     see step 5 for why:
     - `CHECKPOINT_DB_PATH=/data/checkpoints/graph.sqlite`
     - `UPLOAD_DIR=/data/uploads`
5. **Add a Volume to `backend`** (Settings → Volumes → New Volume), mount
   path `/data`. Railway volumes are one-per-service, so both
   `checkpoints/` and `uploads/` share it via the env vars set in step 4 —
   without this, both are wiped on every redeploy (Railway's filesystem is
   otherwise ephemeral). The image pre-creates and chowns `/data` to the
   non-root `app` user (`Dockerfile`) specifically so a fresh volume
   mounted here inherits writable ownership instead of coming up
   root-owned. If the backend still crash-loops with a `PermissionError`
   on `/data/checkpoints` right after adding the volume, that inheritance
   didn't happen (platform-dependent) — the one-time fix is
   `railway run --service backend -- chown -R app:app /data`.
6. **Configure `frontend`** (Settings tab):
   - **Deploy → Custom Start Command**: `sh frontend/start.sh` — the same
     script `docker-compose.yml` and `deploy/ec2.md` run, so the actual
     Streamlit flags live in one place ([frontend/start.sh](../frontend/start.sh))
     instead of being retyped here too.
   - **Networking → Generate Domain**, target port `8501`. This is the
     public URL for the demo.
   - **Variables**: `BACKEND_URL=http://backend.railway.internal:8000`
     (the internal address from step 4 — private networking never touches
     the public internet, so this is also faster than routing through
     `backend`'s own public domain would be, even if you'd left one on).
7. **Networking → target port, both services**: since the image doesn't
   read Railway's injected `$PORT` (it's the same image used unmodified
   on EC2 and locally), explicitly set the target port to `8000`
   (`backend`) / `8501` (`frontend`) in each service's Networking settings
   rather than relying on Railway's auto-detection.
8. **Verify**: open `frontend`'s generated domain. The sidebar should show
   "Backend ready." Process a sample invoice from the dropdown — the
   response comes back over Railway's private network, so this also
   confirms step 4–7 are wired correctly, not just that the containers
   started.

## Known limitations

- **No automatic redeploy on a new image push.** Deploying from
  `ghcr.io/<owner>/invoice-agent:latest` (steps 2–3) pins to whatever
  `:latest` resolved to at deploy time — Railway doesn't poll external
  registries for new tags, so a later push to `main` doesn't reach either
  service until you manually hit Redeploy (or wire up a Railway API call
  in the CI workflow, not set up here). Same limitation `deploy/ec2.md`
  documents for the EC2 box.
- **`exports/invoices.csv` has no env-var override** (unlike checkpoints/
  uploads — see `invoice_agent/graph.py`'s `EXPORT_CSV_PATH`), so it's
  **not** on the volume and is lost on every redeploy. Fine for a demo;
  a real deployment would want this fixed at the code level, not worked
  around here.
- **One Railway Volume per service** — if you ever split `checkpoints/`
  and `uploads/` onto genuinely separate physical concerns, that needs
  two services or an external object store, not two volumes on one.
- **No staging environment** — Railway supports them; not set up here to
  keep this a single, easy-to-follow path for a portfolio deploy.
