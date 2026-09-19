# EC2 — documented deployment

Not the primary path (`deploy/railway.md` is) — this exists to show a
from-scratch cloud deploy on a real 1GB-RAM box, and it's where
`CLAUDE.md`'s "1GB RAM" pitfalls actually apply literally. Follows the
roadmap's instructions closely: t3.micro, pull the CI-built images, never
build on the box — for the Next.js frontend that's not just a preference,
`next build` needs far more than 1GB, which is exactly why its image reads
its backend URLs at runtime instead of baking them in (`web/Dockerfile`).

## Setup

1. **Launch the instance** — EC2 console → Launch Instance → Ubuntu Server
   24.04 LTS (free-tier eligible AMI) → instance type `t3.micro`. Create or
   reuse a key pair for SSH.
2. **Security group** — allow inbound TCP `22` (SSH, restrict to your IP if
   possible), `80` (HTTP), `443` (reserved for a future TLS pass — nothing
   listens there yet, matching the roadmap's scope). No other ports: the
   backend and frontend containers bind to `127.0.0.1` only, and nginx
   (step 9) is the one thing that serves both on port 80.
3. **Elastic IP** — allocate one and associate it with the instance.
   Without this the public IP changes on every stop/start, which breaks
   the DNS/bookmark you hand to anyone viewing the demo.
4. **SSH in** and install Docker Engine + the compose plugin (the
   `docker compose` subcommand, not standalone `docker-compose`):
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER
   newgrp docker
   ```
5. **Add 2GB swap** — a t3.micro's 1GB RAM is not enough headroom for
   Docker itself plus a Python and a Node process under any memory pressure
   (a large PDF, a slow GC pause). Without swap, the OOM killer takes the
   container down instead of it just slowing down:
   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
6. **Make the GHCR package pullable — a one-time repo setting, not a
   per-deploy step.** `.github/workflows/docker-build.yml` pushes using
   the repo's built-in `GITHUB_TOKEN`, and GHCR packages default to
   **private** visibility the first time they're created this way, even
   though the source repo is public. Right after the workflow's first
   successful run, check
   `github.com/<owner>?tab=packages` → `invoice-agent` and `verity-web` → Package settings,
   and if it shows private, either flip it to public (simplest, matches
   this being a portfolio project with no real secrets in the image), or
   keep it private and `docker login ghcr.io` on the box with a
   classic PAT scoped to `read:packages`. The setting then persists
   across every future push to the same package — nothing in CI resets
   it — so this doesn't need repeating. The one case where it does: if
   the package is ever deleted (not just its versions — the whole
   package, e.g. via a repo/org transfer) and a later push recreates it
   from scratch, it comes back private and this step needs redoing once.
7. **Write the production compose file** on the box — deliberately not a
   tracked repo file, since it references an image tag instead of
   building locally (see "Why not commit a second compose file" below):
   ```bash
   mkdir -p ~/invoice-agent && cd ~/invoice-agent
   cat > docker-compose.yml <<'EOF'
   services:
     backend:
       image: ghcr.io/<owner>/invoice-agent:latest
       mem_limit: 512m
       env_file: [.env]
       volumes:
         - checkpoint_data:/app/checkpoints
         - upload_data:/app/uploads
       ports:
         - "127.0.0.1:8000:8000"
       restart: unless-stopped

     frontend:
       image: ghcr.io/<owner>/verity-web:latest
       mem_limit: 256m
       environment:
         # V8 sizes its heap from the host's RAM by default, not from this
         # container's limit - cap it so Node can't outgrow mem_limit.
         NODE_OPTIONS: --max-old-space-size=192
         # nginx (step 9) serves the frontend AND the backend's API paths on
         # one origin, so the browser calls the same address it loaded the
         # page from - no CORS to configure. Use the Elastic IP (or your
         # domain) with no trailing slash.
         API_PUBLIC_URL: http://<elastic-ip>
         # The Next.js server reaches the backend over the compose network.
         API_INTERNAL_URL: http://backend:8000
       ports:
         - "127.0.0.1:3000:3000"
       depends_on:
         backend:
           condition: service_healthy
       restart: unless-stopped

   volumes:
     checkpoint_data:
     upload_data:
   EOF
   ```
   Replace `<owner>` with the lowercased GitHub owner (`humdaansyed`) and
   `<elastic-ip>` with the address from step 3. Both ports are bound to
   `127.0.0.1` only — nginx (step 9) is the one thing allowed to reach
   them from outside the box. The frontend needs no `.env` of its own:
   it never sees `ANTHROPIC_API_KEY` or `SUPABASE_*`.
8. **Add `.env`** in the same directory (`ANTHROPIC_API_KEY`,
   `SUPABASE_URL`, `SUPABASE_KEY`, optional Langfuse vars) — copy it up
   with `scp`, never type secrets directly into an SSH session's shell
   history. Then:
   ```bash
   docker compose pull
   docker compose up -d
   ```
9. **nginx reverse proxy**, port 80 → the frontend for pages, → the
   backend for its API paths (both are on one origin, which is what
   `API_PUBLIC_URL` above points at):
   ```bash
   sudo apt-get update && sudo apt-get install -y nginx
   sudo tee /etc/nginx/sites-available/invoice-agent <<'EOF'
   server {
       listen 80;
       client_max_body_size 25m;   # PDF uploads; the backend caps at 20MB

       # The backend's routes (app/routes.py plus FastAPI's built-in docs
       # at /docs, /redoc, /openapi.json). This is a hand-kept copy of that
       # route table: add a backend route and it must be added here too, or
       # it 404s on EC2 only. The (/|$) keeps look-alike frontend paths
       # such as /export-history on the frontend. None collide with the
       # frontend's pages (/, /runs/...), /healthz, or its /_next assets.
       location ~ ^/(invoices|export(\.csv|/status)|health|docs|redoc|openapi\.json)(/|$) {
           proxy_pass http://127.0.0.1:8000;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header Connection "";
           # POST /invoices blocks for the full extraction (15-40s), and the
           # SSE stream stays open for the whole run - both need a read
           # timeout longer than nginx's 60s default, and the stream must
           # not be buffered or events arrive in one lump at the end.
           proxy_read_timeout 300s;
           proxy_buffering off;
       }

       location / {
           proxy_pass http://127.0.0.1:3000;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
       }
   }
   EOF
   sudo ln -sf /etc/nginx/sites-available/invoice-agent /etc/nginx/sites-enabled/
   sudo rm -f /etc/nginx/sites-enabled/default
   sudo nginx -t && sudo systemctl reload nginx
   ```
10. **systemd unit**, so a reboot brings the stack back without a manual
    SSH session:
    ```bash
    sudo tee /etc/systemd/system/invoice-agent.service <<'EOF'
    [Unit]
    Description=Invoice Agent (docker compose)
    Requires=docker.service
    After=docker.service network-online.target
    Wants=network-online.target

    [Service]
    Type=oneshot
    RemainAfterExit=yes
    WorkingDirectory=/home/ubuntu/invoice-agent
    ExecStart=/usr/bin/docker compose up -d
    ExecStop=/usr/bin/docker compose down

    [Install]
    WantedBy=multi-user.target
    EOF
    sudo systemctl enable --now invoice-agent.service
    ```
11. **Verify** — visit `http://<elastic-ip>/` in a browser. The "Recent
    runs" list loads and the footer shows the ledger row count; upload a
    sample invoice from `data/eval/` and watch it extract and validate
    live, then land on "needs review" or the completed detail view (the
    repo's `data/eval/` has 20 synthetic invoices to upload; they are not
    shipped in the images, and Verity has no built-in sample picker). This is
    the roadmap's literal "Done when": reachable at a public URL,
    processes an invoice live.

## Why not commit a second compose file

The repo-root `docker-compose.yml` (Phase 10, local dev) uses `build:`
so `docker compose up` works from a fresh clone with no image published
yet. The EC2 box's compose file uses `image: ghcr.io/...` instead —
CLAUDE.md's own pitfall ("don't build Docker images on a t3.micro, it
OOMs — exit code 137"). These two are permanently different files serving
different purposes, so keeping the production one inline in this doc
(rather than a second tracked file that looks like it should stay in
sync with the first but structurally can't) is the honest representation.

## Known limitations

- **No automatic TLS.** Port 443 is open in the security group for a
  future `certbot --nginx` pass; nothing terminates HTTPS yet, so this
  URL is HTTP-only, unlike the Railway path.
- **No automatic image updates.** A new push to `main` publishes new
  `:latest` images in GHCR, but the box doesn't pull it — re-run
  `docker compose pull && docker compose up -d` manually, or add a cron
  job / Watchtower if you want that automated later.
- **Single point of failure.** No load balancer, no second instance —
  matches "documented to show AWS range," not a production HA setup.
