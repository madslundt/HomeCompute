# ADR-022: Optional lightweight inference on home-core

## Context

The K15/home-core is the always-on control plane and has 48 GB of RAM. The
DGX Spark/home-spark remains the primary inference appliance for large models
and GPU-heavy workloads. A small local model could keep simple classification,
extraction, or routing available when home-spark is offline, but an unmeasured
model can compete with n8n, LiteLLM, PostgreSQL, and other persistent services.

The repository already defines LiteLLM as the stable authenticated model
boundary and uses task-semantic aliases such as `coding`, `automation`,
`home`, and `assistant`. A local runtime that clients call directly would
duplicate routing and expose another API surface.

## Decision

`home-core` may host an optional lightweight inference backend for bounded,
low-complexity tasks. It is not the primary model host and must not displace
the DGX Spark's compute role.

The backend remains deferred until all of the following are recorded and
qualified:

1. A specific small model and runtime are selected from measured K15 CPU and
   memory results, including the effect on existing services.
2. Model and runtime artifacts are pinned by immutable identity and verified
   before activation.
3. The backend is reachable by LiteLLM through a restricted local path, and
   all consumers use the existing LiteLLM endpoint and approved task aliases.
4. The endpoint has authentication or is confined to an equivalently restricted
   service network; it is not published directly to the LAN or Tailscale.
5. Timeouts, health reporting, resource limits, restart behavior, and the
   home-spark outage behavior are verified. No automatic downgrade of complex
   work is enabled.

Until those gates pass, no model weights, inference runtime, listener, or
model-specific secret are provisioned on home-core. A control-plane health
check must not report an unconfigured optional backend as a service failure.

The primary model and any future optional local model are addressed through
the existing task-semantic alias scheme. Placement-prefixed names such as
`local/fast` are not required; placement and runtime remain LiteLLM policy.

## Consequences

- `home-spark` remains the primary inference plane and owns large-model and
  GPU-heavy services.
- `home-core` retains control-plane capacity before any local inference is
  considered. A candidate that harms control-plane latency or stability is
  rejected even if its isolated benchmark is fast.
- A local backend may provide explicitly selected simple tasks while
  home-spark is unavailable. Complex requests fail clearly unless a caller or
  policy explicitly selects an appropriate alternative.
- Every local-model change is a qualified deployment tuple and requires an
  update to the architecture, operations, and rollback documentation.

## Status

Accepted. Amends the scope of ADR-001 and ADR-017: `home-spark` remains the
primary inference-only appliance, while a separately qualified lightweight
utility backend may later run on `home-core`. This decision does not qualify a
model or authorize activation.

## Evidence

- User-approved control-plane / compute-plane architecture request, 2026-09-25
- [ADR-001](001-gb10-inference-only.md)
- [ADR-003](003-ai-api-boundary.md)
- [ADR-004](004-model-aliases.md)
- [ADR-017](017-consolidated-application-host.md)
- [Home-core rollout plan](../home-core-rollout-plan.md)
