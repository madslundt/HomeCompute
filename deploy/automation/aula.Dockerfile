FROM oven/bun:1.4-debian@sha256:4f6e31d1a54d6a3dd312daef655fc998101b5043d52e12592ac293ef04b9bc73 AS bun
FROM node:26-bookworm-slim@sha256:662933cf47f013bc8e4beb31a6116448427a82057ba7c42c97e4c5ba766504c2 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Reviewed upstream main commit, frozen dependencies, and digest-pinned runtimes.
RUN git init . && git remote add origin https://github.com/Casperjuel/aula-mcp.git \
    && git fetch --depth 1 origin f1360000eaccf7cae528bed1cab6b0d0b2a32cfc \
    && git checkout --detach FETCH_HEAD && rm -rf .git
COPY aula-n8n.patch /tmp/aula-n8n.patch
RUN git apply --check /tmp/aula-n8n.patch \
    && git apply /tmp/aula-n8n.patch
RUN corepack enable && corepack prepare pnpm@11.1.3 --activate \
    && pnpm install --frozen-lockfile

FROM node:26-bookworm-slim@sha256:662933cf47f013bc8e4beb31a6116448427a82057ba7c42c97e4c5ba766504c2
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY --from=build /app /app
USER 1000:1000
CMD ["bun", "packages/mcp-server/src/server.ts"]
