# ADR-018: Support multiple developer harnesses on `home-core`

## Context

ADR-006 selected Codex as the only normal developer-facing harness. Subsequent
evaluation showed that Pi's focused single-agent workflow and OMP's provider
routing, LSP/debugging, and isolated subagent workflows are useful complements.
The desired operating boundary is the dedicated host account and worktree, not
exclusive use of one client.

## Decision

Support Codex, Pi, and OMP as developer harness options. Pi is the default for
the current spec-driven single-agent loop, OMP is available for work needing
its integrated tools or fan-out, and Codex remains the reference for Responses
API compatibility and sandbox-sensitive work.

All harnesses run as the unprivileged `agent` account on `home-core`, use one
top-level worktree per concurrent task, and receive no sudo, Docker socket,
production-state, or production-secret access. Harness credentials and session
state remain private to that account. Adding another harness or remotely
reachable harness service requires a separate security and operations review.

## Alternatives

- Keep Codex exclusive: rejected because it unnecessarily excludes useful
  provider-neutral and integrated workflows.
- Pick one permanent replacement: rejected until matched repository benchmarks
  demonstrate that one harness dominates the others for every workflow.
- Run harnesses on `home-spark`: rejected by the inference-only appliance boundary.

## Consequences

The host carries more pinned dependencies and authentication state. Acceptance
must verify account isolation, resource limits, session recovery, and matched
repository tasks for each enabled harness. Codex compatibility remains an API
gate even when another harness performs implementation.

## Status

Accepted.

## Evidence

- `docs/research/agent-harness-comparison-2026-09-05.md`
- `docs/research/omp-remote-execution-architecture-review-2026-09-05.md`
- `docs/agent-harness-operations.md`
