# EC2 — documented deployment

Not the primary path (`deploy/railway.md` is) — this exists to show a
from-scratch cloud deploy on a real 1GB-RAM box, and it's where
`CLAUDE.md`'s "1GB RAM" pitfalls actually apply literally. Follows the
roadmap's instructions closely: t3.micro, pull the CI-built image, never
build on the box.

## Setup

1. **Launch the instance** — EC2 console → Launch Instance → Ubuntu Server
   24.04 LTS (free-tier eligible AMI) → instance type `t3.micro`. Create or
   reuse a key pair for SSH.
2. **Security group** — allow inbound TCP `22` (SSH, restrict to your IP if
   possible), `80` (HTTP), `443` (reserved for a future TLS pass — nothing
   listens there yet, matching the roadmap's scope). No other ports: the
   backend is only reached via Docker's internal network, never exposed
   to the host's public interface.
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
   Docker itself plus two Python processes under any memory pressure
   (a large PDF, a slow GC pause). Without swap, the OOM killer takes the
   container down instead of it just slowing down:
   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
6. **Make the GHCR package pullable.** `.github/workflows/docker-build.yml`
   pushes using the repo's built-in `GITHUB_TOKEN`, and GHCR packages
   built this way can default to **private** visibility even though the
   source repo is public — check
   `github.com/<owner>?tab=packages` → `invoice-agent` → Package settings,
   and if it shows private, either flip it to public (simplest, matches
   this being a portfolio project with no real secrets in the image), or
   keep it private and `docker login ghcr.io` on the box with a
   classic PAT scoped to `read:packages`.
7. **Write the production compose file** on the box — deliberately not a
   tracked repo file, since it references an image tag instead of
   building locally (see "Why not commit a second compose file" below):
   ```bash
   mkdir -p ~/invoice-agent && cd ~/invoice-agent
   cat > docker-compose.yml <<'EOF'
   services:
     backend:
       image: ghcr.io/<owner>/invoice-agent:latest
       env_file: [.env]
       volumes:
         - checkpoint_data:/app/checkpoints
         - upload_data:/app/uploads
         - export_data:/app/exports
       restart: unless-stopped

     frontend:
       image: ghcr.io/<owner>/invoice-agent:latest
       command:
         - streamlit
         - run
         - frontend/app.py
         - --server.address=0.0.0.0
         - --server.port=8501
         - --server.headless=true
         - --server.fileWatcherType=none
         - --browser.gatherUsageStats=false
       env_file: [.env]
       environment:
         BACKEND_URL: http://backend:8000
       ports:
         - "127.0.0.1:8501:8501"
       depends_on:
         backend:
           condition: service_healthy
       restart: unless-stopped

   volumes:
     checkpoint_data:
     upload_data:
     export_data:
   EOF
   ```
   Replace `<owner>` with the lowercased GitHub owner (`humdaansyed`).
   Note `frontend`'s port is bound to `127.0.0.1` only — nginx (step 9)
   is the one thing allowed to reach it from outside the box.
8. **Add `.env`** in the same directory (`ANTHROPIC_API_KEY`,
   `SUPABASE_URL`, `SUPABASE_KEY`, optional Langfuse vars) — copy it up
   with `scp`, never type secrets directly into an SSH session's shell
   history. Then:
   ```bash
   docker compose pull
   docker compose up -d
   ```
9. **nginx reverse proxy**, port 80 → the frontend container's 8501:
   ```bash
   sudo apt-get update && sudo apt-get install -y nginx
   sudo tee /etc/nginx/sites-available/invoice-agent <<'EOF'
   server {
       listen 80;
       location / {
           proxy_pass http://127.0.0.1:8501;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
       }
   }
   EOF
   sudo ln -sf /etc/nginx/sites-available/invoice-agent /etc/nginx/sites-enabled/
   sudo rm -f /etc/nginx/sites-enabled/default
   sudo nginx -t && sudo systemctl reload nginx
   ```
   The `Upgrade`/`Connection` headers matter — Streamlit's UI runs over a
   WebSocket, and a reverse proxy that doesn't forward the upgrade
   handshake leaves the page loading but permanently frozen.
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
11. **Verify** — visit `http://<elastic-ip>/` in a browser. Sidebar shows
    "Backend ready"; process a sample invoice from the dropdown and
    confirm it completes or reaches "needs review." This is the roadmap's
    literal "Done when": reachable at a public URL, processes an invoice
    live.

## Why not commit a second compose file

The repo-root `docker-compose.yml` (Phase 10, local dev) uses `build: .`
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
- **No automatic image updates.** A new push to `main` publishes a new
  `:latest` in GHCR, but the box doesn't pull it — re-run
  `docker compose pull && docker compose up -d` manually, or add a cron
  job / Watchtower if you want that automated later.
- **Single point of failure.** No load balancer, no second instance —
  matches "documented to show AWS range," not a production HA setup.
