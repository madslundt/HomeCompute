# Preserve the complete managed startup and credential contracts. The published
# image's default inference environment is Nemotron/inference.local; explicitly
# pin the approved local route for every gateway restart and reconstruction.
FROM ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:f52d55af05b63302ca8ae54ca9141b80df433a72ce273ae4aeac08c5af2cb990
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}
ENV NEMOCLAW_INFERENCE_PROVIDER_ID=compatible-endpoint \
    NEMOCLAW_MODEL=automation-moe \
    NEMOCLAW_INFERENCE_BASE_URL=http://ai.home.arpa:18080/v1 \
    NEMOCLAW_PROVIDER_KEY=compatible-endpoint \
    NEMOCLAW_UPSTREAM_PROVIDER=compatible-endpoint \
    NEMOCLAW_INFERENCE_API=openai-completions
