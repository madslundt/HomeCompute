# GB10 inference runtime and gateway decision draft

**Research date:** 2026-08-25  
**Decision status:** Proposed for hardware validation  
**Scope:** Text inference runtime, Codex compatibility, and the northbound gateway for one NVIDIA GB10 / DGX Spark-class appliance. STT and TTS runtimes are separate decisions. Aula is not a component in this architecture; Aula content can only arrive as request data inside the existing n8n workflow.

## Evidence labels

- **Verified** means an official product document, source repository, specification, or vendor-maintained playbook directly supports the statement.
- **Inference** means a design conclusion derived from verified facts; it is not a vendor guarantee.
- **Hardware validation** means the statement must be proven on the actual GB10, pinned container/runtime, model, quantization, context length, and concurrency profile.

## Decision

Use the NVIDIA-tested **vLLM NGC container as the first text-serving runtime**, with a small edge reverse proxy and no stateful AI gateway in the initial proof of concept.

The initial topology should be:

```text
Codex on Mac ───────────┐
Home Assistant ────────┼── https://ai.home ── edge proxy ── vLLM text server
n8n ───────────────────┘                         │
                                                 ├── STT server by fixed path
                                                 └── TTS server by fixed path

Prometheus-compatible scraper ── edge /metrics and runtime /metrics
```

For the first acceptance run, `automation`, `research`, `coding`, `home`, and
`meeting` may be stable names for the same concrete vLLM model. vLLM accepts
multiple served model names for one server. This preserves the consumer
contract without prematurely loading several models. The existing AI Home
LiteLLM candidate owns policy and independent-backend mapping after a separate
Responses/tool-streaming qualification; a generic reverse proxy is not a safe
JSON `model` router.

The edge proxy recommendation is conditional:

- Use **Caddy** for the first trusted-LAN proof of concept because its official reverse proxy supports streaming flush, WebSockets, health checks, header controls, and Prometheus/OpenMetrics. It also keeps internal TLS straightforward.
- Standard Caddy does **not** include first-party request rate limiting. If per-consumer rate limiting is a hard deployment gate, either qualify a stateless LiteLLM layer or use **NGINX OSS** at the edge. Do not silently depend on a third-party Caddy module.
- Keep every runtime port private to loopback or an internal container network. The edge is the only LAN-facing service.

TensorRT-LLM is the performance challenger. Promote it for a particular model only if a controlled GB10 benchmark shows a material, repeatable latency/throughput or memory advantage and its Responses/tool behavior passes the Codex suite. Keep llama.cpp as the quantized-GGUF and unsupported-model fallback. Ollama is useful for experiments and model acquisition, but its native observability and current long-running Responses reliability are not strong enough for the primary service.

## Why vLLM is the lead

### GB10 support

**Verified.** NVIDIA publishes a DGX Spark vLLM playbook and a prebuilt `nvcr.io/nvidia/vllm` container for the ARM64/Blackwell/CUDA 13 platform. NVIDIA's current Spark catalog supplies tested recipes for more than 30 models. Use the NVIDIA-tested image and recipe rather than assuming that an arbitrary upstream wheel or `latest` tag supports SM 12.1 correctly. ([NVIDIA vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md), [NVIDIA build recipe](https://build.nvidia.com/spark/vllm))

**Verified known issue.** Upstream vLLM had an ARM64/SM 12.1 support report in 2026, while NVIDIA's subsequent NGC recipes explicitly support Spark. This is why the deployment artifact must be the tested NVIDIA image rather than an unconstrained upstream install. ([vLLM issue 36821](https://github.com/vllm-project/vllm/issues/36821))

### Codex and the Responses API

