# Scheduled briefing patch contract

This is a source-side contract for a later, reviewed n8n patch. It does not
create a schedule or contain an exported workflow. Keep the existing morning
overview (`WxRovre260928abc`) and tomorrow preparation (`WKu7Zv4dE4ztnaRY`)
triggers, once-per-adult/occurrence guards, recipients, credential references,
and delivery route. They remain the sole owners of those schedules.

Before producing a patch, provide a sanitized workflow graph as an in-memory
preimage, its canonical JSON SHA-256, and the exact ordinary delivery node ID.
`assistant_context_briefings.n8n_patch` changes only that node's `parameters`
and rejects changed preimages, schedule nodes, and credential-bearing nodes.
Review the returned graph diff for preservation of connections, triggers,
recipients, guards, and all unrelated nodes. Never save a live export or private
workflow payload in this repository.

The prepared workflow adapter posts only `{routine_id, scheduled_for}`
to its fixed private relay. The context service derives trusted scope and
destination from its routine-role credential and standing delivery grant.
The service derives `occurrence_key` from the canonical routine plus
Europe/Copenhagen local scheduled day. A repeated delivery after a timeout returns `uncertain`;
neither n8n nor a retry may translate that into a new occurrence or claim
completion. Only an observed delivery receipt can set `completed`.

An HTTP `accepted` response means durable admission only. Workflows must not
claim delivery until they receive a terminal completed result. Errors and lost
responses are recorded/reconciled by occurrence ID; they are never blind-retried
as a fresh message. The model cannot select a recipient, route, schedule, or
permission, and a briefing suggestion never grants authority to take an action.

The executable one-node relay patch is owned by
[`workflow_assistant_context.py`](../../scripts/workflow_assistant_context.py),
with its stricter [owner-edge contract](routine-patch-contract.md). The generic
`n8n_patch` helper above only patches ordinary non-routing content parameters;
it cannot establish the relay wiring. Current live graphs and the qualified
owner-only delivery edges remain unverified.
