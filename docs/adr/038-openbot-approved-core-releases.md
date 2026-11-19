# ADR-038: Approve exact Home Core releases for OpenBot

## Status

Accepted for source implementation. Runtime admission follows the existing
backup and isolation gates in [ADR-037](037-openbot-infrastructure-maintenance.md).

## Context

The isolated infrastructure operator needs to roll out reviewed Home Core
releases and recover selected Compose services. Its existing maintenance
controller includes independent, digest-bound approval and an execution ledger.

## Decision

The isolated OpenBot operator uses the existing maintenance tools to prepare a
Home Core deployment. The root controller resolves the fixed repository's
published `master` to a full commit SHA and records the active base revision.
The owner reviews the source, CI, and exact plan, then approves its digest
through the root controller.

The approved deployment runs the published revision's existing Home Core
release wrapper in a systemd transient unit. This lets the job continue during
MCP and gateway restarts. The wrapper takes the normal release lock, checks
the approved base and current encrypted backup coverage, applies the release,
and keeps the previous revision. The controller records a single attempt and
checks the resulting revision and previous pointer. A new published tip
requires a fresh plan. An uncertain attempt holds subsequent OpenBot releases
until the owner verifies or recovers running state and reconciles the receipt.

The same isolated principal can prepare reviewed Home Core Compose restarts.
These use the existing exact approval path and container health checks.
Existing package and image authorization stays governed by the controller.
The workstation release and rollback commands remain the operator path for
historical revisions and Spark releases.

## Consequences

- OpenBot uses the same scoped MCP connection for releases and maintenance.
- Each Home Core deployment and Compose restart depends on independent root
  approval. Routine package and image candidates keep their existing policy.
- A rollout may briefly interrupt the OpenBot connection. The host ledger and
  transient unit journal retain the outcome for recovery.
- The full release must have a current encrypted backup covering Home Core
  application state. The separate OpenBot installation requires verified
  off-host coverage of its agents VM.

See the [OpenBot operator runbook](../../deploy/openbot/README.md#deploy-or-maintain-home-core-from-openbot).
