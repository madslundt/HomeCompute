# ADR-023: Opt-in n8n automation MoE

## Context

Qwen3.8-27B is the normal text workhorse and currently backs the `automation`
alias. The owner wants an MoE candidate for Danish n8n workflows, especially
structured output and tool calling, without making Qwen3.8 Flash-Next a
default service.

The owner selected NVIDIA's ModelOpt NVFP4 artifact as the next automation
candidate after reviewing Spark Arena and same-Spark user benchmarks. A user
report found higher single-session decode than Unsloth, while NVIDIA's tracker
also has a malformed-call report at 46 tools on vLLM 0.24 with the older
`qwen3_xml` parser. That report does not establish failure with HomeCompute's
`qwen3_coder` parser, but reinforces the need for direct qualification.

## Decision

Stage pinned `nvidia/Qwen3.6-35B-A3B-NVFP4` revision
`1355db6a052410cfd62085d94b58866fd0f2c3c5` as a separate opt-in candidate
under `automation-moe-nvidia`. Keep the currently qualified Unsloth revision
`739af1e7aac320af1682ed1e0cce369af4c5265d` and the active `automation-moe`
route unchanged as rollback. The ordinary `automation` alias continues to use
Qwen3.8-27B.

Use the pinned vLLM 0.28.0 ARM64 image and NVIDIA's GB10 NVFP4 recipe: 128K
context, 0.5 GPU memory utilization, FP8 KV cache, FlashInfer attention,
Marlin MoE backend, `qwen3` reasoning parser, `qwen3_coder` tool parser, and
three-token MTP using Triton. This is isolated from the shared vLLM image used
by other services. MTP-off remains a separate comparison tuple so the checkpoint
comparison can distinguish quantization from the runtime recipe.

Keep one resident automation text model. NVIDIA qualification activation records
whether the qualified Unsloth MoE or Qwen3.8 workhorse is running, stops that
source, starts the NVIDIA candidate, and restores the recorded source on smoke
failure or deactivation. The NVIDIA candidate is excluded from default startup.
Qwen3.8 Flash-Next remains a separate operator-exclusive heavy mode and is
never part of default startup.

Promotion to `automation` requires Danish and code-switching review,
schema-constrained output, tool selection/arguments/results/errors, at least 64
exposed tool definitions, redacted real n8n replay, prompt-injection and
idempotency checks, direct and gateway protocol tests, and measured GB10
memory/latency/recovery evidence. Promote only if it improves accepted-task
success or latency without reducing tool correctness or Danish quality.

## Consequences

The retained text set grows to three checkpoints, but only one automation text
model is resident. NVIDIA consumes disk only after an explicit staged install
and GPU memory only during an operator cold swap. The candidate has a separate
`automation-moe-nvidia` alias; existing n8n aliases are not retargeted during
qualification.

The final four speech checkpoints remain selected and independently staged;
their incompatible runtime and voice/license gates are not weakened to make
room for this text candidate.

## Status

NVIDIA is recorded in the catalog and roster as the next qualification
candidate. It is not deployed or qualified yet. The current Unsloth deployment
and routes remain the known-good rollback target.

The 2026-09-26 Unsloth qualification passed authenticated model discovery, Danish
generation, an exact-schema Danish tool call, automatic selection of tool 47
from 64 definitions, streaming Responses API completion, request-content log
redaction, and restoration of Qwen3.8 after the cold swap. Gateway qualification
remains pending until the dedicated `home-core` link is physically available.

An inactive n8n workflow, `Local MoE — Danish tool-call qualification`
(`3gx5hnQD65rXTe6Y`), is staged in the personal n8n project. It calls
`https://ai.home.arpa/v1/chat/completions` with model `automation-moe` and a
Danish function schema, then exposes the selected tool call for inspection.
Its graph passes n8n validation and a pin-data execution. It is intentionally
unpublished and has no credential assigned: configure the separate
`HomeCompute LiteLLM bearer` templated credential only after the private link
and gateway route are healthy. Existing production workflows remain unchanged.

## Evidence

- `config/gb10-model-roster.json`
- `docs/research/n8n-danish-moe-recommendation-2026-09-26.md`
- `deploy/compute-node/compose.yaml`
- `scripts/setup-compute-automation-moe.sh`
