# Coding model evaluation

Verified: 2026-09-04

Status: benchmark shortlist; no winner selected without GB10 measurements

## Recommendation

Benchmark the following in this order for `coding`:

1. `nvidia/Qwen3.6-35B-A3B-NVFP4` to prove the Codex/Responses integration.
2. `Qwen/Qwen3.8-27B-FP8` as the first coding-quality candidate.
3. `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` with its `-DSpark`
   draft as the GB10-specific performance candidate.

Stop the mandatory wave after these three. Test `ornith-ai/Ornith-1.5-35B-A3B`
only if Qwen3.8 leaves a measured coding/agent gap. Muse Glimmer is conditional
on needing a dense/multimodal control, and Qwen3-Coder-Next FP8 is now only an
optional specialist comparison. Devstral Small 2, GLM-4.7-Flash, and GPT-OSS
are outside the normal coding queue. No model becomes an alias target based on
publisher or community benchmarks alone.

## Evidence and trade-offs

| Candidate | Advantages | Disadvantages | Why it remains in the ladder |
| --- | --- | --- | --- |
| Qwen3.6-35B-A3B NVFP4 | Exact NVIDIA one-Spark recipe and known parser/tool path | Older quality baseline | Mandatory integration proof only |
| Qwen3.8-27B FP8 | Current dense Qwen with strong publisher coding/agent results and positive structured-output owner tests | Recent runtime; no exact NVIDIA one-Spark recipe; speed depends heavily on backend | First coding-quality candidate; retain only after repository-level Codex tasks |
| Nemotron 3.5 Lightning + DSpark | 30B total/3B active NVFP4 target, native MTP, a dedicated DSpark draft, and a documented one-Spark low-concurrency recipe; NVIDIA publishes agent/coding results | Danish is absent from the documented language list; OpenMDW-1.1 needs review; target-only, MTP, and DSpark add serving complexity | First hardware-specific challenger; select it if end-to-end build/test success is competitive and accepted draft tokens materially improve latency without regressions |
| Ornith 1.5 35B-A3B | Strong publisher agent/coding results and low active parameter count | Official BF16 is large, needs custom code, lacks an official Spark quantization, and owner reports conflict | Conditional A/B only if Qwen3.8 leaves a measured gap |
| Muse Glimmer 30B NVFP4 | Official dense Spark agent/multimodal control | Overlaps Qwen3.8 and has no Danish claim | Conditional control only |
| Qwen3-Coder-Next FP8 | Coding-specialized 80B/3B-active model | Roughly 80 GB artifact, no first-party Spark path, and published evaluations used BF16 | Optional control after Qwen3.8 and Ornith, not part of the mandatory wave |

Sources: [Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8),
[Nemotron 3.5 Lightning model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
[Ornith 1.5 model card](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B),
[Muse Glimmer model card](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4),
and the [current shortlist refresh](text-model-shortlist-refresh-2026-09-04.md).

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
- For candidates with MTP support, compare MTP on/off and record
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
