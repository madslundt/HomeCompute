# ADR-020: Workflow-first local inference rollout

## Context

The GB10 is not expected to recover its purchase price through avoided API
spend. Its useful roles are private inference, hands-on operation and learning,
and independent overflow capacity. Those goals do not justify routing every
request locally or expanding the first deployment to every proposed consumer.

The first rollout needs to prove value in workflows that already exist. Aula is
the simplest frequent private automation. Codex can exercise the same text
service without making local execution the default. Danish TTS is useful but
has a separate latency and listening-quality decision. Bank analysis, Hermes,
STT, and model-assisted Home Assistant control do not yet have an initial
production workflow.

## Decision

The `home-core` Caddy/LiteLLM boundary routes only to local GB10 inference. It
does not hold cloud-provider credentials, configure cloud backends, classify a
request as safe for cloud, or perform local-to-cloud fallback. A caller may use
a cloud provider only through a separate explicit path selected before private
context is assembled.

This supersedes the control-plane consequence in ADR-004 that allowed aliases
on this gateway to map to cloud backends. It narrows, but does not replace,
ADR-010: orchestration may still make a visible cloud-fallback decision where
the workflow and data classification permit it.

Use one qualified Qwen3.8-27B service for every text alias initially. Expose
`auto`, but resolve it deterministically to that resident default. Prompt-based
classification may be evaluated in shadow mode later and cannot control model
activation during this milestone.

Implement the first milestone in this order:

1. Qualify the Qwen3.8-27B API and bind the semantic aliases.
2. Replay Aula inputs without notifications or writes, then cut Aula over to
   local-only inference without redesigning its behavior.
3. Offer explicit whole-session `Cloud` and `GB10 Local` Codex modes, with
   Cloud as the normal default.
4. Qualify Plapre Nano v2 on the GB10 against the independent Piper CPU
   fallback. Plapre must keep warm p95 first-audio latency at or below 750 ms,
   warm p95 RTF at or below 0.5, and pass Danish pronunciation, blinded
   listening, ASR verification, one-resynthesis, and fallback checks.

All n8n model calls use the local route during the initial rollout. A local
failure is returned to n8n; it never triggers cloud fallback. Aula retains the
input, retries with bounded backoff, suppresses duplicate delivery, and alerts
the operator if it has not succeeded within two hours. Models may emit
schema-validated transformations and recommendations, but deterministic n8n
steps own writes, notifications, approvals, retry, and idempotency.

Foreground interactive requests take precedence over background automation,
but an active generation is not preempted. Safety-critical and deterministic
Home Assistant behavior never depends on the GB10.

Automatic cloud-plan/local-build/cloud-review coding remains disabled until at
least 20 representative real tasks have been evaluated. Promotion requires at
least 70% to pass build/tests and cloud review without cloud reimplementation,
no more than one evidence-informed local retry, and no serious review defect.
Only bounded, reversible, objectively verifiable implementation work is
eligible. Architecture, security-sensitive changes, destructive migrations,
and ambiguous product work remain cloud work.

Repository privacy is declared in committed project metadata as either
`local_only` or `cloud_allowed`. Unknown projects fail closed to `local_only`.
Ordinary non-public repositories may deliberately declare `cloud_allowed`;
restricted repositories cannot be overridden per request to use cloud.

STT, bank-transaction analysis, Hermes, and Home Assistant LLM reasoning are
separate later features. ADR-021 records the speech selections without
reserving runtime, memory, or operational capacity in this milestone.

## Consequences

The rollout is smaller and directly observable. An idle GB10 is acceptable;
utilization is not a success metric. Local coding must improve the real Codex
workflow before automation is promoted, while privacy-sensitive n8n work never
depends on cloud availability or policy.

The local gateway is simpler and can be tested for absence of cloud backends
and credentials. Callers that later use cloud must implement and expose that
choice themselves. A higher-quality TTS service may consume several GiB on
`home-core` while capacity is plentiful, but it has an explicit removal and
fallback path if latency or future memory pressure makes it unsuitable.

## Status

Accepted.

## Evidence

- `config/model-router-policy.json`
- `config/gb10-model-roster.json`
- `docs/n8n-migration-inventory.md`
- `benchmarks/`
