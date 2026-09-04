# LLM installation recommendation

Verified: 2026-09-04

Status: staged recommendation from current publisher evidence and reproducible
DGX Spark owner tests. No production winner exists before measurement on the
GB10.

## Recommended evaluation order

Install only the three-model mandatory wave. Treat "installed" as available
for controlled evaluation, not resident in unified memory at the same time.

| Priority | Model | Intended role | Why it is in this position |
| --- | --- | --- | --- |
| 1 | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Integration and single-Spark baseline for all text aliases | NVIDIA provides a small official artifact and exact one-Spark recipe. Use it to prove the gateway, parsers, tools, Responses API, recovery, and benchmark harness—not as the presumed quality winner. |
| 2 | `Qwen/Qwen3.8-27B-FP8` | First general, Danish, coding, and agent quality candidate | It is a newer dense 27B Qwen release with strong current publisher coding/agent evidence and a roughly 31 GB official FP8 artifact. Owner tests also show good coding and structured-output behavior, but large runtime-dependent speed differences. Start at 32K and qualify the exact tuple. |
| 3 | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus `-DSpark` draft | GB10 latency and speculative-decoding candidate | NVIDIA provides an exact one-Spark target/MTP/DSpark path. Owner measurements show very high decode speed, but strict-format reliability and long reasoning budgets still require local qualification. Test target-only, MTP, and DSpark separately. |

Stop the mandatory wave after these three. Add a model below only to answer a
specific unresolved question:

| Conditional candidate | Test only when | Disposition |
| --- | --- | --- |
| `ornith-ai/Ornith-1.5-35B-A3B` | Qwen3.8 leaves a measured coding/agent gap | Targeted coding A/B. Publisher benchmarks are promising, but owner reports are mixed and there is no publisher/NVIDIA one-Spark quantized recipe. |
| `nvidia/Muse-Glimmer-30B-NVFP4` | A dense, multimodal, or alternative agent control is still needed | Publisher-supported Spark control; retain only if it adds a role not covered by Qwen3.8 or Nemotron. |
| `nvidia/Gemma-4-31B-IT-NVFP4` | The mandatory wave misses Danish/multilingual fixtures | Multilingual knowledge and dense-behavior control only. |
| `Qwen/Qwen3-Coder-Next-FP8` | A coding-specialized control remains valuable after Qwen3.8 and Ornith | Optional; its 80 GB-class artifact and four-GPU publisher serving example make it less attractive for initial Spark work. |
| `openai/gpt-oss-120b` | A one-time Harmony/MXFP4 protocol comparison is explicitly desired | Optional one-time control; do not retain in the normal install queue. |

The evidence and direct links are in the
[current shortlist refresh](text-model-shortlist-refresh-2026-09-04.md) and the
[GPT-OSS replacement assessment](gpt-oss-replacement-assessment.md).

## Remove or defer

- Remove `openai/gpt-oss-20b`; it has no distinct role after Qwen3.8 and
  Nemotron. Narrow community tests can still score it well, but that does not
  justify its separate Harmony integration surface.
- Remove `openai/gpt-oss-120b` from the normal queue; preserve only the optional
  one-time control above.
- Remove `mistralai/Devstral-Small-2-24B-Instruct-2512`; Mistral Small 4 has
  superseded the family role.
- Remove `zai-org/GLM-4.7-Flash`; it lacks an exact first-party Spark path and
  adds no role that outranks the mandatory wave.
- Do not install official Qwen3.8 Flash-Next, DeepSeek V4, GLM-5.3, Kimi K3, or
  current MiniMax frontier artifacts on one GB10. Their official artifacts do
  not fit the production envelope with runtime, KV cache, other services, and
  the required recovery headroom.
- Treat the community one-Spark Flash-Next conversion as a lab experiment only.
  It demonstrates technical execution, but uses a patched runtime and a
  community quantization, and does not meet the repository's headroom or
  mixed-load qualification requirements.

Watch `mistralai/Mistral-Small-4-119B-2603-NVFP4`,
`IFM/K2-Horizon-MoVA-36B-A4B`, and `ibm-granite/granite-4.2-30b-nvfp4` for a
publisher-supported one-Spark path or compelling independent results. Do not
expand the first wave merely because they fit nominally or top one benchmark.

## Provisional selection by use case

Every recommendation is a benchmark hypothesis. An alternative wins only by
passing the same role fixtures and the shared memory/concurrency gate.

| Use case | First candidate | Switch condition |
| --- | --- | --- |
| Shared Danish automation, research, meeting summaries, and assistant | Qwen3.8-27B after Qwen3.6 integration bring-up | Use Gemma only if it materially improves Danish factuality, entity handling, or multilingual quality while preserving tools and latency. |
| Coding through Codex | Qwen3.8-27B | Try Ornith only after a measured coding gap; try Coder-Next only if both fail and the larger artifact is justified. |
| Home Assistant conversational reasoning | Qwen3.6 first, then the best qualified shared model | Add a dedicated small model only if the shared winner misses p95 latency/concurrency while a smaller candidate meets tool and safety gates. |
| Hermes personal assistant | Best qualified shared model at 64K or greater | Switch only for a material gain on Hermes tools, profile/persistence, long context, and mixed load. |
| High-throughput local agents | Nemotron 3.5 Lightning | Keep it only if target-only/MTP/DSpark speed survives tool correctness, strict schema, Danish, and output-budget tests. |

The detailed scorecards remain authoritative: see the
[cross-role trade-off matrix](model-role-tradeoff-matrix.md),
[model/use-case alignment review](model-use-case-alignment-review.md), and
[GB10 precision audit](gb10-optimized-model-audit.md).

## Hardware and operating constraints

The target has 128 GB unified memory and a 1 TB internal NVMe. Nominal weight
fit is insufficient: the measured configuration must include OS/display,
runtime and containers, model weights, KV cache, CUDA workspaces, STT/TTS,
telemetry, page-cache effects, and at least 10% production memory headroom under
mixed load. Prefer one shared qualified text model and add a resident smaller
`home` process only if measurement requires it.

Disk planning allows 500 GB for qualified active models and voices, 120 GB for
staging/download cache, and 100 GB of emergency free space. Download candidates
sequentially, pin exact revisions and quantizations, record rejected results,
and remove rejected staging artifacts.

Sources: [requirements URS-PERF-007/008 and URS-OPS-007](../requirements.md),
[architecture model-selection design](../architecture.md),
[design memory and storage design](../design-specification.md), and
[ADR-005](../adr/005-storage-strategy.md).

## Qualification rule

For every run, pin and record `(DGX OS, driver, container digest, runtime,
model revision, precision, context, parser, template, flags)`. Benchmark each
candidate alone, then under the required mixed load of Home Assistant voice,
one Codex generation, one n8n request, and background meeting transcription.
Only promote the exact tuple that passes role quality, tool/schema correctness,
latency, memory headroom, and recovery tests. Publisher leaderboards and
community tok/s reports nominate experiments; neither selects production.

Sources: [architecture scheduling and model-routing design](../architecture.md),
[requirements model-role and capacity gates](../requirements.md), and
[coding benchmark rules](coding-model-evaluation.md).
