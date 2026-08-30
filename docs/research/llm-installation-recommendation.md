# LLM installation recommendation

Verified: 2026-08-30

Status: staged recommendation from the documented benchmark shortlists; no
production winner exists before measurement on the GB10

## Recommended evaluation order

Install and test these artifacts in this order. Treat "installed" as available
for controlled evaluation, not necessarily resident in unified memory at the
same time.

| Priority | Model | Intended role | Why it is in this position |
| --- | --- | --- | --- |
| 1 | `nvidia/Qwen3.6-35B-A3B-NVFP4` | First shared test for `automation`, `research`, `home`, `meeting`, and `assistant`; also exercise `coding` | NVIDIA publishes an exact single-DGX-Spark vLLM recipe, the model activates only 3B of 35B parameters per token, and one artifact can exercise every text integration. The 32K Phase C run is only a smoke test; `assistant` still requires a separate 64K-or-greater Hermes qualification. |
| 2 | `Qwen/Qwen3-Coder-Next-FP8` | Primary specialized `coding` quality candidate | It is publisher-built for long-horizon coding agents and activates 3B of 80B parameters. Start at 32K. The publisher card's displayed evaluations used BF16, so the exact FP8 artifact still needs the repository's coding and Codex tests. |
| 3 | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus its `-DSpark` draft | First GB10-specific coding/agent performance challenger | NVIDIA provides a one-Spark, low-concurrency DSpark recipe and agent/coding evaluations. It moves ahead of Devstral as the hardware-specific performance comparison, but its supported-language list omits Danish and its OpenMDW-1.1 license needs review. Test target-only, native MTP, and DSpark separately. |
| 4 | `nvidia/Gemma-4-31B-IT-NVFP4` | Dense multilingual general/coding quality challenger | It provides a publisher/NVIDIA NVFP4 dense control with broad language, function-calling, coding, and multimodal claims. Dense 31B execution may lose latency and mixed-load headroom to the MoE baseline. |
| 5 | `Qwen/Qwen3.8-27B-FP8` | Newer dense Qwen general/coding quality challenger | It is the newer Qwen generation and isolates model quality from the older Qwen3.6 baseline. It requires its own recent runtime and is FP8 rather than a first-party NVFP4 artifact. |
| 6 | `mistralai/Devstral-Small-2-24B-Instruct-2512` | Lower-resource coding and latency control | It is purpose-built for agentic repository work and is useful as an operational floor. It has no documented first-party Spark NVFP4 path and should not outrank the Spark-specific Nemotron comparison. |
| 7 | `openai/gpt-oss-120b` | Serialized high-capacity reasoning/tool comparison | Its publisher-native MXFP4 weights, 5.1B active parameters, structured output, and tool support make it the first large reasoner to test. Its Harmony protocol and approximately 80GB-class fit leave less mixed-load headroom. |

