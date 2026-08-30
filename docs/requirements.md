# User requirements specification

**Version:** 0.2  
**Date:** 2026-08-25  
**Status:** Design baseline; hardware-dependent requirements require Phase C/D evidence

## Purpose and scope

This specification defines Stage 1 (the shared control plane and GB10 inference
services), Stage 2 (Codex integration), and the later speech, meeting, and
personal-assistant stages. The GB10 is primarily a dedicated AI compute
appliance. Hermes/OpenShell is a separate application layer on an always-on
application host; a direct GB10 deployment is allowed only as a non-production
pilot unless a later ADR deliberately changes the inference-only boundary.
Source code, IDEs, builds, tests, Codex, Home Assistant, existing n8n/MCP
services, Meeting Assistant, and canonical durable workflow/personal/meeting
state remain on their current or separately backed-up application hosts.

Aula is part of an existing n8n workflow through the existing Aula MCP. The
live host placement remains to be verified. It is not a consumer, service,
deployment unit, model role, route, or workstream in this project. Its data is
protected by the n8n privacy controls.

Meeting Assistant is an existing consumer and the owner of meeting-domain
state. Plaud support extends that application; it does not create a second
meeting platform on GB10.

Requirement priority uses **Must**, **Should**, and **May**. A Must requirement
can be deferred only by a recorded risk acceptance or an ADR that changes this
baseline.

## System and API requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-AI-001 | The GB10 shall host only AI inference and directly supporting telemetry/model-artifact functions. The shared model control plane, Hermes/OpenShell application runtime, and canonical durable state shall remain off-appliance. A direct-on-GB10 Hermes pilot shall not become production without an ADR that supersedes this boundary and proves backup, restore, encryption, capacity, and failure ownership. | Must | Inspection |
| URS-AI-002 | Clients shall use a stable DNS endpoint and shall not depend on a GB10 IP address, runtime port, container name, or model artifact. | Must | Test, inspection |
| URS-AI-003 | The platform shall expose task-semantic `automation`, `research`, `coding`, `home`, `meeting`, and `assistant` aliases plus stable STT/TTS routes. Alias names shall not encode a vendor, model, runtime, or local/cloud placement. | Must | Contract test |
| URS-AI-004 | A model/runtime replacement behind an alias shall not require a client configuration change. | Must | Replacement test |
| URS-AI-005 | One physical model may implement multiple aliases when qualification shows it meets every role's requirements. | May | Design review |
| URS-AI-006 | The public text boundary shall support the OpenAI Responses API semantics needed by the installed Codex client, including SSE streaming and function calls. | Must | Codex compatibility test |
| URS-AI-007 | Existing n8n or Home Assistant clients may use Chat Completions only where required; it shall not be the Codex compatibility boundary. | Should | Interface test |
| URS-AI-008 | The platform shall expose only qualified public endpoints; runtime administration, profiling, and model-management endpoints shall remain private. | Must | Network test |
| URS-AI-009 | Every request shall have a returned and propagated request ID. | Must | Fault-path contract test |
| URS-AI-010 | Authenticated consumer identity shall distinguish Codex, public and private n8n workloads, Home Assistant, Meeting Assistant, and each Hermes sandbox. A client-supplied label alone shall not establish identity. | Must | Security test |
| URS-AI-011 | The existing AI Home LiteLLM control plane shall be reused if it passes the gateway suite; no second generic model gateway shall be deployed on GB10 by default. | Must | Current-state and deployment inspection |
| URS-AI-012 | The control plane shall provide per-consumer alias allow-lists, revocation, usage attribution, and bounded quotas/rate limits where mixed-load evidence requires them. | Must | Gateway policy and load test |
| URS-AI-013 | `home`, `meeting`, `assistant`, and private `automation` shall have no cloud fallback. `research` may fall back only for explicitly public/redacted workloads; `coding` fallback shall remain visible in Codex orchestration. | Must | Route-policy and egress test |

