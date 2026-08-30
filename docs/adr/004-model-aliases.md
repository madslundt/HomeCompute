# ADR-004: Logical model aliases

## Context

Model families, formats, and runtimes change faster than consumer workflows.
Direct artifact names would couple Codex, n8n, and Home Assistant to deployment
details.

## Decision

Expose task-semantic text aliases: `automation`, `research`, `coding`, `home`,
`meeting`, and `assistant`. Expose STT/TTS through stable routes/protocol capabilities rather
than encoding a runtime or placement in the name. Initially one qualified text
artifact may back multiple text aliases. Alias changes require qualification
and rollback evidence.

`home`, `meeting`, `assistant`, and private automation are local-only. `research` may use a
qualified, explicit cloud fallback. Coding fallback remains visible in Codex
orchestration. A model alias must never imply that sensitive content is safe to
send to cloud; policy is bound to the authenticated consumer and alias.

## Alternatives

- Concrete model names in clients: rejected as brittle.
- One `local` alias: rejected because role-specific qualification and routing are needed.
- Placement-prefixed aliases such as `local-general`: rejected because they leak
  infrastructure policy into consumers and make a future qualified route change
  require consumer edits.
- Additional fast/reasoning aliases: deferred until a real workload justifies them.

## Consequences

Consumers remain stable while the registry changes. Different aliases do not
imply separate resident models. The existing LiteLLM control plane maps aliases
to qualified local or cloud backends and enforces per-key allow-lists.

## Status

Revised by the 2026-08-25 handoff review; proposed until consumer migration is
verified.

## Evidence

- `docs/requirements.md` URS-AI-003/004
- Role-specific research under `docs/research/`
