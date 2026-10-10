FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Build the reviewed upstream main commit with its committed npm lockfile.
RUN git init . && git remote add origin https://github.com/olgasafonova/tilbudstrolden-mcp.git \
    && git fetch --depth 1 origin b20a3a331f82f4415ff8cac42de7706b829f116a \
    && git checkout --detach FETCH_HEAD && rm -rf .git
COPY tilbudstrolden-security.patch /tmp/tilbudstrolden-security.patch
RUN git apply --check /tmp/tilbudstrolden-security.patch \
    && git apply /tmp/tilbudstrolden-security.patch
RUN npm ci && npm run build && npm prune --omit=dev

FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS gateway
WORKDIR /gateway
COPY tilbudstrolden-gateway/package.json tilbudstrolden-gateway/package-lock.json ./
RUN npm ci --omit=dev

FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370
WORKDIR /app
COPY --from=build --chown=1000:1000 /app/dist ./dist
COPY --from=build --chown=1000:1000 /app/node_modules ./node_modules
COPY --from=gateway --chown=1000:1000 /gateway/node_modules /gateway/node_modules
USER 1000:1000
EXPOSE 8000
CMD ["node", "/gateway/node_modules/supergateway/dist/index.js", "--stdio", "node /app/dist/server.js", "--outputTransport", "streamableHttp", "--port", "8000", "--host", "0.0.0.0", "--stateful", "--sessionTimeout", "60000", "--healthEndpoint", "/healthz", "--logLevel", "none"]
