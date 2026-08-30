# Gateway evaluation

**Status:** Initial architecture decision  
**Verified:** 2026-08-25  
**Scope:** Stable LAN API boundary for Codex, existing n8n workflows, and Home Assistant

## Decision

Use a layered boundary, introduced in two steps:

1. **PoC:** connect Codex directly to a pinned vLLM Responses API instance. This isolates model/runtime compatibility from gateway behavior.
2. **Production candidate:** put **Caddy** at the LAN edge for TLS, request IDs, path routing, access control integration, and backend isolation. Reuse the existing AI Home **LiteLLM Proxy** behind Caddy for virtual keys, task-semantic aliases, and qualified local/cloud routing. Do not place LiteLLM in the first direct-runtime compatibility test, and do not deploy a second instance on GB10.

The stable client-facing endpoint remains independent of the runtime and model. The intended text path is:

```text
Codex / existing n8n / Home Assistant
                 |
                 v
        Caddy: https://ai.home (existing AI Home host)
                 |
                 v
     LiteLLM (existing control plane, qualification required)
                 |
                 v
        pinned vLLM backend(s)
```

Audio endpoints can be routed by Caddy to dedicated STT and TTS services without passing through the text-model gateway.

This is a **conditional production selection**, not a claim that the existing
gateway is production-ready. Direct and Caddy/LiteLLM-proxied streaming/tool-call
tests are required before the decision becomes final. If the existing LiteLLM
cannot pass, Caddy-to-vLLM remains the temporary local-only fallback while the
control-plane decision is revisited.

## Current-state reconciliation

The original evaluation considered only the cost of adding a new gateway in
front of one backend. The later current-state inventory found that `ai_home`
already defines LiteLLM as the shared cloud gateway. Reusing and hardening that
instance avoids a second gateway, preserves cloud availability during a GB10
outage, and supplies the virtual keys and deterministic local/cloud routing now
required by the handoff. This changes the total-system decision without
changing the requirement for a direct vLLM baseline.

## Architectural constraints

- Aula is not a gateway consumer or standalone workload. Aula data reaches the platform through the **existing n8n workflow and existing Aula MCP**.
- Clients address task names such as `coding`, `automation`, `research`, `home`, and `meeting`; they do not know the artifact name, quantization, runtime instance, or local/cloud placement.
- The OpenAI Responses API, streaming events, tool calls, and error semantics must survive the entire route.
- Default telemetry contains metadata only. Prompt and response bodies are disabled in every layer.
- Home Assistant safety-sensitive requests must fail visibly. A gateway must not silently substitute a model with different tool behavior.
- The gateway must not become a hidden persistent-memory layer. Existing n8n/Notion state remains authoritative.

## Evidence

### Direct vLLM compatibility

vLLM publishes a dedicated Codex integration and states that its OpenAI-compatible server implements the Responses API used by Codex. Its example config uses a custom Codex provider with `wire_api = "responses"`. Model-specific tool-call support and the matching parser remain prerequisites.

This makes direct Codex-to-vLLM the smallest useful compatibility baseline. A failure in that baseline must not be obscured by a proxy.

### LiteLLM

LiteLLM Proxy documents:

- an OpenAI-compatible `/responses` endpoint;
- streaming;
- model routing, load balancing, and fallbacks;
- logical `model_name` entries backed by provider-specific models;
- cost, logging, and end-user tracking.

Those capabilities fit version-independent aliases and future multi-backend routing. They also add translation and state that can diverge from the backend protocol. The proxy must therefore pass the same Codex tool-loop corpus as the direct vLLM path. Response bridging is not accepted as proof of native semantic equivalence.

LiteLLM fallback is disabled for Home Assistant tool execution unless a specific fallback model has independently passed the same schema, authorization, and safety tests. Coding fallback is orchestrated by Codex rather than hidden inside the gateway so that the run records which provider performed each step.

### Caddy

Caddy's `reverse_proxy` supports streamed responses, upstream health checks, configurable headers, and TLS transports. Caddy is a good fit for the stable LAN name and simple path separation without owning AI model semantics.

Caddy cannot by itself select arbitrary upstreams from the JSON `model` field using ordinary static reverse-proxy configuration. It can route by host, path, method, and headers. Therefore:

- Caddy alone is sufficient when one text backend exposes every accepted alias, or callers use separate paths/headers.
- LiteLLM (or a purpose-built AI router) is justified when the stable OpenAI endpoint must map a request-body model alias to independent backends.

Authentication may be enforced by the upstream API key, mTLS, or an authenticated forward-auth service. The production mechanism must be selected and tested explicitly; merely having TLS does not authenticate a caller.

## Comparison

| Candidate | Responses/tool streaming | Logical AI aliases | TLS/edge controls | Operational cost | Decision |
| --- | --- | --- | --- | --- | --- |
| Direct vLLM | Native candidate; Codex integration documented | Served-model names, but one process cannot provide general multi-backend routing | Backend API key; no complete LAN edge | Lowest | Required PoC baseline |
| Caddy | Pass-through; must verify SSE buffering and disconnect behavior | Path/header routing; not request-body model routing | Strong TLS and reverse-proxy feature set | Low | Selected LAN edge candidate |
| Existing LiteLLM Proxy | `/responses`, streaming, routing, fallback documented | Strong task-alias abstraction | Virtual keys and policy features; TLS delegated to Caddy | Medium incremental | Selected production control-plane candidate after direct PoC |
| nginx | Mature TLS, proxying, rate limits, and logs | No native JSON model router in ordinary config | Strong | Low to medium | Viable alternative; no advantage established over Caddy here |
| Traefik | Strong dynamic service discovery and middleware | No native JSON model router | Strong | Medium | Rejected for now: container discovery is not the central problem |
| llama.cpp server as gateway | Current server exposes OpenAI-style APIs | Can expose aliases/router behavior, but couples boundary to runtime | Limited edge role | Low | Keep as runtime alternative, not the stable gateway |

