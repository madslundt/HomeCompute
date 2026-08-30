# ADR-001: `ai-compute-01` is an inference-only appliance

## Context

The platform already has appropriate hosts for development, Home Assistant,
n8n, MCP servers, and durable workflow state. Co-locating applications on GB10
would increase coupling, resource contention, recovery scope, and sensitive
data residency.

## Decision

Run only AI inference and directly supporting edge, telemetry, and artifact
management services on GB10. Keep source/build/test/Codex on the MacBook and
Home Assistant, Node-RED, n8n, all MCP servers (including Aula MCP), Meeting
Assistant, and existing state on their verified current services.

## Alternatives

- General application server on GB10: rejected due to scope and contention.
- Move n8n/MCP/Home Assistant: rejected; no requirement or benefit.
- Add persistent agent/vector memory: rejected until a future requirement proves need.

## Consequences

GB10 is simpler to rebuild and models can consume its resources predictably.
Network availability becomes an explicit dependency for local AI. Aula remains
inside the existing n8n workflow, never a GB10 service.

## Status

Accepted.

## Evidence

- `docs/requirements.md` URS-AI-001
- `docs/architecture.md` deployment and component responsibilities