## Model-role requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-GEN-001 | `automation` shall support Danish and English summarization, extraction, classification, synthesis, and structured JSON for existing n8n workflows. | Must | General benchmark |
| URS-GEN-002 | `automation` shall not introduce persistent memory; existing n8n/Notion state remains authoritative. | Must | Architecture inspection |
| URS-GEN-003 | `automation` shall pass at least 95% of schema-constrained fixtures and 90% of factual/instruction rubric points, with no sensitive-data logging. | Must | Benchmark and log canary |
| URS-GEN-004 | Web search shall remain an explicit retrieval capability owned by the calling workflow. The LLM shall receive normalized results and shall not be treated as the search provider. | Must | Workflow inspection and integration test |
| URS-GEN-005 | Frequent workflows shall normalize, deduplicate, compare prior state, and invoke an LLM only for new or materially changed input where feasible. | Should | Representative workflow test |
| URS-CODE-001 | `coding` shall support repository inspection, patch generation, shell/tool calls, multi-turn correction, and representative .NET, Python, Vue, and React/TypeScript tasks. | Must | Coding benchmark |
| URS-CODE-002 | At least 70% of the agreed representative coding task set shall pass build/tests and frontier review without cloud reimplementation. | Must | Acceptance benchmark |
| URS-CODE-003 | `coding` shall never be selected solely from synthetic coding leaderboard results. | Must | Selection record inspection |
| URS-HA-001 | Home Assistant shall remain the authority that exposes and executes allowed tools and entity actions. | Must | Architecture and integration test |
| URS-HA-002 | Deterministic Assist intents shall be attempted before `home` where Home Assistant supports that routing. | Must | Flow test |
| URS-HA-003 | `home` shall select the exact expected tool, entity, and arguments for at least 98% of the normal-action acceptance corpus and 100% of safety-critical denial fixtures. | Must | HA benchmark |
| URS-HA-004 | `home` shall produce zero hallucinated entity executions in the acceptance corpus. | Must | HA benchmark |
| URS-HA-005 | Entity-count tests shall include 10, 25, 50, and at least 100 exposed entities. | Must | Benchmark inspection |
| URS-STT-001 | The STT capability shall support Danish and English audio and integrate with Home Assistant and Meeting Assistant through supported protocols. | Must | STT integration test |
| URS-STT-002 | On the agreed Danish corpus, word error rate shall be no greater than 15% for clean speech and 25% for the noisy/far-field set. | Must | STT benchmark |
| URS-STT-003 | STT shall preserve Danish/English code-switching without forcing translation, and the benchmark shall include Danish-only, English-only, and mixed meetings. | Must | Mixed-language benchmark |
| URS-TTS-001 | The TTS capability shall provide a qualified Danish voice and integrate with Home Assistant through a supported local protocol. | Must | TTS integration test |
| URS-TTS-002 | The Danish pronunciation set shall achieve at least 95% reviewer pass rate and a mean naturalness score of at least 3.5/5. | Must | Blinded listening test |

