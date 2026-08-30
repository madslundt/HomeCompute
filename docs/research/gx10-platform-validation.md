# GX10 platform handoff validation

> Model-shortlist note: this platform review predates the same-day
> [model omission review](model-shortlist-omission-review.md) and
> [MTP review](mtp-model-claim-review.md). Those notes and the canonical
> [installation recommendation](llm-installation-recommendation.md) supersede
> its earlier Qwen3.5-first model analysis; the architecture and runtime
> conclusions remain current.

**Research date:** 2026-08-25  
**Status:** Recommendation against current primary sources and the repository design baseline  
**Scope:** ASUS Ascent GX10 / NVIDIA GB10 text serving, gateway, containers, Open WebUI, n8n, and Home Assistant integration. No setup or configuration was changed.

## Local-integration addendum

The primary-source conclusions below remain valid for a greenfield inference
path: direct vLLM and Caddy are the smallest protocol baseline, and LiteLLM must
not obscure that test. A subsequent local inventory found two material existing
systems outside the original repository baseline:

- `ai_home` already defines LiteLLM as the shared cloud-model gateway; and
- `meeting-assistant` already owns transcription selection, rolling/final
  summaries, and durable meeting records.

Therefore the integrated production recommendation is now **Caddy -> existing
LiteLLM -> GB10 vLLM/cloud**, with direct Caddy/vLLM retained for qualification.
This avoids deploying a second LiteLLM and keeps cloud routing available during
a GB10 outage. Likewise, Plaud processing should extend Meeting Assistant
rather than add a new meeting worker/database. See `docs/current-state.md`,
ADR-011, and ADR-012. The existing LiteLLM is only a candidate until it is
pinned and passes the full Responses/tool/privacy/latency suite.

## Outcome

Do **not** replace the repository's current architecture with the pasted handoff verbatim. The handoff is directionally sound, but its mandatory LiteLLM layer and broad initial service list would add state, translation, and resource contention before those capabilities are needed.

Keep the current staged design:

```text
Codex / n8n / Home Assistant
             |
             v
      Caddy: https://ai.home
             |
             v
 NVIDIA-qualified vLLM + one qualified text model

Caddy fixed routes --> separate STT/TTS services

Optional later: Caddy --> LiteLLM --> multiple text backends/cloud
```

This remains the smallest design that meets the present requirements while
preserving a clean upgrade path. The main new evidence is that NVIDIA now
publishes a Spark-specific, agent-ready
`nvidia/Qwen3.6-35B-A3B-NVFP4` vLLM recipe. It is the first smoke-test
candidate, not a production promotion without a pinned-image start-up test and
local Danish, tool, latency, MTP, and mixed-load evidence. Qwen3.5 is retained
only as a regression comparison if measured Danish behavior warrants it.

## What the handoff gets right

