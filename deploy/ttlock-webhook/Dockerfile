FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

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
