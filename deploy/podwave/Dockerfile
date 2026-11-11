FROM ghcr.io/astral-sh/uv:python3.15-trixie-slim@sha256:7bdac30f57aed9c88f9651cb10183c67dbc2bbd0602e9d4844bbcca19752f631

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PODWAVE_DATA=/data \
    PODWAVE_STATIC=/app/public \
    PODWAVE_BIND=0.0.0.0 \
    PODWAVE_PORT=8080

WORKDIR /app
COPY --from=podwave /pyproject.toml /uv.lock /.python-version ./
COPY --from=podwave /server/ ./server/
COPY --from=podwave /public/ ./public/

RUN groupadd --gid 1000 python \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin python \
    && mkdir -p /data && chown 1000:1000 /data \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen

USER python
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).status==200 else 1)"]
CMD ["/app/.venv/bin/python", "/app/server/podwave.py"]
