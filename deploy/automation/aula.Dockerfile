FROM oven/bun:1.4-debian@sha256:4f6e31d1a54d6a3dd312daef655fc998101b5043d52e12592ac293ef04b9bc73 AS bun
FROM node:26-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Upstream default-branch HEAD, frozen dependencies, and digest-pinned runtimes.
RUN git init . && git remote add origin https://github.com/Casperjuel/aula-mcp.git \
    && git fetch --depth 1 origin HEAD \
    && git checkout --detach FETCH_HEAD && rm -rf .git
COPY aula-n8n.patch /tmp/aula-n8n.patch
RUN git apply --check /tmp/aula-n8n.patch \
    && git apply /tmp/aula-n8n.patch
RUN corepack enable && corepack prepare pnpm@11.1.3 --activate \
    && pnpm install --frozen-lockfile

FROM node:26-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY --from=build /app /app
USER 1000:1000
CMD ["bun", "packages/mcp-server/src/server.ts"]
