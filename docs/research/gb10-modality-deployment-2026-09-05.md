# GB10 modality deployment readiness

Verified: 2026-09-05

Status: decision record for implementation and qualification. This is not a quality ranking.

## Scope and evidence rules

This report covers the single ASUS Ascent GX10 planned as `home-spark`: one NVIDIA GB10, 128 GB unified memory, and 1 TB local storage. NVIDIA's directly documented reference is DGX Spark. A DGX Spark/GB10 result is strong SoC evidence for GX10, but the partner system's shipped OS, driver, firmware, and container behavior still need to be recorded and qualified separately.

Terms used below:

- **Implementation-ready for qualification**: the publisher provides an exact artifact and a GB10/DGX Spark or Linux ARM64 runtime path. It may be configured and benchmarked, but it is not the active production choice until repository-owned acceptance tests pass.
- **Blocked**: at least one mandatory artifact, license, architecture, API, entitlement, immutable-pin, or resource prerequisite is absent.
- **Scheduled**: load only in a maintenance/benchmark window. It is not an always-resident service.
- **Verified** means a fact stated by a cited publisher/runtime source. **Derived** means arithmetic over publisher file sizes. **Hypothesis** means the target machine must prove it.

No candidate is called “best.” Publisher benchmarks nominate candidates only; repository measurements determine eligibility and rank.

## Platform and runtime foundation

