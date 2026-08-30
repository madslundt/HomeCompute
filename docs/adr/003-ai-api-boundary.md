# ADR-003: Stable AI API boundary

## Context

Clients must survive runtime/model replacement and must not know private ports.
The edge also owns TLS, client authentication integration, request identity,
route allow-listing, and backend isolation.

## Decision

Expose `https://ai.home` through Caddy as the only client-LAN boundary. Reuse
the existing AI Home LiteLLM instance behind Caddy for virtual keys, semantic
aliases, and qualified local/cloud routing. Use OpenAI Responses for Codex,
compatibility Chat Completions only when existing clients require it, and
qualified audio routes/protocols. Keep all runtimes and management endpoints
private. Keep a direct vLLM test listener for protocol/performance comparison,
not as the normal consumer endpoint.

## Alternatives

- Direct runtime endpoints: rejected for production; couples security and clients to runtime.
- NGINX edge: viable if first-party rate limiting becomes mandatory.
- Traefik: rejected initially; dynamic discovery adds unnecessary surface.
- Second LiteLLM on GB10: rejected because AI Home already defines the control plane.

## Consequences

Direct runtime remains a test-only baseline. The combined Caddy/LiteLLM path
must pass streaming, authentication, request-ID, latency, privacy, virtual-key,
and route-policy tests. Cloud access remains available when GB10 is down, while
private aliases fail closed.

## Status

Superseded in part by ADR-011; combined gateway PoC gate open.

## Evidence

- `docs/research/gateway-evaluation.md`
- `docs/current-state.md`
- `docs/adr/011-reuse-ai-home-control-plane.md`
- `docs/architecture.md`
