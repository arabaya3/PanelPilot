# PanelPilot web image.
#
# IMPORTANT: the build context is the REPO ROOT, not apps/web.
# apps/web depends on "@panelpilot/shared-types": "*", an npm workspace
# package, and the only lockfile lives at the root. Building from apps/web
# alone cannot resolve either. Because the context is the root, the ignore
# rules for this build come from the ROOT .dockerignore — a .dockerignore
# inside apps/web would never be read.
#
# Targets:
#   runner  production standalone server, no node_modules  (default)
#   dev     next dev with hot reload, for docker compose up

# ---------------------------------------------------------------------------
# deps — install the workspace once, cached on manifests alone.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps

# Next's SWC binary is glibc-linked; this shim lets it run on musl.
RUN apk add --no-cache libc6-compat
WORKDIR /repo

# Only the manifests, so editing source does not reinstall the world.
# Every workspace referenced by the lockfile must be present or npm ci fails.
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/
COPY packages/shared-types/package.json packages/shared-types/

# npm ci, not npm install: it installs exactly the lockfile and errors if
# package.json has drifted from it.
RUN npm ci

# ---------------------------------------------------------------------------
# builder — produce .next/standalone.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

RUN apk add --no-cache libc6-compat
WORKDIR /repo

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY . .

# next.config.mjs sets eslint.ignoreDuringBuilds and typescript.ignoreBuildErrors
# to false, so this step also lints and type-checks. A broken build fails here
# rather than shipping.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace @panelpilot/web

# ---------------------------------------------------------------------------
# dev — next dev with the source bind-mounted by compose.
# Never use this target for a deployed image.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS dev

RUN apk add --no-cache libc6-compat
WORKDIR /repo

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/web/node_modules ./apps/web/node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
USER node
EXPOSE 3000

# -H 0.0.0.0 so the dev server is reachable from outside the container;
# Next binds localhost by default, which would be unreachable from the host.
CMD ["npm", "run", "dev", "--workspace", "@panelpilot/web", "--", "-H", "0.0.0.0", "-p", "3000"]

# ---------------------------------------------------------------------------
# runner — standalone output only. Last stage, so it is the default target.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    # The standalone server binds this; without 0.0.0.0 it listens on
    # localhost inside the container and nothing can reach it.
    HOSTNAME=0.0.0.0

# The standalone bundle carries its own minimal node_modules, so this image
# gets no npm install, no build tooling, and no source tree.
#
# outputFileTracingRoot is the repo root, so the bundle keeps the workspace
# layout and the entrypoint is apps/web/server.js — NOT ./server.js.
COPY --from=builder --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=node:node /repo/apps/web/public ./apps/web/public

# node:alpine ships an unprivileged `node` user (uid 1000); no need to make one.
USER node
EXPOSE 3000

CMD ["node", "apps/web/server.js"]
