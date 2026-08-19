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
2. **New Project → Deploy from GitHub repo** → select this repository.
   Railway creates one service from the Dockerfile it finds at the repo
   root. Rename it `backend` (Settings → General).
3. **Add a second service** to the same project (`+ New` → `GitHub Repo`
   → the same repository again) — this creates a second, independent
   service also built from the same `Dockerfile`. Rename it `frontend`.
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
     untraced if these are unset). Also set `CHECKPOINT_DB_PATH=/data/
     checkpoints/graph.sqlite` and `UPLOAD_DIR=/data/uploads` — see step 5.
5. **Add a Volume to `backend`** (Settings → Volumes → New Volume), mount
   path `/data`. Railway volumes are one-per-service, so both
   `checkpoints/` and `uploads/` share it via the env vars set in step 4 —
   without this, both are wiped on every redeploy (Railway's filesystem is
   otherwise ephemeral).
6. **Configure `frontend`** (Settings tab):
   - **Deploy → Custom Start Command**:
     ```
     streamlit run frontend/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --server.fileWatcherType=none --browser.gatherUsageStats=false
     ```
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