| Claim or direction | Validation | Consequence |
| --- | --- | --- |
| The ASUS Ascent GX10 is a GB10 system with 128 GB coherent unified memory. | Verified by ASUS. It uses the GB10 Grace Blackwell Superchip, DGX OS/Ubuntu, and 128 GB unified memory. [ASUS product page](https://www.asus.com/us/networking-iot-servers/desktop-ai-supercomputer/ultra-small-ai-supercomputers/asus-ascent-gx10/) | Treat NVIDIA DGX Spark software guidance as the primary compatibility baseline for this ASUS partner system, then verify the shipped ASUS image/firmware. |
| vLLM is the best first general-purpose text runtime. | NVIDIA's current Spark playbook supports ARM64, Blackwell, CUDA 13, the NVIDIA Container Toolkit, NGC vLLM images, and a large tested model matrix. vLLM also documents Codex over the Responses API and model-specific tool parsers. [NVIDIA vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md), [vLLM Codex integration](https://docs.vllm.ai/en/stable/serving/integrations/codex/) | Retain vLLM as the lead, using a qualified NVIDIA/ARM64-capable image rather than assuming an arbitrary upstream wheel or floating image works. |
| A 30–40B MoE with about 3B active parameters is a strong first automation candidate. | NVIDIA's Qwen3.6 NVFP4 card describes a 35B-total/3B-active, Blackwell-compatible, vLLM-ready artifact and includes a DGX Spark command with MTP, FP8 KV cache, and model-specific parsers. [NVIDIA Qwen3.6 NVFP4 model card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4) | Smoke-test NVIDIA's exact Qwen3.6 NVFP4 recipe first and compare MTP on/off; do not call it the installation winner yet. The publisher provides no Danish-specific acceptance evidence, so Danish and code-switching remain local gates. Do not reserve 262K context merely because the model supports it. |
| Consumers should use stable capability names, not artifact names. | vLLM's Codex setup requires the requested name to match `--served-model-name`; n8n's OpenAI credential supports a base-URL override and model discovery from `/models`. [vLLM Codex integration](https://docs.vllm.ai/en/stable/serving/integrations/codex/), [n8n OpenAI credential source](https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/credentials/OpenAiApi.credentials.ts) | Use task names such as `automation`, `research`, `coding`, `home`, and `meeting`. One vLLM process may expose several names for one model during the first phase. |
| Docker Compose is appropriate; Kubernetes is not needed. | DGX Spark includes the NVIDIA container runtime, and Docker Compose supports explicit NVIDIA GPU device reservations. [DGX Spark container runtime](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html), [Docker Compose GPU support](https://docs.docker.com/compose/how-tos/gpu-support/) | Use Compose v2, private networks, health checks, persistent model cache mounts, and `capabilities: [gpu]`. Kubernetes would solve no current single-appliance requirement. |
| Home Assistant speech should use replaceable local services. | Home Assistant's supported local service boundary is Wyoming, which connects external Whisper, Piper, wake-word, and other voice services. [Home Assistant Wyoming integration](https://www.home-assistant.io/integrations/wyoming) | A generic `/v1/audio/*` API may exist internally, but the Home Assistant-facing adapter must implement Wyoming (or another officially supported integration). |

## Changes I would make to the pasted handoff

### 1. Keep LiteLLM out of the direct runtime baseline

LiteLLM does provide `/responses`, streaming, routing, fallbacks, load balancing, cost tracking, logging, and logical model definitions. It is a credible later router. Its own documentation also says it may bridge Chat Completions when a provider lacks native Responses support. That translation is precisely why it should not sit in the first Codex/vLLM compatibility test. [LiteLLM Responses API](https://docs.litellm.ai/docs/response_api), [LiteLLM model management](https://docs.litellm.ai/docs/proxy/model_management)

Recommended gate:

- Start with direct Codex-to-vLLM qualification, then Caddy pass-through.
- In a greenfield build, add LiteLLM only when independent backends or virtual
  keys/quotas are concrete requirements. In this integrated setup they already
  are, and an instance already exists, so qualify that instance rather than add
  another.
- Before promotion, rerun the complete Responses SSE, multi-turn tool, cancellation, privacy, and latency suite through LiteLLM.
- Keep Home Assistant tool execution local-only and fail closed; do not enable hidden model fallback for actions.

This preserves the handoff's good abstraction goal without making a stateful AI gateway a prerequisite for one backend.

### 2. Keep vLLM primary; benchmark challengers rather than deploying them all

NVIDIA now documents all of the serious GB10 options, but their API and artifact roles differ:

| Runtime | Current primary-source position | Recommended role |
| --- | --- | --- |
| **vLLM** | NVIDIA Spark recipe, broad tested matrix, production metrics, OpenAI server, and explicit Codex Responses integration. [NVIDIA recipe](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md), [vLLM Codex docs](https://docs.vllm.ai/en/stable/serving/integrations/codex/) | Primary text-server candidate. |
| **TensorRT-LLM** | NVIDIA provides a Spark playbook and OpenAI-compatible serving, but its current Spark matrix does not list Qwen3.5/3.6-35B-A3B. [TensorRT-LLM Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md) | Conditional performance challenger only when the winning model/revision is supported. Promote only for a material measured gain and API/tool parity. |
| **SGLang** | NVIDIA provides a CUDA 13 Spark container recipe and supported-model list; its public serving guide is centered on Chat Completions. The Spark matrix currently stops at Qwen3-32B, and a current SGLang gateway issue reports a Codex `custom` tool-type incompatibility. [NVIDIA SGLang playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/sglang/README.md), [SGLang OpenAI API docs](https://docs.sglang.io/docs/basic_usage/openai_api_completions), [SGLang gateway issue](https://github.com/sgl-project/sglang/issues/30781) | Conditional Chat/structured-output throughput challenger, not the first Codex boundary until Responses and tool behavior are proven. |
| **llama.cpp** | NVIDIA documents an `sm_121` CUDA build and GGUF serving on Spark. Current `llama-server` exposes `/v1/responses`, implemented by converting the request to Chat Completions. [NVIDIA llama.cpp playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/llama-cpp/README.md), [llama-server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | GGUF/quantized compatibility fallback and lightweight baseline. Qualify translated Responses semantics before Codex use. |
| **Ollama** | NVIDIA supports Ollama on Spark and secure remote access through NVIDIA Sync. [NVIDIA Ollama playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/ollama/README.md) | Exploration and model acquisition only. Avoid duplicating the production model server. |
| **MLX** | MLX now has an official Linux/aarch64 CUDA 13 install option and SM121a build path, so the old statement "MLX is Apple-only" is no longer true. Its own MLX-LM server is not recommended for production and Responses support remains an open change. [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html), [MLX-LM server warning](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md), [open Responses change](https://github.com/ml-explore/mlx-lm/pull/1207) | Emerging experiment only; exclude from the initial serving benchmark. Revisit if the CUDA serving ecosystem matures or a required model depends on it. |
| **NVIDIA NIM** | NVIDIA provides NIM on Spark with curated profiles, health endpoints, Responses, and metrics; the LLM NIM runtime is backed by vLLM rather than being a separate engine. [NIM on Spark](https://build.nvidia.com/spark/nim-llm), [NIM API reference](https://docs.nvidia.com/nim/large-language-models/latest/reference/api-reference.html) | Conditional packaging/operations alternative if a supported profile and applicable terms reduce lifecycle work. Do not benchmark it as a distinct inference engine. |

Use NVIDIA's same-workload Spark methodology for vLLM, TensorRT-LLM, SGLang, and llama.cpp rather than comparing unrelated published numbers. It covers TTFT, time per output token, inter-token latency, end-to-end latency, and throughput. [NVIDIA Spark performance guide](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/connect-two-sparks/assets/performance_benchmarking_guide.md)

### 3. Do not move PostgreSQL, workflow state, or general application services to GX10

The handoff's possible service list (`postgres`, `redis`, meeting workers, research workers, monitoring) conflicts with the accepted inference-appliance boundary. Keep Home Assistant, Node-RED, n8n, MCP servers, application databases, and durable queues on their current hosts. Only GPU inference and directly supporting edge/telemetry/artifact functions belong on GX10.

If long transcription needs a queue, reuse an existing broker or add the smallest queue on the application host. A GB10 reboot or model-service rebuild must not lose the authoritative job record. Temporary worker scratch space may live on GX10; original audio, transcripts, and workflow state should remain in the existing durable storage design.

### 4. Treat Open WebUI as optional UX, not infrastructure

The pasted handoff does not require a human chat UI, and the repository explicitly keeps Codex as the developer harness. Do not install Open WebUI initially.

If a household/admin chat UI later becomes a real requirement:

- Run standard Open WebUI without bundled Ollama and connect it to `https://ai.home/v1`; Open WebUI officially supports OpenAI-compatible, Open Responses, vLLM, and llama.cpp providers. [Open WebUI provider connections](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/)
- Expose only qualified aliases through `/v1/models`; do not let the UI bypass Caddy to runtime ports.
- Place its persistent volume and backup responsibility on an application host, not the inference appliance. Open WebUI stores users, chats, uploads, vector data, and configuration under `/app/backend/data`. [Open WebUI quick start](https://docs.openwebui.com/getting-started/quick-start/), [Open WebUI update/backup guide](https://docs.openwebui.com/getting-started/updating/)
- Pin a release/digest. Open WebUI documents `main`/`latest` as rolling tags, which conflicts with the production pinning requirement.
- Do not give it GPU access unless a measured local feature requires it; the LLM remains on vLLM.

NVIDIA's Open WebUI Spark playbook is a valid demo, but it intentionally bundles Ollama, model management, persistent UI data, and GPU inference. That is the wrong topology for this platform because it duplicates the selected server and broadens GX10 state. [NVIDIA Open WebUI playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/open-webui/README.md)

### 5. Tighten container and operating-system rules

- Baseline the appliance on its shipped, supported DGX OS release. NVIDIA's current release notes list DGX OS 7.5.0, driver 580.159.03, CUDA 13.0.2, and unified-memory OOM improvements; record the actual ASUS versions before selecting an image. [DGX Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
- Use NVIDIA Container Toolkit GPU access, never a privileged container just to reach the GPU.
- Bind only Caddy to the LAN. Backend ports stay on a private Compose network; avoid `network_mode: host` unless a measured runtime requirement justifies it.
- Pin image digest, model revision, tokenizer/chat template, reasoning parser, tool parser, quantization, context limit, and launch flags as one qualified tuple. NVIDIA examples sometimes use `latest`; examples are not production pins.
- Reserve memory for the OS/display, KV cache, CUDA workspaces, STT/TTS, and recovery. NVIDIA explicitly warns that Spark's unified memory can still produce memory issues even when nominal capacity appears sufficient. [NVIDIA vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)
- Use `restart: unless-stopped`, separate liveness from model readiness, cap logs, and test a clean reboot, OOM recovery, and a 24-hour mixed-load soak.

## Integration consequences

### n8n

n8n can use the stable endpoint without a custom node: its OpenAI credential has a configurable base URL and authenticates with a bearer key, and the current OpenAI Chat Model node supports custom model IDs and a Responses toggle. [n8n OpenAI credential](https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/credentials/OpenAiApi.credentials.ts), [n8n OpenAI Chat Model source](https://github.com/n8n-io/n8n/blob/master/packages/%40n8n/nodes-langchain/nodes/llms/LMChatOpenAi/LmChatOpenAi.node.ts)

Migrate one low-risk workflow first. Verify structured output/tool behavior against the exact n8n node version; a configurable base URL proves reachability, not feature equivalence.

### Home Assistant

Use native deterministic Assist intents before LLM fallback. Home Assistant remains the only component allowed to expose and execute a restricted set of entities/tools. For text reasoning, Node-RED can call `ai.home` directly with a schema-constrained request; a direct official Ollama integration exists, but adopting it would couple HA to the exploratory runtime. For speech, use Wyoming-facing adapters for local STT/TTS. [Home Assistant local voice guide](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/), [Wyoming integration](https://www.home-assistant.io/integrations/wyoming)

### Meeting diarization

Use `pyannote/speaker-diarization-community-1` as the first local diarization
candidate, not as a preselected production winner. The official pipeline can
run locally, accepts in-memory waveforms, can be moved to CUDA, returns ordinary
and exclusive speaker diarization, and supports known/min/max speaker counts.
Exclusive diarization is useful for aligning speaker turns with ASR timestamps.
The weights are CC-BY-4.0 but gated: an operator must accept the Hugging Face
conditions and supply a token for initial download. Cache an accepted pinned
revision so runtime processing can remain offline.
([pyannote Community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1),
[official pyannote.audio repository](https://github.com/pyannote/pyannote-audio))

Do not use hosted `precision-2` for the local-only default. No first-party
NVIDIA DGX Spark pyannote recipe was found, so ARM64/CUDA/container startup,
memory, long-file speed, overlap handling, diarization error rate, and
word-to-speaker merge accuracy remain GB10 gates. Diarization labels are
anonymous speakers, not identity claims.

### Cloud fallback

Keep deterministic, consumer-owned fallback at first:

- Home actions and private meetings: local retry, then pending/fail closed.
- Public research: n8n may explicitly choose a cloud alias/provider after policy checks.
- Coding: Codex orchestration records the switch; do not hide it in a gateway.

After the direct-runtime baseline passes, qualify the discovered existing
LiteLLM and move only proven policies into it. Sensitive payload eligibility
must remain explicit in the authenticated alias/key policy, not inferred by
another model.

## Recommended validation order

1. Record the actual GX10 DGX OS, driver, CUDA, firmware, Docker, and NVIDIA Container Toolkit versions.
2. Smoke-test the NVIDIA `nvidia/Qwen3.6-35B-A3B-NVFP4` recipe first on a
   pinned vLLM artifact, using a realistic 16K/32K starting context and MTP
   on/off. Stage Gemma 4 31B NVFP4 and Qwen3.8-27B only as challengers; promote
   none until it passes.
3. Qualify direct `/v1/responses`, SSE termination, structured output, multi-turn tools, cancellation, Danish/English/code-switching, and one real n8n workflow. Exercise roughly 1, 10, and 40+ tools, including namespaced MCP tools; NVIDIA's Qwen3.6 recipe has a current model-specific report of malformed calls with a large tool surface. [NVIDIA Spark playbook issue](https://github.com/NVIDIA/dgx-spark-playbooks/issues/89)
4. Add Caddy and repeat the same contract, privacy, and latency tests through `https://ai.home`.
5. Run llama.cpp as the required quantized baseline. Benchmark TensorRT-LLM only if it supports the winning model/revision; add SGLang only if Chat/structured-output throughput under the real workload is a decision factor.
6. Add Wyoming-backed STT/TTS one service at a time and prove Home Assistant voice remains responsive during a background transcription.
7. After the direct/Caddy baseline, qualify the existing LiteLLM because the
   discovered setup already has cloud backends and now requires centralized
   virtual keys and privacy policy; rerun the full contract suite.
8. Add Open WebUI only if a distinct non-Codex human-chat use case is accepted.

## Final recommendation

Keep **direct Caddy -> pinned NVIDIA-compatible vLLM** as the qualification
baseline. For the discovered integrated setup, promote the already-defined AI
Home LiteLLM only after the same suite passes, producing **Caddy -> existing
LiteLLM -> vLLM/cloud** without another gateway on GX10. Use one qualified
Qwen-class primary model, separate speech/diarization inference, durable state
off GX10, and explicit policy-based cloud fallback. Include NVIDIA's agent-ready
Qwen3.6-35B-A3B NVFP4 recipe and llama.cpp baseline in the first evaluation;
MLX's Linux CUDA path remains experimental for serving.
