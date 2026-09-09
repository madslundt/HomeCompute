# ADR-007: Local-first implementation workflow

## Context

The possible later Stage 2 flow uses cloud planning/review, GB10
implementation, one evidence-informed local retry, then cloud implementation
fallback without manual model switching. Installed Codex 0.145.0 can apply
`model_provider` from a custom agent role file, but 0.149.1 omits it and nearby
releases have reported cross-provider assignment-loss bugs. Local coding must
also prove useful on real work before automation is considered.

## Decision

Begin with explicit whole-session Cloud and GB10 Local modes; Cloud is the
default where committed repository metadata permits it. Unknown repositories
default to `local_only`; ordinary private repositories may be explicitly
`cloud_allowed`. Run at least 20 representative local coding tasks and require
at least 70% to complete with passing verification, no cloud reimplementation,
at most one local retry, and no serious cloud-review defect. Crossing this gate
only makes automation eligible for consideration and does not activate it.

If automation is later approved, also test and pin the installed client,
proving provider selection plus initial/follow-up assignment delivery. Block
every client upgrade until it passes the same canary.

## Alternatives

- Upgrade to 0.149.1 and assume model-only role override can switch providers: rejected.
- Manual switching in the normal flow: rejected as Stage 2 acceptance behavior.
- Gateway-level silent fallback: rejected because it hides agent/provider identity.
- Abandon Codex immediately: rejected while an official capability is emerging.

## Consequences

Stage 1 and explicit local coding trials can proceed. Automatic Stage 2 remains
disabled until both the real-task evidence gate and the versioned canary pass,
followed by a separate human promotion decision. A successful 0.145.0 canary
creates an upgrade hold until a newer client is separately qualified.

## Status

Explicit trial accepted; automatic activation disabled pending the real-task
gate, URS-CODEX-003, V-CODEX-VER-001, and a separate promotion decision.

## Evidence

- `docs/research/inference-runtime-evaluation.md` Codex-specific consequences
- `docs/research/codex-compatibility.md`
- `docs/verification-strategy.md` Stage 2 verification