## Personal assistant requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-PA-001 | Home Assistant, Node-RED, Zigbee2MQTT, MQTT, and deterministic/safety-critical home behavior shall continue operating when the GB10, Hermes, or the model gateway is unavailable. | Must | Outage demonstration |
| URS-PA-002 | Hermes shall be treated as a version-pinned candidate agent runtime and shall not enter production until its supported application-host install path, 64K-context local OpenAI-compatible model path on GB10, tool loop, persistence, upgrade, and rollback behavior pass the pilot suite. A direct DGX Spark/ARM64 deployment may be evaluated but is not the production default. | Must | Compatibility pilot |
| URS-PA-003 | Production profiles shall use separate OpenShell sandboxes created through the qualified NemoClaw/Hermes path. The exact released tuple and effective filesystem, process, network, inference, and credential policies shall pass live denial tests; prompt instructions and blueprint defaults alone are not a security boundary. | Must | Security inspection and negative test |
| URS-PA-004 | `owner`, `partner`, and `family` shall run in separate sandboxes/security principals. Discord identity/channel routing shall select the principal before any content reaches the model. | Must | Cross-profile routing test |
| URS-PA-005 | Every canonical personal-data record and retrieval path shall enforce `owner_scope` below the model: Owner may read `owner` and `shared`, Partner may read `partner` and `shared`, and Family may read only `shared`. The same rule applies to credentials and tools. | Must | Authorization test |
| URS-PA-006 | Hermes working memory, sessions, and learned skills shall be isolated per profile and shall not be the canonical record of email, transactions, meetings, calendar events, tasks, home observations, or documents. | Must | Storage and isolation inspection |
| URS-PA-007 | Canonical personal history shall use a versioned event contract with stable source identity, timestamp, `owner_scope`, provenance, processing state, structured metadata, and optional embeddings/relationships. Ingestion shall be idempotent and raw-source retention shall be explicit. | Must | Schema and replay test |
| URS-PA-008 | Each profile and integration shall use distinct revocable credentials. The LiteLLM administrative key, another person's channel token, database role, or tool credential shall never be mounted into a profile. | Must | Credential matrix and revocation test |
| URS-PA-009 | The initial human interface shall be allow-listed Discord text with private Owner/Partner contexts, a shared Family context, and a separate notification destination. Proactive messages shall be attributable, rate-limited, deduplicated, and suppressible. | Should | Discord integration test |
| URS-PA-010 | Discord voice is a later stage behind independent STT/TTS APIs. Session context may persist during a call, but speaker recognition alone shall never grant private-profile access or consequential permissions. | Should | Voice and identity test |
| URS-PA-011 | The assistant may autonomously read permitted data, analyze, recommend, and draft. Sending messages, changing bookings, deleting data, transferring money, changing significant home logic, or another consequential external action shall require explicit human approval unless a narrowly scoped action has a separately recorded authorization. | Must | Approval/denial test |
| URS-PA-012 | n8n or another explicitly assigned workflow owner shall handle deterministic ingestion, retries, schedules, normalization, and delivery. Only one scheduler shall own each job; Hermes shall receive a bounded task rather than broad Docker or host control. | Must | Ownership inventory and duplicate-run test |
| URS-PA-013 | Data-source rollout shall start with synthetic/manual and low-risk sources, then calendar/Plaud/email/home analytics; financial data shall wait for passing profile isolation, audit, retention, backup/restore, and approval controls. | Must | Phase-gate inspection |
| URS-PA-014 | Private profile data shall have no implicit cloud fallback. Any cloud use shall be selected by a deterministic policy before submission, visibly disclosed, and limited to explicitly public or redacted content. | Must | Forced-local-failure egress test |

## Meeting and Plaud requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-MTG-001 | Meeting Assistant shall remain the owner of meeting lifecycle, user-visible state, and durable meeting records. | Must | Architecture and source inspection |
| URS-MTG-002 | The system shall support an explicit Plaud audio import path without depending on an undocumented private API. | Must | Import contract test |
| URS-MTG-003 | Each imported meeting shall retain the original audio, raw STT transcript, speaker-attributed transcript, cleaned transcript, summary, and structured decisions/actions with provenance between layers. | Must | Artifact and recovery test |
| URS-MTG-004 | Raw and speaker-attributed transcripts shall be immutable inputs; LLM cleanup shall create a derived artifact and shall not overwrite source text. | Must | Mutation/provenance test |
| URS-MTG-005 | The meeting pipeline shall support unknown speaker labels and diarization; speaker identity resolution may be added later. | Must | Diarization benchmark |
| URS-MTG-006 | Meeting output shall include summary, decisions, action items, topics, participants or speaker labels, and detected languages in a versioned structured schema. | Must | Schema contract test |
| URS-MTG-007 | Meeting audio, transcripts, and derived content shall be local-only by default. A local failure shall remain pending/failed and shall never trigger silent cloud transmission. | Must | Egress-denial fault test |
| URS-MTG-008 | Plaud's own transcription shall be included as a benchmark reference when available; local quality shall be measured rather than assumed equivalent. | Should | Comparative benchmark |

