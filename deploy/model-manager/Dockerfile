FROM node:22.15.0-bookworm-slim@sha256:01cbafb3480d272d73613e5e74206ec79326f4a9ebb4d089f636b53d12d87a04 AS frontend-build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.12.10-slim-bookworm@sha256:97983fa8cc88343512862c62307159a82261c3528dc025f79e5a3f7af43e50b4
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_MANAGER_CATALOG=/app/config/model-catalog.json \
    MODEL_MANAGER_USERNAME_FILE=/run/model-manager/username \
    MODEL_MANAGER_PASSWORD_FILE=/run/model-manager/password \
    MODEL_MANAGER_SSH_KEY=/run/model-manager/spark_key \
    MODEL_MANAGER_KNOWN_HOSTS=/run/model-manager/known_hosts
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates openssh-client gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 manager \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin manager
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt
COPY backend/app.py ./app.py
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY --from=frontend-build /src/dist ./dist
RUN chmod 0555 /app/docker-entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
