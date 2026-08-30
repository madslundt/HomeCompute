# ADR-006: Codex remains the developer harness

## Context

The existing developer experience, tools, repositories, builds, and tests are
on the MacBook. A second local-model coding UI would fragment workflow and
verification.

## Decision

Keep `codex` as the only normal developer-facing harness. Configure the GB10 as
a machine-local custom provider for qualified local sessions. Do not move Codex
or development repositories to GB10.

## Alternatives

- Separate local coding UI/CLI: rejected.
- Run the developer environment on GB10: rejected by appliance boundary.
- Hide GB10 behind an unrelated agent framework: rejected unless Codex proves infeasible.

## Consequences

GB10 must implement the exact Responses/tool semantics expected by the released
Codex client. Provider setup is a machine-local installation step because
project configuration cannot define/override provider selection.

## Status

Accepted for whole-session local PoC.

## Evidence

- `docs/research/codex-compatibility.md`
- `docs/requirements.md` URS-CODEX-001/002/004

