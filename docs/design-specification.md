# Design specification

**Version:** 0.2  
**Date:** 2026-08-25  
**Status:** Proposed; implementation is gated by Phase C/D evidence

## 1. Design inputs

- [User requirements](requirements.md)
- [Current-state analysis](current-state.md)
- [Architecture](architecture.md)
- [Runtime evaluation](research/inference-runtime-evaluation.md)
- [Gateway evaluation](research/gateway-evaluation.md)
- [Codex compatibility](research/codex-compatibility.md)
- Role-specific model research in `docs/research/`

## 2. Qualified artifact unit

A deployment is identified by an immutable release manifest containing:

```yaml
platform:
  dgx_os: exact-version
  kernel: exact-version
  nvidia_driver: exact-version
  cuda: exact-version
edge:
  image: registry/name@sha256:digest
text_runtime:
  image: nvcr.io/nvidia/vllm@sha256:digest
  version: exact-version
  launch_config_sha256: digest
models:
  - logical_aliases: [coding, automation, research, home, meeting, assistant]
    repository: owner/model
    revision: immutable-commit
    weight_format: exact-format
    quantization: exact-value
    tokenizer_revision: immutable-commit
    chat_template_sha256: digest
    reasoning_parser: exact-value
    tool_parser: exact-value
audio:
  stt: immutable-artifact
  tts: immutable-artifact
configuration_revision: git-commit
qualification_record: docs/benchmarks/result-file.md
```

The real manifest schema may be JSON or YAML. All fields above are required or
explicitly marked `not-applicable`; human-readable tags do not replace digests
or model revisions.

## 3. Host baseline

The host setup shall:

1. verify it is running on the supported GB10/DGX Spark hardware and supported DGX OS;
2. record firmware, kernel, NVIDIA driver, CUDA, display reservation, and power mode;
3. install/configure the selected container runtime using the NVIDIA-supported path;
4. create dedicated directories, service identities, and a private container network;
5. configure host firewall rules before starting inference listeners;
6. configure time synchronization, local DNS resolution, and certificate trust inputs;
7. configure disk/memory monitoring and bounded journal/log retention;
8. reject unsupported or already-divergent state unless an explicit override is recorded.

Bootstrap scripts are idempotent, use strict error handling, never fetch a
floating production artifact, and provide a read-only preflight mode.

## 4. Service topology

### 4.1 Edge and control plane

One Caddy service on the existing AI Home host binds the client-facing TLS
listener. It shall:

- accept only the documented API paths and methods;
- pass authentication to the qualified LiteLLM virtual-key layer and keep audio
  route authorization explicit;
- set or validate a request ID and return it in every response;
- derive a trusted consumer identity and replace untrusted `X-AI-Consumer` input;
- stream without response buffering;
- use explicit connect, response-header, idle, and total-duration behavior by route;
- forward to GB10 service names over an authenticated, encrypted private host
  link (Tailscale or an equivalently restricted LAN/VPN design);
- emit metadata-only structured access records;
- keep admin and metrics endpoints on the management listener.

The existing LiteLLM instance runs behind Caddy and shall:

- issue distinct virtual keys for Codex, public n8n, private n8n, Home
  Assistant, and Meeting Assistant;
- allow only the aliases and routes assigned to each key;
- map task aliases to a pinned GB10 backend or an explicitly approved cloud
  provider;
- disable hidden fallback for `home`, `meeting`, and private automation;
- keep prompt/response callbacks, request-body logging, and its UI off in
  normal operation;
- use a separately credentialed database if virtual-key persistence requires
  one; it shall not share LibreChat's database/schema or credentials.

The prototype master key is administrative only and is never issued to a
consumer. Caddy and LiteLLM must be pinned and pass the combined gateway PoC.

### 4.2 Text runtime

The baseline is the pinned NVIDIA NGC vLLM image. The service shall:

- bind only to the GB10 private service interface and accept traffic only from
  the qualified control-plane/management identities;
- expose Responses and health/metrics endpoints only to the edge/management network;
- use the exact qualified chat template, reasoning parser, and tool parser;
- advertise only approved logical names;
- disable prompt and output logging;
- set an explicit context limit and automatic-compaction input metadata for Codex;
- reserve measured memory headroom rather than maximizing model utilization;
- use the qualified scheduler and bounded concurrency;
- restart on process failure but not loop indefinitely after repeated OOM/configuration failures.

