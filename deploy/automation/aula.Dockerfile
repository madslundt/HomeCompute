FROM oven/bun:1.3-debian@sha256:9dba1a1b43ce28c9d7931bfc4eb00feb63b0114720a0277a8f939ae4dfc9db6f AS bun
FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Full commit, frozen dependencies, and digest-pinned runtimes.
RUN git init . && git remote add origin https://github.com/Casperjuel/aula-mcp.git \
    && git fetch --depth 1 origin af49805ae9c6d7c9026f6e559f2e01ca209c9e46 \
    && git checkout --detach FETCH_HEAD && rm -rf .git
COPY aula-n8n.patch /tmp/aula-n8n.patch
RUN git apply --check /tmp/aula-n8n.patch \
    && git apply /tmp/aula-n8n.patch
RUN corepack enable && corepack prepare pnpm@11.1.3 --activate \
    && pnpm install --frozen-lockfile

FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY --from=build /app /app
USER 1000:1000
CMD ["bun", "packages/mcp-server/src/server.ts"]
