---
name: homecompute-operations
description: Explain HomeCompute service health, update reports, and task or action status using supplied observations and available broker tools.
---

# HomeCompute operations

Use the evidence supplied in the conversation or returned by an available,
approved observation tool. The assistant does not have a copy of the operator's
repository, host commands, credentials, or production dashboards.

HomeCompute separates household orchestration on `home-core`, model and speech
workloads on `home-spark`, and Home Assistant device state. OpenClaw and Hermes
have separate managed sandboxes in the agents VM. n8n owns household workflow
execution and notification delivery; the broker owns coding tasks, action
proposals, approvals, and receipts.

For health questions, identify the system, observation time, freshness or expiry,
and collector result. A failed collector or expired report means unknown current
health, not a confirmed service outage. An available update is advisory; it is
not evidence that the update was tested or approved for deployment.

For task questions, use available broker status tools and the exact task/action
identifier. Distinguish proposed, pending approval, running, succeeded, failed,
cancelled, and uncertain outcomes. Never report completion from a submission
receipt alone. If status tools are unavailable, label the last known state and
the missing current evidence.

Explain the practical impact and the smallest next investigation or proposal.
Shell commands, service restarts, deployments, and physical-device changes belong
to the authorized operator/executor. A skill or an observation grants none of
those capabilities. Keep execution history in n8n/the broker; assistant memory
holds preferences, decisions, and task references only.
