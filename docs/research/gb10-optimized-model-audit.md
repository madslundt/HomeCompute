# GB10 optimized-model and precision audit

Verified: 2026-08-30

Status: primary-source audit of the repository's planned model set. This note
does not select a production model; exact revisions, containers, kernels,
contexts, and workloads still require measurement on the target GB10.

## Bottom line

The current first-install choice is still the right smoke-test artifact:
`nvidia/Qwen3.6-35B-A3B-NVFP4` is a pre-quantized NVIDIA Model Optimizer
checkpoint, appears in NVIDIA's DGX Spark vLLM support matrix, and has an exact
single-Spark launch recipe. Its card reports 35B total/3B active parameters,
262K context, vLLM support, approximately 3.06x lower disk/GPU-memory demand
than BF16, and close BF16-versus-NVFP4 results on NVIDIA's listed evaluations.
[NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4),
[NVIDIA Spark vLLM recipe and matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#run-agent-ready-qwen36-35b-model-with-vllm)

The shortlist should nevertheless add two August 2026-era benchmark tracks:

1. `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` paired with
   `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark`. NVIDIA gives
   an explicit one-DGX-Spark vLLM recipe, 30B-total/3B-active architecture,
   tool/reasoning parsers, and a DSpark draft made specifically for DGX Spark
   and low-concurrency serving. Danish is not in the card's supported
   post-training language list, so this is an agent/coding and performance
   candidate, not a presumptive Danish `home` winner.
   [target card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4),
   [DSpark card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark)
2. `nvidia/Qwen3.6-27B-NVFP4` as the publisher-provided dense Qwen control.
   NVIDIA's card identifies a 27B hybrid-attention, 262K-context, multimodal,
   ModelOpt checkpoint for vLLM on Blackwell and reports about 2.5x less
   disk/GPU-memory demand than 16-bit. It is not in the current NVIDIA Spark
   vLLM support matrix and has no Spark-specific command in its card, so it is
   a Blackwell-compatible challenger rather than a Spark-qualified replacement
   for the 35B-A3B baseline.
   [NVIDIA model card](https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4),
   [NVIDIA Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix)

Most importantly, an `NVFP4` filename does **not** prove native W4A4 Tensor
Core execution on GB10. NVIDIA's Nemotron 3.5 Lightning hardware matrix says
the checkpoint is stored as NVFP4 on DGX Spark but executes W4A16 through the
Marlin MoE backend, and explicitly marks its native FP4 Tensor Core path
"No — runs via Marlin." This still provides a much smaller stored/resident
model and lower memory traffic; it is not the same as exercising the GB10's
native FP4 W4A4 compute path. The runtime logs and selected kernel must be
captured in every benchmark.
[NVIDIA Nemotron 3.5 Lightning hardware matrix](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#model-summary)

## Terms used in this audit

| Term | Meaning here | What it does not mean |
| --- | --- | --- |
| Pre-quantized checkpoint | Low-precision tensors and quantization metadata are already stored in the artifact. NVIDIA's `*-NVFP4` ModelOpt cards and Qwen's `*-FP8` cards are examples. | It does not mean the original model was trained in that precision, or that every layer and activation uses it. NVIDIA describes most of these as post-training quantized derivatives. [ModelOpt PTQ and export documentation](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#post-training-quantization-ptq) |
| NVFP4 | NVIDIA's block-scaled 4-bit floating-point format for Blackwell. GB10 has fifth-generation Tensor Cores with FP4 support. | It does not identify W4A4 versus W4A16, high-precision layers, KV-cache precision, or the kernel actually selected. [DGX Spark hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html#ai-ml-capabilities), [NVIDIA NVFP4 playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nvfp4-quantization/README.md#overview) |
| MXFP4 | The native stored MoE-weight format of `gpt-oss-20b/120b`; OpenAI says the released evaluation was performed in that format. | It is not NVFP4. NVIDIA Model Optimizer can make an offline NVFP4 export from it using `--cast_mxfp4_to_nvfp4`. [OpenAI model card](https://huggingface.co/openai/gpt-oss-120b#model-architecture-and-parameters), [NVIDIA cast documentation](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#mxfp4--nvfp4-cast-for-gpt-oss) |
| FP8 checkpoint | Publisher-stored eight-bit artifact, such as Qwen3-Coder-Next-FP8 or Qwen3.8-27B-FP8. | It is not dynamically converted to NVFP4 merely because it runs on Blackwell. [Qwen3-Coder-Next-FP8 card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8#highlights), [Qwen3.8-27B-FP8 card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) |
| Offline derived checkpoint | A new artifact produced by calibrating/converting a source model with Model Optimizer and exporting a unified Hugging Face checkpoint. | It is not a publisher-provided checkpoint and needs its own provenance, calibration, accuracy, and runtime qualification. [ModelOpt workflow](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#getting-started), [Spark end-to-end quantization playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nvfp4-quantization/README.md) |
| Runtime setting | Loader and kernel choices such as `--quantization modelopt`, `--moe-backend marlin`, FlashInfer, or `--kv-cache-dtype fp8`. | These flags do not turn BF16/FP8 weight files into a new NVFP4 checkpoint. KV-cache precision is separate from weight precision. [Qwen3.6 Spark command](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4#usage), [ModelOpt KV-cache formats](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#kv-cache-quantization) |
| GGUF Q4/Q8 | A llama.cpp-compatible artifact/container with its own quantization scheme. NVIDIA supports CUDA-offloaded GGUF serving on Spark when memory permits. | Q4 is not automatically NVFP4 and does not by itself establish a native FP4 Tensor Core path. Keep GGUF as the required llama.cpp baseline. [NVIDIA llama.cpp Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/llama-cpp/README.md) |
| MTP/DSpark/DFlash | Speculative decoding: a draft mechanism proposes tokens for target-model verification. | It is independent of weight precision and cannot be treated as an FP4 variant of the target model. The Nemotron DSpark artifact is a 967M draft, not a standalone target. [NVIDIA DSpark card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) |

## Audit of the planned text models

The planned set below comes from `llm-installation-recommendation.md`, the
role-specific evaluations, and their conditional candidates.

| Planned candidate | Artifact actually planned | Precision/provenance finding | GB10 disposition |
| --- | --- | --- | --- |
| Qwen3.6 35B-A3B | `nvidia/Qwen3.6-35B-A3B-NVFP4` | NVIDIA pre-quantized ModelOpt NVFP4 artifact; exact single-Spark vLLM recipe; 35B/3B active. The Spark quantization playbook identifies the matching low-concurrency layout as NVFP4 weight-only W4A16 for MoE MLPs/`lm_head`, FP8 attention, and FP8 KV. [card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4), [layout/recipe](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nvfp4-quantization/README.md#step-4-choose-a-quantization-recipe) | Keep priority 1. It is the best-supported appliance smoke test, but record the Marlin/kernel path and do not call it proof of end-to-end W4A4 Tensor Core execution. |
| Qwen3-Coder-Next | `Qwen/Qwen3-Coder-Next-FP8` | Official Qwen pre-quantized fine-grained FP8 checkpoint, 80B/3B active, 256K context, vLLM 0.15+. The current NVIDIA ModelOpt matrix includes the Qwen `Next` family under NVFP4, but the current NVIDIA Spark vLLM/TRT matrices do not list Coder-Next and this audit found no NVIDIA- or Qwen-published Coder-Next NVFP4 card. [Qwen card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8), [ModelOpt matrix](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#hugging-face-supported-models), [Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) | Keep the publisher FP8 artifact as the trusted coding baseline. An in-house ModelOpt NVFP4 export is a separate experimental artifact, not a drop-in identity-preserving optimization. Do not promote community NVFP4 checkpoints. |
| Gemma 4 31B IT | `nvidia/Gemma-4-31B-IT-NVFP4` | NVIDIA pre-quantized ModelOpt artifact for vLLM/Blackwell; current Spark vLLM matrix lists it. The card identifies dense 30.7B, 256K context and close BF16/NVFP4 publisher evaluation results. [card](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4), [Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) | Keep as the first dense NVFP4 quality challenger. Verify exact Spark flags because the generic card's usage example is multi-GPU, while the Spark matrix supplies platform support but no model-specific one-Spark command. |
| Qwen3.8 27B | `Qwen/Qwen3.8-27B-FP8` | Official Qwen fine-grained FP8 checkpoint for vLLM/SGLang; no Qwen- or NVIDIA-published NVFP4 checkpoint was found. The current vLLM recipe's NVFP4 commands use third-party `Inferact/...` and `unsloth/...` artifacts, not Qwen/NVIDIA artifacts; it separately uses Qwen's official FP8 checkpoint. [Qwen card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8), [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) | Keep FP8 as the provenance-safe artifact. Do not replace it with a third-party NVFP4 merely because the vLLM recipe demonstrates a kernel. Prefer NVIDIA's Qwen3.6-27B NVFP4 as the first dense FP4 control. |
| Devstral Small 2 / Devstral 2 | Publisher checkpoints | Neither current NVIDIA Spark matrix lists these exact artifacts, and the current ModelOpt excerpt does not list the Mistral/Devstral architecture. [vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [TRT-LLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md#model-support-matrix), [ModelOpt matrix](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#hugging-face-supported-models) | Retain Devstral Small as the low-memory quality/latency baseline in its publisher-supported format or as a controlled GGUF baseline. Do not assume an NVFP4 path. Keep 123B deferred. |
| gpt-oss-120b / 20b | `openai/gpt-oss-120b`, optionally `openai/gpt-oss-20b` | OpenAI ships native MXFP4 MoE weights and says all released evaluations used that quantization; NVIDIA's current Spark vLLM and TRT-LLM matrices list both MXFP4 models. ModelOpt documents an offline MXFP4-to-NVFP4 export path. [OpenAI card](https://huggingface.co/openai/gpt-oss-120b), [vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [TRT matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md#model-support-matrix), [cast](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#mxfp4--nvfp4-cast-for-gpt-oss) | Keep 120B as the high-capacity comparison and 20B as a compact `home` control. Benchmark the native MXFP4 artifact first; treat an NVFP4 cast as a second exact artifact tuple. |
| Nemotron 3 Super | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` | NVIDIA pre-quantized NVFP4 artifact appears in both Spark matrices and has a dedicated one-Spark playbook. The current recipe requires model-specific parsers/backends and, for vLLM MTP+NVFP4, a CUDA 13 nightly rather than assuming a generic stable image. [Spark matrices](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [dedicated playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md) | Keep conditional/late. Its support is real, but a 120B checkpoint has much less mixed-load headroom than the 20–22GB efficient candidates. |
| GLM-4.7-Flash | `zai-org/GLM-4.7-Flash` | The current ModelOpt matrix explicitly lists GLM-4.7 for NVFP4 and says its MTP layers are loaded but excluded from quantization; it is absent from the current Spark runtime matrices. [ModelOpt matrix and note](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#hugging-face-supported-models), [Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) | Keep as a conditional model-quality candidate. If tested in NVFP4, produce and qualify a local ModelOpt derivative with complete calibration provenance. |

### Speech, embeddings, and rerankers

Do not generalize the LLM FP4 recommendation to the audio and retrieval
shortlists. NVIDIA's current ModelOpt table lists Whisper for FP8 but not
NVFP4. NVIDIA's Spark vLLM matrix lists Qwen3-VL embedding/reranker base models,
not the repository's small `Qwen3-Embedding-0.6B` and
`Qwen3-Reranker-0.6B` identities. These models are small enough that integration,
quality, and batching should be measured before creating extra precision
variants.
[ModelOpt support matrix](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#hugging-face-supported-models),
[NVIDIA Spark vLLM matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix)

## Newly relevant publisher/NVIDIA artifacts

| Candidate | Why it merits a benchmark | Important limit |
| --- | --- | --- |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` + `...-DSpark` | NVIDIA's exact one-Spark vLLM command pins vLLM 0.27.1, Marlin, FP8 KV, parsers, and a 967M DSpark draft; the target card reports 30B/3B active, 21.6GB artifact size, 1M validated context, and agent/coding use. [target](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4), [draft](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) | GB10 execution is W4A16/Marlin, explicitly not native FP4 Tensor Core. Danish is not a listed supported post-training language. OpenMDW-1.1 needs license review. |
| `nvidia/Qwen3.6-27B-NVFP4` | Publisher-provided dense NVFP4 control against the current dense Qwen3.8 FP8 challenger; 27B, 262K, vLLM, Blackwell, about 2.5x compression. Its checked config declares `W4A16_NVFP4`. [card](https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4), [config](https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4/blob/main/config.json) | No first-party Spark-specific recipe or listing in the current Spark vLLM matrix. Benchmark W4A16 kernel behavior rather than inferring native W4A4. |
| `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` | NVIDIA explicitly supports one DGX Spark, publishes a Spark vLLM 0.20.0 command, reports 31B/3B active and 20.9GB NVFP4 versus 61.5GB BF16, and includes text, image, video, and audio inputs plus tool/reasoning parsers. [card and Spark recipe](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) | This is an omni-analysis candidate, not a drop-in replacement for the separate low-latency STT service. Its card does not establish Danish acceptance. |
| `nvidia/Gemma-4-26B-A4B-NVFP4` | NVIDIA pre-quantized 25.2B/3.8B-active, 256K, 140+-language, text/image checkpoint with vLLM/Blackwell support and tool/reasoning parsers. [NVIDIA card](https://huggingface.co/nvidia/Gemma-4-26B-A4B-NVFP4) | The NVFP4 artifact is newer than the current Spark matrix row, which lists only the base 26B-A4B model. Treat it as Blackwell-compatible, not yet Spark-matrix-qualified. |
| `nvidia/Qwen3-8B-NVFP4` or `nvidia/Qwen3-14B-NVFP4` | Both are pre-quantized NVIDIA checkpoints in the current Spark vLLM matrix and are much smaller candidates for a reserved low-latency `home` process. [matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [8B card](https://huggingface.co/nvidia/Qwen3-8B-NVFP4), [14B card](https://huggingface.co/nvidia/Qwen3-14B-NVFP4) | Older/smaller model quality and Danish tool accuracy may lose to the shared Qwen3.6 baseline; only add a resident process if the mixed-load latency gate requires it. |

Other NVIDIA-published NVFP4 artifacts in the current Spark vLLM matrix include
Qwen3-32B, Llama-3.1-8B, Llama-3.3-70B, Qwen2.5-VL-7B, Phi-4 reasoning, and
Phi-4 multimodal. This proves a broader supported catalog, not that each model
belongs in the repository's role shortlist.
[NVIDIA Spark vLLM model support matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix)

## Format and runtime support on GB10

| Runtime/path | What NVIDIA currently supports | Consequence for this repository |
| --- | --- | --- |
| NVIDIA NGC vLLM | NVIDIA's Spark matrix lists BF16, FP8, MXFP4, and NVFP4 artifacts. The repository's Qwen3.6 command is model-specific and uses ModelOpt loading, FP8 KV, FlashInfer attention, Marlin MoE, and optional MTP. [matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [Qwen command](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4#usage) | Keep vLLM first. Pin the exact NGC image digest and model revision. Treat every precision/backend/parser change as a new qualification tuple. |
| TensorRT-LLM | NVIDIA publishes a separate one-/two-Spark matrix with NVFP4, FP8, and MXFP4 models and says its optimizations include kernels, layouts, and quantization. [NVIDIA TRT-LLM Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md) | Keep as the controlled performance challenger. It may expose native W4A4 kernels for some artifacts, but prove the exact chosen model and parser rather than transferring a result from another model. |
| NVIDIA Model Optimizer | Creates a persistent calibrated/exported checkpoint; unified HF export can be consumed by vLLM, TensorRT-LLM, or SGLang. The current matrix includes Qwen Next, GLM-4.7, GPT-OSS, Nemotron 3 and several other families for NVFP4. [documentation](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md) | Use only after published artifacts are exhausted. Store recipe, source revision, ModelOpt/container revision, calibration data, output hashes, accuracy deltas, and license. |
| SGLang | NVIDIA's release notes state NVFP4 support on Blackwell including DGX Spark. Individual cards may still limit variants; for example, Nemotron Omni currently says SGLang FP8/NVFP4 support is forthcoming while vLLM is available. [NVIDIA SGLang release notes](https://docs.nvidia.com/deeplearning/frameworks/pdf/SGLang-Release-Notes.pdf), [Nemotron Omni card](https://huggingface.co/nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4) | Do not infer per-model support from the runtime-wide feature. Add SGLang only with an exact publisher recipe and same-workload advantage. |
| llama.cpp / GGUF | NVIDIA documents CUDA compilation for GB10 (`sm_121`) and says any fitting GGUF checkpoint can run. [NVIDIA llama.cpp playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/llama-cpp/README.md) | Keep Q4/Q8 GGUF as the controlled portability/baseline lane. Do not label those runs NVFP4 or native FP4 without kernel evidence. |
| Ollama | NVIDIA's Spark coding-agent playbook exposes `qwen3.6:35b-a3b-nvfp4` at roughly 22GB, alongside Q8 and BF16 variants. [NVIDIA playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/cli-coding-agent/README.md) | Useful for exploratory checks, not a substitute for the pinned vLLM production tuple or its Responses/tool qualification. |

## Recommended revised benchmark order

1. Keep `nvidia/Qwen3.6-35B-A3B-NVFP4` first, MTP off then on. Capture the
   actual MoE/GEMM kernels, not just the checkpoint metadata.
2. Add `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus its official
   DSpark draft as the first low-concurrency agent/coding performance
   challenger. Run target-only, native MTP, and DSpark as separate tuples.
3. Add `nvidia/Qwen3.6-27B-NVFP4` beside `Qwen/Qwen3.8-27B-FP8` for the dense
   comparison. This cleanly compares a publisher NVIDIA NVFP4 artifact with
   Qwen's official newer FP8 artifact without accepting community provenance.
4. Keep `nvidia/Gemma-4-31B-IT-NVFP4` as the first dense Spark-matrix-qualified
   quality challenger; stage Gemma 4 26B-A4B NVFP4 only after a safe Spark
   runtime tuple is pinned.
5. Keep `Qwen/Qwen3-Coder-Next-FP8` as the primary specialized coding model.
   Only build a local NVFP4 derivative after FP8 measurement shows memory or
   bandwidth is the limiting factor.
6. Benchmark `openai/gpt-oss-120b` in its publisher-native MXFP4 form before an
   optional ModelOpt NVFP4 cast. Keep Nemotron 3 Super late because nominal fit
   is not mixed-load headroom.
7. If `home` misses its TTFT target under concurrent coding load, try the
   current shared model first, then a Spark-matrix-listed Qwen3-8B/14B NVFP4
   reserved process. Do not add it merely because it is small.

## Required evidence for claiming "GB10 accelerated"

For every promoted tuple, record:

- exact model revision and checkpoint hash, plus whether it is publisher
  pre-quantized, publisher-native MXFP4, or locally derived;
- weight layout by component (W4A4, W4A16, FP8/BF16 exceptions), activation
  precision, KV-cache precision, and draft-model precision;
- runtime/container digest, CUDA/driver/DGX OS, loader, attention/MoE/GEMM
  backend, and the selected kernel reported at startup;
- MTP/DSpark/DFlash off/on, accepted draft tokens, and additional memory;
- cold load, p50/p95 TTFT, inter-token latency, throughput, peak unified memory,
  KV capacity, host responsiveness, and 24-hour mixed-load stability;
- Danish, tool-call, Responses streaming, coding, and role-specific quality
  deltas versus the higher-precision publisher artifact.

NVIDIA reports up to 1 PFLOP FP4 with sparsity for DGX Spark, 128GB unified
memory, and 273GB/s memory bandwidth. Those peak hardware properties do not
select a model or prove that a W4A16 Marlin tuple uses native FP4 Tensor Cores.
[DGX Spark hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html#compute-performance)

## Source boundary

This audit used NVIDIA product documentation, NVIDIA-maintained Spark and
Model Optimizer repositories, NVIDIA model cards, and official model-publisher
cards. The Qwen3.8 provenance warning also cites vLLM's own current recipe
because it is the primary source for which third-party NVFP4 handle that recipe
actually launches. Community model cards and anecdotal benchmark claims were
not used to recommend an artifact.
