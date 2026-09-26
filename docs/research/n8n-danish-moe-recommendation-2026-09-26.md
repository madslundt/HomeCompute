# MoE recommendation for Danish n8n workflows on Home Spark

Verified: 2026-09-26

Scope: choose one additional sparse text model for tool/function calling from
n8n, with Danish as a first-class input and output language, on the existing
128 GiB NVIDIA GB10 appliance. This is a source-based deployment recommendation,
not a local quality or capacity result.

## Recommendation

Stage **`unsloth/Qwen3.6-35B-A3B-NVFP4` at
`739af1e7aac320af1682ed1e0cce369af4c5265d`** as the n8n MoE candidate.
Expose it under a dedicated alias such as `automation-moe`; do not silently
replace the current `automation` route until it passes the Danish and tool-call
qualification suite.

Run it as a text-only, non-thinking vLLM profile with `qwen3` reasoning parsing,
`qwen3_coder` tool parsing, automatic tool choice, and an initial 64K context.
Keep MTP disabled for the correctness baseline, then benchmark the model's
native MTP head as a separate tuple. Do not autostart Qwen3.8 Flash-Next.

This choice has the best balance found here:

- Qwen reports 35B total parameters with 3B activated, native 262,144-token
  context, and explicit vLLM tool-serving support using
  `--tool-call-parser qwen3_coder`. Its own evaluation includes general-agent,
  tool, and MCP-oriented tests, and the card describes the model as strong in
  tool calling. [Qwen3.6 model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- The preceding Qwen3.5 family release explicitly lists Danish among more than
  200 supported languages. Qwen3.6 shares the Qwen3.5 model architecture and
  tokenizer family, but Qwen publishes no Danish-specific Qwen3.6 score; Danish
  quality therefore remains a local acceptance gate, not a proven win.
  [Qwen3.5 multilingual language list](https://qwen.ai/blog?id=qwen3.5),
  [Transformers Qwen3.5/3.6 architecture documentation](https://huggingface.co/docs/transformers/main/model_doc/qwen3_5)
- The selected Unsloth checkpoint is Apache-2.0, contains the MTP module, is
  documented for vLLM, and has a DGX Spark-specific `sm_121a` / FlashInfer MoE
  path. The publisher says it fits within 32 GB VRAM; the Hugging Face model API
  reports 26.489 GB of safetensors at the selected revision.
  [Pinned artifact](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-NVFP4/tree/739af1e7aac320af1682ed1e0cce369af4c5265d),
  [artifact API](https://huggingface.co/api/models/unsloth/Qwen3.6-35B-A3B-NVFP4/revision/739af1e7aac320af1682ed1e0cce369af4c5265d?blobs=true)

## Why this quant, not NVIDIA's Qwen3.6 quant

NVIDIA's `nvidia/Qwen3.6-35B-A3B-NVFP4` has the cleanest official single-Spark
recipe: vLLM, tensor parallelism 1, FP8 KV cache, FlashInfer attention, Marlin
MoE, MTP, `qwen3_xml`, and automatic tool choice. NVIDIA reports a roughly
3.06x storage/memory reduction from BF16 and a 94.7 score on its quantized
tau2-Bench Telecom run. [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4)

However, an unresolved issue in NVIDIA's own DGX Spark playbook repository
reports a reproducible tool-count cliff on that exact ModelOpt artifact and
GB10 recipe: the NVIDIA quant emitted malformed tool markup with 46 tools,
while the Qwen FP8 and Unsloth NVFP4 artifacts passed the reporter's same-box
comparison. This is first-hand issue evidence, not a vendor-confirmed defect,
but it directly conflicts with the intended n8n role and is sufficient to avoid
making the NVIDIA quant the default before independent reproduction.
[NVIDIA playbook issue #89](https://github.com/NVIDIA/dgx-spark-playbooks/issues/89)

The Unsloth artifact is not a Qwen- or NVIDIA-published quantization, so pin its
revision and treat calibration, runtime, parser, context, and backend as one
immutable qualification tuple. The model card currently recommends vLLM
`>=0.25.0`, FlashInfer `>=0.6.13`, `nvidia-cutlass-dsl>=4.5.2`,
`CUTE_DSL_ARCH=sm_121a`, and `--moe-backend flashinfer_b12x` for DGX Spark.
Live qualification against the pinned NVIDIA 26.08 image found that this mixed
checkpoint needs `--moe-backend cutlass` with
`VLLM_FP8_MOE_BACKEND=triton`: the published `b12x` path rejects the artifact's
compressed-tensors source format during live warm-up in this image, while the
FlashInfer CUTE-DSL and TRT-LLM paths reject the installed Spark CUDA target.
vLLM otherwise applies a single MoE selector to both the NVFP4 and FP8 expert
paths. Those requirements mean it needs a separately
pinned runtime profile; it must
not be dropped into the current Qwen3.8 launcher merely by changing `--model`.
[Unsloth runtime instructions](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-NVFP4#vllm-run-instructions)

## Comparison with the closest alternatives

| Candidate | Tool and Danish evidence | GB10 fit | Disposition |
| --- | --- | --- | --- |
| `unsloth/Qwen3.6-35B-A3B-NVFP4` | Qwen documents `qwen3_coder` serving and reports 67.2 TAU3, 37.0 MCPMark, and 62.8 MCP-Atlas for the BF16 base model. Qwen3.5 family documentation explicitly includes Danish; there is no Qwen3.6 Danish benchmark. | 26.489 GB weights; artifact publisher documents vLLM and an `sm_121a` DGX Spark backend. | **Select, then qualify locally.** |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | Google documents native function calling, 35+ out-of-box languages and 140+ pretraining languages; it does not enumerate Danish or publish Danish results. Google reports 68.2 on Tau2 for the BF16 26B-A4B model. | 25.2B/3.8B active, 256K context, 18.788 GB weights; NVIDIA documents vLLM, Blackwell and the `gemma4` parser. | Best fallback if Qwen fails Danish fixtures, but weaker direct Danish evidence and lower same-publisher agent/tool results than Qwen3.6. |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | Explicitly trained for agents, multi-step tools and structured output, with an exact `qwen3_coder` recipe. Its six supported post-training languages omit Danish. | Exact one-GB10 vLLM/DSpark recipe; 30B/3B active and 21.6 GB artifact. | Reject for this role because Danish is an explicit priority; retain only as a speed control. |

Sources for the table: [Qwen3.6 evaluations and serving](https://huggingface.co/Qwen/Qwen3.6-35B-A3B),
[Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4),
[Gemma NVFP4 card](https://huggingface.co/nvidia/Gemma-4-26B-A4B-NVFP4),
[Gemma function-calling guide](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4),
and [Nemotron 3.5 Lightning card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4).

The benchmark numbers above are publisher results for different models and
harnesses. They rank evidence, not production behavior, and should not be
numerically blended into one score.

## Residency and routing consequences

The selected MoE checkpoint plus the installed Qwen3.8-27B NVFP4 weights total
about 50 GB before KV/recurrent caches, compilation workspaces, vision state,
containers, the OS, and speech services. Weight fit therefore does not prove
that two independently sized vLLM memory pools can safely coexist. The current
roster also deliberately limits the appliance to one resident text model.

The safe first deployment is:

1. Download and verify the pinned MoE artifact without autostarting it.
2. Give it a separate vLLM service/profile, port, health check, and immutable
   release record; run text-only with 64K context and low concurrency.
3. Route only the explicit `automation-moe` alias to it. n8n workflows opt in;
   the existing aliases continue to use Qwen3.8-27B.
4. Initially activate it by an explicit drain/switch operation. Consider
   simultaneous residency only after measuring aggregate unified-memory use
   with all intended STT/TTS/embedding services active and preserving recovery
   headroom.
5. Keep Flash-Next downloaded only if desired for the heavy lane and never in
   the default startup set.

If local measurements justify two resident text services, assign fixed,
non-overlapping memory budgets rather than letting both vLLM processes reserve
their defaults. A plausible weight sum is not an allocation policy.

## Required acceptance gates

Promotion to the ordinary n8n `automation` route should require all of these on
the pinned GB10 tuple:

- Danish: multi-turn instructions, summaries, date/number formatting, Danish
  letters and names, Danish-English code switching, and native review of
  factuality and naturalness.
- Tools: correct selection, exact JSON Schema arguments, no-call decisions,
  parallel and sequential calls, tool errors, tool-result continuation, and
  at least 64 exposed tool definitions to cover the reported failure class.
- n8n safety: invalid JSON, schema mismatch, prompt injection inside tool data,
  retry/idempotency behavior, timeouts, and no execution based only on prose.
- Runtime: OpenAI Chat Completions and Responses behavior used by the gateway,
  streaming, health/auth failures, restart/cold-load time, p50/p95 latency,
  throughput, peak unified memory, and recovery after OOM or process failure.
- A/B: Qwen3.6 MoE against the installed Qwen3.8-27B on the same redacted n8n
  fixtures. Promote only if task success or latency improves without reducing
  tool correctness or Danish acceptance.

Thinking should be disabled by default for routine automation to reduce latency
and make schema behavior easier to test. A workflow may explicitly request
thinking only after that separate mode passes the same tool-loop checks.
