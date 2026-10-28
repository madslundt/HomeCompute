# Hermes practical use guide

**Checked:** 2026-10-01  
**Scope:** whether and how to begin using the configured HomeCompute Hermes deployment.

## Current state

The agents KVM service is running on `home-core`; the Ubuntu guest answers on
its host-only address. On 2026-10-01, the synthetic `agent-owner` sandbox was
recreated with NemoClaw `0.0.129`, Hermes `0.21.3`, and OpenShell `0.0.116`.
The checked-in health helper passed, a harmless prompt returned through
`assistant-canary`, and fresh snapshot `synthetic-baseline-final-20261001` was
created afterward. No personal data or integrations are present.

The daily `agents-vm-bootstrap` Restic job has completed successfully and
currently holds same-host snapshots. This is useful for the synthetic canary,
but it is not an off-host backup. Host configuration still has production
off-host backups disabled, and the VM remains classified `synthetic-only`.

## Recommended first use

1. From the workstation, connect to the owner canary in an interactive terminal:

   ```bash
   ssh -tt -o IdentitiesOnly=yes \
     -i ~/.ssh/id_ed25519_ai-services-01 \
     -J home-core hermes-operator@10.77.20.2 \
     'export PATH="$HOME/.nvm/versions/node/v22.23.3/bin:$HOME/.local/bin:$PATH"; nemohermes launch agent-owner'
   ```

   Keep this canary tool-light and use synthetic examples. Do not add messaging,
   web search, MCP servers, host mounts, personal data, or schedules yet.
2. Before moving to real personal data, configure encrypted off-host backup,
   restore the guest and Hermes state in an isolated drill, and close the
   documented network, inference-key, capacity, and policy gates. Then onboard
   only one adult sandbox first.

The repository-specific command sequence and gate schemas are documented in
[`deploy/hermes/README.md`](../../deploy/hermes/README.md). The release tuple is
pinned in [`config/hermes-release.json`](../../config/hermes-release.json).

## Good operating practices

- Give Hermes a job when persistent conversational context or a bounded
  multi-step tool loop adds value. Ask it to analyze, recommend, or draft; keep
  deterministic ingestion, retries, schedules, deduplication, and delivery in
  n8n or the existing system that owns them.
- Start with one capability at a time. Prefer read-only access; test both the
  allowed action and representative denials before expanding the tool surface.
  Keep identity, data access, and approval rules enforced outside the model.
- Treat retrieved pages, messages, email, and webhook payloads as untrusted
  input. HMAC authenticates a webhook sender; it does not make the body safe to
  follow as instructions.
- Save only useful, reviewable preferences in memory. Correct or remove stale
  memories. Keep source-of-truth records in their existing canonical services;
  Hermes memory and `state.db` are working context, not the household database.
- Use separate sandboxes, credentials, state, and backups for each person.
  Profiles inside one shared runtime are not the intended household privacy
  boundary. Keep family-shared information in an explicitly shared projection.
- Do not create a Hermes cron job for a task already scheduled by n8n or Home
  Assistant. Name one scheduler per job and make triggered writes idempotent.
  Require explicit human approval for messages sent externally, purchases,
  account changes, and home-control writes.
- Hermes needs at least 64K model context; measure latency and memory use under
  the real mixed workload before increasing concurrency or enabling proactive
  jobs.

## Is it the right tool?

Hermes is worth a narrow trial for a private assistant that remembers reviewed
preferences and can carry out a bounded sequence of approved read-only tools—for
example, a personal briefing or a household planning draft. It is a poor fit
for routine extraction, classification, summarization, or deterministic
automations that already work as direct LiteLLM/n8n model calls. It should not
replace Codex as the coding workspace, Home Assistant as the home-control
authority, or n8n as the workflow scheduler.

Judge the pilot against the simpler direct-model path: did retained context or
tool use save enough effort to justify the extra runtime, state, latency, and
maintenance? If one concrete task does not show that gain, there is no need to
activate Hermes for that task.

## Primary sources

- [Hermes quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart/) (provider setup and 64K context minimum)
- [Hermes security](https://hermes-agent.nousresearch.com/docs/user-guide/security/) (approval defaults, user authorization, isolation)
- [Hermes CLI](https://hermes-agent.nousresearch.com/docs/user-guide/cli) (interactive terminal use)
- [Hermes webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/) (signed events and untrusted inputs)
- [NemoClaw Hermes quickstart](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/quickstart) (sandbox connection and launch)
- [NemoClaw sandbox state and backups](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/state-and-backups/understand-sandbox-state) (persistent Hermes state)