## Required interface

### Stable routes

The exact audio schemas remain a Phase B design item, but the boundary should reserve:

```text
POST /v1/responses
GET  /v1/models
POST /v1/audio/transcriptions
POST /v1/audio/speech
GET  /health/live
GET  /health/ready
```

Only endpoints that are actually implemented and contract-tested are advertised.

### Consumer metadata

Callers send a controlled value:

```text
X-AI-Consumer: codex
X-AI-Consumer: n8n
X-AI-Consumer: home-assistant
```

The edge replaces or validates this header rather than trusting arbitrary internet-originated values. Aula remains represented as `n8n`; it is not a separate consumer label.

### Request identity

- Accept a syntactically valid client request ID or generate one at the edge.
- Return the effective request ID on success and error responses.
- Propagate it through LiteLLM, runtime, and metrics labels where supported.
- Never place prompt text, user names, entity state, or Aula content in the ID.

### Errors and retry ownership

- Preserve useful HTTP status codes and machine-readable error bodies.
- Do not retry non-idempotent streamed generations after output has begun.
- Bound connection attempts and total request duration by workload class.
- Surface backend saturation distinctly from model/tool validation failures.
- Codex owns cloud fallback. n8n owns its workflow retry policy. Home Assistant fails closed for tool actions.

## Security and privacy baseline

- Bind inference backends to a private service network; expose only Caddy to the client LAN.
- Use TLS on the LAN endpoint where practical and pin the local trust root on clients.
- Require a distinct credential per consumer and rotate it without changing model aliases.
- Deny unrecognized consumers, models, methods, and paths.
- Disable administrative and model-management endpoints on the client-facing listener.
- Do not log authorization headers, cookies, prompt bodies, response bodies, tool arguments, or audio.
- Keep proxy and runtime debug modes off in normal operation.
- Configure retention for metadata logs and metrics; access is least-privilege.

The n8n workflow may carry sensitive Aula data. The privacy control applies to the n8n request path and its logs; it does not justify a separate Aula service.

## Observability

Record bounded, low-cardinality metadata:

```text
timestamp
request_id
consumer
logical_model
actual_model
runtime
status
input_tokens
output_tokens
latency_seconds
time_to_first_token_seconds
tokens_per_second
queue_seconds
fallback_used
```

Do not use request IDs, users, prompts, tool names with unbounded arguments, or Home Assistant entity state as metric labels. Detailed request correlation belongs in access logs with controlled retention.

## Acceptance tests

The gateway choice passes only when all applicable tests succeed both directly and through the candidate route:

1. Codex starts a streaming Responses request and completes a multi-turn tool loop.
2. Streaming event order and required fields match the client expectation.
3. Client cancellation releases backend work promptly.
4. A malformed tool call produces an explicit failure; no model substitution occurs.
5. `coding`, `automation`, `research`, `home`, and `meeting` resolve to the documented actual model/version and policy.
6. Unknown models and consumers are rejected.
7. Request IDs propagate through success, 4xx, 5xx, timeout, and cancellation paths.
8. A disconnected client does not cause an automatic duplicate generation.
9. Rate limits isolate consumers so background n8n work cannot starve Home Assistant.
10. Access, gateway, and runtime logs contain no supplied canary prompt, tool argument, Aula fixture, or audio bytes.
11. Gateway overhead is measured for time to first token and total latency; the allowed budget is set from PoC data rather than guessed here.
12. Restarting or replacing a backend does not require a Codex, n8n, or Home Assistant model-name change.

## Reconsideration triggers

Revisit the choice if:

- LiteLLM changes or corrupts Codex Responses streaming/tool semantics;
- the existing AI Home instance cannot be isolated, pinned, or upgraded safely;
- one vLLM instance can safely serve all required aliases and Caddy-only operation materially reduces failure modes;
- the chosen models require different runtimes;
- Caddy cannot meet the selected authentication or per-consumer rate-limit design without nonstandard plugins;
- an AI-specific gateway demonstrates lower complexity while passing the same contract and privacy tests.

## Sources

- [vLLM: Using vLLM with OpenAI Codex](https://docs.vllm.ai/en/latest/serving/integrations/codex/)
- [vLLM: Tool Calling](https://docs.vllm.ai/en/latest/features/tool_calling/)
- [vLLM: Production metrics](https://docs.vllm.ai/en/latest/design/metrics/)
- [LiteLLM: Responses API](https://docs.litellm.ai/docs/response_api)
- [LiteLLM: Model management](https://docs.litellm.ai/docs/proxy/model_management)
- [LiteLLM: Routing and load balancing](https://docs.litellm.ai/docs/routing)
- [Caddy: `reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Caddy: TLS](https://caddyserver.com/docs/caddyfile/directives/tls)
- [nginx: HTTP load balancing](https://nginx.org/en/docs/http/load_balancing.html)
- [Traefik: HTTP rate limit middleware](https://doc.traefik.io/traefik/middlewares/http/ratelimit/)
- [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
