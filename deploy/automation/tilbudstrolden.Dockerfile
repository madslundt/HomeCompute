FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Build the reviewed upstream main commit with its committed npm lockfile.
RUN git init . && git remote add origin https://github.com/olgasafonova/tilbudstrolden-mcp.git \
    && git fetch --depth 1 origin 1749d507faadda5c8bd699e190828da172889448 \
    && git checkout --detach FETCH_HEAD && rm -rf .git
RUN npm ci && npm run build && npm prune --omit=dev

FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS gateway
WORKDIR /gateway
COPY tilbudstrolden-gateway/package.json tilbudstrolden-gateway/package-lock.json ./
RUN npm ci --omit=dev

FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e
WORKDIR /app
COPY --from=build --chown=1000:1000 /app/dist ./dist
COPY --from=build --chown=1000:1000 /app/node_modules ./node_modules
COPY --from=gateway --chown=1000:1000 /gateway/node_modules /gateway/node_modules
USER 1000:1000
EXPOSE 8000
CMD ["node", "/gateway/node_modules/supergateway/dist/index.js", "--stdio", "node /app/dist/server.js", "--outputTransport", "streamableHttp", "--port", "8000", "--host", "0.0.0.0", "--stateful", "--sessionTimeout", "60000", "--healthEndpoint", "/healthz", "--logLevel", "none"]
