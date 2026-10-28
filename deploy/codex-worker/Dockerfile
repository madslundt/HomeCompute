FROM node:24.14.0-bookworm-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8
# Frozen Debian archive; npm artifacts have integrity hashes in package-lock.
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260317T000000Z bookworm main\n' > /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends git python3 ca-certificates squid && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /opt/worker
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts && ln -s /opt/worker/node_modules/.bin/codex /usr/local/bin/codex && codex --version
COPY *.py ./
# Build contexts can originate from private operator files (mode0600).
# Runtime broker/proxy users must be able to read the public implementation.
RUN chmod 0444 /opt/worker/*.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python3", "/opt/worker/worker.py"]
