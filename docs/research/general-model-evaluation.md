# General model evaluation

Verified: 2026-08-25

Status: benchmark shortlist; Danish and structured-output measurements pending

## Recommendation

Start `automation` and `meeting` evaluation with:

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
