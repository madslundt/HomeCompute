# ADR-011: Reuse the AI Home control plane

## Context

The `ai_home` repository already defines a shared LiteLLM gateway, cloud
providers, PostgreSQL, Redis, monitoring, and consumer services on the Mac
Mini. The earlier GB10 design deferred LiteLLM and placed the stable edge on
the appliance without first inventorying this stack. That would create two
gateways and make cloud access depend unnecessarily on GB10 availability.

## Decision

Qualify and extend the existing LiteLLM deployment as the single model control
plane. Put a small authenticated TLS edge such as Caddy in front of it. Route
task-semantic aliases to GB10 vLLM or approved cloud providers. Keep the direct
Codex-to-vLLM route only as a compatibility and performance baseline.

Run the control plane on the existing always-on host, not on GB10. Use distinct
virtual keys for Codex, n8n public workflows, n8n private workflows, Home
Assistant, Meeting Assistant, and other approved consumers. If LiteLLM needs
persistence, use a separately credentialed database rather than LibreChat's
schema. Do not reuse its master key as a consumer key. Connect the control
plane to GB10 over Tailscale or an equivalently restricted encrypted private
host link; do not expose runtime ports to the ordinary client LAN.

## Alternatives

- Second LiteLLM on GB10: rejected because it duplicates the control plane and
  makes cloud routing unavailable during a GB10 outage.
- Caddy directly to a single vLLM backend: retained only as a PoC baseline; it
  cannot own task-aware local/cloud policy or independent virtual keys alone.
- Custom LLM gateway: rejected unless a verified requirement cannot be met by
  LiteLLM plus the edge.

## Consequences

The existing `ai_home` deployment must be treated as a prototype until image
pins, TLS, virtual keys, logging, model aliases, and database isolation pass the
verification suite. A LiteLLM failure affects both local and cloud aliases, so
the control plane needs an explicit recovery and rollback test. Codex's
implementation fallback remains visible in Codex orchestration, and Home
Assistant/meeting private routes never receive silent cloud fallback.

## Status

Proposed; live `ai_home` inventory and gateway qualification pending.

## Evidence

- `docs/current-state.md`
- `docs/research/gateway-evaluation.md`
- `docs/verification-strategy.md` V-GW-001
