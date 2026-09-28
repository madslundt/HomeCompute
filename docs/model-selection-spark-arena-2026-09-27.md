# Model selection from Spark Arena

**Checked:** 2026-09-27 21:18 UTC  
**Leaderboard snapshot:** generated 2026-09-27 21:00 UTC  
**Hardware context:** one NVIDIA DGX Spark / GB10, 128 GiB unified memory

## Finding

There is no clearly better general replacement for the current automation model on this leaderboard. The active local automation deployment is `unsloth/Qwen3.6-35B-A3B-NVFP4`; the active Home Assistant deployment is Gemma 4 E4B QAT W4A16. The latest checked deployment baseline records Qwen3.6-35B-A3B as active, while the model roster also lists Qwen3.8-27B as the primary workhorse. Confirm the host's live process before changing deployment; source configuration and deployment observations are not identical.

On Spark Arena's `tg128 @ d16384 (c2)` decode test, the top entry for a substantial single-Spark model is `nvidia/Qwen3.6-35B-A3B-NVFP4` at **178.29 tokens/s** (rank 12 overall). It uses vLLM with speculative MTP. This is the same model family as the deployed automation model, so the actionable lead is to qualify the NVIDIA NVFP4 checkpoint and benchmark recipe against the existing Unsloth checkpoint, rather than replace the model family. The board's score does not prove the checkpoint is more accurate or that its speed carries over to our runtime and prompt mix.

For the automation deployment's configured concurrency of four, `tg128 @ d16384 (c4)` has a single one-Spark Qwen3.6-35B-A3B NVFP4 result: **170.69 tokens/s** with vLLM and MTP (rank 1 in that test). This is a stronger workload match, but the sparse number of submissions at that test setting makes rank 1 less informative than a broad head-to-head comparison.

Other relevant results on that same test:

