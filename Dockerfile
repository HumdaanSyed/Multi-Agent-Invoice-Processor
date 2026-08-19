# syntax=docker/dockerfile:1
#
# One shared image for both the FastAPI backend and the Streamlit frontend
# (Phase 10) - docker-compose.yml runs the same image twice with different
# commands rather than building two separate images. That's a deliberate
# simplification, not an oversight: streamlit's own dependency tree
# (pandas/pyarrow/numpy/pillow, ~120MB) ends up in the backend image too,
# but the backend process never imports that code, so it costs disk/build
# time, not runtime RAM - see deploy/README.md for the full tradeoff.

# --- builder -----------------------------------------------------------
FROM python:3.12-slim AS builder

# Official static uv binary - matches this repo's own uv_build constraint
# (pyproject.toml: "uv_build>=0.12.2,<0.13.0"), no pip bootstrap needed.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependency layer first, cached independently of source changes -
# --no-install-project skips installing invoice-agent itself, so this
# layer only invalidates when pyproject.toml/uv.lock change, not on every
# code edit. --locked (not a plain sync) fails the build if the lockfile
# is stale instead of silently re-resolving - the same reproducibility
# guarantee `uv sync --locked` gives locally.
# No `--mount=type=cache` here (tried, then reverted): standard
# docker/buildx accepts a cache mount with no `id=` (infers one from the
# target path); Railway's builder rejected that outright, then rejected
# an explicit `id=uv-cache` too, requiring instead a literal
# `id=s/<railway-service-id>-...` with the real service ID hardcoded in
# the Dockerfile (env vars/build-args aren't evaluated in that position -
# confirmed via Railway's own docs and community reports). This file
# builds identically for local dev, CI, and - per deploy/railway.md - two
# *separate* Railway services from the same Dockerfile; hardcoding one
# service's ID would break the others, so a cache mount isn't worth it
# here. This layer is still cached at the Docker-layer level (unchanged
# between builds when pyproject.toml/uv.lock don't change) - it just
# doesn't get uv's own finer-grained package cache on top of that.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Now the source. uv_build's module-root="" (pyproject.toml) discovers
# every top-level importable package when installing the project itself,
# same as a local `uv sync` - so this copies everything .dockerignore lets
# through, not just app/invoice_agent/frontend. The runtime stage below is
# what actually trims the image down.
COPY . .
RUN uv sync --locked --no-dev

# --- runtime -----------------------------------------------------------
FROM python:3.12-slim AS runtime

# curl for the HEALTHCHECK below; nothing else here needs a compiler or
# dev headers, so no other apt packages.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

# Only the built venv and the specific source directories the running
# services actually import - not tests/, scripts/, evals/, or anything
# else that made it into the builder's build context. data/eval/ is the
# frontend's committed sample-invoice dropdown (20 synthetic PDFs, 300KB);
# harmless in the backend image, and the frontend can't skip it without
# losing that dropdown.
COPY --from=builder --chown=app:app /app/.venv ./.venv
COPY --from=builder --chown=app:app /app/app ./app
COPY --from=builder --chown=app:app /app/invoice_agent ./invoice_agent
COPY --from=builder --chown=app:app /app/frontend ./frontend
COPY --from=builder --chown=app:app /app/data/eval ./data/eval

# Runtime state dirs. App code already creates these lazily
# (mkdir(parents=True, exist_ok=True) in app/main.py, app/uploads.py,
# invoice_agent/db.py) - that's for local/non-root-agnostic dev, but it's
# not enough on its own here: checkpoints/uploads/exports must exist
# *before* docker-compose.yml's named volumes mount over them, or Docker
# auto-creates those mount-point directories itself, owned by root - and
# a non-root process can't create a file under a root-owned directory
# (this exact regression: dropping this mkdir in favor of chown alone
# broke `docker compose up` with `sqlite3.OperationalError: unable to
# open database file`, since chown has nothing to chown until the
# directory exists). So both steps are needed: mkdir creates them (giving
# each volume mount an existing, image-owned directory to copy ownership
# from on first mount), then chown -R covers the whole /app tree - not
# just these three - so any other mkdir(parents=True) call elsewhere in
# the codebase (the MCP ingestion modules under
# invoice_agent/mcp_servers/) also lands somewhere app-writable without
# needing a matching edit here.
# /data is also pre-created: it's not used by anything in this image, but
# it's the conventional mount point cloud platforms (Railway) put a
# persistent volume at - pre-chowning it here means a fresh volume
# mounted there on first boot inherits app-writable ownership instead of
# staying root-owned (see deploy/railway.md).
RUN mkdir -p checkpoints uploads exports /data \
    && chown -R app:app /app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 8000

# Hits GET /health specifically (not /health/ready) - zero I/O, so a
# transient Anthropic/Supabase blip never causes an orchestrator to kill
# and restart a healthy container mid-invoice-run. See app/routes.py.
# This is baked into image metadata, so it applies even to a container
# started with a different CMD - a service running the frontend command
# below must override `healthcheck:` too (docker-compose.yml does; a
# platform without a compose file, like Railway, relies on its own
# separate health-check configuration instead of this one).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Single worker, no --workers flag - CLAUDE.md's 1GB-RAM pitfall.
# docker-compose.yml overrides this CMD entirely for the frontend service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
