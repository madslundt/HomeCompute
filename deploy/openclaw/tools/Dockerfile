FROM debian@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium python3 squid ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home browser \
    && mkdir -p /home/browser/profile && chown browser:browser /home/browser/profile
COPY browser_worker.py /opt/homecompute/browser_worker.py
USER 10001:10001
CMD ["python3", "/opt/homecompute/browser_worker.py"]