Sources: [general-model shortlist](general-model-evaluation.md),
[coding-model shortlist](coding-model-evaluation.md), and
[Home Assistant shortlist](home-assistant-model-evaluation.md). The corresponding
publisher facts are in the official
[NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[Qwen3-Coder-Next FP8 model card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8),
[Nemotron 3.5 Lightning model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Devstral Small 2 model card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512),
and [gpt-oss-120b model card](https://huggingface.co/openai/gpt-oss-120b).

## Provisional selection by use case

Every recommendation below is a benchmark hypothesis. The alternative wins
only by passing the same role fixtures and the shared memory/concurrency gate.

| Use case | Provisional recommendation | Credible alternatives and advantages | Main disadvantages | Select an alternative when |
| --- | --- | --- | --- | --- |
| Shared Danish automation, research, meeting summaries, and first `assistant` path | Qwen3.6-35B-A3B NVFP4 | Gemma 4 31B NVFP4 offers a dense, broadly multilingual quality control; Qwen3.8-27B FP8 offers the newer dense Qwen generation | Qwen3.6 has no Danish-specific publisher acceptance result; dense alternatives may cost more latency and memory bandwidth | An alternative materially improves Danish factuality/schema/tool scores and still passes Home Assistant and mixed-load latency/headroom |
| Coding through Codex | Qwen3-Coder-Next FP8 | Nemotron 3.5 Lightning + DSpark has the strongest exact one-Spark performance path; Gemma 4 and Qwen3.8 are dense quality controls; Devstral Small is the lower-resource control; gpt-oss-120b is the large reasoning control | Coder-Next's published card results are not FP8 results; every candidate has a distinct parser/runtime tuple | Another candidate improves build/test plus cloud-review acceptance without failing Responses/tools, interactive latency, or memory gates |
| Home Assistant conversational reasoning | Shared Qwen3.6 first | Qwen3.5-4B is the newer small quality hypothesis; NVIDIA Qwen3-8B/14B NVFP4 are first-party Spark-listed precision controls; gpt-oss-20b is a native MXFP4 tool-use control | No candidate has proven Danish entity/tool accuracy; another resident process adds operational and memory cost | Shared Qwen misses p95 TTFT/concurrency, then a smaller candidate reaches 98% normal tool accuracy, all safety denials, zero hallucinated executions, and the latency target |
| Hermes personal assistant | Qwen3.6 at 64K or greater | Gemma 4 is the multilingual dense quality alternative; Nemotron 3.5 is an English/coding-agent performance alternative | Phase C is only 32K; Nemotron's language list omits Danish; long contexts increase cache pressure | An alternative passes the full Hermes tool, profile, persistence, 64K, and mixed-load suite with a material quality or latency advantage |
| Difficult local reasoning | gpt-oss-120b in publisher-native MXFP4 | Nemotron 3 Super NVFP4 has an exact single-Spark path and NVIDIA agent focus; the shared model avoids a costly model swap | Both large candidates reduce resident headroom; Nemotron's language list omits Danish and its tuple is more specialized | A large candidate produces a meaningful task-quality gain over Qwen3.6 that justifies serialized loading, license, parser, and recovery cost |

The detailed role scorecards remain authoritative. See the
[cross-role trade-off matrix](model-role-tradeoff-matrix.md),
[model/use-case alignment review](model-use-case-alignment-review.md) and
[GB10 precision audit](gb10-optimized-model-audit.md) for the distinction
between model quality, stored precision, and the kernel actually used.

## Conditional and control candidates

- Test `Qwen/Qwen3.5-4B`, `nvidia/Qwen3-8B-NVFP4`,
  `nvidia/Qwen3-14B-NVFP4`, or `openai/gpt-oss-20b` only if the shared model
  misses Home Assistant latency or concurrency. Limit the first fallback round
  to two candidates: Qwen3.5-4B for the newer-small quality hypothesis and one
  NVIDIA NVFP4 model for the Spark-optimized hypothesis.
- Retain `zai-org/GLM-4.7-Flash` as a secondary tool-quality control, not the
  first GB10 fallback: no exact publisher/NVIDIA Spark NVFP4 artifact is
  established for the distinct Flash checkpoint.
- Install `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` only for the
  NVIDIA-optimized runtime comparison. Its documented post-trained language
  list does not include Danish, so it should not displace Qwen3.6 before Danish
  evaluation.
- Use `nvidia/Qwen3.6-27B-NVFP4` only for a focused dense-Qwen
  precision/runtime A/B against Qwen3.8-27B FP8. It adds no new use-case role,
  is not in the current Spark support matrix, and should not expand the
  mandatory quality ladder.
- Defer `mistralai/Devstral-2-123B-Instruct-2512`. The repository labels it a
  stretch candidate whose license and memory headroom may reject it.

Sources: [Home Assistant model evaluation](home-assistant-model-evaluation.md),
[general model evaluation](general-model-evaluation.md), and
[coding model evaluation](coding-model-evaluation.md).

## Hardware and operating constraints

The target has 128 GB unified memory and a 1 TB internal NVMe. Nominal weight
fit is not sufficient: the measured configuration must include the OS/display,
runtime and containers, model weights, KV cache, CUDA workspaces, STT/TTS,
telemetry, page-cache effects, and at least 10% production memory headroom under
mixed load. The initial architecture therefore calls for one shared qualified
text model where possible and only a reserved smaller `home` process if
measurements require it.

Disk planning allows 500 GB for qualified active models and voices, 120 GB for
staging/download cache, and 100 GB of emergency free space. The storage ADR
explicitly rejects retaining every downloaded model. Download candidates
sequentially, pin exact revisions and quantizations, and remove rejected
staging artifacts after results are recorded.

Sources: [requirements URS-PERF-007/008 and URS-OPS-007](../requirements.md),
[architecture model-selection design](../architecture.md),
[design memory and storage design](../design-specification.md), and
[ADR-005](../adr/005-storage-strategy.md).

## Practical initial layout

Begin with NVIDIA Qwen3.6 NVFP4 serving all six text aliases for integration
testing. This does not qualify all six roles: `assistant` requires a later 64K
or greater Hermes run, and each alias retains its own scorecard. Evaluate
Qwen3-Coder-Next separately for coding, then Nemotron 3.5 Lightning target-only,
MTP, and DSpark as the first GB10-specific performance comparison. Keep
Devstral Small as the lower-resource control, with Gemma 4 31B and Qwen3.8-27B
as dense quality challengers. Do not keep all candidates resident by default.

Benchmark each alone, then run the required mixed load of Home Assistant voice,
one Codex generation, one n8n request, and background meeting transcription.
Only promote the exact model revision, weight/activation/KV precision, context
limit, parser, runtime image, backend/kernel path, and speculative-decoding
settings that pass the role benchmarks and memory gate.

This layout follows the documented workload order: P0 Home Assistant voice, P1
interactive Codex, P2 user-triggered n8n/meeting interaction, P3 scheduled n8n,
and P4 batch meeting work. The architecture
permits one artifact to back several logical aliases, but only after it passes
each role's tests. No publisher benchmark alone selects the production model.

Sources: [architecture scheduling and model-routing design](../architecture.md),
[requirements model-role and capacity gates](../requirements.md), and
[coding benchmark rules](coding-model-evaluation.md).
