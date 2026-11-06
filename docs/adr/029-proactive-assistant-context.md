# ADR-029: Shared context with an owner conversation and existing controllers

## Context

The owner wants Home Assistant, Daythread, Aula and Calendar connected to a
proactive two-way assistant, followed by broader integration and tested workflow
improvement. Hermes, OpenClaw, n8n, task/reminder stores and scoped retrieval
already exist. Their independent histories and notifications do not establish
one durable view of the owner's day. Implementation is tracked by
[issue 61](https://github.com/madslundt/HomeCompute/issues/61).

## Decision

Implement a small authenticated context service on home-core, initially disabled
and using shadow attention. Its source journal and derived context are separate
from source application artifacts. Bind principal, data domain, visibility,
source grants and operation role from an independent server credential. Reuse
scoped private SQLite and finite tools. Models cannot select credentials,
origins, scope, source ingestion, promotion approval or device actions.

Select the existing owner-only Hermes Mattermost conversation as the initial
context user. Its explicit context tools are opt-in and are not inherited by
OpenClaw specialist accounts. Keep OpenClaw's task tree and coding broker,
existing private/shared task stores, n8n schedules and Home Assistant controllers
authoritative. Framework memory is not the cross-source canonical ledger.

Source revisions, duplicate admission, pending processing and deletion tombstones
are durable. Attention enforces freshness, quiet hours and budgets outside the
model. Feedback and improvement candidates are scoped application data; a
reusable accepted skill/workflow still needs reviewed Git source and its owning
lifecycle. OMH external memory and dreaming remain disabled.

This refines ADR-013's separate canonical event-service boundary and ADR-015's
memory lifecycle for the current Mattermost deployment. It does not change
inference placement, work-domain admission, household privacy, current action
approvals or the authority of the existing controllers.

Extend this boundary with optional scope-filtered Harrier retrieval and
source-linked cross-source briefing interpretations. Existing n8n workflows
retain schedule ownership; a separate finite routine credential admits a
date-derived occurrence. Chat tools can inspect and pause routines, but cannot
admit a run. Delivery requires a qualified expiring standing owner-DM grant.

Native delegation uses a dedicated no-tool OpenClaw role and worker-only SSH
credential. Actual native receipts distinguish admission, completion, timeout
and cancellation. Local source erasure cannot prove native transcript erasure:
the pinned native session lifecycle archives ordinary deleted transcripts.

Improvement promotion runs as a separate trusted operator. Its automatic patch
classes affect only bounded briefing presentation and stricter correlation
thresholds. Independent replay, canary, a standing class grant, protected Git
review, reconciliation and a guarded policy installer precede activation. Models
cannot approve, publish or deploy. Keep root-controller custody separate from
model-writable feedback state. A restored service stays fenced until separately
trusted current deletion evidence is replayed and derived state is scrubbed.

## Consequences

The front door can change without migrating original recordings or task ledgers.
One source-owned component must now be backed up and restored consistently,
including revision/cursor state and deletion tombstones. A disabled declaration
and synthetic tests are not production readiness. Daythread needs a qualified
authenticated export path; its ordinary profile header is not proof of identity.

## Status

Implemented as an opt-in source candidate. Production enablement, actual
authenticated client paths, notification ownership changes, and standing action
or automatic deployment permissions remain unqualified and undeployed.

## Related documents

- [Implementation plan](../proactive-personal-assistant-plan.md)
- [Context service runbook](../../deploy/assistant-context/README.md)
- [Personal memory contract](../personal-data-and-memory.md)
