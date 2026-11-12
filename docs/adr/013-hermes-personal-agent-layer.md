# ADR-013: Hermes is an application layer outside GB10

## Context

The personal-assistant handoff proposes Hermes with NemoClaw/OpenShell,
per-person profiles, Discord, proactive jobs, personal data, and local
inference. Hermes is now supported on Linux aarch64 and NVIDIA documents a
tested DGX Spark path. Hermes also owns persistent `state.db`, sessions,
memories, skills, messaging state, schedules, and backups, so it is an
application runtime rather than an inference service.

ADR-001 keeps GB10 inference-only so it is rebuildable, predictable under
mixed load, and not the sole home of durable personal data.

## Decision

Add Hermes as an optional Phase I personal-agent layer, with household scope
expansion in Phase J, after the text-runtime and shared-gateway gates. Run
`agent-owner`, `agent-partner`, `agent-child1`, and `agent-child2` in four
separately qualified OpenShell sandboxes, with an optional fifth
`agent-family` sandbox containing explicitly shared household context only.
ADR-017 places those sandboxes in a dedicated KVM guest managed by the NixOS
`home-core` host. Use the NemoClaw Hermes integration and route inference
through the existing `ai.home.arpa`/LiteLLM path to GB10 vLLM.

### Owner ChatGPT model profiles (2026-11-12)

The owner may use three additional Hermes profiles, `chatgpt-sol`,
`chatgpt-terra`, and `chatgpt-luna`, through the existing LiteLLM gateway.
Each profile is created from the owner's `default` profile so it receives the
same owner tool and MCP configuration, skills, and curated memory snapshot. Its
sessions and future memory remain profile-local. Each profile is fixed to one
ChatGPT subscription alias and uses a separate model-only LiteLLM key with MCP
denied; owner tools continue to use the existing separate Hermes MCP credential.
These profiles are assistants for the same owner, not boundaries between
household members. Their prompts and owner context returned by tools may be sent
to OpenAI through the ChatGPT subscription. Do not start a messaging gateway for
these profiles.

Hermes memory remains per-sandbox working context. A separate canonical event
service enforces principal, data-domain, and visibility scope as refined by
ADR-015; n8n/application services retain ingestion,
scheduling, retries, and request-bound approval for consequential actions.
Discord text is the first interface. Voice, private email, Home Assistant
writes, and banking are later gates.

A direct-on-GB10 NemoHermes deployment is allowed as a compatibility demo. It
does not become production without an ADR that explicitly supersedes ADR-001
and proves durable-state ownership, encrypted backup/restore, capacity,
contention, and failure recovery.

## Alternatives

- Run all Hermes sandboxes and their state on GB10: rejected for production
  because it changes the accepted appliance boundary and competes with 64K
  inference sessions, speech, meetings, Home Assistant, and Codex.
- Use one multi-profile Hermes process across different principals: rejected
  for private email/banking because profile routing is not a sufficient security
  boundary. The owner-only profiles above share one owner's authority and do not
  replace per-person sandbox boundaries.
- Deploy another LiteLLM/vLLM stack with NemoClaw: rejected because NemoClaw can
  consume the existing OpenAI-compatible endpoint and duplicate gateways would
  split policy, credentials, telemetry, and rollback.
- Treat Hermes/OpenShell approval prompts as universal human approval: rejected
  because they cover shell/network controls, not business actions such as email,
  calendar, Home Assistant, deletion, or financial operations.

## Consequences

The GB10 remains rebuildable and dedicated to inference. The assistant gains a
supported local model path without duplicating the model gateway. Operations
must now qualify and back up an application guest plus four private sandboxes
and the optional family sandbox, enforce cross-sandbox access below the model,
and re-run memory/mixed-load tests at
Hermes' required 64K context. Assistant availability depends on both hosts, but
deterministic home control remains independent of both.

## Status

Accepted as the design baseline; implementation proceeds through Phase I
gates. Amended by [ADR-017](017-consolidated-application-host.md): `home-core`
remains NixOS and manages a dedicated Ubuntu 24.04 KVM `agents` guest. Each
person receives a separate OpenShell sandbox inside that guest; the VM restores
the separate-kernel boundary from the trusted gateway while sandbox policy,
state, credentials, and backups provide the household privacy boundaries.

## Evidence

- `docs/research/hermes-personal-assistant-verification.md`
- `docs/requirements.md` URS-PA-001 through URS-PA-014
- `docs/architecture.md` section 13
- `docs/verification-strategy.md` V-PA-001 through V-PA-006