For the first PoC, Codex connects directly to the private/test listener so the
runtime protocol is isolated. For acceptance, all clients use the edge.

### 4.3 Existing AI router

LiteLLM is absent only from the direct runtime PoC. The production candidate is
the existing AI Home instance, upgraded through a staged configuration and
rollback procedure. It is accepted only after the complete
direct-vs-Caddy/LiteLLM contract, latency, fault, virtual-key, route-policy, and
log-canary suites pass.

No automatic fallback is configured for Home Assistant tools, private
automation, or meetings. Public `research` fallback is deterministic and
recorded. Coding fallback remains visible in Codex orchestration.

### 4.4 Audio services

STT, TTS, and later diarization are independent GB10 inference services behind
fixed audio paths. The selected STT/TTS implementation must support Home
Assistant through a maintained Wyoming path when it satisfies the chosen model,
and STT must also support Meeting Assistant's OpenAI-compatible transcription
contract. Diarization uses a private versioned route consumed only by Meeting
Assistant. Every service exposes separate liveness/readiness. Audio bytes and
transcripts are not logged by infrastructure.

### 4.5 Telemetry

The implementation exposes Prometheus-compatible metrics to a management-only
scraper and structured metadata logs with bounded retention. Required signals:

- readiness/liveness and restarts;
- request count by authenticated consumer, logical alias, status, and error class;
- queue, TTFT, inter-token and end-to-end latency histograms;
- input/output token counts and throughput;
- unified-memory high-water, KV-cache pressure, CPU, disk, temperature/power where available;
- model load/cold start and OOM/recovery;
- STT/TTS first-result, diarization latency, and real-time factor.

Request IDs are log fields, not metrics labels. Prompts, outputs, audio,
transcripts, tool payloads, entity state, authorization, Aula content, and
meeting content are forbidden.

### 4.6 Personal agent runtime

Hermes is introduced only after the inference/control-plane gate and runs on
an always-on application host, not on the production GB10 appliance. Start with
one synthetic-data `owner` sandbox. If qualified, create independent `owner`,
`partner`, and `family` OpenShell sandboxes; never multiplex trust domains through
one writable home, state database, process, or credential bundle.

Each sandbox receives only:

- its own persistent state, memory/skills directory, and Discord credential;
- a per-profile LiteLLM virtual key and logical aliases;
- a per-profile personal-event API credential enforcing `owner_scope`;
- explicitly enumerated managed MCP/API providers and network destinations;
- a bounded temporary workspace with retention and size limits.

Hermes has no direct database-superuser, Docker socket, broad host filesystem,
Home Assistant administrator, MQTT broker, or unrestricted LAN access.
NemoClaw supplies the Hermes integration and OpenShell supplies the sandbox;
pin and qualify both. Managed credentials remain at approved egress boundaries
where the selected integration supports them. Any secret injected into the
sandbox is treated as visible to the agent. A direct-on-GB10 NemoHermes setup is
a non-production compatibility pilot unless a later ADR changes the appliance
and durable-state boundaries.

## 5. Northbound API contract

### 5.1 DNS and TLS

The production base is `https://ai.home`. The chosen local DNS and certificate
authority are external infrastructure dependencies and documented installation
inputs. Consumers never use an IP or container port.

### 5.2 Routes

| Method and path | Consumer | Logical role | Notes |
| --- | --- | --- | --- |
| `POST /v1/responses` | Codex; new agent clients | request `model` | Required Codex path |
| `POST /v1/chat/completions` | Hermes and existing clients when necessary | request `model` | Compatibility path, separately tested at 64K for Hermes |
| `GET /v1/models` | Authenticated clients | qualified aliases | No internal artifact metadata required |
| `POST /v1/audio/transcriptions` | Meeting Assistant; approved adapters | STT by route/config | Size and duration bounded; no cloud fallback |
| Wyoming STT/TTS | Home Assistant | qualified speech services | Supported Home Assistant boundary |
| `POST /v1/audio/speech` | Approved clients | TTS by route/config | Qualified Danish voice allow-list |
| `POST /v1/audio/diarizations` | Meeting Assistant | diarization by route | Private versioned schema; no cloud fallback |
| `GET /health/live` | monitoring | none | Edge/process availability |
| `GET /health/ready` | monitoring | none | Qualified backend/model readiness |

If the selected audio integration uses Wyoming rather than the OpenAI audio
routes, the public design is updated by ADR; unused routes are not advertised.

