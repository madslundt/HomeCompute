FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 gateway \
    && useradd --system --uid 10001 --gid gateway --home-dir /nonexistent --shell /usr/sbin/nologin gateway

WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-compile --require-hashes -r requirements.lock
COPY --chown=gateway:gateway app ./app
COPY --chown=gateway:gateway healthcheck.py ./healthcheck.py

USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-server-header", "--no-proxy-headers", "--limit-concurrency", "32"]
