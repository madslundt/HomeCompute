# ADR-019: Final single-GX10 model roster

## Context

The ASUS Ascent GX10 is a 128 GiB unified-memory GB10 appliance dedicated
primarily to local inference. Frontier models plan and review difficult work;
the local text model normally executes well-defined implementations and serves
n8n, Hermes, Home Assistant, structured output, and tool calls.

The earlier ADR left the everyday lane undecided between Nemotron Lightning and
Qwen3.8-27B and retained provisional Whisper/Piper modality services. The owner
has now selected the final checkpoint set. Checkpoint selection is final, but
runtime qualification on the physical GX10 remains an evidence gate.

## Decision

Use `unsloth/Qwen3.8-27B-NVFP4` as the always-available production workhorse.
Prefer non-thinking mode for already-specified implementation and routine tool
work.

Support two mutually exclusive serving profiles for the same checkpoint:

1. Establish the baseline with vLLM and the model's native `qwen3_5_mtp` head.
2. Add `incoai/Qwen3.8-27B-DFlash2` under SGLang and retain it as the normal
   profile only if real workload benchmarks show material improvement without
   regressions in stability, tools, JSON, context, or concurrency.

The DFlash checkpoint is a speculative drafter, not a client-visible model.
Do not substitute the Syvai W4A16 drafter unless a measured memory constraint
requires it.

Use `RadixArk/Qwen3.8-Flash-Next-NVFP4` with the pinned
`blazux/qwen3.8-Flash-DGX` recipe as an operator-controlled heavy mode. Stop the
27B service before starting Flash-Next, and restart the 27B service after the
heavy session. LiteLLM must not dynamically load either checkpoint per request.

The speech selection below is historical and is superseded by ADR-021:

- Danish STT: `syvai/hviske-v5.3`;
- English STT: `nvidia/parakeet-tdt-0.6b-v2`;
- Danish TTS: `syvai/plapre-nano-v2`;
- English TTS: `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`.

The full Hviske model is accepted for personal, non-commercial use. Its
CC BY-NC 4.0 restriction must be revisited before any commercial use.

## Deployment sequence

1. Deploy Qwen3.8-27B with native MTP on vLLM.
2. Record a workload baseline.
3. Deploy the SGLang/DFlash profile.
4. Compare both profiles on coding wall time, generation throughput, TTFT,
   long context, tool and JSON correctness, concurrency, memory, and soak
   stability.
5. Keep the better qualified 27B runtime as normal production.
6. Add the two STT and two TTS services.
7. Add Flash-Next last and verify the serialized cold-swap procedure.

## Consequences

The retained general-purpose text roster is exactly two checkpoints, with at
most one resident at a time. All semantic LiteLLM aliases resolve to the 27B
workhorse during normal mode. Heavy mode changes the active service behind the
stable boundary only through an operator lifecycle action.

The repository may record a selected checkpoint before declaring it live
qualified. Routing remains fail-closed until the selected vLLM tuple passes the
physical-host gates. SGLang, speech, and heavy-mode profiles require their own
immutable runtime image pins and tests; model selection alone is not a safe
runtime recipe.

Do not install additional general text models without a measured gap and a
reviewed change. ADR-021 owns the current speech roster and exclusions.

## Status

Accepted for text; speech portion superseded by ADR-021. Live runtime
qualification remains pending.

## Evidence

- `config/gb10-model-roster.json`
- `docs/research/gb10-model-installation-recommendation-2026-09-07.md`
- Publisher model cards and immutable repository revisions recorded in the
  roster
