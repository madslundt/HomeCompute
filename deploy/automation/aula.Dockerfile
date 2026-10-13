FROM oven/bun:1.4-debian@sha256:923bedb355f7746666659f71e724998159a5c8c54bd1cb1030e1975a0698415b AS bun
FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Reviewed upstream main commit, frozen dependencies, and digest-pinned runtimes.
RUN git init . && git remote add origin https://github.com/Casperjuel/aula-mcp.git \
    && git fetch --depth 1 origin 8f9787221f933fbbc395f384228317639602caad \
    && git checkout --detach FETCH_HEAD && rm -rf .git
COPY aula-n8n.patch /tmp/aula-n8n.patch
RUN git apply --unidiff-zero --check /tmp/aula-n8n.patch \
    && git apply --unidiff-zero /tmp/aula-n8n.patch
RUN corepack enable && corepack prepare pnpm@11.1.3 --activate \
    && pnpm install --frozen-lockfile

FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY --from=build /app /app
USER 1000:1000
CMD ["bun", "packages/mcp-server/src/server.ts"]
