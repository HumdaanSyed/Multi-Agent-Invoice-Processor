# Deploy — index

Phase 10 adds a public deployment path on top of Phase 9's containerized
stack (`Dockerfile`, `web/Dockerfile`, `docker-compose.yml`). Two guides:

- **[railway.md](railway.md)** — the primary path. One click of setup
  effort, HTTPS included, a free Volume for persistence. Requires a card
  on file (Railway's own requirement, not this project's).
- **[ec2.md](ec2.md)** — documented to show AWS range, matches the
  roadmap's explicit 1GB-RAM-box instructions (swap, single worker, nginx,
  systemd). More setup, no built-in HTTPS (out of scope this phase).

Both pull the images `.github/workflows/docker-build.yml` builds and
publishes to `ghcr.io/<owner>/invoice-agent` (backend) and
`ghcr.io/<owner>/verity-web` (frontend) on every push to `main` — neither
guide builds an image on the target machine.

## Two images, one runtime-configured frontend

The backend and the Next.js frontend (Verity) are separate images, each
with its own Dockerfile — the frontend is a multi-stage build on Next.js's
`output: "standalone"`, so the runtime image carries only `server.js` and
the `node_modules` it traces, not the build toolchain.

The frontend image has **no deployment-specific value baked in**. A
`NEXT_PUBLIC_*` variable would be inlined into the JavaScript at
`next build` time, freezing one backend URL into the published image — and
building on the target instead isn't an option on a 1GB box. So
`app/layout.tsx` reads `API_PUBLIC_URL` at request time and hands it to the
browser, and the server reads `API_INTERNAL_URL` for its own fetches (they
differ under Docker Compose and Railway's private networking). One image,
promoted unchanged through local, Railway, and EC2. See `web/lib/config.ts`.

## What neither guide does for you

Account creation, adding a payment method, and clicking "Deploy" are
yours to do — an agent can write correct instructions but shouldn't hold
your credit card. Both guides are written so you can follow them start to
finish without guessing at a missing step.
