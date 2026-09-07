# Model-router selection for `home-core`

**Verified:** 2026-09-07
**Status:** recommendation for design review; implementation and hardware
qualification remain open
**Scope:** local-only OpenAI-compatible routing for Codex, n8n, Home Assistant,
and interactive clients, including manual model selection, prompt/context-based
`auto`, and optional serialized model activation on the GB10 compute node

## Recommendation

Keep **Caddy + LiteLLM** as the version-one API boundary and routing gateway.
Do not replace the deployed LiteLLM 1.99.1 stack with Envoy AI Gateway, Kong, or
Portkey for this requirement set.

Use three public selection forms on the same OpenAI-compatible endpoint:

- `model: auto` invokes prompt/context classification;
- stable capability names such as `general`, `fast`, `coding`, and `reasoning`
  resolve deterministically; and
- exact, allow-listed model/version names pin a concrete model.

Only `auto` may invoke the small local classifier. Capability and exact-model
requests must bypass classification, so manual selection adds no classifier
latency. All authenticated clients may use `auto`, and manual selection always
wins because it does not enter that path.

LiteLLM 1.99.x already supplies the most useful gateway primitives: OpenAI
Chat Completions and Responses surfaces, streaming, virtual keys and model
allow-lists, logical model names, config-file operation, and hooks. Its new
Auto Routing feature can classify with heuristics, a small LLM, or a custom
classifier and can include a bounded number of prior turns. However, Auto
Routing is explicitly **beta**, and its documented classifier contract returns
a tier rather than the full confidence, reason, eligibility, and activation
decision required here
([LiteLLM Auto Routing](https://docs.litellm.ai/docs/proxy/auto_routing)).
The live documentation also includes features newer than 1.99.1. In particular,
pre-dispatch context-window escalation is documented as arriving in 1.101.0;
the installed version must not be assumed to have it. Until an upgrade passes
the protocol suite, hard context eligibility belongs in the custom policy
component and LiteLLM's deployment pre-call checks
([Auto Routing context-window behavior](https://docs.litellm.ai/docs/proxy/auto_routing#context-window)).

Therefore add a narrow, private **routing-policy and activation coordinator**,
but do **not** make it another client-facing OpenAI proxy. It must be the sole
owner of the final `auto` decision and return an exact model, decision ID, and
inference lease atomically. LiteLLM continues to own HTTP/SSE, OpenAI
request/response translation, client authentication, aliases, and the final
upstream call; it must consume the coordinator's exact decision rather than
independently reclassifying it or widening its candidate set. This avoids both
reimplementing Responses streaming and creating split-brain policy. LiteLLM
documents routing plugins and proxy pre-call hooks that provide a plausible
integration seam
([Auto Routing](https://docs.litellm.ai/docs/proxy/auto_routing),
[custom callbacks](https://docs.litellm.ai/docs/observability/custom_callback)).

Treat native LiteLLM Auto Routing as the initial shadow classifier and a
benchmark signal, not as the final policy owner. Its tier result is too narrow
to atomically express eligibility, lifecycle state, confidence, admission, and
an exact leased target. If a prototype proves LiteLLM can own all of those
concerns without an external policy decision, simplify toward LiteLLM; do not
split them halfway.

This is a conditional choice. The exact pinned LiteLLM/vLLM/Codex tuple must
still pass the repository's direct-versus-proxied Responses, SSE, tool-loop,
cancellation, privacy, and latency corpus before promotion.

## Why a narrow custom component remains necessary

None of the evaluated gateways documents the required GB10 lifecycle contract:

1. distinguish installed, active, loading, draining, and failed models;
2. serialize activation and coalesce concurrent requests for the same target;
3. never interrupt active inference;
4. enforce minimum residency and switch cooldown to prevent thrashing;
5. hold the original request through activation when load-on-demand is enabled;
6. apply a measured, per-model activation timeout; and
7. expose the selected model, confidence/reason code, queue time, and activation
   outcome without retaining prompt or response bodies.

This is not ordinary gateway load balancing. It coordinates service state on a
separate compute machine and must understand resource exclusivity. vLLM
Semantic Router makes the boundary explicit: it selects model paths but does
not provision the inference backends it references
([balanced-routing recipe](https://github.com/vllm-project/semantic-router/blob/main/config/recipes/balance/README.md)).
The same separation is the safest interpretation of the other gateways'
documented routing features: they choose among configured upstreams; they do
not safely unload and start mutually exclusive GB10 model services. This last
sentence is an inference from their documented feature and configuration
surfaces, not a claim that no external integration could be written.

The custom component should consequently own only selection policy and model
lifecycle state. It should not retain conversation state, proxy response
streams, execute tools, or implement inference.

## Required behavior

### Selection

| Requested `model` | Classifier call | Resolution |
| --- | --- | --- |
| `auto` | Yes, small local model | Hard eligibility rules first; classifier chooses among eligible capabilities/models |
| capability alias | No | Configured deterministic mapping |
| exact model/version | No | That exact allow-listed model |

Eligibility is evaluated before semantic classification: endpoint/protocol,
tools, modality, context window, privacy class, installed/active state, and
operator policy can eliminate a candidate. The selector sees only data already
present in the OpenAI request: the current prompt, bounded recent context,
required tools, and request characteristics. It has no hidden conversation
store.

Low-confidence handling is configuration-as-code with these initial values:

- `confidence_threshold`: calibrated from shadow results, not guessed;
- `low_confidence_policy: strongest_available`;
- alternatives retained for later use: `fixed_default` and `reject`.

If the selector is unavailable, times out, or returns an invalid result,
`auto` uses the configured **active default**, records `selector_fallback`, and
must not trigger a model load. Manual capability and exact-model requests are
unaffected.

### Activation

`load_on_demand` is configurable globally and per model and defaults to
`false`.

- With `load_on_demand: false`, `auto` chooses the fastest active model expected
  to meet the configured quality floor. A manually selected inactive model
  returns a clear OpenAI-style `model_not_active` error.
- With `load_on_demand: true`, `auto` is quality-first across eligible installed
  models. A manual request for an installed but inactive model may also activate
  exactly that model. The original request waits, then runs after readiness;
  this is a bounded inline wait, not durable suspension. A client disconnect,
  LiteLLM restart, worker cancellation, or end-to-end timeout loses the waiter.
  Successful activation is transparent, but clients must still tolerate an
  ordinary OpenAI error when activation cannot complete.
- An activation failure or measured per-model timeout returns a clear error. It
  never silently substitutes another model for an explicit manual request.

Only one activation job may run at a time. Existing requests drain before a
switch; queued requests targeting the same model share the activation; minimum
residency, cooldown, and target batching limit thrashing. Version one has no
per-client queue priority; use bounded FIFO target batches with an explicit
starvation limit instead.

The coordinator must stop admitting new work before drain, release leases only
after a stream ends or is cancelled, remove disconnected waiters, cap queue
length and queued bytes, reconcile against the real GB10 process after restart,
and fence stale activation jobs. Readiness requires both the expected served
artifact/version and a successful warm-up inference. Failed activation rolls
back to and verifies the last known-good model.

NVIDIA's current Spark vLLM playbook warns that loading can take several minutes
and uses a 900-second readiness wait in its example, which supports measuring
each actual artifact rather than setting one universal interactive timeout
([NVIDIA DGX Spark vLLM playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md)).

### Configuration, visibility, and privacy

Routing policy, model registry, aliases, confidence behavior, lifecycle limits,
and feature flags remain version-controlled. LiteLLM's file-owned model
definitions require reload/restart after changes, while database-owned models
can change without a restart; its own guidance recommends one primary source
of truth. Use the file/GitOps path for this deployment
([LiteLLM model management](https://docs.litellm.ai/docs/proxy/model_management)).
Treat a validated rolling restart as the production reload mechanism rather
than depending on development-style hot reload.

Expose an authenticated, read-only status surface containing active policy
version, active/loading/draining model, queue state, and last activation result.
Do not expose management operations on the client listener.

Default logs contain metadata only:

```text
request_id
client_key_identity
requested_model
selected_capability
selected_model
selection_mode
classifier_confidence
classifier_reason_code
selector_fallback
activation_outcome
queue_seconds
latency_seconds
input_tokens
output_tokens
status
```

Prompt, response, tool-argument, and private entity content is excluded. LiteLLM
documents `turn_off_message_logging` specifically for suppressing messages and
responses while retaining metadata, and the checked-in configuration already
sets it along with disabled spend/error logs
([LiteLLM logging](https://docs.litellm.ai/docs/proxy/logging),
[repository config](../../deploy/control-plane/litellm-config.yaml)). Evaluation
samples require explicit opt-in capture and redaction.

## Candidate comparison

| Candidate | Protocol and manual selection | Prompt/context `auto` | Auth/config/observability | Local lifecycle | Fit for `home-core` |
| --- | --- | --- | --- | --- | --- |
| **LiteLLM 1.99.1** | `/v1/chat/completions` and `/v1/responses`; Responses streaming; logical model names/groups; exact model entries | Beta Auto Routing supports heuristics, a small LLM, bounded context, keyword/semantic rules, and custom classifier plugins | Virtual keys with per-key model access; YAML/GitOps; callbacks and metadata-only logging controls | No documented service activation/drain coordinator | **Best base. Keep and extend narrowly.** |
| **Envoy AI Gateway 1.1** | Fully documented Chat Completions and Responses with streaming, function calls, model selection, fallbacks, and OpenAI-compatible backends including vLLM | Core gateway routes by extracted model name; richer semantic choice requires an external processor or vLLM Semantic Router | Gateway API resources; Envoy security policies; access logs/metrics | No constituent model activation contract | Technically strong, but replacing the existing Compose stack with its documented Kubernetes 1.32+/Envoy Gateway deployment adds disproportionate operational surface |
| **Kong AI Gateway** | AI Proxy Advanced documents Chat Completions, function calling, streaming, aliases, vLLM, and Responses routes | Semantic load balancing matches prompt embeddings to target descriptions | Mature gateway auth/logging and declarative config | Configures targets, not GB10 process residency | Capable but commercially licensed for AI Proxy Advanced/semantic routing and materially heavier than the incumbent |
| **Portkey OSS Gateway** | Lightweight local OpenAI-compatible gateway; current docs describe Responses, SSE, tools, and routing configs | Conditional routing is request/metadata based; no equally clear OSS prompt-to-model classifier contract | Local deployment and local console; several virtual-key, RBAC, export, and private-deployment features are documented as hosted/enterprise | No documented activation contract | Attractive general gateway, but OSS 2.0 is labelled pre-release and feature-boundary ambiguity creates more risk than benefit here |
| **vLLM Semantic Router 0.3** | Stable OpenAI/Anthropic-compatible entrypoints, including Chat Completions and Responses, in front of heterogeneous endpoints | Strongest purpose-built option: prompt, context, tools, difficulty, domain, policy, session state, and configurable recipes | Apache-2.0; YAML; replay/evaluation/observability, with body capture that must be disabled for this privacy policy | Explicitly does not provision referenced inference backends | **Best challenger for the `auto` decision engine**, but not a replacement for gateway auth or the activation coordinator |
| **Bifrost** | OpenAI-compatible Chat Completions and Responses, streaming, model aliases, vLLM and SGLang backends | Declarative rules route on request fields; prompt-semantic selection requires custom hook logic | OSS gateway with plugins and telemetry | No documented activation contract | Credible lightweight challenger, but requires more custom selection work than LiteLLM and brings no lifecycle advantage |

### Evidence behind the matrix

LiteLLM's Responses documentation states that `/responses` follows the OpenAI
spec and supports streaming, load balancing, fallbacks, logging, and tracking.
It also warns that Chat Completions may be bridged when a provider lacks native
Responses support, which is why direct and proxied protocol tests remain
mandatory
([LiteLLM Responses API](https://docs.litellm.ai/docs/response_api)). Its virtual
keys can restrict the models a key may call, and aliases can remap a requested
name to a model group
([LiteLLM virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys)).

Envoy AI Gateway 1.1 documents full Chat Completions and Responses support,
including streaming, function calling, body/header model selection, token
metrics, fallback, and OpenAI-compatible providers; its `/v1/models` is derived
from configured routes
([supported endpoints](https://aigateway.envoyproxy.io/docs/capabilities/llm-integrations/supported-endpoints/)).
It can inherit JWT, mTLS, external authorization, API-key, IP, and TLS controls
from Envoy Gateway
([security](https://aigateway.envoyproxy.io/docs/capabilities/security/)). The
documented production path requires Kubernetes 1.32 or newer and Envoy Gateway
1.8.1 or newer
([prerequisites](https://aigateway.envoyproxy.io/docs/getting-started/prerequisites/)).

Kong AI Proxy Advanced documents OpenAI Responses from 3.11 onward, OpenAI
format translation, vLLM targets, streaming, aliases, several load-balancing
algorithms, and a semantic algorithm
([AI Proxy Advanced](https://developer.konghq.com/plugins/ai-proxy-advanced/),
[semantic example](https://developer.konghq.com/plugins/ai-proxy-advanced/examples/semantic/)).
The official plugin catalog marks AI Proxy Advanced and the relevant semantic
plugins as requiring an AI license
([Kong plugin catalog](https://developer.konghq.com/plugins/)).

Portkey's current Responses documentation claims streaming SSE, custom function
tools, structured output, and use with routing/fallback gateway configs across
native and adapted providers
([Portkey Responses API](https://portkey.ai/docs/product/ai-gateway/responses-api)).
Its open-source repository offers Node, Docker, and Compose deployment and marks
Gateway 2.0 as pre-release; the same repository separates several security,
observability, and private-deployment features into the enterprise offering
([Portkey gateway repository](https://github.com/Portkey-AI/gateway)).

vLLM Semantic Router describes itself as a decision layer rather than a gateway
or model server. Clients can call stable OpenAI Chat Completions or Responses
entrypoints while recipes combine request, conversation, capability, policy,
and system-state signals to select a backend
([project introduction](https://github.com/vllm-project/semantic-router/blob/main/website/docs/intro.md),
[system overview](https://github.com/vllm-project/semantic-router/blob/main/website/docs/overview/semantic-router-overview.md)).
Its balanced recipe exposes an `auto` alias but warns that replay can retain up
to 4 KiB of request and response content in memory and must be reviewed or
disabled for sensitive deployments
([balanced-routing recipe](https://github.com/vllm-project/semantic-router/blob/main/config/recipes/balance/README.md)).

Bifrost documents OpenAI-style Responses and streaming across vLLM and other
backends, model aliases, and declarative routing rules
([supported providers](https://docs.getbifrost.ai/providers/supported-providers/overview),
[model aliases](https://docs.getbifrost.ai/providers/aliasing-models),
[routing rules](https://docs.getbifrost.ai/providers/routing-rules)). It is a
valid fallback candidate if LiteLLM later fails gateway qualification, but its
documented rule surface does not eliminate the custom prompt classifier or
activation coordinator required by this design.

## Validation gates before commitment

1. Confirm a pinned LiteLLM candidate newer than 1.99.1 handles both
   `/v1/responses` and `/v1/chat/completions`, including streaming function
   calls, with the exact installed Codex and chosen vLLM/model/parser tuple.
2. Prove capability and exact-model requests never call the classifier; measure
   classifier overhead only on `auto`.
3. Run shadow mode first: serve the fixed active default, record the proposed
   selection and reason, and score decisions against real local workloads.
4. Test low-confidence, selector timeout/malformed output, and selector outage;
   all must use the active default, record `selector_fallback`, and initiate no
   activation.
5. Exercise concurrent activation, drain, timeout, failed readiness, duplicate
   target requests, residency, cooldown, batching, and rollback. Verify no
   active inference is terminated and only one activation runs.
6. Measure cold load and first-request warm-up per exact model artifact; set the
   activation timeout from measured tail latency plus margin.
7. Insert canary secrets into prompts, responses, tool arguments, and errors;
   verify they are absent from Caddy, LiteLLM, coordinator, runtime, database,
   and metrics storage.
8. Keep vLLM Semantic Router as the benchmark challenger. Reconsider it only if
   LiteLLM's beta auto path cannot meet selection-quality, session-continuity,
   explainability, or maintenance gates without accumulating equivalent custom
   routing logic.
9. Test client disconnect, queue overload, starvation, LiteLLM/coordinator/GB10
   restart, and network partition during classify, drain, activate, warm-up,
   inference, and streaming.
10. Verify exact-model non-substitution, fallback authorization, model binding
    across Responses continuations and tool loops, classifier non-recursion,
    multi-worker behavior, and served-artifact identity after every activation.

## Revised staged rollout after independent review

1. **Manual baseline:** retain Caddy and LiteLLM; expose capability aliases and
   exact allow-listed names. Keep `load_on_demand: false`.
2. **Shadow auto:** run LiteLLM Auto Routing and the challenger selector only as
   observers while the active default serves requests. Measure selection
   quality and confirm manual paths incur no classifier work.
3. **Active auto without switching:** enable `model: auto` only across already
   active backends. Use the active default on selector failure and never start a
   model from a failed or low-confidence decision.
4. **Lifecycle prototype:** implement one atomic operation:

   ```text
   decide_and_acquire(request facts, identity, deadline)
     -> exact model + decision id + inference lease
   ```

   Align the client, Caddy, LiteLLM, classifier, coordinator, drain, activation,
   warm-up, and inference deadlines. Prove bounded inline waiting and all
   lifecycle failure cases before enabling it for clients.
5. **Optional load-on-demand:** enable per model only after the lifecycle gates
   pass. Keep a global kill switch and `false` as the default.

## Decision boundary

The gateway decision is **LiteLLM, conditionally retained**. Native LiteLLM Auto
Routing is the first shadow-mode benchmark, not the final policy owner. The
production automatic-selection decision is **one atomic policy owner returning
an exact leased model**; whether that can be implemented cleanly through a
pinned LiteLLM routing integration is a prototype gate. The lifecycle decision
is **custom coordinator required**, with load-on-demand disabled until proven.

This division minimizes new protocol surface while preserving a clean escape
hatch: capability aliases and exact names remain useful even if `auto` is
disabled, rolled back to shadow mode, or later moved to vLLM Semantic Router.
