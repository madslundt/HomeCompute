# Coding model evaluation

Verified: 2026-08-30

Status: benchmark shortlist; no winner selected without GB10 measurements

## Recommendation

Benchmark the following in this order for `coding`:

1. `Qwen/Qwen3-Coder-Next-FP8` as the exact first benchmark artifact from the
   Qwen3-Coder-Next family.
2. `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` with its `-DSpark`
   draft as the first GB10-specific agent-performance challenger.
3. `nvidia/Gemma-4-31B-IT-NVFP4` as the dense general/coding challenger.
4. `Qwen/Qwen3.8-27B-FP8` as the newer dense Qwen challenger.
5. `mistralai/Devstral-Small-2-24B-Instruct-2512` as the low-memory,
   low-latency baseline.
6. `openai/gpt-oss-120b` as the reasoning/tool-use comparison.
7. `mistralai/Devstral-2-123B-Instruct-2512` only if a supported quantization
   leaves enough KV-cache and concurrency headroom.

Qwen3-Coder-Next is recommended first because it is explicitly trained for
long-horizon coding agents, has only 3B active parameters per token, and uses a
standard Qwen tool-call path. That is a quality/compatibility hypothesis, not a
precision result: the evaluations displayed on its publisher card used BF16,
so the exact FP8 artifact must earn the alias. Nemotron follows immediately
because it offers the most concrete one-Spark performance experiment. No model
becomes an alias target based on its publisher benchmark alone.

## Evidence and trade-offs

| Candidate | Advantages | Disadvantages | Why it remains in the ladder |
| --- | --- | --- | --- |
| Qwen3-Coder-Next (`Qwen/Qwen3-Coder-Next-FP8`) | 80B total/3B active, 262K native context, Apache-2.0, built for coding agents and failure recovery; official vLLM launch includes a Qwen tool-call parser | Publisher-card benchmark results were produced with BF16, not the exact FP8 artifact; no exact NVIDIA single-Spark recipe is established | Recommended first for the strongest task alignment and likely active-compute efficiency; start at 32K and require repository-level Codex results |
| Nemotron 3.5 Lightning + DSpark | 30B total/3B active NVFP4 target, native MTP, a dedicated DSpark draft, and a documented one-Spark low-concurrency recipe; NVIDIA publishes agent/coding results | Danish is absent from the documented language list; OpenMDW-1.1 needs review; target-only, MTP, and DSpark add serving complexity | First hardware-specific challenger; select it if end-to-end build/test success is competitive and accepted draft tokens materially improve latency without regressions |
| Gemma 4 31B | Dense 30.7B, 256K context, Apache-2.0, native function calling, coding/agentic focus, and an MTP assistant checkpoint | Dense execution may reduce latency and mixed-load headroom versus 3B-active MoE candidates | Quality control that tests whether dense-model consistency outweighs the resource cost |
| Qwen3.8-27B | Dense 27B, 262K context, Apache-2.0, newer Qwen coding/agent generation with native MTP | Requires a recent pinned runtime, is FP8 rather than a first-party NVFP4 artifact, and has no current single-Spark proof | New-generation Qwen control; select it only for a material task-quality gain that survives the operational gates |
| Devstral Small 2 | 24B, 256K context, Apache-2.0, designed for agentic repository work; publisher says it can run on a 32 GB Mac | Coding specialization does not prove Codex Responses compatibility or superior patch quality; no first-party Spark NVFP4 path | Lower-resource floor and possible fallback when interactive latency or voice-load coexistence dominates |
| gpt-oss-120b | 117B/5.1B active in publisher-native MXFP4, Apache-2.0, function calling and structured outputs | Approximately 80GB-class weight fit leaves less headroom; Harmony formatting and Codex Responses behavior require validation | Large reasoning control, loaded serially; select only if quality gains justify the swap and memory cost |
| Devstral 2 123B | FP8 123B, 256K context and tool-oriented coding | Restrictive/other model license and weak unified-memory headroom | Deferred stretch candidate; test only after license and fit checks pass |

Sources: [Qwen3-Coder-Next FP8 model card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8),
[Nemotron 3.5 Lightning model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Devstral Small 2 model card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512),
[gpt-oss-120b model card](https://huggingface.co/openai/gpt-oss-120b),
[Devstral 2 model card](https://huggingface.co/mistralai/Devstral-2-123B-Instruct-2512).

## Required scorecard

| Criterion | Weight |
| --- | ---: |
| Accepted by cloud review without reimplementation | 30% |
| Codex and tool-call compatibility | 20% |
| Build/test pass rate | 15% |
| Review finding severity | 10% |
| Time to first token and interactive latency | 10% |
| Throughput | 5% |
| Peak unified memory and KV-cache headroom | 5% |
| License and operational fit | 5% |

## Benchmark rules

- Run the same bounded .NET, Python, Vue 3, and React/TypeScript tasks with a
  pinned runtime, prompt, context limit, quantization, and tool schema.
- Record full-build and targeted-test outcomes; HumanEval-style generation is
  not a substitute.
- Measure cold load, warm TTFT, tokens/s, peak unified memory, context growth,
  tool-call validity, retries, and cloud-review findings.
- For Qwen and Gemma candidates with MTP support, compare MTP on/off and record
  accepted draft tokens plus end-to-end task time. Compare Q4/Q8/NVFP4 only as
  explicit artifact tuples; do not infer quality or speed from bit count alone.
- Repeat each task at least three times. A one-off successful patch is not a
  reliable model selection signal.
- Test alone and under simultaneous `automation` and Home Assistant voice
  traffic.

## Rejection rules

Reject a candidate if it cannot complete the Codex Responses/tool loop, if its
license is unacceptable, if it cannot leave operational memory headroom, or if
more than the agreed fraction of representative tasks require cloud
reimplementation. Thresholds belong in the design/verification phase.
