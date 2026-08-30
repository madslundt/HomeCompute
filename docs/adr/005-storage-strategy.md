# ADR-005: Internal-first rebuildable storage

## Context

The target has 1 TB internal NVMe. Models, containers, caches, and logs can fill
it quickly, while workflow state already lives elsewhere. External storage may
be added later but should not mask an undefined retention policy.

## Decision

Use internal storage for OS, active/rollback images and model artifacts,
bounded caches, and bounded telemetry. Preserve at least 100 GB planning
headroom, alert at 80% use, and freeze changes at 90% until space is recovered.
Treat GB10 as rebuildable; keep source configuration and durable workflow state
off-appliance.

## Alternatives

- Buy/use external storage immediately: deferred pending measured capacity/rebuild need.
- Retain all downloaded models: rejected.
- Store workflow or vector state locally: rejected as out of scope.

## Consequences

Artifact cleanup and rollback policy are mandatory. External storage later
requires an ADR covering integrity, performance, backup, and failure behavior.

## Status

Proposed; measured artifact sizes pending.

## Evidence

- `docs/design-specification.md` storage design
- `docs/requirements.md` URS-OPS-007

