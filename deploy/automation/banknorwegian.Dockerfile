# Digest matches the tracked python slim base used by ttlock-webhook; no PyPI
# dependencies are installed, so the runtime is stdlib-only by construction.
FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d

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
