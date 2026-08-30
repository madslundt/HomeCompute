# ADR-002: Primary text inference runtime

## Context

The runtime must support GB10, Codex Responses/SSE/tools, model breadth,
observability, reproducibility, and acceptable performance. vLLM,
TensorRT-LLM, llama.cpp, and Ollama all have Spark paths but different protocol
and operational maturity.

## Decision

Use a pinned NVIDIA-compatible vLLM container for the first text PoC. Run
llama.cpp as the required GGUF/quantized baseline. Benchmark TensorRT-LLM
against the same winning model only when that exact revision is supported.
Keep Ollama for exploration only; treat SGLang and MLX serving as conditional
experiments rather than initial production candidates.

## Alternatives

- TensorRT-LLM primary: deferred until its measured gain justifies model/parser complexity.
- llama.cpp primary: retained as required baseline/fallback; translated
  Responses/tool semantics still need qualification.
- Ollama primary: rejected for the critical path due to observability and current streaming risk.

## Consequences

The deployed artifact must pin the NVIDIA image, model/template/parser tuple.
The decision is not production-final until direct Codex compatibility and
same-workload GB10 benchmarks pass.

## Status

Proposed; Phase C/D gate open.

## Evidence

- `docs/research/inference-runtime-evaluation.md`
- `docs/research/codex-compatibility.md`
