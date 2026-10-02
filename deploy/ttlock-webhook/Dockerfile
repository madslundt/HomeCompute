FROM python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8

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
