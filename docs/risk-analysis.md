# Risk analysis

**Version:** 0.1  
**Date:** 2026-08-25  
**Status:** Open risks require Phase C/D evidence

## Method

Severity (S) and likelihood (L) are scored 1–5. Initial score is `S × L`:
1–4 low, 5–9 medium, 10–15 high, and 16–25 critical. Residual scores are
estimated after the listed controls and must be revisited with measured data.
A high/critical residual risk blocks production unless explicitly accepted.

| ID | Hazard / failure | Impact | S | L | Initial | Controls and detection | Residual target | Owner / evidence |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| R-001 | Local coding model produces inferior or unsafe changes | Regressions, security flaws, developer rework | 4 | 4 | 16 | Frontier planning; bounded tasks; build/tests; cloud diff review; one evidence-informed retry; cloud reimplementation fallback; track review findings | 8 | Stage 2 owner; coding acceptance suite |
| R-002 | vLLM Responses/SSE/tool behavior differs from installed Codex | Tool loop stalls, malformed patch calls, lost work | 4 | 4 | 16 | Direct runtime PoC; exact pinned Codex client; terminal-event, parallel/namespaced tool, compaction, cancel and fault corpus; pin runtime/model/parser/template | 6 | Platform owner; Codex compatibility record |
| R-003 | Codex cross-provider behavior is version-fragile or loses the delegated task | Wrong/empty local work or target workflow unavailable | 5 | 5 | 25 | Pin installed 0.145.0 candidate; prove provider plus assignment/follow-up contents; block unqualified upgrades; whole-session modes on failure; preserve cloud planning/review | 10 | Stage 2 owner; version/canary evidence |
| R-004 | Model selects wrong Home Assistant tool/entity/arguments | Wrong physical action or safety event | 5 | 3 | 15 | Deterministic intent first; HA LLM API authority; least exposed entities; 10/25/50/100+ entity fixtures; denial/dangerous-action tests; fail closed; no hidden fallback | 5 | HA owner; tool accuracy and denial record |
| R-005 | Coding/n8n/meeting load delays voice | Unusable or misleading home control | 4 | 4 | 16 | Mixed-load tests; P0 priority; bounded background concurrency; smaller/reserved home model if needed; pause batch audio; admission control; latency alerts | 6 | Platform/HA owner; concurrency benchmark |
| R-006 | Resident weights, KV cache and workspaces exceed unified memory | OOM, service restart, cross-workload outage | 4 | 4 | 16 | Measured budget; 10% headroom; context/concurrency caps; consolidation/quantization; OOM injection/recovery; temperature/memory alerting | 6 | Platform owner; memory budget and soak |
| R-007 | Prompt, response, tool, audio, transcript, meeting, or Aula data enters infrastructure logs | Personal-data disclosure | 5 | 3 | 15 | Metadata-only config at all layers; distinct production/debug profiles; synthetic canary strings; retention/access controls; debug only with nonsensitive fixtures | 5 | Security owner; automated log canary |
| R-008 | Caller spoofs `X-AI-Consumer` to bypass policy/priority | Starvation, unauthorized use, misleading audit | 4 | 3 | 12 | Derive identity from credential/mTLS; replace or validate header; separate revocable credentials; negative tests | 4 | Security owner; auth matrix |
| R-009 | Edge/gateway modifies streaming semantics | Codex/clients hang or duplicate requests | 4 | 3 | 12 | Direct-vs-proxied corpus; disable buffering; client cancellation test; no retry after stream starts; gateway latency budget | 4 | Platform owner; gateway contract record |
| R-010 | AI gateway silently falls back to an unqualified or cloud model | Different tool behavior, hidden quality/safety change, private-data egress | 5 | 3 | 15 | Disable fallback for home/meeting/private automation; Codex fallback only in visible orchestration; per-key alias allow-list; actual-model telemetry; egress fault tests | 5 | Platform owner; fallback tests |
| R-011 | Floating or mismatched image/model/parser/template causes drift | Irreproducibility and latent incompatibility | 4 | 4 | 16 | Immutable manifest, digests/revisions/checksums, SBOM/license record, clean rebuild, rollback rehearsal, configuration hash | 4 | Release owner; release manifest |
| R-012 | Single GB10 hardware failure removes local AI | Voice reasoning and local implementation unavailable | 3 | 3 | 9 | Deterministic HA intents remain; explicit unavailable behavior; cloud planning/review independent; documented rebuild; monitoring; no claim of HA service availability | 6 | Operations owner; outage/rebuild test |
| R-013 | 1 TB SSD fills with models, caches, images, or logs | Failed updates, corrupt services, unavailable inference | 4 | 3 | 12 | Quotas/envelopes; 80/90% alerts; bounded caches/logs; active+rollback policy; cleanup/runbook; add external storage only by evidence | 4 | Operations owner; storage test |
| R-014 | Model or dataset license/provenance is incompatible | Legal/compliance exposure; forced replacement | 4 | 3 | 12 | Primary-source license verification; immutable revision; record usage restrictions; reject unclear license; replacement via aliases | 4 | Platform owner; selection record |
| R-015 | Supply-chain compromise in image/model/script | Host compromise or poisoned output | 5 | 2 | 10 | Trusted registries/repos; digest/revision pins; checksum/signature where available; least-privilege containers; secret scan; dependency review; update staging | 5 | Security/release owner; provenance audit |
| R-016 | STT misrecognizes Danish command | Wrong intent or action | 4 | 3 | 12 | Danish clean/noisy corpus; confidence/confirmation policy for ambiguous or dangerous actions; deterministic intent validation; no execution on invalid entity | 5 | HA owner; STT + end-to-end tests |
| R-017 | TTS is slow or mispronounces critical content | Confusing feedback, poor usability | 3 | 3 | 9 | Danish pronunciation/listening suite; first-audio/RTF gates; short responses; fallback notification behavior | 4 | HA owner; TTS record |
| R-018 | Metrics labels or request IDs leak sensitive/high-cardinality data | Privacy exposure and monitoring failure | 4 | 3 | 12 | Fixed label allow-list; request IDs only in bounded logs; adversarial label values; scrape/storage audit | 4 | Operations/security owner; telemetry test |
| R-019 | Retry after partial streaming duplicates generation or tool action | Duplicate side effects and inconsistent UI | 5 | 2 | 10 | No automatic retry once output begins; tool execution stays in client authority; idempotency where workflow owns it; disconnect/fault injection | 5 | Platform/consumer owners; stream fault test |
| R-020 | Existing n8n/Aula/Notion workflow is redesigned or migrated accidentally | Scope expansion, state loss, workflow regression | 4 | 2 | 8 | Treat n8n as one API consumer; no Aula route/service; no MCP or state migration; integration contract only; configuration review | 2 | Project owner; architecture audit |
| R-021 | Existing AI Home stack is assumed deployed or production-ready from source alone | Failed rollout, unsafe exposure, false reuse assumptions | 4 | 4 | 16 | Live inventory gate; pin images; inspect active data/keys/backups; stage upgrade; rollback; label unobserved services unverified | 6 | Control-plane owner; current-state and deployment record |
| R-022 | Shared LiteLLM control plane fails or translates Responses incorrectly | Both local and cloud aliases unavailable; Codex/tool loops fail | 4 | 3 | 12 | Direct vLLM baseline; full proxied corpus; health/recovery tests; pinned rollback; cloud providers remain directly available to Codex planning/review | 5 | Platform owner; V-GW-001 and outage test |
| R-023 | Plaud ingestion depends on an undocumented API or loses original audio | Integration breaks or irreplaceable recording is lost | 5 | 3 | 15 | Documented export/manual import first; durable copy and checksum before processing; retain source; import idempotency; recovery test | 5 | Meeting owner; import contract and recovery record |
| R-024 | Diarization assigns words to the wrong speaker | Incorrect decisions/actions or reputational harm | 4 | 4 | 16 | Speaker-attribution benchmark; confidence/unknown labels; transcript timestamps; human review; never infer identity silently | 8 | Meeting owner; diarization benchmark |
| R-025 | LLM cleanup alters or invents transcript content | Loss of evidentiary integrity; wrong summary/actions | 5 | 3 | 15 | Immutable raw/attributed layers; derived cleaned layer; provenance/checksums; code-switch fixtures; semantic-diff review; raw-text links | 5 | Meeting owner; provenance and cleanup tests |
| R-026 | Meeting audio retention leaks data or exhausts application-host storage | Privacy incident or failed imports/backups | 5 | 3 | 15 | Explicit durable location; encryption/access control; retention/deletion policy; disk alert; backup/restore; never store blobs in GB10 DB | 5 | Meeting/security owner; storage/privacy record |
| R-027 | Dirty Meeting Assistant worktree is modified before its current changes are reconciled | User work overwritten or conflicting meeting contracts | 4 | 4 | 16 | Read-only discovery; separate reviewed task; baseline/diff review; preserve user changes; tests before integration | 4 | Meeting owner; repository handoff record |
| R-028 | AI Home prototype ports or Docker socket expose the control host | Unauthorized API/container access or host compromise | 5 | 4 | 20 | Live bind/firewall scan; Caddy-only listener; private binds; remove or tightly replace Docker-socket scheduler access; distinct virtual keys; negative tests | 5 | Control-plane/security owner; V-SEC-001/003 and host audit |
| R-029 | AI Home consumers use the master key or an unprovisioned key | Excess privilege or nonfunctional clients | 4 | 4 | 16 | Inventory active clients; administrative master key held only by operator; issue/test/revoke virtual keys; per-key alias allow-list | 4 | Control-plane owner; credential matrix |
| R-030 | A Hermes profile or shared process exposes one person's memory, session, credentials, vector results, or notifications to another | Severe household privacy breach | 5 | 4 | 20 | Separate OpenShell sandboxes, state roots, bots, virtual keys, API/database roles and MCP providers; adversarial cross-sandbox corpus; Family receives shared only | 5 | Agent/security owner; V-PA-002 |
| R-031 | NemoClaw blueprint intent differs from the effective OpenShell policy or a custom skill bypasses managed credentials | Host/LAN access or secret disclosure | 5 | 3 | 15 | Pin tuple; inspect live Landlock/seccomp/network policy; default-deny egress; managed providers; treat injected secrets as visible; negative scans | 5 | Security owner; V-PA-001/002 |
| R-032 | Hermes persistent `state.db`, memory, sessions, skills, cron or messaging state is lost with a sandbox/host | Lost context, duplicate schedules, unreliable assistant | 4 | 3 | 12 | Application-host placement; encrypted offline snapshots; restore rehearsals; destroy guard; canonical data outside Hermes; idempotent schedules | 4 | Agent/operations owner; V-PA-001/006 |
| R-033 | Hermes' 64K context and concurrent profiles exhaust GB10 unified memory or delay P0 voice | OOM, stalled assistant or degraded home voice | 4 | 4 | 16 | Re-run memory/mixed-load gates at 64K; concurrency/admission limits; measure active-session residency; P0 priority; fail explicitly | 6 | Inference owner; V-MEM-001/V-PA-001 |
| R-034 | Discord user/channel routing or bot credentials select the wrong profile | Private disclosure or unauthorized tools/actions | 5 | 3 | 15 | Exact numeric allow-lists; separate bots/credentials; pre-model routing; DM/server/channel negative tests; revocation and audit | 5 | Messaging/security owner; V-PA-003 |
| R-035 | Hermes shell approval or OpenShell egress approval is mistaken for consent to a business action | Email/HA/calendar/financial action executes without informed approval | 5 | 4 | 20 | External pending-proposal service; exact immutable arguments, owner, expiry and one-time approval; agent never holds executor credential; denial/replay tests | 5 | Workflow/security owner; V-PA-004 |
| R-036 | Overlapping Hermes cron, Ofelia, n8n and HA schedules duplicate work or spam notifications | Alert fatigue, duplicate side effects and loss of trust | 3 | 4 | 12 | One named scheduler per job; idempotency keys; deduplication, rate limit, quiet hours, suppression and delivery audit | 4 | Workflow owner; V-PA-006 |
| R-037 | Discord voice is assumed full duplex or Parakeet-compatible when the current path pauses during TTS and expects Whisper providers | Unusable conversation, missed speech or hidden adapter scope | 3 | 4 | 12 | Voice last; document half-duplex baseline; test pause/interruption/Opus/ARM64/code-switch; explicit Parakeet adapter ownership | 4 | Voice owner; V-PA-005 |
| R-038 | Existing AI Home Hermes Compose/profile files are promoted without qualification | Unsupported config, exposed ports, master-key leakage or false isolation | 5 | 4 | 20 | Treat files as intent only; live inventory; supported installer; immutable artifacts; replace master key; OpenShell sandbox tests; staged rollback | 5 | Agent/control-plane owner; Phase I record |
| R-039 | Employer code, meetings, or documents cross into personal memory, family retrieval, backups, or cloud routes | Contractual/IP/privacy incident | 5 | 4 | 20 | Mandatory `data_domain`; separate work principal/store/index/credentials/backups; employer admission record; cross-domain denial corpus | 5 | Work-data/security owner; V-PA-007 |
| R-040 | Inaccurate, sensitive, stale, or supposedly deleted assistant memory continues influencing responses | Manipulation, distress, privacy failure, loss of trust | 5 | 3 | 15 | Versioned memory contract; review queue; sensitive-inference confirmation; provenance; tombstones; cache/index deletion; backup expiry | 5 | Agent/data owner; V-PA-008 |
| R-041 | “Local AI” is mistaken for local transport while Discord, Plaud, Notion, or search providers retain content | Unexpected third-party disclosure | 5 | 3 | 15 | End-to-end data map; route disclosure; provider allow-list; retention/config inventory; local alternative for restricted data | 5 | Privacy owner; V-SEC-009 |
| R-042 | NixOS root or backup access defeats expected partner confidentiality | Undisclosed household privacy breach | 5 | 3 | 15 | Recorded administrator threat model; named admin/access audit; optional user-held application encryption; isolated restore test | 5 | Household/security owner; V-PA-008 |
| R-043 | Container escape from a co-located workload reaches the gateway, its database, and materialized control-plane secrets on the shared `ai-services-01` kernel | Full control-plane compromise and personal-data disclosure from the one boundary that remains | 5 | 2 | 10 | Scored for the staged state: Hermes is deferred, so the prompt-injectable component holding tool credentials is absent and only the gateway and user-authored n8n workflows are co-located. Controls ranked by what they buy — no Docker socket, non-root, dropped capabilities raise escape cost; per-project networks and per-consumer LiteLLM keys limit the likelier non-escape compromise; state subtree and sops group defend only below root. Enforced by `validate-repository.sh`, not by prose | 6 | Security owner; URS-PA-019/020/021, V-SEC-001/003 |
| R-043a | R-043 is re-scored to likelihood 3 (score 15) the moment an agent sandbox, browser worker, or toolbox lands on the host kernel | Accepting a materially larger risk without a decision point | 5 | 3 | 15 | Treat this row as the trigger, not a separate risk: the `agents` microVM is a precondition for Hermes touching real or unauthored data, and the `automation` microVM for browser workers (URS-PA-020). Escape attempted from inside the sandbox during acceptance | 6 | Security owner; ADR-017 staged kernel plan |
| R-044 | Gateway, PostgreSQL, n8n, and later agent sandboxes contend for CPU on one host, or an unbounded sandbox starves the gateway | Gateway latency regression; in the worst case OOM taking automations and inference routing down together | 3 | 2 | 6 | Weights stay on `ai-compute-01`, so co-located services hold runtime memory and 48 GB leaves substantial headroom; contention rather than exhaustion is the exposure. Measure gateway/database/n8n usage before the agent pilot; set per-project memory and CPU limits from measurement; alert on host pressure; stage sandboxes one at a time | 4 | Platform owner; ADR-017 capacity record |
| R-045 | A single `ai-services-01` failure now stops gateway, automations, and agents together | Broader outage than ADR-016 assumed, with more state to restore | 3 | 3 | 9 | Deterministic home control stays on the Home Assistant appliance; off-host encrypted backup covers every `/srv/state` subtree; isolated restore test spans all projects, not just the gateway; documented rebuild order | 6 | Operations owner; restore test |

