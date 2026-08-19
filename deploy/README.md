# Deploy — index

Phase 10 adds a public deployment path on top of Phase 9's containerized
stack (`Dockerfile`, `docker-compose.yml` at the repo root). Two guides:

- **[railway.md](railway.md)** — the primary path. One click of setup
  effort, HTTPS included, a free Volume for persistence. Requires a card
  on file (Railway's own requirement, not this project's).
- **[ec2.md](ec2.md)** — documented to show AWS range, matches the
  roadmap's explicit 1GB-RAM-box instructions (swap, single worker, nginx,
  systemd). More setup, no built-in HTTPS (out of scope this phase).

Both pull the image `.github/workflows/docker-build.yml` builds and
publishes to `ghcr.io/<owner>/invoice-agent` on every push to `main` —
neither guide builds the image on the target machine.

## One shared image, not two

`Dockerfile` produces a single image serving both the FastAPI backend and
the Streamlit frontend; `docker-compose.yml` runs it twice with different
`command:`s rather than building separate backend/frontend images. This is
a deliberate simplification: streamlit's own dependency tree (pandas,
pyarrow, numpy, pillow — about 120MB combined) ends up sitting unused in
the backend image too, but the backend *process* never imports streamlit
or any of those packages, so it costs image size and build time, not
runtime RAM on the resource-constrained box. Splitting them would mean
moving `pyproject.toml`'s flat dependency list into per-service
optional-dependency groups and running two separate `uv sync`/Docker
build passes — a real refactor that also touches local dev, not something
this phase's scope (deploy the thing that already works) calls for.

## What neither guide does for you

Account creation, adding a payment method, and clicking "Deploy" are
yours to do — an agent can write correct instructions but shouldn't hold
your credit card. Both guides are written so you can follow them start to
finish without guessing at a missing step.
