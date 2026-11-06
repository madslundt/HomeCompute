# Legacy Hermes 0.21.3 recipe retained for rollback; see config/hermes-release.json for selection.
# Preserve the complete managed startup and credential contracts. The published
# image's default inference environment is Nemotron/inference.local; explicitly
# pin the approved local route for every gateway restart and reconstruction.
FROM ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:f52d55af05b63302ca8ae54ca9141b80df433a72ce273ae4aeac08c5af2cb990
USER root
ARG NEMOCLAW_CORPORATE_CA_B64=
RUN if [ -n "$NEMOCLAW_CORPORATE_CA_B64" ]; then \
      install -d /usr/local/share/nemoclaw /usr/local/share/ca-certificates \
      && printf '%s' "$NEMOCLAW_CORPORATE_CA_B64" | base64 --decode > /usr/local/share/nemoclaw/corporate-ca.pem \
      && openssl crl2pkcs7 -nocrl -certfile /usr/local/share/nemoclaw/corporate-ca.pem > /dev/null \
      && cp /usr/local/share/nemoclaw/corporate-ca.pem /usr/local/share/ca-certificates/homecompute.crt \
      && chmod 0444 /usr/local/share/nemoclaw/corporate-ca.pem /usr/local/share/ca-certificates/homecompute.crt \
      && update-ca-certificates; \
    fi
USER sandbox
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}
ENV NEMOCLAW_CONTEXT_WINDOW=65536 \
    NEMOCLAW_INFERENCE_PROVIDER_ID=compatible-endpoint \
    NEMOCLAW_MODEL=automation-moe \
    NEMOCLAW_INFERENCE_BASE_URL=http://ai.home.arpa:18080/v1 \
    NEMOCLAW_PROVIDER_KEY=compatible-endpoint \
    NEMOCLAW_UPSTREAM_PROVIDER=compatible-endpoint \
    NEMOCLAW_INFERENCE_API=openai-completions
USER root
COPY patch-private-request-logging.py /usr/local/lib/nemoclaw/homecompute-private-request-logging.py
RUN python3 /usr/local/lib/nemoclaw/homecompute-private-request-logging.py /opt/hermes/agent/agent_runtime_helpers.py
ENV HERMES_DUMP_REQUESTS=0 HERMES_DUMP_REQUEST_STDOUT=0
COPY browser-client.mjs /usr/local/lib/nemoclaw/homecompute-browser-client.mjs
COPY browser-relay.mjs /usr/local/lib/nemoclaw/browser-relay.mjs
COPY patch-browser-transport.py /usr/local/lib/nemoclaw/patch-browser-transport.py
RUN python3 /usr/local/lib/nemoclaw/patch-browser-transport.py /opt/hermes/tools/browser_tool_cdp.py
# The vendor browser client is installed outside the agent's Landlock read
# roots. Publish the same reviewed payload inside the existing read-only /usr
# root; keep npm offline and avoid an unpinned npx fallback.
RUN cp -a /opt/nemoclaw-agent-browser-runtime /usr/local/lib/homecompute-agent-browser-runtime \
    && chmod 0555 /usr/local/lib/nemoclaw/homecompute-browser-client.mjs \
    && chmod 0444 /usr/local/lib/nemoclaw/browser-relay.mjs \
    && ln -sfn /usr/local/lib/nemoclaw/homecompute-browser-client.mjs /usr/local/bin/agent-browser \
    && test "$(/usr/local/bin/agent-browser --version)" = "agent-browser 0.26.0"
# The managed policy is the native agent's routing authority. Regenerate it
# with NVIDIA's installed generator; changing mutable config alone is ignored.
RUN HOME=/sandbox node /opt/nemoclaw-hermes-config/generate-config.ts \
    && install -o root -g root -m 0444 /sandbox/.hermes/managed-policy.json /usr/local/share/nemoclaw/hermes-managed-policy.json \
    && rm /sandbox/.hermes/managed-policy.json \
    && chown sandbox:sandbox /sandbox/.hermes/config.yaml /sandbox/.hermes/.env \
    && chmod 0640 /sandbox/.hermes/config.yaml /sandbox/.hermes/.env \
    && python3 -c 'import json; p=json.load(open("/usr/local/share/nemoclaw/hermes-managed-policy.json")); c=p["config"]; assert c["model"]["context_length"] == 65536; assert c["model"]["base_url"] == "http://ai.home.arpa:18080/v1"; assert c["custom_providers"][0]["base_url"] == "http://ai.home.arpa:18080/v1"'
USER sandbox