## Performance and capacity requirements

These are user-experience gates, not claims about untested hardware. Phase C/D
must record the achieved values and any approved threshold revision.

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-PERF-001 | A warm `home` request shall have p95 model time-to-first-token no greater than 750 ms on the production entity/tool schema. | Must | Mixed-load benchmark |
| URS-PERF-002 | A warm end-to-end Home Assistant voice turn for a simple command shall complete first audible confirmation within 2.5 s p95 on the local LAN. | Must | Voice benchmark |
| URS-PERF-003 | STT and TTS shall each sustain a p95 real-time factor no greater than 0.5 on the acceptance corpus. | Must | Audio benchmark |
| URS-PERF-004 | TTS shall produce first audio within 750 ms p95 for short Home Assistant responses. | Must | TTS benchmark |
| URS-PERF-005 | Under one Codex generation plus one background n8n or meeting request, `home` shall still meet URS-PERF-001 and URS-PERF-002. | Must | Concurrent benchmark |
| URS-PERF-006 | The gateway shall add no more than 50 ms p95 or 5% p95 time-to-first-token, whichever is larger, compared with the qualified direct-runtime baseline. | Must | A/B benchmark |
| URS-PERF-007 | Memory planning shall include model weights, KV cache, workspaces, OS/display reservation, audio services, telemetry, and recovery headroom; nominal fit in 128 GB is insufficient. | Must | Measurement and analysis |
| URS-PERF-008 | A production configuration shall retain at least 10% measured unified-memory headroom under the acceptance mixed load and shall recover predictably from a forced allocation failure. | Must | Stress test |

## Security and privacy requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-SEC-001 | Only the authenticated edge endpoint shall be reachable from the client LAN; runtime and management listeners shall be private. | Must | Port scan, configuration inspection |
| URS-SEC-002 | LAN API traffic shall use TLS where supported by the selected clients, with a documented local trust and rotation procedure. | Must | TLS test |
| URS-SEC-003 | Codex, public n8n, private n8n, Home Assistant, and Meeting Assistant shall use distinct revocable credentials. Secrets shall not be committed to source control or container images. | Must | Secret scan, revocation test |
| URS-SEC-004 | Unknown consumers, models, methods, and paths shall be denied. | Must | Negative test |
| URS-SEC-005 | Home Assistant tools shall be least-privilege; the model shall not receive unrestricted administrative, Zigbee, MQTT, Node-RED, or device access. | Must | Permission test |
| URS-SEC-006 | Prompt, response, audio, transcript, tool argument/result, authorization, Aula, and meeting content logging shall be disabled by default at every infrastructure layer. | Must | Canary/log inspection |
| URS-SEC-007 | Metadata logs shall have documented access controls and retention. Debug content logging shall require an explicit time-bounded maintenance procedure using nonsensitive fixtures. | Must | Procedure and configuration test |
| URS-SEC-008 | Dependency images, model revisions, templates, parsers, and launch configuration shall be pinned and traceable; floating `latest` shall not be production input. | Must | Bill-of-materials inspection |

