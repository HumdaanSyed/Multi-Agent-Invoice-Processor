# Deploy — index

- **[ec2.md](ec2.md)** — the maintained path. One EC2 instance: host nginx
  (TLS, HSTS, rate limits) in front of the frontend, the backend on a
  private Docker network, secrets from AWS Secrets Manager through the
  instance's IAM role, nightly checkpoint backups to S3. Files in
  [`aws/`](aws/).
- **[railway.md](railway.md)** — the earlier Phase 10 path; predates
  authentication (see its banner).

Both pull the images `.github/workflows/docker-build.yml` builds and
publishes to `ghcr.io/<owner>/invoice-agent` (backend) and
`ghcr.io/<owner>/verity-web` (frontend) on every push to `main` — nothing
is built on the target machine (`next build` won't fit on a small box).

## Two images, one runtime-configured frontend

The frontend image has **no deployment-specific value baked in**: Supabase
settings, the allowlist and the backend's internal URL are all read at
request time (`web/lib/config.ts`, `web/lib/supabase/env.ts`), so one image
is promoted unchanged from local to production. The browser only ever
talks to the frontend's own origin; the frontend's `/api` proxy is the one
path to FastAPI. See [`../docs/auth.md`](../docs/auth.md).

## What neither guide does for you

Account creation, payment methods, DNS, and clicking "Launch" are yours to
do — an agent can write correct instructions but shouldn't hold your
credit card.
