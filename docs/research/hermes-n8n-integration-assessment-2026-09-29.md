# Hermes and n8n integration assessment

**Verified:** 2026-09-29  
**Scope:** whether and how Hermes should participate in HomeCompute's n8n automations.  
**Recommendation:** integrate selectively, after the Hermes deployment gate; do not route ordinary n8n model calls through Hermes by default.

## Why an integration can help

Hermes is useful when an automation needs a personal-agent loop: a profile-scoped conversation, retained session context, or a bounded sequence of tool use. Its API server exposes authenticated OpenAI-compatible chat/responses endpoints and a native run API with status, event streaming, cancellation, session continuity, and an idempotency key for safely retrying run creation. Its webhook adapter can accept signed events and invoke an agent or a configured job. These give n8n practical call-in points without a custom Hermes node. ([Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [Hermes webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/))

n8n's HTTP Request node supports authenticated REST calls, so a workflow can call Hermes with an API credential; n8n's Webhook node can also receive a callback using Basic, Header, or JWT authentication. ([n8n HTTP Request node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest), [n8n Webhook node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook))

## Where it fits here

Keep n8n as the owner of event ingestion, polling/schedules, normalization, deduplication, retries, durable workflow state, delivery, and action approvals. Those responsibilities already match the accepted [Hermes architecture](../architecture.md#13-personal-assistant-platform), [ADR-013](../adr/013-hermes-personal-agent-layer.md), and the requirements for one scheduler per job and n8n-owned deterministic work ([URS-PA-012](../requirements.md)). The current n8n model workflows call the shared `automation-moe` endpoint directly; that is the right simple path for bounded summarization, extraction, classification, and schema-constrained output ([current state](../current-state.md#n8n-local-model-status), [n8n routing policy](n8n-model-routing-and-scheduling.md)).

Use Hermes for an optional, allow-listed handoff where profile identity and conversational context add value—for example, n8n can normalize a newly arrived household event, apply source and principal scope, then submit a bounded task to the corresponding Hermes profile. Hermes can analyze, recommend, or draft and return a result; n8n can record/deliver it and enforce any approval before an external write. This is a good fit for on-demand personal briefings or a reviewed follow-up on selected calendar/email events. It is not a reason to turn Shopping, Aula, or Notion's routine model calls into full agent runs.

There is a second integration direction: Hermes can connect to n8n's official instance-level MCP server. n8n lets admins expose workflows individually; clients can run exposed workflows, and n8n 2.13+ also offers tools to create/edit workflows. This gives the agent control over automation, so reserve it for a concrete admin use case and expose only narrowly selected tools. It is not required for n8n to invoke Hermes. ([Hermes MCP integration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp), [n8n MCP server](https://docs.n8n.io/connect/connect-to-n8n-mcp-server/))

## Boundaries and costs

- Do not let Hermes become a second owner of the same scheduled task. Hermes itself supports cron/jobs and webhook-triggered cron; use one scheduler for each job. If n8n owns it, n8n should trigger Hermes for that run rather than maintaining a duplicate Hermes schedule. ([Hermes cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/), [event-triggered jobs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/))
- A Hermes agent run has more moving parts, persistent sessions/state, tool access, and model work than a direct completion. Use it only when those capabilities materially improve the task; keep frequent unchanged inputs out of the agent loop. Hermes documents a minimum 64K context for tool-using agents, which also needs separate capacity qualification against this shared GB10 workload. ([Hermes FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq))
- Keep data authorization and consequential-action approval outside model instructions. The accepted design requires profile/data-domain scoping below the model and an explicit approval service or n8n workflow before sending, deleting, changing bookings/home settings, or moving money ([ADR-013](../adr/013-hermes-personal-agent-layer.md), [URS-PA-011/012](../requirements.md)). Hermes webhook HMAC proves who sent an event, not that text inside the event is trustworthy; payload text still needs untrusted-input handling. ([Hermes webhook security](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/))
- Current-state documentation reports that the Hermes substrate is on `home-core`, while the agents VM is disabled until off-host backup is configured. This is therefore a future gated integration, not a live capability today. ([current state](../current-state.md#observed-live-snapshot))

## Suggested first integration

After the VM backup/restore and Hermes API/profile gates pass, add one disabled or low-risk n8n workflow path for a synthetic or manually triggered, profile-scoped event. Use n8n HTTP Request -> authenticated Hermes `/v1/runs` with an `Idempotency-Key`; poll status or consume run events, validate the result, and write it through the existing workflow path. Give Hermes no broad n8n administration tools and no direct consequential-action credentials. Measure end-to-end success, latency, duplicate behavior, model capacity, and whether retained profile context improves accepted outcomes versus the current direct `automation-moe` call. Promote only that demonstrated use case.

## Decision

**Yes, Hermes may benefit a few n8n automations as a profile-scoped agent endpoint.** Its value is contextual, multi-step personal assistance. Keep the current model-only n8n path for ordinary LLM operations and keep deterministic workflow ownership in n8n. The first integration should be a single bounded event handoff after Hermes is enabled and qualified.