### 5.3 Request identity and errors

The edge uses `X-Request-ID` unless the PoC finds a client conflict. Accepted
IDs are bounded ASCII/UUID-like values; otherwise the edge replaces them. It
returns the effective value on normal and error responses.

Errors use HTTP status plus a bounded machine-readable object with `code`,
`message`, and `request_id`. Messages contain no prompt fragments or internal
filesystem/container details. Error classes distinguish authentication,
unknown model, invalid request/tool data, saturation, timeout, unavailable
backend, and internal failure.

### 5.4 Retry semantics

- Connection retry is bounded and permitted only before response streaming begins.
- A generation is never replayed automatically after output has begun.
- n8n owns workflow-level retry and idempotency.
- Meeting Assistant owns meeting import/transcription retry and artifact state.
- Home Assistant tool calls fail closed and are not moved to an unqualified model.
- Codex implementation retry/fallback is orchestration-level and recorded.

## 6. Logical model registry

Source control contains a declarative registry with:

```yaml
aliases:
  coding:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: orchestration-only
  automation:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: false
  research:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: approved-public-only
  home:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: false
  meeting:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: false
  assistant:
    backend: text-primary
    qualified_release: release-id
    cloud_fallback: false
    minimum_context: 65536
  speech-to-text:
    backend: stt-primary
    qualified_release: release-id
  text-to-speech:
    backend: tts-primary
    qualified_release: release-id
```

Consumers see only alias names. A registry change requires benchmark evidence,
contract tests, configuration validation, and a rollback entry. Alias
proliferation requires an ADR and distinct workload evidence.

## 7. Scheduling and isolation

Workload intent is P0 Home Assistant voice, P1 interactive Codex or assistant
conversation, P2 user-triggered n8n/Meeting Assistant interaction, P3
scheduled n8n/Hermes analysis, and P4 meeting transcription/diarization.
Authentication determines the trusted consumer and profile; a client header
is informational.

The initial runtime test shall compare:

1. one shared text instance with supported request priority derived from the
   authenticated route where supported;
2. one shared instance without priority;
3. a reserved small `home` instance, if memory permits;
4. a qualified AI router deriving priority from authenticated identity, only if needed.

No production priority mechanism is selected until mixed-load evidence proves
that URS-PERF-001/002/005 remain satisfied. Background n8n and meeting
throughput may be reduced to preserve P0/P1 latency.

## 8. Model selection and memory design

Each role uses the weights and fixtures defined in its research/benchmark plan.
Selection records include quality, compatibility, latency, throughput, memory,
license, and operational fit. A model with an unclear or unacceptable license
is rejected even if its quality is higher.

The measured memory budget shall include:

```text
OS + display reservation
container/runtime overhead
resident weights
KV cache at qualified context and concurrency
CUDA/runtime workspaces and graph capture
STT, TTS, and diarization services
inference telemetry
filesystem/page cache effects
10% production headroom
```

Hermes qualification adds a 64K-context session per concurrently resident
profile. Earlier 16K/32K runtime results do not qualify the agent path. The
mixed-load suite shall measure realistic active-session concurrency rather
than assuming three maximum-size KV caches are always resident.

When the sum does not fit, apply controls in this order: consolidate aliases on
one qualified model, reduce context/concurrency within requirements, choose a
qualified quantization, dedicate a smaller `home` runtime, then use controlled
model residency/swapping. External storage does not solve unified-memory
pressure.

## 9. Codex integration design

The machine-local user configuration defines the GB10 provider and `coding`;
repository configuration does not attempt to override provider
definitions. Secrets are injected through a named environment variable.

Phase C accepts a whole Codex session on GB10. Automatic Stage 2 is activated
only when all are true:

1. the exact pinned client implements the role-layer `model_provider` override (installed 0.145.0 is the first candidate);
2. provider metadata proves that the child actually uses GB10 and `coding`;
3. initial and follow-up assignment canaries reach the custom-provider child unencrypted/usable;
4. desktop/CLI client behavior needed by this project is consistent;
5. a cloud planner can delegate a bounded task to GB10 with tools/MCP intact;
6. failure evidence reaches the local retry;
7. repeated failure selects cloud implementation and records the reason;
8. cloud review receives the final diff and verification record;
9. a GB10 outage leaves planning/review usable.

