# ADR-023: Opt-in n8n automation MoE

## Context

Qwen3.8-27B is the normal text workhorse and currently backs the `automation`
alias. The owner wants an MoE candidate for Danish n8n workflows, especially
structured output and tool calling, without making Qwen3.8 Flash-Next a
default service.

NVIDIA publishes a single-Spark Qwen3.6-35B-A3B NVFP4 recipe, but an unresolved
issue in NVIDIA's playbook repository reports malformed tool syntax with 46
tools on that exact ModelOpt checkpoint. The same reporter's GB10 comparison
passed with the Unsloth NVFP4 artifact. This is sufficient to avoid selecting
the NVIDIA quant for a tool-first role until independently reproduced.

## Decision

Retain pinned `unsloth/Qwen3.6-35B-A3B-NVFP4` revision
`739af1e7aac320af1682ed1e0cce369af4c5265d` as the opt-in automation MoE
candidate. Serve it only as `automation-moe` during qualification. The ordinary
`automation` alias continues to use Qwen3.8-27B.

Use the pinned NVIDIA vLLM 26.08 image, 64K context, FP8 KV cache,
native `cutlass` NVFP4 MoE backend, portable `triton` for the mixed FP8
expert path, `qwen3` reasoning parser, `qwen3_coder` tool parser, and disabled
thinking. The separate FP8 override is required because vLLM applies the MoE
selector to both quantization paths. The pinned image's FlashInfer CUTE-DSL
and TRT-LLM kernels reject its Spark CUDA target, while the publisher's
suggested `b12x` kernel was rejected during live warm-up because this image
accepts its compressed-tensors source only in W4A16 mode. Keep
native MTP disabled for the correctness baseline; enabling it creates a
separate tuple and benchmark.

Keep one resident text model. Activation explicitly stops Qwen3.8-27B, starts
the MoE, and restores Qwen3.8 on failure or deactivation. Do not autostart or
dynamically load the MoE. Qwen3.8 Flash-Next remains a separate
operator-exclusive heavy mode and is never part of default startup.

Promotion to `automation` requires Danish and code-switching review,
schema-constrained output, tool selection/arguments/results/errors, at least 64
exposed tool definitions, redacted real n8n replay, prompt-injection and
idempotency checks, direct and gateway protocol tests, and measured GB10
memory/latency/recovery evidence. Promote only if it improves accepted-task
success or latency without reducing tool correctness or Danish quality.

## Consequences

The retained text set grows to three checkpoints, but the resident limit stays
one. The model consumes disk only after an explicit staged install and GPU
memory only during an operator cold swap. n8n can opt into `automation-moe`
during evaluation without silently changing existing workflows.

The final four speech checkpoints remain selected and independently staged;
their incompatible runtime and voice/license gates are not weakened to make
room for this text candidate.

## Status

Staged and directly qualified on `home-spark`; not promoted to `automation`.

The 2026-09-26 qualification passed authenticated model discovery, Danish
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
