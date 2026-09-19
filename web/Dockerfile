# syntax=docker/dockerfile:1
#
# Verity (Next.js) frontend image - separate from the backend's Dockerfile at
# the repo root. Multi-stage with Next.js's `output: "standalone"`
# (next.config.ts): the runtime stage carries only server.js plus the traced
# node_modules it needs, not the full dependency tree or the build toolchain.
#
# Build once, deploy anywhere: nothing deployment-specific is baked in. The
# backend URLs are read at *request* time from API_PUBLIC_URL (what the
# browser calls) and API_INTERNAL_URL (what this server calls) - see
# lib/config.ts. That matters for deploy/ec2.md: `next build` needs more RAM
# than a 1GB box has, so the image is built in CI and pulled, never built on
# the target.

# --- deps --------------------------------------------------------------
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- builder -----------------------------------------------------------
FROM node:22-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# next/font/google downloads and self-hosts the three fonts here, so the
# build needs outbound internet; the running container does not.
RUN npm run build

# --- runtime -----------------------------------------------------------
FROM node:22-slim AS runtime
WORKDIR /app

# curl for the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The `node` user ships with the official image - non-root, like the backend.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/healthz || exit 1

CMD ["node", "server.js"]
