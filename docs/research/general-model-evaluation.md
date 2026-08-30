# General model evaluation

Verified: 2026-08-30

Status: benchmark shortlist; Danish and structured-output measurements pending

## Recommendation

Start `automation`, `research`, and `meeting` evaluation with:

1. `nvidia/Qwen3.6-35B-A3B-NVFP4` for the Spark-qualified efficient
   multilingual/tool-use baseline, tested with MTP on and off.
2. `nvidia/Gemma-4-31B-IT-NVFP4` as the dense general/coding quality
   challenger, including its compatible MTP assistant path.
3. `Qwen/Qwen3.8-27B-FP8` as the newer dense Qwen challenger.
4. `openai/gpt-oss-120b` for higher-capacity reasoning and structured output.
5. `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` as the NVIDIA-optimized
   high-capacity GB10 comparison.

The winning shared model may also satisfy `home` initially if its latency and tool
accuracy are acceptable. This consolidation is preferred over keeping another
model resident for architectural neatness.

Qwen3.6 is the provisional recommendation because it combines an exact
NVIDIA-published single-Spark recipe, NVFP4 weights, low active parameter count,
long context, tools, and a shared runtime path. The recommendation is not a
claim that it is best at Danish: no shortlisted model has publisher evidence
that settles Danish factuality or structured-output acceptance. The dense
Gemma and Qwen3.8 candidates remain mandatory quality controls for that reason.

## Alternatives, advantages, and switch conditions

| Candidate | Advantages | Disadvantages | Select it over Qwen3.6 when |
| --- | --- | --- | --- |
| Qwen3.6-35B-A3B NVFP4 | Exact one-Spark recipe; 35B total/3B active; native MTP, tools, reasoning, long context, and one artifact for several aliases | No Danish-specific acceptance evidence; MoE routing and speculative decoding add tuple-specific runtime variables | It remains the default if it clears all role scorecards and gives the best combined quality, latency, and mixed-load headroom |
| Gemma 4 31B NVFP4 | Dense broadly multilingual quality control; function calling, coding/agentic focus, and compatible MTP assistant path | Dense 30.7B execution may be slower and leave less concurrency headroom than a 3B-active MoE model | Danish factuality, extraction, or schema reliability improves materially and the dense cost still passes latency and memory gates |
| Qwen3.8-27B FP8 | Newer dense Qwen generation with native MTP and long context | Recent runtime requirement, FP8 instead of a first-party NVFP4 artifact, and no exact single-Spark recipe established | New-generation quality improves the shared scorecard enough to justify its separate runtime and dense resource cost |
| gpt-oss-120b MXFP4 | Publisher-native low-precision 117B/5.1B-active reasoner with tools and structured outputs | Approximately 80GB-class fit, Harmony protocol, and model swaps reduce mixed-load simplicity | Difficult reasoning or research synthesis improves enough to justify serialized loading; it is not the default resident shared model |
| Nemotron 3 Super NVFP4 | Exact one-Spark minimum configuration and NVIDIA agent/tool focus | Danish is absent from the documented post-trained language list; 120B/12B-active class reduces headroom; specialized parser/license tuple | Agent throughput or difficult reasoning materially beats the shared model and Danish is not required for that promoted workload |

`nvidia/Qwen3.6-27B-NVFP4` is an optional precision/runtime A/B against
Qwen3.8-27B FP8, not another mandatory quality tier. It adds no distinct role
and is not currently listed in NVIDIA's generic Spark support matrix.

## Evidence

- Qwen3.6-35B-A3B is 35B total/3B active with 262K native context. NVIDIA
  publishes an NVFP4 derivative and a single-DGX-Spark vLLM command with native
  MTP, tool, reasoning, and KV-cache settings. Aggregate multilingual evidence
  still does not establish Danish acceptance.
- Gemma 4 31B is a 30.7B dense, 256K-context text/image model with native
  function calling, coding/agentic capabilities, and an MTP assistant
  checkpoint. Its publisher evidence makes it a credible quality challenger,
  not a measured GB10 winner.
- Qwen3.8-27B is the newer dense Qwen generation. Its recent runtime
  requirements and lack of a first-party single-Spark recipe make it a staged
  challenger rather than the first appliance smoke test.
- gpt-oss-120b is 117B/5.1B active in MXFP4 and supports function calling and
  structured outputs. Its publisher states that it fits an 80 GB accelerator,
  which suggests—without proving—room on a 128 GB unified-memory GB10.
- Nemotron 3 Super NVFP4 explicitly lists one DGX Spark as its minimum GPU
  configuration and targets tool use and high-volume agentic workloads. Its
  post-trained supported-language list does not include Danish, so it is a
  performance/control candidate rather than the assumed Danish winner.

Sources: [NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[gpt-oss-120b model card](https://huggingface.co/openai/gpt-oss-120b),
[Nemotron 3 Super NVFP4 model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4).

## Workload fixture groups

- Danish and English summarization with factuality checks.
- Danish extraction and classification with accented names and local date/time
  conventions.
- Strict JSON Schema output, malformed-input recovery, and no-extra-text tests.
- Research synthesis over pinned Tavily-like result fixtures.
- Comparison of new facts with a pinned prior Notion-state fixture.
- Long-context degradation and prompt-injection resistance.
- n8n concurrency and background throughput.
- Existing n8n Aula workflow samples, handled as sensitive test data with
  redacted/synthetic fixtures and no prompt logging. Aula is not a separate
  consumer or deployment target.

## Selection measures

Measure task accuracy, unsupported-claim rate, schema validity, Danish human
rating, TTFT, throughput, peak memory, concurrency degradation, and recovery
from invalid upstream data. Store the exact model revision, runtime image,
quantization, prompt version, and fixture commit with each result.
