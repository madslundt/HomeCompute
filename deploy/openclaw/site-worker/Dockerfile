FROM mcr.microsoft.com/playwright/python@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
LABEL org.homecompute.site-worker="1"
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/site/requirements.txt
RUN pip install --no-cache-dir --require-hashes --target /opt/site/python -r /opt/site/requirements.txt
COPY scripts/openclaw_site_worker.py scripts/openclaw_site_session_worker.py scripts/openclaw_site_drivers.py scripts/openclaw_site_registry.py scripts/openclaw_bitwarden.py scripts/openclaw_site_egress.py scripts/openclaw_site_network_health.py /opt/site/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/opt/site:/opt/site/python
USER 1000:1000
ENTRYPOINT []
CMD ["python", "/opt/site/openclaw_site_worker.py"]
