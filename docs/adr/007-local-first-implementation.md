# ADR-007: Local-first implementation workflow

## Context

The target Stage 2 flow uses cloud planning/review, GB10 implementation, one
evidence-informed local retry, then cloud implementation fallback without
manual model switching. Installed Codex 0.145.0 can apply `model_provider` from
a custom agent role file, but 0.149.1 omits it and nearby releases have reported
cross-provider assignment-loss bugs.

## Decision

Adopt the local-first orchestration as the target design. Test and pin installed
0.145.0, proving provider selection plus initial/follow-up assignment delivery.
Until that complete canary passes, offer explicit whole-session cloud and GB10
modes. After the gate, implement the retry/fallback flow inside Codex
orchestration and record each provider transition. Block every client upgrade
until it passes the same canary.

## Alternatives

- Upgrade to 0.149.1 and assume model-only role override can switch providers: rejected.
- Manual switching in the normal flow: rejected as Stage 2 acceptance behavior.
- Gateway-level silent fallback: rejected because it hides agent/provider identity.
- Abandon Codex immediately: rejected while an official capability is emerging.

## Consequences

Stage 1 and local coding benchmarks can proceed. Automatic Stage 2 remains
blocked, with a precise pinned-version/canary exit condition rather than a
fabricated implementation. A successful 0.145.0 result creates an upgrade hold
until a newer client is separately qualified.

## Status

Target accepted; activation blocked on URS-CODEX-003 and V-CODEX-VER-001.

## Evidence

- `docs/research/inference-runtime-evaluation.md` Codex-specific consequences
- `docs/research/codex-compatibility.md`
- `docs/verification-strategy.md` Stage 2 verification