| Candidate | Spark Arena result | Assessment |
|---|---:|---|
| Qwen3.6-35B-A3B-NVFP4, vLLM, 1 Spark | 178.29 tok/s | Best substantial-model speed result found; same family as current automation model. [Benchmark details](https://spark-arena.com/benchmark/sub1782724431960) |
| Qwen3.6-35B-A3B-NVFP4, vLLM, concurrency 4 | 170.69 tok/s | Only one single-Spark entry found for the matching `c4` test; uses MTP. [Benchmark details](https://spark-arena.com/benchmark/sub1788019477321) |
| Ornith-1.5-35B-A3B-NVFP4, SGLang, 1 Spark | 147.10 tok/s | Same nominal 35B/3B-active size, but slower in this test; not a speed upgrade. [Benchmark details](https://spark-arena.com/benchmark/sub1788737797119) |
| Gemma 4 E2B-it FP8, vLLM, 1 Spark | 130.45 tok/s | Small-model speed candidate only. The active Gemma E4B checkpoint is not represented in this test, so the board cannot establish an E2B speedup over the current Home Assistant model. Qualify tool-call and answer quality before considering it. |
| LFM2.5-230M FP8 / LFM2.5-350M BF16 | 571.94 / 494.53 tok/s | Fastest entries, but their parameter scale makes speed alone an unsuitable basis for replacing the household assistant or automation agent. |

Qwen3.8-27B NVFP4 entries on this test are around 18–40 tok/s for single-Spark runs, far below the top Qwen3.6-35B-A3B result. Qwen3.8 may still be useful as a quality-first cold-swap model, but this leaderboard does not support promoting it for throughput.

## Recommendation

1. Use NVIDIA Qwen3.6-35B-A3B NVFP4 as the next automation qualification candidate. Git now records its pinned artifact, isolated vLLM runtime, GB10 recipe, and candidate-only route. Keep the qualified Unsloth deployment on the active route until NVIDIA passes the same local fixtures, tool-call correctness, time-to-first-token, decode speed, context stability, and memory gates.
2. Keep Gemma 4 E4B for Home Assistant until a quality-and-tool-call comparison shows a smaller option works as well. Gemma 4 E2B is a speed experiment, not a demonstrated drop-in upgrade.
3. Do not use raw leaderboard rank as model quality. Spark Arena reports tokens/s for a selected inference workload; its test matrix varies prompt depth, concurrency, and prefill/decode type. Use a matching workload and recipe, then validate on HomeCompute's own tasks.

## Sources

- [Spark Arena leaderboard](https://spark-arena.com/leaderboard)
- [Spark Arena snapshot for `tg128 @ d16384 (c2)`](https://spark-arena.com/static/snapshot/test?test=tg128%20%40%20d16384%20%28c2%29)
- [Spark Arena snapshot for `tg128 @ d16384 (c4)`](https://spark-arena.com/static/snapshot/test?test=tg128%20%40%20d16384%20%28c4%29)
- [Spark Arena Qwen3.6-35B-A3B benchmark entry](https://spark-arena.com/benchmark/sub1782724431960)
- [Spark Arena Ornith-1.5 benchmark entry](https://spark-arena.com/benchmark/sub1788737797119)
- [SparkRun Spark Arena benchmarking guide](https://sparkrun.dev/tutorials/spark-arena/)
- [vLLM's Qwen3.6-35B-A3B recipe](https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.6-35B-A3B.yaml), including GB10 NVFP4 serving flags and its measured vLLM profile
- Local deployment/configuration evidence: `config/model-catalog.json`, `config/gb10-model-roster.json`, and `docs/model-routing-refactor-baseline-2026-09-27.md`.

## NVIDIA NVFP4 versus the deployed Unsloth checkpoint

The deployed automation artifact is pinned to `unsloth/Qwen3.6-35B-A3B-NVFP4`, revision `739af1e7aac320af1682ed1e0cce369af4c5265d`. On the check date, that was also the current Hugging Face revision. NVIDIA's candidate is a distinct quantization build of the same Qwen3.6 base model, not a new model family.

### User-reported same-Spark A/B

A user benchmarked both artifacts on one DGX Spark / GB10 with vLLM 0.25.0, the same automatic MoE backend and harness, and three repeats of 256 generated tokens. The author's single-session median results were:

| Mode | NVIDIA ModelOpt NVFP4 | Current Unsloth compressed-tensors NVFP4 | NVIDIA relative gain |
|---|---:|---:|---:|
| MTP off | 76.66 tok/s | 67.52 tok/s | about 14% |
| MTP spec=2 | 97.96 tok/s | 81.61 tok/s | about 20% |
| MTP spec=3 | 108.30 tok/s | 89.92 tok/s | about 20% |
| MTP spec=3, 8 concurrent requests (aggregate) | 375.8 tok/s | 344.5 tok/s | about 9% |

At MTP spec=2 and 8 concurrent requests the results were nearly tied, with Unsloth slightly ahead (361.4 vs 357.4 aggregate tok/s). This is the strongest available evidence of a speed advantage for NVIDIA at low concurrency. It is one user's Spark, run with vLLM 0.25.0, not the current HomeCompute vLLM image or service mix. The user's full methodology, measurements, and caveats are in the [same-condition comparison](https://dev.classmethod.jp/articles/dgx-spark-qwen3-6-35b-a3b-nvfp4-new-champion/).

### Quality and tool-call evidence

NVIDIA's model card compares NVFP4 with base BF16 and reports small mixed changes: NVFP4 is lower on MMLU-Pro (85.0 vs 85.6), GPQA Diamond (84.8 vs 84.9), tau-squared Telecom (94.7 vs 95.5), SciCode (40.6 vs 40.8), and AIME 2025 (88.8 vs 89.2); equal on AA-LCR; and slightly higher on IFBench and MMMU-Pro. That supports near-parity on those published evaluations, not an across-the-board quality improvement over the currently deployed Unsloth quant.

There is a directly relevant user report in NVIDIA's public playbook tracker: on a DGX Spark using NVIDIA's NVFP4 checkpoint, vLLM 0.24.0, and NVIDIA's then-current `qwen3_xml` tool parser, the reporter got clean calls in 5/5 runs with 20–45 tools but only 0/5 clean calls at 46 tools (all attempts were malformed). In a same-box quantization check with the same prompt and flags, Unsloth NVFP4 passed 5/5 while NVIDIA NVFP4 passed 0/5. This is a narrow but serious report for tool-heavy agents. HomeCompute currently uses `qwen3_coder` and has already qualified the deployed Unsloth checkpoint for selection from a 64-tool surface, so the report is not proof NVIDIA fails with our parser or exact workflow—but it makes an unchecked swap unjustified. See [NVIDIA issue #89](https://github.com/NVIDIA/dgx-spark-playbooks/issues/89) and the local [automation qualification record](current-state.md).

### Recipe/runtime delta for HomeCompute

The current automation service uses vLLM 0.27.1, the Unsloth checkpoint, 128K maximum model length, four sequences, a conservative 0.4 GPU memory utilization, and MTP disabled for its correctness baseline. NVIDIA's published DGX Spark recipe now calls for vLLM 0.28.0 or newer for the optimized decode path, NVIDIA ModelOpt NVFP4 with Marlin, FP8 KV cache, FlashInfer attention, MTP spec=3, and a different context/concurrency/memory profile. The [vLLM recipe](https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.6-35B-A3B.yaml) documents these settings and a separate vLLM 0.28.0 speed test of 97.7 decode tok/s at concurrency 1.

Therefore, the headline A/B gain is not a measured HomeCompute gain: switching to the official recipe also changes the runtime, MTP status, serving flags, memory budget, and potentially parser behavior. Compare both checkpoints under one pinned runtime and parser first, then compare deployable recipes as a separate experiment. MTP-on is a separate tuple because HomeCompute intentionally disabled it for the tool-correctness baseline.

### Recommendation

Do not replace the current automation artifact with NVIDIA NVFP4 based on speed results alone. NVIDIA is a strong candidate for a controlled test—especially if single-request latency matters—but the deployed Unsloth quant currently has the stronger evidence for the actual 64-tool task. Promote NVIDIA only if the current HomeCompute fixture set passes, including repeated selection and valid arguments across the full tool surface, with no regression in Danish, structured output, or recovery. Measure both MTP off and MTP on; retain the existing production route until those checks and GB10 memory/latency measurements pass.

Additional primary sources: [NVIDIA NVFP4 model card and its accuracy table](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4), [NVIDIA vLLM 0.28 recipe](https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.6-35B-A3B.yaml), and the repository's opt-in, single-resident qualification gates in `docs/adr/023-n8n-automation-moe.md`.

## Owner direction and exact candidate identity

The owner selected the NVIDIA ModelOpt artifact as the next automation model to use. Pin it as `nvidia/Qwen3.6-35B-A3B-NVFP4`, revision `1355db6a052410cfd62085d94b58866fd0f2c3c5` (the current Hugging Face head checked 2026-09-27), Apache-2.0. Its publisher tree contains 17 files and 23,462,477,857 bytes; its chat-template SHA-256 is `e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259`, matching the deployed Unsloth checkpoint. The safetensors index at that exact revision includes MTP tensors.

HomeCompute currently runs the candidate role on vLLM 0.27.1 with Unsloth weights, Marlin/Cutlass-related custom backend settings, and MTP off; the NVIDIA's optimized GB10 profile requires vLLM 0.28.0+, ModelOpt loading, Marlin, FlashInfer, FP8 KV, and MTP spec=3. Isolate the automation runtime image from the shared text/Gemma runtime before upgrading it; globally changing `VLLM_IMAGE` would change other services too.

The intended sequence is to make NVIDIA the staged automation candidate, keep the known-good Unsloth artifact available for rollback, and promote the NVIDIA tuple only after the same 64-tool, Danish, structured-output, recovery, and mixed-load gates pass on the GB10. The unresolved 46-tool user report is why the user-facing production aliases should not switch before that direct qualification.
