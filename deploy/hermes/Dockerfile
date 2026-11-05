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
ENV NEMOCLAW_INFERENCE_PROVIDER_ID=compatible-endpoint \
    NEMOCLAW_MODEL=automation-moe \
    NEMOCLAW_INFERENCE_BASE_URL=http://ai.home.arpa:18080/v1 \
    NEMOCLAW_PROVIDER_KEY=compatible-endpoint \
    NEMOCLAW_UPSTREAM_PROVIDER=compatible-endpoint \
    NEMOCLAW_INFERENCE_API=openai-completions