Until then, source control may contain an inactive version-scoped template, but
Stage 2 remains not accepted. Upgrading beyond the qualified client is blocked
until the same canary passes because 0.149.1 omits this provider override.

## 10. Meeting and Plaud design

Meeting Assistant remains the aggregate owner. A Plaud import creates a meeting
record and copies or securely moves the original audio into the selected
durable library before submitting inference. The design never edits source
artifacts in place:

```text
original audio
  -> raw STT transcript
  -> speaker-attributed transcript
  -> cleaned transcript
  -> versioned structured summary
```

Each derived artifact records its input checksum, producing service/model
release, timestamp, status, and schema version. Cleanup fixes punctuation and
obvious recognition errors but receives instructions to preserve meaning and
Danish/English code-switching. It cannot overwrite raw or attributed text.

The initial ingestion contract is explicit file import or a watched folder fed
by a documented Plaud export. Meeting Assistant owns retries and visible state.
No generic queue is added initially. If long-job recovery measurements prove
the application process is insufficient, introduce the smallest durable queue
on the application host using a separate Redis database/user or another
qualified broker; the GB10 never owns the authoritative job record.

The durable library location, encryption, backup, retention, consent, and
deletion behavior are Phase H design gates. Imported original audio is not
stored in PostgreSQL. Diarization initially produces `Speaker 1`, `Speaker 2`,
and so on; identity resolution is separately consented future scope.

## 11. Security design

- Trust is per consumer credential or client certificate, not IP address or header.
- Secrets are read from root/service-readable files or external environment injection with restrictive permissions.
- Containers run without privileged mode, with only required devices/mounts/capabilities.
- Filesystem mounts are read-only except explicit cache/state directories.
- Model and image artifacts are allow-listed and checksum/revision verified.
- The edge rejects unexpected content types and enforces request/audio size limits.
- Private aliases and audio routes have no configured cloud backend and pass an
  egress-denial test during local failure.
- Management, metrics, and container sockets are never exposed to the client LAN.
- Dependency/model license and provenance are recorded in the release manifest.
- Profile routing is derived from an authenticated Discord identity/channel or
  an internal scheduler credential before Hermes sees the request.
- Retrieval uses separate database/API roles and mandatory `owner_scope`
  predicates; a model-supplied scope is never authoritative.
- Consequential write tools create a pending proposal and cannot execute until
  a separate authenticated approval path binds the owner, exact action and
  arguments, expiry, and one-time approval. Hermes shell approvals and
  OpenShell egress approvals do not substitute for this business-action gate.

## 12. GB10 storage design

The 1 TB internal SSD is divided by measured quota rather than fixed partition
unless host constraints require partitions. Initial planning envelopes are:

| Class | Planning envelope | Control |
| --- | ---: | --- |
| OS, runtime, working reserve | 160 GB | System updates and recovery reserve |
| Pinned container images | 100 GB | Keep active and rollback generations |
| Qualified active models/voices | 500 GB | Manifest-controlled; no duplicate ad-hoc downloads |
| Staging/download cache | 120 GB | Evictable LRU/manual cleanup |
| Logs/metrics/temp | 20 GB | Rotation and retention |
| Free emergency headroom | 100 GB | Low-space alert before use |

These are adjustable envelopes, not claims about exact model size. Production
shall alert at 80% used and treat 90% as a change freeze until space is
recovered. External storage is added by ADR if qualified/rollback artifacts do
not fit or rebuild time is unacceptable.

Meeting audio and transcripts are excluded from these envelopes because they
remain in Meeting Assistant's separately backed-up durable library.

## 13. Deployment and rollback sequence

1. Run read-only host preflight and verify supported artifact tuple.
2. Pull artifacts by digest/revision and verify license/checksum metadata.
3. Render configuration with references to external secrets; validate offline.
4. Start candidate services on private/test endpoints.
5. Run smoke and compatibility tests, then warm representative models.
6. Switch the alias/edge route atomically to the candidate.
7. Run post-switch contract and privacy canaries.
8. Retain the last qualified release until the soak gate passes.
9. On failure, switch aliases/routes to the prior manifest; never mutate the prior release in place.

## 14. Design verification thresholds

All numerical acceptance thresholds are defined in `requirements.md`. The
benchmark corpus version, sample count, confidence/percentile method, warm/cold
condition, and exact artifact tuple accompany every result. A revised threshold
requires documented rationale, impact/risk review, and requirements version
change; the benchmark does not silently redefine success after seeing results.