NVIDIA documents GB10 as a 20-core Arm system with 128 GB LPDDR5x unified memory, 273 GB/s bandwidth, and 1 TB or 4 TB NVMe options. The GB10 compilation target is SM121 (`121-real`). On unified memory, model weights, KV cache, runtime workspaces, CPU services, display, page cache, and the OS compete for the same 128 GB; conventional VRAM-only accounting is invalid. [`DGX Spark hardware`](https://docs.nvidia.com/dgx/dgx-spark/hardware.html), [`compilation guide`](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/porting/compilation.html), [`known issues`](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html)

The current NVIDIA vLLM image is `nvcr.io/nvidia/vllm:26.08-py3`. NGC marks it signed, scanned, multi-architecture, and 10.91 GB compressed. Its Linux ARM64 manifest digest is:

```text
nvcr.io/nvidia/vllm@sha256:d049bead397430803ac5b705d50094492f23782716677d746ab721e43f054cf6
```

It contains vLLM 0.27.1, CUDA 13.4.1, Transformers 5.14.1, and FlashInfer 0.6.17. NVIDIA explicitly discusses DGX Spark support and warns that unified-memory deployments may need `--gpu-memory-utilization 0.7` rather than the effective near-1.0 default. Larger context reserves more KV cache. [`NGC vLLM`](https://catalog.ngc.nvidia.com/orgs/nvidia/containers/vllm), [`26.08 release notes`](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-08.html), [`Spark playbook`](https://build.nvidia.com/spark/vllm/instructions)

The Founders Edition reference release is DGX OS 7.5.0 with driver 580.159.03 and CUDA Toolkit 13.0.2. NVIDIA says partner GB10 systems may update on a different schedule, so those values are reference inputs, not GX10 facts. [`DGX Spark release notes`](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)

Every runnable deployment tuple must freeze:

```text
host OS + kernel + driver + firmware
container repository + tag + linux/arm64 OCI digest + signature
model repository + full revision + file hashes + license/notice snapshot
runtime/backend versions + quantization + attention/MoE kernels
chat template + reasoning/tool parsers + trust-remote-code state
context + batch/concurrency + KV precision + memory-utilization limits
API schema + readiness path + cache/export hashes
```

An upstream ARM64 image is not blanket SM121 proof. Upstream vLLM issue [#38484](https://github.com/vllm-project/vllm/issues/38484) documents missing native SM121 cubins in an inspected published ARM64 0.27.1 image. Prefer NVIDIA's GB10-tested image/model path or prove the exact kernel path before use.

All services remain private. The authenticated listener stays at `10.77.10.10:8000`; only edge health may be unauthenticated. Model-specific ports such as NIM 9000/50051 must bind locally or sit behind that authenticated edge. Prompts, content, and request IDs must not become metric labels.

## Mandatory text wave

The mandatory wave is unchanged: `coding`, `automation`, `research`, `home`, `meeting`, and `assistant` remain stable aliases; private aliases have no cloud fallback. “Installed” means available to benchmark, not simultaneously resident.

| Candidate | Exact artifact and license | GB10/runtime/API evidence | Expected memory and storage | Decision and missing prerequisite |
|---|---|---|---|---|
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | Revision `1355db6a052410cfd62085d94b58866fd0f2c3c5`; Apache-2.0. HF weight files total **23,424,338,320 bytes** (derived from the publisher tree). | NVIDIA card provides a single-DGX-Spark vLLM recipe. It is a 35B-total/3B-active text/image/video model. The recipe requires the Qwen3 reasoning parser, `qwen3_xml` tool parser, Marlin MoE, FP8 KV cache, FlashInfer attention, and MTP settings. OpenAI chat API. [`model card`](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4) | **23.424 GB is weights only**. KV cache, visual encoder inputs, CUDA graphs, workspaces, and container memory are unmeasured on this GX10. Disk also needs the 10.91 GB compressed runtime plus unpacked layers/cache. | **Implementation-ready for qualification.** First integration baseline. Blocked from promotion until the exact recipe image digest, startup on GX10, bounded context/concurrency, authenticated API, text/tool/vision correctness, and mixed-load memory are recorded. |
| `Qwen/Qwen3.8-27B-FP8` | Revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`; Apache-2.0. Publisher weights total **30.867 GB**. | Qwen documents vLLM/SGLang and OpenAI chat requests with text, image, and video. The current vLLM recipe verifies **text serving** and requires Transformers >=5.8.0, but does not publish a single-GB10 recipe. [`model card`](https://huggingface.co/Qwen/Qwen3.8-27B-FP8), [`vLLM recipe`](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) | **30.867 GB is weights only**. Dense-model KV/workspace and visual-path peaks on GB10 are unknown. | **Blocked as a supported GB10 deployment; qualification experiment only.** Missing model-specific GB10 startup/kernel evidence, vision serving proof, exact image digest, bounded memory/context, and repository text/Codex benchmark evidence. Compatibility with the current NVIDIA image is a hypothesis, not proof. |
| `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | Target revision `cc84af2fe71647d87f4486c064f320e1e7535243`, **21.562 GB** weights. Draft `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark` revision `8a0177116d138011e63103110f136ec0ca09ebbf`, **1.349 GB** weights. OpenMDW-1.1. [`target`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4), [`draft`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark), [`license`](https://raw.githubusercontent.com/OpenMDW/OpenMDW/refs/heads/main/1.1/LICENSE.OpenMDW-1.1) | Publisher gives an exact single-GB10 vLLM 0.27.1 route. GB10 uses W4A16 Marlin, FP8 KV cache, prefix caching, `nemotron_v3` reasoning, and `qwen3_coder` tool parsing. The DSpark checkpoint is the publisher's Spark draft model for speculative decoding. OpenAI chat API. | Target + draft weights are **22.911 GB**. Publisher flags use `--gpu-memory-utilization 0.85`; that is not approval to consume 85% when speech and host services coexist. Working memory and acceptance context remain unmeasured. | **Implementation-ready for qualification.** Benchmark target-only first, then target+draft as separate immutable tuples. Blocked from promotion until license review, GX10 mixed-load memory, parser/tool correctness, acceptance latency/quality, and exact container digest are recorded. |

Mandatory text artifact total, including the Nemotron draft, is approximately **77.2 GB of weight files**. That is disk inventory, not a claim that all four processes fit or should run concurrently.

## Speech-to-text

| Candidate | Exact artifact and license | GB10/runtime/API evidence | Expected memory and storage | Decision and missing prerequisite |
|---|---|---|---|---|
| Parakeet 1.1B RNNT Multilingual Speech NIM | `nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual:1.5.0`; ARM64 digest `sha256:c9e798801652ccb10953284489ed212973ad25d86a04a74361bc2075a69a6f08`; NIM/model terms from `/v1/license`. Image is **11.97 GB compressed**. | Current matrix explicitly lists DGX Spark and `da-DK`, `en-GB`, `en-US` for `type=default`. Streaming REST is `POST /v1/audio/transcriptions`; Riva gRPC uses 50051 and WebSocket uses 9000. [`support matrix`](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/asr.html), [`deployment`](https://docs.nvidia.com/nim/speech/latest/asr/deploy-asr-models/parakeet-rnnt.html) | Smallest useful streaming profile `diarizer=sortformer,mode=str,type=default,vad=silero`: publisher reports **12.83 GB GPU + 7.061 GB CPU**. Offline is 25.56 + 7.381 GB; `mode=all` is 49.99 + 19.32 GB. On UMA, both compete in one 128 GB pool. | **Implementation-ready for qualification; rollout entitlement-gated.** Missing NVIDIA AI Enterprise self-hosting entitlement, NGC credentials, exact license capture, cache/export hashes, resolution of NVIDIA's contradictory generic “x86_64 only” prerequisite, and Danish/English WER/latency/memory evidence. RNNT has no word confidence and timestamps can share boundaries. |
| `openai/whisper-large-v3-turbo` | Revision `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`; MIT; primary safetensor **1,617,824,864 bytes**. [`model card`](https://huggingface.co/openai/whisper-large-v3-turbo) | vLLM supports Whisper through the OpenAI transcription API, and NVIDIA vLLM supports ARM64 Spark generally. No cited publisher source validates this exact model/runtime tuple on GB10. | **1.618 GB weights** plus audio buffers/runtime. Peak UMA and throughput are unknown. | **Blocked as the supported fallback; scheduled experiment.** Missing model-specific GB10 proof, exact serving image and decoder path, authenticated route/Wyoming adaptation, timestamp/streaming contract, and target WER/latency. |
| `nvidia/parakeet-rnnt-110m-da-dk` | Revision `ea77aca1c88bf322d969312d58d77f5aebf832ed`; NVIDIA Open Model License; `.nemo` **451,952,640 bytes**. [`model card`](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk), [`license`](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/) | Publisher validates NeMo 2.5 and Blackwell, but states Riva does not yet support it. The card exposes a Python `transcribe` call, not a network service. No exact GB10/ARM64 container is stated. | **0.452 GB artifact**; working memory unknown. | **Blocked.** Missing explicit GB10/ARM64 runtime, owned authenticated `/v1/audio/transcriptions` and Wyoming adapters, streaming/timestamps, and measured Danish accuracy. |
| `CoRal-project/roest-v3-whisper-1.5b` | Revision `7182cced29631ef7d7776eb8c01f24624b9b9b04`; custom OpenRAIL-derived license; weights **3,087,131,264 bytes**. [`model card`](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b), [`license`](https://huggingface.co/Alvenir/coral-1-whisper-large/blob/main/LICENSE) | Publisher gives a Transformers pipeline, not GB10/ARM64 or service evidence. | **3.087 GB weights** plus runtime; peak UMA unknown. | **Blocked; scheduled Danish quality challenger only.** Missing license/use-case approval, GB10 runtime proof, service adapters, and repository benchmark evidence. |

## Text-to-speech

| Candidate | Exact artifact and license | ARM64/GB10/runtime/API evidence | Expected memory and storage | Decision and missing prerequisite |
|---|---|---|---|---|
| Piper `da_DK-talesyntese-medium` | Voice revision `1162a9173d0ce503555aed757976b7a9912eae4c`; ONNX **63,201,294 bytes** plus 4,878-byte config. Piper 1.8.0 commit `639388b6317fc4731e91d53da42aea68fd4166ff`; Linux ARM64 wheel **34,131,751 bytes**, SHA-256 `3f60c1917de6d8e8033f395878ad3f88f6dfee88a8b05f98971a275f76a38484`. Runtime GPL-3.0; voice repository MIT; dataset CC0. [`runtime`](https://github.com/OHF-Voice/piper1-gpl), [`voice`](https://huggingface.co/rhasspy/piper-voices/tree/1162a9173d0ce503555aed757976b7a9912eae4c/da/da_DK/talesyntese/medium) | Exact Linux ARM64 wheel exists; this is CPU inference, so no GB10 kernel dependency. Native HTTP is `POST /synthesize` returning WAV, not the platform's `/v1/audio/speech`; native Wyoming is also absent. [`HTTP API`](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md) | Known artifacts are **about 97.3 MB**. Publisher does not state peak RAM or first-audio latency for GX10. | **Implementation-ready for adapter work; blocked from active route until integrated.** Smallest resident TTS baseline. Missing authenticated OpenAI speech and Wyoming adapters, GPL distribution/compliance review, Danish pronunciation/latency/load benchmark, and measured RAM. |
| Chatterbox TTS Multilingual Speech NIM | `nvcr.io/nim/nvidia/chatterbox-tts-multilingual:1.1.0`; upstream 500M model MIT; NIM runtime terms apply. NGC reports **16.43 GB compressed**, but its public page does not expose the ARM64 manifest digest. | NVIDIA matrix explicitly lists DGX Spark and `da-DK`/`en-US`. REST endpoints are `/v1/audio/synthesize` and `/v1/audio/synthesize_online`; WebSocket 9000 and Riva gRPC 50051. [`matrix`](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/tts.html), [`tutorial`](https://docs.nvidia.com/nim/speech/latest/get-started/tutorials/tts.html) | Only Spark profile is batch 8: **44.61 GiB GPU + 4.86 GiB host** in the shared pool. Input limit is 500 characters. | **Blocked from rollout; scheduled challenger.** Missing NVAIE entitlement, authenticated resolution of the Linux ARM64 digest, cache hashes/license capture, platform API/Wyoming adaptation, and Danish quality evidence. NVIDIA warns non-English output can mispronounce, mix languages, or hallucinate words. Too large for the initial resident set. |
| Direct `ResembleAI/chatterbox` multilingual | Revision `b535042c2ac08f66a8f2d486679efcac3228c07c`; MIT; publisher lists Danish and English. [`repository`](https://github.com/resemble-ai/chatterbox), [`license`](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE) | Publisher documents Python/CUDA, not GB10/ARM64 validation or a stable service protocol. | HF repository is about **5.32 GB**; peak UMA unknown. | **Blocked.** Missing exact GB10 runtime/container, immutable weight-file manifest, authenticated speech/Wyoming API, streaming/concurrency behavior, and target benchmark. |

## Embeddings and reranking

These are two different stages: embedding creates/query-searches an index; reranking scores only the retrieved top-K. A model or dimension change requires re-indexing. Keep query/document instruction semantics and score interpretation in the persisted index manifest.

| Candidate | Exact artifact and license | GB10/runtime/API evidence | Expected memory and storage | Decision and missing prerequisite |
|---|---|---|---|---|
| Text embedding: `Qwen/Qwen3-Embedding-4B-GGUF`, `Qwen3-Embedding-4B-Q8_0.gguf` | Revision `f4602530db1d980e16da9d7d3a70294cf5c190be`; LFS SHA-256 `b60ae5ce2dd6a0b77f82cadf21def1f310a3e10cde380ad0081b07a9d416949d`; Apache-2.0. | NVIDIA Spark playbook commit `347390338d67262394710802d92b4e48bfc6001c` serves this exact file with CUDA llama.cpp `--embeddings`; OpenAI-compatible embeddings. [`artifact`](https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/tree/f4602530db1d980e16da9d7d3a70294cf5c190be), [`playbook`](https://github.com/NVIDIA/dgx-spark-playbooks/blob/347390338d67262394710802d92b4e48bfc6001c/nvidia/multi-agent-chatbot/assets/docker-compose-models.yml) | File is **4,279,660,224 bytes**. Runtime memory is unpublished. | **Implementation-ready for qualification.** Missing immutable digest for the locally built llama.cpp image, corpus retrieval benchmark, chosen 32–2,560 dimension, batch/context memory, and authenticated edge integration. No paired Qwen reranker has exact GB10 validation. |
| Text embedding NIM: `nvidia/nemotron-3-embed-1b` | NIM `nvcr.io/nim/nvidia/nemotron-3-embed-1b:2.3`; BF16 checkpoint revision `c0c9fea93ea424587517f2c59e20db9f1d6bf615`; OpenMDW-1.1. | NIM 2.3 matrix explicitly lists ARM64 GB10. `POST /v1/embeddings`, with `input_type=query|passage`; 2,048 dimensions and 4,096-token limit. [`matrix`](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/2.3/support-matrix.html), [`API`](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/2.3/use-the-api-openai.html) | 1,141M parameters; BF16 weight estimate **about 2.13 GiB**. No GB10 working-memory or complete cache size is published. | **Implementation-ready; entitlement/digest-gated.** Missing NVAIE/NGC access, Linux ARM64 OCI digest, cache hashes, license capture, corpus benchmark, and mixed-load memory. |
| Text/image reranking NIM: `nvidia/llama-nemotron-rerank-vl-1b-v2` | NIM `nvcr.io/nim/nvidia/llama-nemotron-rerank-vl-1b-v2:2.3`; checkpoint revision `0c733be3b6451289f4288c0b170376d1fbeda55a`; NVIDIA Open Model License plus Llama terms. | NIM 2.3 explicitly supports ARM64 GB10 FP16. `POST /v1/ranking`; text query and up to 512 text/image passages; GB10 limit 8,192 tokens. [`matrix`](https://docs.nvidia.com/nim/nemo-retriever/text-reranking/2.3/support-matrix.html), [`API`](https://docs.nvidia.com/nim/nemo-retriever/text-reranking/2.3/use-the-api-openai.html) | Generic fallback reports **7.30 GB GPU and 3.10 GB disk**, not a GB10 measurement. FP8 falls back to FP16 on GB10. | **Implementation-ready; entitlement/digest-gated.** Missing NVAIE/NGC access, OCI/cache hashes, exact Llama terms capture, text corpus and document-image benchmark, and GB10 UMA measurement. |
| Qwen3 safetensors embedding/reranker family | Small control: `Qwen3-Embedding-0.6B` revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` + `Qwen3-Reranker-0.6B` revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`; Apache-2.0. | Qwen and vLLM document `/v1/embeddings`, `/score`, and Cohere/Jina-compatible `/v1/rerank`. No first-party source validates these exact safetensors checkpoints on GB10. [`embedding card`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [`reranking API`](https://docs.vllm.ai/en/latest/models/pooling_models/scoring/) | Exact weight files are about **1.192 GB each; 2.383 GB combined**. Larger 4B/8B pairs require about 15.5/30+ GB weight-only. | **Blocked as a supported deployment; scheduled control.** Missing model-specific GB10 runtime proof, exact runtime digest/overrides, corpus benchmark, index schema, and peak memory. |

The lowest-risk explicit-GB10 pair is Nemotron 3 Embed 1B followed by Llama Nemotron VL Rerank 1B in text mode. This is a support/readiness recommendation, not a quality claim. Keep both scheduled until a real corpus and latency requirement justify residency.

## Multimodal documents and vision

PDF/PPT ingestion requires a separate, pinned page renderer. OCR/parse APIs below accept images, not whole documents. Page ordering, render DPI/colorspace, and document hash belong in the ingestion manifest.

| Candidate | Exact artifact and license | GB10/runtime/API evidence | Expected memory and storage | Decision and missing prerequisite |
|---|---|---|---|---|
| Nemotron OCR v2 NIM | Model `nvidia/nemotron-ocr-v2`, revision `0e83e83f17943524b90afa6c0fd82ac2bc1a40ca`; `nvcr.io/nim/nvidia/nemotron-ocr-v2:2.0`; NVIDIA software/AI product terms and AI Foundation Models Community License. | Current 2.0 matrix explicitly names `NVIDIA-GB10`/DGX Spark FP16. `POST /v1/ocr` accepts one or more base64 JPEG/PNG images and returns text, confidence, and normalized polygon boxes with word/sentence/paragraph merge levels. [`matrix`](https://docs.nvidia.com/nim/ingestion/image-ocr/2.0/support-matrix.html), [`API`](https://docs.nvidia.com/nim/ingestion/image-ocr/2.0/api-reference.html) | NVIDIA reports GB10 memory as **N/A** because it is UMA and publishes no complete disk size. Throughput mode uses two engines/batch 16 and more memory than latency mode. | **Implementation-ready; entitlement/digest-gated.** Start in latency mode. Missing NVAIE/NGC access, OCI/cache hashes and terms capture, pinned page renderer, authenticated ingestion route, document accuracy/throughput test, and measured UMA/disk. |
| Reuse `Qwen/Qwen3.8-27B-FP8` | Same mandatory-wave revision/license above. | Publisher supports image/video chat, but current recipe verifies text and lacks a GB10 path. | No extra weights if it wins the text benchmark; visual working-memory peak remains unknown. | **Blocked, but highest-leverage hypothesis.** If it wins text and its GB10 visual path passes, reuse avoids a second resident VLM. Do not plan residency around this until measured. |
| `nvidia/Qwen3.6-27B-NVFP4` | Revision `0893e1606ff3d5f97a441f405d5fc541a6bdf404`; Apache-2.0. | vLLM recipe explicitly validates one GB10 and OpenAI chat, but requires vLLM >=0.28.0; its current image is a moving nightly. [`recipe`](https://recipes.vllm.ai/Qwen/Qwen3.6-27B), [`artifact`](https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4) | Publisher states **about 22 GB weights**; context/KV/vision peaks additional. | **Blocked on immutable runtime pin; scheduled VLM challenger.** Resolve a Linux ARM64 digest for a released >=0.28 image or freeze a nightly, then measure API/vision/memory. |
| Nemotron Parse 2.0 | `nvidia/NVIDIA-Nemotron-Parse-2.0` revision `b6742064f4a8cf22a10383ece5e7fbead355ac04`; OpenMDW-1.1; tokenizer CC-BY-4.0. Publisher validates vLLM 0.20–0.26. | Publisher test hardware is A100/H100. “Blackwell” and multi-arch labels are not explicit GB10 support. One RGB page via OpenAI chat with exact control-token prompt. [`model card`](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0) | Safetensor **3,612,281,104 bytes**; NIM compressed size **11.27 GB**; GB10 working memory unknown. | **Blocked for supported GB10 use; scheduled port experiment only.** Missing model-specific GB10 support, compatible immutable runtime, exact prompt/parser fixture, page-render pipeline, and layout/table benchmark. |
| Phi-4 multimodal NVFP4 | `nvidia/Phi-4-multimodal-instruct-NVFP4` revision `617cfabb9ad6c2c6e318fd21c1961536b84f65a1`; TensorRT-LLM `1.3.0rc13`; NVIDIA Open Model License plus MIT base. | NVIDIA Spark playbook demonstrates local image inference on one Spark, but not the desired multimodal service request. The model card excludes deployment in the EU. [`playbook`](https://build.nvidia.com/spark/trt-llm/instructions), [`card`](https://huggingface.co/nvidia/Phi-4-multimodal-instruct-NVFP4) | 5.6B parameters; no complete publisher GB10 memory/disk figure. | **Blocked for this Denmark deployment.** Geography is a hard blocker; service API and immutable container digest are also missing. |
| GLM-5.3-Flash NIM | `nvcr.io/nim/zai-org/glm-5.3-flash:2.1.2-variant`; MIT upstream. | NVIDIA requires **two** 128 GB GB10 nodes with ConnectX-7/RoCE. OpenAI chat supports image/video. [`matrix`](https://docs.nvidia.com/nim/vision-language-models/2.1.2-variant/support-matrix.html), [`deployment`](https://docs.nvidia.com/nim/vision-language-models/latest/deploy-on-dgx-spark.html) | Publisher states **208 GB** NIM cache. | **Blocked on one GX10.** A second Spark, 100 Gb/s interconnect/RoCE, duplicate immutable deployment, and 208 GB cache are outside the current plan. |

## Smallest safe resident set

Start with only:

1. **One** mandatory-wave text model: Qwen3.6 35B A3B for integration, or Nemotron target-only for the first latency run. Never keep all mandatory candidates resident.
2. Parakeet multilingual NIM's smallest Danish-capable streaming profile, only if the NVAIE/NGC gates are satisfied.
3. Piper Danish TTS as a CPU process after the authenticated OpenAI/Wyoming adapters exist.
4. Platform telemetry and the authenticated edge; no retrieval, OCR, or second VLM resident initially.

Known lower bound with Nemotron target is roughly **41.6 GB**: 21.562 GB text weights + 12.83 GB and 7.061 GB publisher speech profile allocations + about 0.1 GB Piper artifacts. With Qwen3.6, it is roughly **43.4 GB**. These are not peak-process predictions: they omit the text KV cache/workspaces, NIM cache behavior, Piper RAM, OS/display, containers, telemetry, and filesystem cache. Enforce the repository's minimum 10% memory headroom and accept the set only after the real mixed-load run.

If NVAIE is unavailable, there is no equally grounded Danish STT substitute yet. Keep Whisper Turbo scheduled and treat local speech rollout as blocked rather than silently substituting an unverified path.

## Scheduled alternatives and 1 TB storage

- Run the mandatory text candidates one at a time; evaluate Nemotron target before target+DSpark.
- Load embeddings/rerankers only for indexing/evaluation until a measured query-latency requirement justifies residency.
- Run OCR/document/VLM candidates in maintenance windows, after draining the resident text service if their measured coexistence envelope is unknown.
- Keep Chatterbox TTS scheduled; its published Spark profile is too large for the initial resident set.
- Model discovery is metadata-only and review-only. It must not download, cache, activate, promote, or rewrite aliases. Human approval creates an immutable acquisition manifest; benchmark evidence may rank but never auto-promotes.

Known artifacts for the mandatory text wave, Parakeet image, Piper runtime/voice, and Qwen embedding GGUF total about **93.5 GB** before unpacked container layers, NIM-downloaded caches, duplicate revisions, staging, and logs. The publisher does not disclose complete OCR/retrieval NIM cache sizes, so absence of those numbers is a storage prerequisite, not zero. Retain the repository policy of at most 500 GB active artifacts, 120 GB staging, and 100 GB free; garbage-collect only unreferenced revisions after an immutable active/rollback manifest exists.

## Promotion gates and missing prerequisites

The following are mandatory; absence is failure, not success:

1. **Machine baseline:** record GX10 OS image, kernel, driver, CUDA compatibility, firmware, 128 GB UMA behavior, and free disk. Do not copy DGX Spark release pins onto the partner system.
2. **Artifact manifest:** exact model revision/file hashes/license, Linux ARM64 OCI digest/signature, runtime dependency lock, and hashes of any first-start NIM/model cache. Tags and `main` are insufficient.
3. **Access and legal:** NGC credentials; NVAIE self-hosting entitlement for NIMs; acceptance/archive of NIM, OpenMDW, NVIDIA Open Model, Llama, GPL, dataset, and custom OpenRAIL terms as applicable. Geography exclusions fail closed.
4. **Network/API:** authenticated binding at `10.77.10.10:8000`; unauthenticated health only at the edge; no direct public NIM ports; fixed OpenAI chat/transcription/speech contracts and Wyoming adapters where required.
5. **Text behavior:** context/concurrency/KV limits, reasoning/tool parser fixtures, Codex compatibility through documented interfaces only, alias routing, and proof that private aliases never cloud-fallback. The automatic cloud→local→cloud flow remains gated and must not gain undocumented Codex fields.
6. **Speech behavior:** Danish/English WER set, timestamps/streaming semantics, realtime factor, first/complete transcript latency, TTS pronunciation/naturalness, first audio, long-text splitting, cancellation, and concurrent text-load memory.
7. **Retrieval behavior:** representative corpus, instruction/query semantics, embedding dimension/type/distance, re-index policy, top-K, rerank score semantics, recall/quality, and latency/memory.
8. **Document behavior:** deterministic page renderer, accepted formats/DPI/colorspace, page ordering, image limits, OCR/layout/table ground truth, malformed/encrypted document handling, and content-retention policy.
9. **Resource proof:** startup, idle, warm, peak, and recovery memory/storage under isolated and mixed load; context and batch limits; minimum 10% UMA headroom; no swap-thrash acceptance.
10. **Evidence-only ranking:** write schema-versioned benchmark evidence with plan/release identity and deterministic candidate eligibility/rank. Zero or one winner is valid. A discovery/update monitor must distinguish `unbenchmarked`, `not_better`, and `outperforms_active`; it never downloads or promotes.
11. **Observability/privacy:** readiness and useful saturation/failure metrics without prompt, response, transcript, document content, or request-ID labels.
12. **Rollback:** retain the active and last-known-good immutable tuples and prove rollback before promotion.

Until these gates pass, “deployable” means a publisher-supported qualification path, not production readiness.
