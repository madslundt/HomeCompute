# ADR-038: Keep OpenBot upgrades in Git and service restarts behind approval

## Status

Accepted for source implementation. Runtime admission follows the backup
and isolation gates in [ADR-037](037-openbot-infrastructure-maintenance.md).
[ADR-039](039-shared-openbot-maintenance.md) supersedes the separate server
decision and retains the execution policy.

## Context

The isolated OpenBot operator can reduce the work of reviewing upgrades and
diagnosing outages. A full Home Core release can interrupt the gateway it
depends on, and the owner already uses a trusted workstation for approving
and applying releases. Persistent package and image changes belong in Git.

## Decision

Keep the existing isolated instance and scoped credentials. Expose health
reports for the two compute hosts and a read-only Home Core upgrade proposal
tool. NixOS proposals use the existing lockfile workflow. Docker proposals
identify the owning source template and return an immutable candidate when
the trusted observation report has fresh evidence for its tracked tag. The
owner reviews and publishes the Git change.

The only host execution available to the OpenBot principal is an exact,
root-approved Compose restart for Homepage, Open WebUI, Piper, or Wyoming STT.
Reuse the controller's state snapshot, one-attempt receipt, and restart
verification. The existing operator workstation CLI remains the Home Core
release and rollback path.

The scoped rollout may stage a published release while an existing Home Core
release stays active. The scoped gateway installer adds exactly the isolated
alias to the running configuration and records the prior mount for rollback.
The guest workflow verifies a separate encrypted agents-VM snapshot in the
active NAS repository before installing the operator. The standard host
deployment remains the path for changing Home Core's declared release.

## Rollout gates

Verify the separate instance and credentials, report freshness, and the
proposal results against source. Confirm the shared team server lacks the
private tool alias. Exercise an approved restart for one service and verify
its client path. Confirm that unapproved, expired, and unrelated plans cannot
execute through the OpenBot principal.

See the [OpenBot operator runbook](../../deploy/openbot/README.md#isolated-infrastructure-maintenance-server).
