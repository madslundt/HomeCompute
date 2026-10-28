# ADR-025: Qualify Flash-Next with a quality baseline and performance challenger

**Status:** Proposed / Qualification Pending  
**Date:** 2026-09-30  
**Supersedes:** no production decision. ADR-023's current Qwen3.6 automation decision and ADR-024's artifact/deployment/route separation remain in force.

## Context

The current production n8n automation model remains
`unsloth/Qwen3.6-35B-A3B-NVFP4` at revision
`739af1e7aac320af1682ed1e0cce369af4c5265d`, served through the existing
`automation-moe` compatibility alias. The owner reports that reasoning helps
the multi-step Aula, offers, and shopping workflows. Production request
metadata and active n8n model-node settings still need a metadata-only live
inspection before the Qwen3.6 baseline is frozen.

Flash-Next is evaluated as a cold-swapped text model on the 128 GiB unified
memory Spark. It must not remain resident alongside Qwen3.8-27B or another large
text model. Qwen3.8-27B remains a useful substantially smaller workhorse/fallback
artifact, but future routing is not decided here. The final speech stack and
co-service capacity need separate physical qualification.

The Blazux recipe using NVIDIA NVFP4 is the quality-oriented Flash-Next
reference. The separate `dime-online/qwen3.8-Flash-DGX-UltraFast` implementation
is a performance challenger using an AutoRound W4A16 routed-expert target,
lower-precision side components, file-backed PLE and optimized MTP. Published
upstream results are useful input, not HomeCompute qualification evidence.

## Decision proposed

Keep NVIDIA's `nvidia/Qwen3.8-Flash-Next-NVFP4` at revision
`fc694b54fb0174e0913e6adf86691ef85a4ead47` using Blazux source commit
`b05e14681325f3cc5bd22e7f48537feeeb0bf266`. The source Dockerfile's vLLM
`v0.30.0` ARM64 base is pinned to digest
`sha256:4864d46625cbc3307623e29ac742030655e27249feba7b97ec925ce4cc4dfb56`.
The final locally built image ID must still be captured on `home-spark` and
attached to every benchmark run. This is the quality baseline and starts with
hybrid mode, native 262144 context, YaRN off,
MTP 2, four sequences, prefix cache on, deterministic top-k on, effort alias
on, `auto`/BF16 KV cache, and the 1024-token long-prefill threshold. Start GPU
memory utilization at 0.68; qualify 0.70 and 0.72 in separate runs only after
the full speech mix is stable. Keep the primary automation setting at thinking
enabled and `reasoning_effort=xhigh`; collect `medium` as a separate diagnostic
tuple. Use the supported `qwen3_coder` tool parser, `qwen3` reasoning parser,
and automatic OpenAI tool choice.

Add UltraFast as a separate operator-on-demand deployment with its own pinned
model, PLE table, source commit, parent image, build inputs, launch patch, and
v16b settings. Its MTP target verification only establishes behavior relative
to the UltraFast target. It does not establish quality parity with the distinct
NVIDIA NVFP4 Blazux target. Keep consumer aliases semantic and use only the
private `automation-qualification` route for A/B qualification. Production
`automation` and `automation-moe` remain on Qwen3.6 unless the owner later
approves a separate cutover.

## Promotion evidence required

- Same deterministic or controlled HomeCompute corpus on the Blazux reference
  and UltraFast challenger, with repeated runs and exact release manifests.
- Hermes agent loops with 15-30 sequential tool calls, exact MCP selection and
  arguments, Danish tasks, realistic coding repair, and 32K/64K/128K context.
- Tool-error and malformed-result recovery, prefix-cache correctness, mixed
  service memory/swap behavior, and an overnight soak on physical GB10 hardware.
- Quality and reliability gates are blocking; throughput cannot offset a
  regression in task completion, exact tool use, structured output, Danish
  extraction, long-context correctness, or recovery.
- Metadata-only verification of the actual Qwen3.6 production reasoning tuple.
- Exact local image ID, runtime configuration, fixture hash, timestamps, and
  sanitized results for every comparison.

Hard gates are zero unauthorized side effects, zero fabricated tool success,
zero fabricated source facts, valid required structured output, passing
security cases, and no critical malformed tool calls. Flash xhigh must match or
exceed the Qwen3.6 reasoning baseline on each required quality dimension. A
critical failure blocks promotion regardless of aggregate scores.

## Rollback

Keep the exact Qwen3.6 artifact cached and its existing deployment available.
The candidate lifecycle records the running text containers, stops only known
text services, leaves speech services alone, and restores the recorded services
if startup or protocol smoke fails. Production route changes and n8n consumer
migration are a later gated operation. No model download is allowed in the
rollback path.

## Consequences

This ADR remains proposed until evidence exists. Current production stays
Qwen3.6. Blazux/NVIDIA is the Flash-Next quality baseline; UltraFast is a
performance challenger and is not production-qualified. Qwen3.8-27B remains a
retained cold fallback. No long-term alias routing is encoded before workload
qualification and owner review.
