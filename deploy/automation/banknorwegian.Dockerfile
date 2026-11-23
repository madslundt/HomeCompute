# Digest matches the tracked python slim base used by ttlock-webhook; no PyPI
# dependencies are installed, so the runtime is stdlib-only by construction.
FROM python:3.15.2-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app
COPY banknorwegian-mcp/server.py ./server.py
RUN python -m py_compile server.py && rm -rf __pycache__

USER 1000:1000
EXPOSE 8090
CMD ["python", "server.py"]