## Critical decision risks

### Codex cross-provider routing

R-003 is not mitigated by a gateway. Installed Codex 0.145.0 can apply a
role-level provider in source, while 0.149.1 omits that field and 0.146.0 issue
reports show custom-provider children losing assignments. Stage 2 automatic
routing therefore remains **not accepted** until the pinned 0.145.0 provider,
initial-task, follow-up, tool, retry and review canary passes. Every later client
upgrade repeats the gate. This does not block Stage 1 or a complete local Codex
session PoC.

### Home Assistant physical actions

Model quality is not the only control. Home Assistant remains the enforcement
point, tools/entities are explicitly exposed, deterministic intents take
precedence, and unqualified fallback is forbidden. Any expansion of exposed
dangerous operations requires risk review and new denial/confirmation tests.

### Sensitive n8n data

The platform does not create an Aula subsystem. R-007 applies because the
existing n8n workflow can place Aula-derived content in an ordinary
`automation` request. A canary fixture verifies that edge, gateway, runtime,
error, container, and telemetry outputs do not retain body content.

## Risk review triggers

Review this file when any of the following changes:

- Codex stable version or provider/subagent behavior;
- runtime/container, model revision, quantization, tokenizer/template, or parser;
- gateway, authentication, rate-limit, or logging mechanism;
- exposed Home Assistant entities/tools or dangerous-action policy;
- resident model set, context, concurrency, priority, or storage layout;
- STT/TTS/diarization model, voice, protocol, audio hardware, Plaud import, or meeting storage;
- Hermes/NemoClaw/OpenShell version, sandbox image/policy, profile route,
  Discord bot/channel, personal event schema/store, managed provider, schedule,
  approval service, or 64K context/concurrency;
- a security advisory, license change, failed soak, privacy canary, OOM, or fallback event.
