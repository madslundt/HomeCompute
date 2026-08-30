# LLM installation recommendation

Verified: 2026-08-25

Status: staged recommendation from the documented benchmark shortlists; no
production winner exists before measurement on the GB10

## Recommended install set

Install and test these artifacts in this order. Treat "installed" as available
for controlled evaluation, not necessarily resident in unified memory at the
same time.

| Priority | Model | Intended role | Recommendation |
| --- | --- | --- | --- |
| 1 | `nvidia/Qwen3.6-35B-A3B-NVFP4` | `automation`, `home`, `meeting`; also exercise `coding` | Install first. NVIDIA publishes a single-DGX-Spark vLLM recipe for this exact efficient artifact. Qualify its native MTP path on/off; Danish, tool, Responses, and mixed-load quality still require local tests. |
| 2 | `Qwen/Qwen3-Coder-Next-FP8` | `coding` | Install as the primary specialized coding candidate. Start its Codex PoC at 32K context rather than assuming its 256K native context is operationally affordable. |
| 3 | `nvidia/Gemma-4-31B-IT-NVFP4` | Dense `automation`/`coding` quality challenger | Stage after the efficient Qwen baseline. Evaluate the compatible Gemma MTP assistant path, Danish, tools, and Codex behavior; do not promote it from publisher or anecdotal quality alone. |
| 4 | `Qwen/Qwen3.8-27B-FP8` | Dense `automation`/`coding` quality challenger | Stage using the separately pinned recent runtime it requires. Do not assume dense 27B inference wins the `home` latency role. |
| 5 | `mistralai/Devstral-Small-2-24B-Instruct-2512` | Low-memory coding baseline; possible small shared or `home` fallback | Install as the operational control for latency and memory headroom. |
| 6 | `openai/gpt-oss-120b` | High-capacity `coding`/`automation` comparison | Stage only after the first five have baseline results; nominal weight fit does not prove mixed-workload memory headroom. |

Sources: [general-model shortlist](general-model-evaluation.md),
[coding-model shortlist](coding-model-evaluation.md), and
[Home Assistant shortlist](home-assistant-model-evaluation.md). The corresponding
publisher facts are in the official
[NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[Qwen3-Coder-Next model card](https://huggingface.co/Qwen/Qwen3-Coder-Next),
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Devstral Small 2 model card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512),
and [gpt-oss-120b model card](https://huggingface.co/openai/gpt-oss-120b).

## Conditional candidates

- Install `zai-org/GLM-4.7-Flash` or `openai/gpt-oss-20b` only if a shared
  Qwen3.6 instance misses the Home Assistant latency or concurrency gate. They
  are compact tool-use comparisons, not documented production selections.
- Install `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` only for the
  NVIDIA-optimized runtime comparison. Its documented post-trained language
  list does not include Danish, so it should not displace Qwen3.6 before Danish
  evaluation.
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

Begin with NVIDIA Qwen3.6 NVFP4 serving `automation`, `home`, and `meeting`,
while Qwen3-Coder-Next is evaluated separately for `coding`. Keep Devstral Small
2 available as the low-memory control, with Gemma 4 31B and Qwen3.8-27B as
dense quality challengers. Do not keep all candidates resident by default.
Benchmark each alone, including MTP on/off where supported, then run the
required mixed load of Home Assistant voice, one Codex generation, one n8n
request, and background meeting transcription. Only promote the exact
model revision, quantization, context limit, parser, runtime image, and launch
flags that pass the role benchmarks and memory gate.

This layout follows the documented workload order: P0 Home Assistant voice, P1
interactive Codex, P2 user-triggered n8n/meeting interaction, P3 scheduled n8n,
and P4 batch meeting work. The architecture
permits one artifact to back several logical aliases, but only after it passes
each role's tests. No publisher benchmark alone selects the production model.

Sources: [architecture scheduling and model-routing design](../architecture.md),
[requirements model-role and capacity gates](../requirements.md), and
[coding benchmark rules](coding-model-evaluation.md).
