# ADR-019: Bound the single-GB10 model roster

## Context

Earlier planning kept several general text, speech, and retrieval candidates in
view while the GB10 runtime was being established. Current owner reports and
single-Spark recipes provide enough evidence to narrow the expensive text
evaluation, but they do not replace workload qualification. Keeping every
plausible checkpoint would consume storage, duplicate roles, and make aliases
drift toward unbenchmarked defaults.

The target is one NVIDIA GB10 with 128 GiB unified memory. Models installed on
the Mac are outside this decision.

## Decision

Retain at most two production text models and run at most one text model at a
time:

1. Use `RadixArk/Qwen3.8-Flash-Next-NVFP4` with the pinned
   `blazux/qwen3.8-Flash-DGX` recipe as the scheduled quality lane for difficult
   coding, research synthesis, and corpus cross-referencing.
2. Benchmark `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` against
   `RadixArk/Qwen3.8-27B-NVFP4` on identical local workloads. Retain one as the
   everyday assistant/tool/automation model and remove the loser after the
   rollback window.

Until that benchmark records a winner and the exact runtime tuples pass their
gates, the router has no default, no bound aliases, and no authorized model
selection. Benchmark runners address candidate runtimes directly. A later
reviewed change may bind the logical aliases from ADR-004.

Use the speech and retrieval models in `config/gb10-model-roster.json` only
under their stated activation conditions. They do not relax the one-resident
text-model limit.

## Alternatives

- Keep Nemotron and Qwen3.8-27B permanently: rejected because they compete for
  the same everyday role.
- Use Flash-Next for every request: rejected until latency, startup, memory,
  and routine-task efficiency are measured on this GB10.
- Retain DeepSeek V4 Flash as another quality model: rejected because it
  overlaps Flash-Next and current operational evidence is stronger on paired
  GB10 systems.
- Reuse NVIDIA's similarly named Flash-Next checkpoint with the `blazux`
  recipe: rejected because it is a distinct artifact without the same pinned
  single-GB10 evidence.
- Keep Qwen3.6 as the everyday fallback: rejected from the retained roster; it
  remains only as a legacy integration tuple until a replacement runtime is
  qualified.

## Consequences

The GB10 needs separate immutable launch profiles and serialized transitions
between everyday and deep-work text models. A failed or incomplete benchmark
leaves routing disabled rather than selecting a candidate by assumption.
Speech, embeddings, reranking, and diarization remain conditional services and
must pass their own Danish, privacy, and mixed-load gates.

The roster validator and router tests enforce model identities, conditional
dispositions, required exclusions, the pending-qualification fail-closed state,
and the one-resident text-model limit.

## Status

Accepted.

## Evidence

- `docs/research/gb10-model-installation-recommendation-2026-09-07.md`
- `docs/research/llm-installation-recommendation.md`
- `docs/research/model-role-tradeoff-matrix.md`
- `config/gb10-model-roster.json`
