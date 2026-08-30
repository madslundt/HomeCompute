# Coding model evaluation

Verified: 2026-08-25

Status: benchmark shortlist; no winner selected without GB10 measurements

## Recommendation

Benchmark the following in this order for `coding`:

1. `Qwen/Qwen3-Coder-Next-FP8` as the exact first benchmark artifact from the
   Qwen3-Coder-Next family.
2. `nvidia/Gemma-4-31B-IT-NVFP4` as the dense general/coding challenger.
3. `Qwen/Qwen3.8-27B-FP8` as the newer dense Qwen challenger.
4. `mistralai/Devstral-Small-2-24B-Instruct-2512` as the low-memory,
   low-latency baseline.
5. `openai/gpt-oss-120b` as the reasoning/tool-use comparison.
6. `mistralai/Devstral-2-123B-Instruct-2512` only if a supported quantization
   leaves enough KV-cache and concurrency headroom.

No model becomes an alias target based on its publisher benchmark alone.

## Evidence and trade-offs

| Candidate | Primary-source facts | GB10 hypothesis |
| --- | --- | --- |
| Qwen3-Coder-Next (`Qwen/Qwen3-Coder-Next-FP8` artifact) | 80B total/3B active, 256K native context, Apache-2.0, purpose-built for coding agents and failure recovery; official vLLM launch includes a Qwen tool-call parser | Best first balance of agentic coding quality, active compute, and model size; reduce context to 32K for the first PoC |
| Gemma 4 31B | Dense 30.7B, 256K context, Apache-2.0, native function calling, coding/agentic focus, and an MTP assistant checkpoint | High-quality dense control; qualify the NVIDIA NVFP4 artifact and compatible MTP path without assuming the quoted user's result transfers |
| Qwen3.8-27B | Dense 27B, 262K context, Apache-2.0, newer Qwen coding/agent generation with native MTP | Strong dense Qwen comparison; requires its own recent pinned runtime and has no current single-Spark proof |
| Devstral Small 2 | 24B, 256K context, Apache-2.0, designed for agentic repository work; publisher says it can run on a 32 GB Mac | Residency/latency baseline and possible fallback when voice load is high |
| gpt-oss-120b | 117B/5.1B active, MXFP4, Apache-2.0, designed to fit one 80 GB GPU, function calling and structured outputs | Strong fit on 128 GB unified memory, but Harmony formatting and Codex Responses behavior require careful validation |
| Devstral 2 123B | FP8 123B, 256K context and tool-oriented coding; restrictive/other model license | Quality stretch candidate; memory headroom and license review may reject it |

Sources: [Qwen3-Coder-Next model card](https://huggingface.co/Qwen/Qwen3-Coder-Next),
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