## Reliability and operations requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-OPS-001 | All Stage 1 services shall start automatically after a clean host reboot. | Must | Reboot test |
| URS-OPS-002 | Liveness and readiness checks shall distinguish process availability, model readiness, and dependency failure. | Must | Fault injection |
| URS-OPS-003 | A single backend failure shall not expose an unqualified alternate model. Home Assistant tool execution shall fail closed. | Must | Fault injection |
| URS-OPS-004 | A failed service shall either recover automatically within 5 minutes or raise an actionable alert containing no sensitive content. | Must | Recovery test |
| URS-OPS-005 | Disk, memory, model-load, queue, error, request latency, TTFT, throughput, restart, and OOM signals shall be observable. | Must | Metrics inspection |
| URS-OPS-006 | Telemetry shall use bounded-cardinality labels and shall not use prompts, user data, request IDs, or entity state as metric labels. | Must | Metrics inspection |
| URS-OPS-007 | The 1 TB internal SSD shall have documented allocation, model cache limits, log retention, low-space alerts, and a rebuild/restore procedure. | Must | Storage test |
| URS-OPS-008 | Deployment shall be reproducible from a clean supported host using source-controlled, idempotent scripts and configuration plus separately supplied secrets/models. | Must | Destructive rebuild rehearsal |
| URS-OPS-009 | Upgrade and rollback shall operate on the qualified tuple of OS/driver, container digest, runtime, model revision, quantization, tokenizer/template, parser, and flags. | Must | Upgrade/rollback rehearsal |
| URS-OPS-010 | The platform shall run a 24-hour mixed-workload soak without unhandled failure, sensitive logging, unrecovered OOM, or loss of required service readiness. | Must | Soak test |

## Codex Stage 2 requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-CODEX-001 | Codex shall remain the only normal developer-facing harness; no separate coding UI shall be introduced. | Must | Workflow demonstration |
| URS-CODEX-002 | The installed Codex client shall successfully run a complete local session using the GB10 custom provider and `coding`. | Must | PoC |
| URS-CODEX-003 | Planning and review shall use the configured cloud frontier provider while implementation uses GB10 only after a pinned Codex client passes cross-provider provider-selection, task-delivery, tool, retry, and review tests; every client upgrade shall requalify this path. | Must | Version check and trace test |
| URS-CODEX-004 | No undocumented or unreleased Codex configuration field shall be used in the production workflow. | Must | Configuration inspection |
| URS-CODEX-005 | After URS-CODEX-003 passes, the normal workflow shall perform one local implementation attempt, one evidence-informed local retry, then cloud implementation fallback without manual provider switching. | Must | End-to-end fault scenario |
| URS-CODEX-006 | Planning/review shall remain usable during a GB10 outage; implementation shall record an explicit fallback or blocked reason. | Must | Outage test |
| URS-CODEX-007 | Coding metrics shall record task/model/runtime, attempts, build/tests, fallback, review findings, duration, and token/performance metadata without source or prompt content. | Must | Metrics test |
| URS-CODEX-008 | Until URS-CODEX-003 is feasible, the platform shall expose explicit whole-session cloud and GB10 modes and shall report Stage 2 automatic routing as not accepted. | Must | Documentation and UX test |

## Maintainability and change requirements

| ID | Requirement | Priority | Verification |
| --- | --- | --- | --- |
| URS-MNT-001 | Architecture, requirements, design, risks, verification, ADRs, diagrams, operations, security, storage, model selection, fallback, and troubleshooting shall be source controlled. | Must | Documentation audit |
| URS-MNT-002 | Benchmarks and acceptance fixtures shall be version controlled; result records shall identify the complete qualified artifact tuple. | Must | Artifact audit |
| URS-MNT-003 | Every major component shall have a documented requirement and removal/reconsideration trigger. | Must | Design review |
| URS-MNT-004 | Model replacement shall use qualification and rollback procedures, not ad-hoc cache edits. | Must | Change rehearsal |
| URS-MNT-005 | Configuration validation and smoke tests shall run before deployment changes are activated. | Must | CI/deployment test |

## Traceability and acceptance rule

The verification strategy maps every Must requirement to one or more test,
inspection, analysis, or demonstration records. A Stage cannot be marked
accepted while a Must requirement for that Stage is failed, untested, or
silently waived. Hardware-dependent rows may remain **Not Run** before the GB10
is available; this is not a pass.