**Verified.** Current vLLM documents a first-class Codex integration using `wire_api = "responses"`, a `/v1` base URL, and a `model` matching `--served-model-name`. Its online server implements `/v1/responses`, streaming, response IDs/cancellation, Chat Completions, embeddings, and ASR endpoints. Tool and reasoning parsers remain model-specific. ([vLLM Codex integration](https://docs.vllm.ai/en/stable/serving/integrations/codex/), [vLLM online serving](https://docs.vllm.ai/en/latest/serving/online_serving/))

**Verified.** Codex CLI 0.149.1, released 2026-08-24, uses the Responses wire API; its shipped provider enum rejects the former Chat wire API. A custom provider is configured with a base such as `https://ai.home/v1`, and Codex appends `responses`. Codex sends tools, `tool_choice`, `parallel_tool_calls`, instructions, input items, optional reasoning controls, and `stream=true`. Its SSE client requires a terminal `response.completed`; an endpoint that merely resembles Chat Completions is insufficient. ([Codex 0.149.1 release](https://github.com/openai/codex/releases/tag/rust-v0.149.1), [provider implementation at 0.149.1](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/model-provider-info/src/lib.rs), [Responses request construction](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/codex-api/src/common.rs), [Responses SSE parser](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/codex-api/src/sse/responses.rs))

**Verified.** Codex itself connects to MCP servers and executes their calls. The GB10 runtime does not need to implement MCP; it must faithfully emit Responses `function_call` items and consume later `function_call_output` items. ([OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Codex protocol types](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/protocol/src/models.rs))

**Hardware validation.** vLLM has had Responses/Codex regressions involving developer roles, multi-turn calls, parallel tools, and namespaced tools. These reports do not negate the documented integration, but they make the exact vLLM version, model, chat template, reasoning parser, and tool parser part of the qualified artifact. ([developer-role issue](https://github.com/vllm-project/vllm/issues/42407), [multi-turn Codex issue](https://github.com/vllm-project/vllm/issues/45273), [namespaced-tools issue](https://github.com/vllm-project/vllm/issues/46737), [parallel tool-streaming issue](https://github.com/vllm-project/vllm/issues/39584))

### Serving and operations

**Verified.** vLLM exposes Prometheus metrics including time to first token and supports per-request timing data. For streaming clients, the final usage chunk can be requested with `stream_options.include_usage`. ([per-request metrics](https://docs.vllm.ai/en/latest/features/per_request_metrics/))

**Verified.** Request and output logging are separately controlled and default off in the current CLI. Debug-level request logging can expose prompts. vLLM API keys protect its versioned inference routes but do not protect every server route, which is another reason never to expose it directly. ([vLLM serve CLI](https://docs.vllm.ai/en/latest/cli/serve/))

**Verified.** vLLM provides FCFS and integer-priority scheduling, with lower integer values scheduled first. A nonzero request priority requires the priority scheduler. ([scheduler configuration](https://github.com/vllm-project/vllm/blob/main/vllm/config/scheduler.py), [request protocol](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/completion/protocol.py), [V1 guide](https://github.com/vllm-project/vllm/blob/main/docs/usage/v1_guide.md))

**Inference.** This primitive can represent P0 through P3, but it is not yet an end-to-end policy. Codex and generic OpenAI clients do not automatically send a vLLM-specific priority, and Caddy cannot safely inject a field into a streaming JSON request body. Either consumers must send the extension, a qualified model-aware gateway must derive it from authenticated identity, or a small dedicated Home Assistant instance must provide hard isolation.

**Verified.** vLLM does not promise general deterministic reproducibility because batching and performance optimizations affect execution. Its documented reproducibility scope is limited to the same hardware and vLLM version, with batch-invariant serving requirements. ([vLLM reproducibility](https://docs.vllm.ai/en/latest/usage/reproducibility/))

## Runtime comparison

Scores are decision aids, not benchmark results: 5 is the strongest fit for this platform, 1 the weakest. A dagger (†) marks a score dominated by hardware validation.

| Criterion | Priority | vLLM | TensorRT-LLM | llama.cpp | Ollama |
|---|---:|---:|---:|---:|---:|
| Explicit GB10 / Spark path | Critical | 5 | 5 | 5 | 5 |
| Model breadth for target roles | Critical | 4 | 3 | 5 | 4 |
| Codex integration | Critical | 5† | 2† | 2† | 4† |
| Multi-turn and parallel tools | Critical | 4† | 3† | 3† | 3† |
| Responses API and SSE | High | 5† | 3† | 3† | 3† |
| Expected GB10 performance | High | 4† | 5† | 3† | 3† |
| NVIDIA optimization | High | 4 | 5 | 3 | 3 |
| Native observability | High | 5 | 4 | 4 | 2 |
| Memory efficiency / quantization | High | 4† | 4† | 5† | 4† |
| Operational simplicity | Medium | 3 | 2 | 3 | 5 |
| Model switching | Medium | 2 | 2 | 5 | 5 |
| Reproducible deployment | Medium | 4 | 3 | 4 | 3 |
| **Decision** |  | **Primary** | **Benchmark challenger** | **Fallback** | **Experiment only** |

### TensorRT-LLM

**Verified.** NVIDIA publishes a Spark-specific TensorRT-LLM playbook, NGC containers, a model matrix, and `trtllm-serve` commands with model-specific reasoning and tool parsers. This is the most explicitly NVIDIA-optimized option. ([Spark TensorRT-LLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/trt-llm/README.md), [NVIDIA build recipe](https://build.nvidia.com/spark/trt-llm))

**Verified, but new.** Current TensorRT-LLM examples include a Responses client and Prometheus integration. Earlier serving documentation and the Spark recipes center on Chat Completions, and no official Codex-on-Spark integration was found. Treat Responses support as a version-specific, newly expanding surface. ([current serving examples](https://nvidia.github.io/TensorRT-LLM/latest/examples/trtllm_serve_examples.html), [official Responses client source](https://github.com/NVIDIA/TensorRT-LLM/blob/main/examples/serve/openai_responses_client.py), [Prometheus example](https://nvidia.github.io/TensorRT-LLM/examples/prometheus_metrics.html))

**Hardware validation.** The project exposes health and metrics endpoints and can export per-request performance data, but metrics are still described as evolving across backends. No primary-source, same-model, same-quantization GB10 comparison was found that proves it faster than vLLM for this workload. Its additional engine/configuration and parser coupling are justified only after the controlled benchmark. ([serve command](https://nvidia.github.io/TensorRT-LLM/commands/trtllm-serve.html), [LLM API reference](https://nvidia.github.io/TensorRT-LLM/llm-api/reference.html))

### llama.cpp

**Verified.** NVIDIA's Spark playbook documents a CUDA build targeting `121a-real`, GGUF models, and an OpenAI-compatible server. Upstream llama.cpp offers continuous batching, speculative decoding, function calling, a current `/v1/responses` implementation, Prometheus metrics, and router mode that can dynamically load and unload model presets. ([NVIDIA llama.cpp playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/llama-cpp/README.md), [llama.cpp repository](https://github.com/ggml-org/llama.cpp), [server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [function-calling documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md))

**Inference.** GGUF quantization and dynamic model loading give llama.cpp the best fallback position when a desired model does not fit or is unsupported by the lead runtime. That does not make it the safest Codex server: the Responses surface is newer, function calling depends heavily on the model's chat template, and aggressive KV-cache quantization can reduce tool-call quality.

**Verified known issues.** Current upstream reports cover namespaced MCP tool names, newer Codex tool types, tool-result validation, and templates that silently omit tools. Router-mode metrics also need validation rather than assuming one aggregate endpoint. ([namespaced tools](https://github.com/ggml-org/llama.cpp/issues/25193), [Codex tool-type issue](https://github.com/ggml-org/llama.cpp/issues/20156), [tool-result validation](https://github.com/ggml-org/llama.cpp/issues/23553), [template tool omission](https://github.com/ggml-org/llama.cpp/issues/27129), [router metrics discussion](https://github.com/ggml-org/llama.cpp/discussions/19197))

### Ollama

**Verified.** NVIDIA supplies a Spark playbook, and Ollama officially documents Codex integration, streaming tools, parallel and multi-turn calls, and a partial OpenAI Responses API. Its Responses implementation is non-stateful: it does not support `previous_response_id` or conversations. ([NVIDIA Ollama playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/ollama/README.md), [Ollama Codex integration](https://docs.ollama.com/integrations/codex), [tool calling](https://docs.ollama.com/capabilities/tool-calling), [OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility))

**Verified gap.** Ollama exposes request timing fields, but a native Prometheus `/metrics` endpoint remains an open feature request. Current reports also show long reasoning/tool streams ending without the required Responses terminal event and prior Codex stream/tool-parser failures. ([API timing types](https://github.com/ollama/ollama/blob/main/api/types.go), [metrics request](https://github.com/ollama/ollama/issues/3144), [Responses terminal-event issue](https://github.com/ollama/ollama/issues/17118), [Codex stream issue](https://github.com/ollama/ollama/issues/14600))

**Inference.** Ollama's excellent operational simplicity does not outweigh missing server-level metrics and the current Codex streaming risk for the critical path. Retain it for quick model trials, not as the stable northbound contract.

### Reproducibility and memory-efficiency interpretation

**Verified.** All four runtimes expose a seed or deterministic/greedy sampling control, but that is narrower than end-to-end reproducibility. TensorRT-LLM's `SamplingParams` has a random seed and greedy controls; llama.cpp exposes `--seed`; Ollama documents seeded reproducible output for supported OpenAI endpoints; and vLLM explicitly documents its limited reproducibility conditions. ([TensorRT-LLM sampling source](https://github.com/NVIDIA/TensorRT-LLM/blob/main/tensorrt_llm/sampling_params.py), [llama.cpp server sampling options](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility), [vLLM reproducibility](https://docs.vllm.ai/en/latest/usage/reproducibility/))

**Verified caution.** llama.cpp has a current closed report of fixed-seed server output differing under GPU/cache behavior, and Ollama has a current report that context shifting conflicts with fixed-seed reproduction. Seed support therefore must not be represented as byte-identical replay under batching, caching, tool calls, or version changes. ([llama.cpp issue 19981](https://github.com/ggml-org/llama.cpp/issues/19981), [Ollama issue 16635](https://github.com/ollama/ollama/issues/16635))

**Inference.** The reproducibility target for this platform should be operational: a pinned deployment tuple, captured request parameters and IDs, repeatable acceptance outcomes, and statistically stable performance. Exact token-for-token output is only an optional same-build diagnostic.

**Verified.** The runtimes save memory through different mechanisms. vLLM uses PagedAttention and continuous batching; TensorRT-LLM offers paged KV cache, cross-request reuse, offload/eviction, and FP8/NVFP4/other weight and KV-cache quantization; llama.cpp's core interchange is quantized GGUF and it offers KV-cache quantization; Ollama exposes quantized model variants and load/unload lifecycle through its model API. These features are not directly comparable without the exact model and context workload. ([NVIDIA vLLM recipe](https://build.nvidia.com/spark/vllm), [TensorRT-LLM KV cache](https://nvidia.github.io/TensorRT-LLM/features/kvcache.html), [TensorRT-LLM quantization](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html), [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Ollama API specification](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml))

**Hardware validation.** For each candidate, measure model weights, runtime workspace, prefix/cache residency, and KV bytes per active token rather than comparing only model file sizes. Also rerun tool-accuracy tests after weight or KV-cache quantization; lower memory use is not acceptable if structured-call quality regresses.

## GB10 memory and compatibility constraints

**Verified.** DGX Spark has a GB10, 128 GB of unified system memory, 273 GB/s memory bandwidth, and supports models up to roughly 200 billion parameters under suitable formats. Current DGX OS release notes list CUDA 13.0.2 and describe improved out-of-memory handling for unified memory; the display can reserve 2 or 4 GB. ([hardware specification](https://docs.nvidia.com/dgx/dgx-spark/hardware.html), [DGX OS release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html))

**Verified caution.** Unified memory is shared by the operating system, containers, model weights, KV cache, runtime workspaces, and display. NVIDIA's porting and troubleshooting material warns that conventional GPU memory reporting is different on this architecture and that a workload can still encounter memory pressure within nominal capacity. ([porting guide](https://docs.nvidia.com/dgx/dgx-spark-porting-guide/dgx-spark-porting-guide.pdf), [TensorRT-LLM troubleshooting](https://build.nvidia.com/spark/trt-llm/troubleshooting))

**Inference.** “Fits in 128 GB” is not an acceptance criterion. Reserve operating-system and display headroom, then measure weight residency, peak workspace, KV-cache growth at target context, concurrency, page-cache effects, and recovery from an OOM. A single very large model may preclude hard isolation through duplicate processes.

**Verified.** NVIDIA's model playbooks sometimes require a nightly runtime, a model-specific environment setting, an NVFP4 path, or an MTP/tool parser. Compatibility therefore belongs to the entire tuple `(DGX OS, driver, container digest, runtime version, model revision, quantization, parser, launch flags)`, not to the runtime name alone. ([NVIDIA Nemotron playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md))

Pin and record all of that tuple, plus tokenizer and chat-template revisions. Store commands and checksums in version control. Never deploy floating `latest` or unpinned model revisions.

## Gateway and reverse-proxy comparison

| Option | Verified strengths | Material limitation | Use |
|---|---|---|---|
| Caddy | SSE-friendly flush behavior, WebSockets, automatic HTTPS, health checks, header controls, Prometheus/OpenMetrics | Rate limiting is a non-standard module; no general JSON `model` routing | Initial edge on trusted LAN |
| NGINX OSS | Native leaky-bucket rate limiting, mature TLS/proxying, auth subrequests; SSE works with buffering disabled | TLS and metrics need more assembly; configuration is easier to get wrong for streaming | Edge if rate limiting is mandatory now |
| Traefik | Native rate-limit middleware, Prometheus/OTel metrics, dynamic service discovery | More moving control-plane/configuration surface than a fixed single appliance needs | Reject initially |
| LiteLLM | Model aliases, Responses proxy, auth/virtual keys, rate limits, routing/fallbacks, streaming, observability hooks | Adds an AI protocol hop; optional DB/UI/callbacks risk persistence and content logging; Codex pass-through needs qualification | Conditional model-aware gateway |
| vLLM Agentic API | Codex-oriented Responses gateway, state hydration and server tools | Young project; SQLite persistence by default and production hardening still on roadmap | PoC fallback only, not baseline |

Sources: [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy), [Caddy metrics](https://caddyserver.com/docs/metrics), [Caddy non-standard rate-limit module](https://caddyserver.com/docs/json/apps/http/servers/routes/handle/reverse_proxy/handle_response/routes/handle/rate_limit/), [NGINX proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html), [NGINX request limiting](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html), [NGINX auth request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html), [Traefik rate limiting](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/ratelimit/), [Traefik metrics](https://doc.traefik.io/traefik/reference/install-configuration/observability/metrics/), [LiteLLM gateway documentation](https://docs.litellm.ai/), [vLLM Agentic API](https://github.com/vllm-project/agentic-api).

**Inference.** Caddy, NGINX, and Traefik can route fixed URL paths and hosts, but none should be assumed to resolve an OpenAI request's JSON `model` value. This is why a reverse proxy is the security/TLS edge, while alias-to-backend selection is either inside one runtime or in a qualified AI gateway.

**Inference.** Do not add vLLM Agentic API initially. Its built-in state store contradicts the “no additional persistence unless required” constraint. It becomes relevant only if direct vLLM cannot satisfy a proven Codex Responses/state requirement.

### Recommended northbound contract

- `https://ai.home/v1/responses` for Codex and text agents.
- Preserve `/v1/chat/completions` only for existing n8n/Home Assistant clients that require it; new agent integrations should prefer Responses.
- Stable task strings: `automation`, `research`, `coding`, `home`, and `meeting`
  for text; STT and TTS are resolved by their separate audio services.
- Generate or preserve a request ID and return it to the caller.
- Derive consumer identity from a credential or mTLS identity. `X-AI-Consumer` may be recorded as metadata but must not be trusted if a client can spoof it.
- Stream end to end. Caddy must not buffer SSE; an NGINX alternative must set proxy buffering appropriately.
- Do not expose runtime health, profiling, or non-versioned routes to the LAN.

## Authentication, privacy, and observability

The minimum safe configuration is:

1. Terminate TLS and authenticate at the edge. Use separate credentials or client certificates for Codex, Home Assistant, and n8n so identity and revocation do not depend on a caller-supplied label.
2. Bind vLLM and audio servers only to loopback or the internal container network. Runtime API keys are defense in depth, not the outer security boundary.
3. Keep vLLM request/output logging disabled and production log level at INFO. Never enable debug request logs during live Aula, Home Assistant, or personal automation traffic.
4. Configure edge access logs to retain timestamp, request ID, authenticated consumer, route, alias, status, latency, token counts when available, and error class. Exclude authorization headers, query secrets, prompt bodies, outputs, tool arguments/results, and Aula content.
5. Scrape runtime and proxy metrics from a management-only network. Alert on availability, TTFT, total latency, error rate, queue depth, KV-cache pressure, memory high-water, OOM/restart events, and missing terminal stream events.
6. If LiteLLM is introduced, run it stateless initially: no UI/database unless a later requirement proves the need, no prompt/response callbacks, and an explicit inspection of its access and error logs.

**Aula boundary.** There is no Aula service, Aula model, Aula adapter, or Aula
route in this design. The existing n8n workflow may call its existing Aula MCP
and pass resulting data to private `automation`. At the inference boundary that
is ordinary n8n request content, governed by the same no-body-logging and access
policies.

## Codex-specific configuration consequences

**Verified.** Provider definitions, including `model_provider` and `model_providers`, are machine-local user configuration and cannot be overridden by a repository `.codex/config.toml`. The configured `model` string is sent to the provider; `model_catalog_json` supplies Codex-side context/tool metadata but is not server-side alias routing. ([custom model providers](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers), [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference))

Use a user-level provider resembling:

```toml
model = "coding"
model_provider = "gb10"

[model_providers.gb10]
name = "GB10 Local"
base_url = "https://ai.home/v1"
env_key = "GB10_API_KEY"
wire_api = "responses"
```

The exact TLS trust and environment-key delivery must be validated without placing secrets in the project repository.

**Verified.** Non-OpenAI custom providers in Codex 0.149.1 use local model-generated compaction through ordinary Responses requests rather than OpenAI's remote compaction endpoint. Configure the real context window and a conservative automatic-compaction threshold, then test long sessions. ([provider capability source](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/model-provider/src/provider.rs), [compaction implementation](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/core/src/tasks/compact.rs))

**Verified version split.** The installed Codex CLI is 0.145.0. Its tagged role implementation loads the custom agent file as a high-precedence configuration layer and explicitly lets that file replace `model_provider`. Codex 0.149.1 changed role handling to a bounded typed override set that includes `model` but omits `model_provider`. The desired handoff is therefore a candidate on pinned 0.145.0, not a capability that can be assumed across upgrades. ([0.145.0 role implementation](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/core/src/agent/role.rs), [0.149.1 role implementation](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/core/src/agent/role.rs), [subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents))

**Decision consequence.** First qualify a whole Codex 0.145.0 session on the GB10 provider, then test a cloud parent delegating a unique assignment and follow-up to a 0.145.0 GB10 child. Open issues show that custom-provider children on nearby versions can lose dynamic task content or receive only encrypted content, so selected provider metadata alone is not a pass. If 0.145.0 fails, or pinning that older client is operationally unacceptable, use explicit whole-session modes until a newer stable client restores and passes the same cross-provider canary. ([cross-provider issue 34833](https://github.com/openai/codex/issues/34833), [cross-provider issue 35932](https://github.com/openai/codex/issues/35932))

## Acceptance and benchmark plan

### API and Codex gate

Run these against the public `https://ai.home/v1` endpoint, not only the runtime port:

1. Text-only `POST /responses` with SSE; assert exactly one terminal `response.completed`.
2. A shell function call, `function_call_output`, and final answer over multiple turns.
3. One real namespaced MCP tool call, result replay, and final answer.
4. Parallel function calls and interleaved streaming deltas.
5. Reasoning-enabled multi-turn tool use; verify reasoning/tool continuity, not merely UI display.
6. Cancellation, client disconnect, retry, idle timeout, and malformed tool arguments.
7. A large tool result and a long thread that triggers automatic and manual compaction.
8. Every stable alias, correct model catalog metadata, real context window, and compaction threshold.
9. Whole-session Codex on GB10, followed by a pinned 0.145.0 cloud-parent/GB10-child canary that proves provider, initial assignment, and follow-up delivery. Requalify every client upgrade.
10. Confirm no prompt, response, tool argument, tool output, authorization value, or Aula content appears in proxy/runtime logs.

### Same-workload runtime benchmark

NVIDIA publishes an official Spark benchmarking method for vLLM, TensorRT-LLM, SGLang, and llama.cpp, including TTFT, time per output token, inter-token latency, end-to-end latency, and throughput. No primary-source apples-to-apples result was found for this exact appliance and workload, so do not use incomparable vendor examples to select the winner. ([NVIDIA performance benchmarking guide](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/connect-two-sparks/assets/performance_benchmarking_guide.md))

Benchmark vLLM and TensorRT-LLM first, with llama.cpp as the quantized baseline. Hold constant:

- model revision, tokenizer/template, precision or equivalent quantization, and reasoning/tool parser;
- prompt and output token distributions, context length, concurrency, and warm-up;
- DGX OS/driver, power mode, container limits, and background services;
- streaming and identical sampling parameters where supported.

Record p50/p95/p99 TTFT, inter-token latency, end-to-end latency, input/output tokens per second, request throughput, queue time, tool-call correctness, terminal-event correctness, model load/cold-start time, memory high-water, KV-cache utilization, OOM/recovery, and 24-hour soak failures.

Exercise the real mixed workload: P0 Home Assistant, P1 interactive Codex, P2 user-triggered n8n, and P3 scheduled n8n. The acceptance question is not only peak tokens per second; it is whether P0 latency remains bounded while P1 is generating and P2/P3 queue predictably.

## Open decisions after hardware validation

1. Which exact model and quantization backs each text alias.
2. Whether one shared vLLM instance meets P0 latency under Codex load or `home` needs a reserved small instance.
3. Whether vLLM's request-priority extension can be supplied by every relevant client; otherwise whether LiteLLM or process isolation is required.
4. Whether TensorRT-LLM delivers enough measured gain to justify its model-specific engine and parser complexity.
5. Whether rate limiting is a production gate. If yes, qualify stateless LiteLLM or replace Caddy with NGINX rather than relying on an ungoverned plugin.
6. Whether different text aliases eventually need different model processes. Only then qualify a model-aware gateway.
7. Whether installed 0.145.0 passes the complete cross-provider canary and can be pinned safely, or which later stable version restores and qualifies the role-provider path.

## Final recommendation

Proceed with **Caddy → NVIDIA NGC vLLM** as the smallest evidence-backed GB10 proof of concept, using stable logical aliases, private runtime ports, strict metadata-only logging, and Prometheus-compatible metrics. This is not yet a production sign-off: native per-consumer rate limiting, end-to-end workload priority, model-specific tool behavior, unified-memory headroom, and the Codex acceptance suite remain explicit gates.

Benchmark **TensorRT-LLM** against the exact winning model before freezing the runtime. Keep **llama.cpp** available for GGUF/quantized fallbacks and use **Ollama** only for exploratory model trials. Add **LiteLLM** only when multiple model processes, quotas, or automatic routing create a demonstrated need and only after it passes the complete Responses/tool-streaming and privacy test suite.
