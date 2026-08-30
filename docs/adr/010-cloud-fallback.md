# ADR-010: Explicit orchestration-level cloud fallback

## Context

Local coding can fail due to quality, ambiguity, unsupported tools, GB10
outage, or runtime incompatibility. Hidden gateway fallback would obscure which
model implemented a change and can be unsafe for Home Assistant actions.

## Decision

After the pinned Codex cross-provider canary passes, run one local
implementation, one local retry with failure evidence, then cloud implementation
fallback. Allow immediate cloud escalation for recorded architecture, security,
requirement ambiguity, local model failure, or unsupported-task reasons. Cloud
review remains final. Disable hidden Home Assistant model fallback; fail closed.

## Alternatives

- Infinite/local-only retry: rejected.
- Gateway model substitution: rejected.
- Cloud implementation for every large task: rejected; planner decomposition comes first.
- Manual provider selection: rejected for normal accepted Stage 2.

## Consequences

Fallback requires a qualified client version, explicit trace/metrics, and tests
for task delivery, retry context and GB10 outage. Until that gate, the workflow
is a documented target rather than an active production feature. Client
upgrades are held until requalification.

## Status

Proposed; blocked with ADR-007.

## Evidence

- `docs/requirements.md` URS-CODEX-003/005/006/008
- `docs/verification-strategy.md` V-CODEX-E2E-001
