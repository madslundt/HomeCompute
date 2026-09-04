# General model evaluation

Verified: 2026-09-04

Status: benchmark shortlist; Danish and structured-output measurements pending

## Recommendation

Start `automation`, `research`, and `meeting` evaluation with:

1. `nvidia/Qwen3.6-35B-A3B-NVFP4` for integration and the known-good
   single-Spark baseline, tested with MTP on and off.
2. `Qwen/Qwen3.8-27B-FP8` as the first general, Danish, and agent quality
   candidate.
3. `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` in target-only, MTP,
   and DSpark modes as the performance candidate.

The winning shared model may also satisfy `home` initially if its latency and tool
accuracy are acceptable. This consolidation is preferred over keeping another
model resident for architectural neatness.

Qwen3.6 is the bring-up recommendation because it has an exact NVIDIA-published
single-Spark recipe. Qwen3.8 is the first production-quality hypothesis. Test
Gemma 4 only if the mandatory wave leaves a Danish/multilingual gap, and Muse
Glimmer only if a dense/multimodal control is still required. GPT-OSS-20B and
120B, Devstral Small 2, and GLM-4.7-Flash are outside the normal queue.

## Alternatives, advantages, and switch conditions

| Candidate | Advantages | Disadvantages | Select it over Qwen3.6 when |
| --- | --- | --- | --- |
| Qwen3.6-35B-A3B NVFP4 | Exact one-Spark recipe; 35B total/3B active; native MTP, tools, reasoning, long context, and one artifact for several aliases | No Danish-specific acceptance evidence; MoE routing and speculative decoding add tuple-specific runtime variables | It remains the default if it clears all role scorecards and gives the best combined quality, latency, and mixed-load headroom |
| Qwen3.8-27B FP8 | Newer dense Qwen generation with native MTP, long context, and strong current coding/agent evidence | Recent runtime requirement, FP8 instead of first-party NVFP4, and no exact NVIDIA one-Spark recipe | It passes all hard gates and materially improves the shared role score over Qwen3.6 |
| Nemotron 3.5 Lightning NVFP4 + DSpark | Exact one-Spark target/speculation recipes and high measured owner throughput | Danish post-training support is unclear; strict-format reliability and output budget need testing | It preserves tool/schema quality while materially improving end-to-end latency or throughput |
| Gemma 4 31B NVFP4 | Broad multilingual dense control | Dense execution may reduce latency and mixed-load headroom | The mandatory wave misses Danish or multilingual fixtures and Gemma closes the gap |
| Muse Glimmer 30B NVFP4 | Official dense Spark, agent, and multimodal control | No Danish evidence and overlaps Qwen3.8 | A dense or multimodal role remains unresolved after the mandatory wave |

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
- Nemotron 3.5 Lightning is a 30B/3B-active official NVFP4 artifact with exact
  one-Spark target, MTP, and DSpark paths. It is the performance candidate, not
  presumed to be the Danish or strict-schema quality winner.
- Muse Glimmer is an official dense NVIDIA NVFP4 Spark artifact, retained only
  when a dense, multimodal, or alternate-agent control answers a real gap.

Sources: [NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[Gemma 4 31B model card](https://huggingface.co/google/gemma-4-31B-it),
[Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Nemotron 3.5 Lightning model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
[Muse Glimmer model card](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4),
and the [current shortlist refresh](text-model-shortlist-refresh-2026-09-04.md).

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
