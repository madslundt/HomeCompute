# Implementation plan

**Version:** 0.2  
**Date:** 2026-08-25  
**Execution rule:** a downstream phase starts only when its named gate passes

The role-based, operator-facing sequence for both physical nodes is the
[platform execution plan](platform-execution-plan.md). This document remains
the detailed feature/qualification phase ledger.

## Status legend

- **Complete:** source artifact exists and has been internally reviewed.
- **Ready:** inputs exist; execution can start when hardware/integration access is available.
- **Blocked:** a named external capability is absent.
- **Pending:** depends on earlier work.

## Phase A — Research

**Status: Repository research complete; live infrastructure inventory blocked**

| Work package | Output | Exit condition |
| --- | --- | --- |
| A1 Platform/runtime research | `docs/research/inference-runtime-evaluation.md` | GB10 paths, protocol, metrics, memory, trade-offs and decision documented |
| A2 Codex research | `docs/research/codex-compatibility.md` | Released-client capabilities and blocker documented |
| A3 Model-role research | Coding/general/home/STT/TTS research files | Candidates, licenses, scorecards and benchmark needs documented |
| A4 Gateway research | `docs/research/gateway-evaluation.md` | Direct baseline plus the current-state reconciliation to the existing AI Home control plane documented |
| A5 Boundary review | All research docs | Aula appears only inside existing n8n path; no migration/persistence scope added |
| A6 Current-state review | `docs/current-state.md` | AI Home and Meeting Assistant reuse mapped; live HAOS/Mac Mini state explicitly unverified |
| A7 Handoff validation | `docs/research/gx10-platform-validation.md` | Current primary-source GB10/runtime evidence and handoff deltas recorded |
| A8 Hermes verification | `docs/research/hermes-personal-assistant-verification.md` | Hermes/NemoClaw/OpenShell, DGX Spark, Discord, HA/n8n, memory, context, and approval boundaries verified from primary sources |

Decision: direct PoC with pinned NVIDIA-compatible vLLM; production candidate
reuses AI Home LiteLLM behind Caddy; llama.cpp is the required quantized
baseline; TensorRT-LLM is a conditional same-model challenger. Meeting
Assistant remains the meeting-domain owner.

## Phase B — Requirements and design

**Status: Independent repository review applied 2026-08-25; awaiting user
design approval before target execution**

| Work package | Output | Exit condition |
| --- | --- | --- |
| B1 URS | `docs/requirements.md` | Functional/nonfunctional requirements and numerical gates identified |
| B2 Architecture | `docs/architecture.md` | Required source-controlled diagrams and boundaries exist |
| B3 Design | `docs/design-specification.md` | API, services, identity, logging, storage, scheduling, deployment and rollback specified |
| B4 Risk analysis | `docs/risk-analysis.md` | Initial/residual risks, controls and triggers recorded |
| B5 Verification | `docs/verification-strategy.md` | Tests and requirement traceability defined |
| B6 ADRs | `docs/adr/001` through `016` | Major decisions/status/evidence recorded |
| B7 Plan/dependencies | This file and `dependency-graph.md` | Ordered packages, gates and blockers visible |

## Phase B-S — AI services-node foundation

**Status: NixOS configuration ready; hardware execution pending**

The future always-on x86 target is `ai-services-01`, documented in
`nixos-control-plane-node-plan.md`, ADR-016, and the root flake. Installation
must not migrate unverified live services as part of provisioning.

| Gate | Outcome | Blocks |
| --- | --- | --- |
| S0 NixOS host | Firmware, labelled filesystems, pinned flake, UPS, SMART, firewall, and reboot pass | Control-plane workload |
| S1 Host services | SSH, Tailscale, Docker, Home Manager, sops-nix, `/srv/state`, backup, and restore pass | S2-S5 |
| S2 AI gateway | Existing AI Home inventory, equivalence, recovery, and rollback pass on `ai-services-01` | C3/E production edge |
| S3 Automations | n8n/MCP inventory and low-risk staged migration pass as an isolated Compose project on `ai-services-01` | G workflow integration |
| S3A Agent project | Per-project network, user, state, secret, and resource isolation plus backup and denial tests pass | I/J personal-agent pilot |
| S4 Toolbox | Restricted tools/CI runner project passes credential and network checks | Optional developer workloads |
| S5 Optional HAOS | Separate HAOS/radio migration and restore evidence pass | Any Home Assistant relocation |

S0/S1 can proceed independently of compute-node model qualification. S2 needs the
private compute link for local-route tests and the existing gateway inventory
for safe migration. The old hosts remain rollback targets until their
replacement gate passes.

## Phase C — Minimal PoC

**Status: Deployment scaffold ready; C0/C1 execution blocked pending user
design approval and target GB10 access. Live control-plane inventory blocks C3,
not the direct-runtime baseline.**

The read-only preflight, immutable-input validator, first-candidate Compose
definition, setup/rollback commands, and D2 deployment views are implemented in
`scripts/setup-compute-node.sh`, `deploy/compute-node`, `config`, and `diagrams`. Their use and
deliberate automation boundary are documented in `setup-guide.md`. No gate is
marked passed by the existence of this scaffold.

### C0 — Capture environment

1. Record GB10 hardware, DGX OS, firmware, kernel, driver, CUDA, power mode,
   display reservation, network/DNS, and free disk.
2. Resolve immutable NVIDIA NGC vLLM image digest and candidate model revision.
3. Record model license, tokenizer/template, parser, format/quantization, and
   intended context/concurrency.
4. Create the first release-manifest draft; do not use floating tags.

**Gate C0:** artifact tuple is complete, licensed, retrievable, and fits a
conservative estimated memory envelope.

### C1 — Direct Codex to vLLM

1. Start vLLM on a test-only private listener with request/output logging off.
2. Configure a temporary machine-local Codex GB10 provider.
3. Execute V-CODEX-001: streaming, terminal event, serial/parallel/namespaced
   tools, MCP, malformed calls, cancel/disconnect, reasoning, and compaction.
4. Capture sanitized protocol/metrics evidence and exact commands.
5. Fix by changing only a pinned runtime/model/template/parser tuple; record
   every attempted tuple.

**Gate C1:** V-CODEX-001 passes without adapter/proxy. If it fails, evaluate
the smallest adapter or next runtime and update ADR-002 before proceeding.

### C2 — Home Assistant restricted-tool PoC

1. Expose a small test area and safe entities through Home Assistant LLM API.
2. Connect candidate `home` through the supported integration.
3. Execute deterministic, allowed, unexposed, ambiguous, and dangerous fixtures.
4. Verify model has no direct device/admin access and no body logging.

**Gate C2:** V-HA-001 passes. Failure triggers model/schema/integration review,
not broader permissions.

### C3 — Shared control-plane PoC

1. Inventory the live AI Home deployment and record active LiteLLM version,
   configuration, consumers, database, keys, ports, backups, and rollback path
   without exporting secrets or request content.
   Explicitly verify host bind addresses/firewall rules, remove consumer use of
   the LiteLLM master key, and replace or justify Ofelia's Docker-socket access.
2. Stage pinned Caddy and LiteLLM versions with local TLS, private upstream,
   request ID, virtual keys, per-key alias allow-lists, and metadata-only logs.
3. Repeat the complete Codex protocol suite through `https://ai.home.arpa` and the
   combined Caddy/LiteLLM path.
4. Run authentication, revocation, cross-alias denial, local-only egress,
   log-canary, cancellation, recovery, and direct/proxied latency tests.

**Gate C3:** V-GW-001 and relevant V-SEC tests pass within the latency budget.

## Phase D — Benchmark and select

**Status: Pending C1–C3**

### D1 — Build fixture framework

Create versioned fixture manifests and runners for:

```text
benchmarks/general
benchmarks/research
benchmarks/coding
benchmarks/home-assistant
benchmarks/assistant
benchmarks/stt
benchmarks/tts
benchmarks/meeting
benchmarks/diarization
benchmarks/mixed-load
```

Runners emit machine-readable raw results plus a sanitized summary template.
They refuse a run without the release/artifact manifest.

### D2 — Select text and audio artifacts

1. Run all shortlisted models against role scorecards.
2. Run the Qwen3.6 NVFP4 baseline with MTP on and off; Qwen3-Coder-Next FP8;
   Nemotron 3.5 Lightning target-only, native-MTP, and DSpark modes; Gemma 4
   31B NVFP4; Qwen3.8-27B FP8; and the bounded role-specific controls. Use
   Qwen3.6-27B NVFP4 only for an optional precision/runtime A/B. Compare Q4/Q8
   GGUF only as controlled artifact tuples, not as different model identities.
   Benchmark vLLM against llama.cpp only where both support the same exact
   model/revision; add TensorRT-LLM under the same rule.
3. Select the simplest alias-to-artifact mapping meeting every role threshold.
4. Run M1–M5 mixed load and V-MEM-001.
5. Decide shared vs reserved `home`, priority mechanism, resident set, and
   context/concurrency. LiteLLM is already selected as the control-plane
   candidate but still must pass C3.
6. Publish dated results, including candidate pros/cons and the measured reason
   each winner displaced its alternatives, then update ADRs/design.

**Gate D:** every alias has a winning qualified tuple, threshold evidence,
license record, memory budget, and rollback candidate. No leaderboard-only
selection is accepted.

## Phase E — Stage 1 implementation

**Status: Pending Gate D**

Implement in this order:

1. repository schemas, manifest validator, and configuration tests;
2. read-only host preflight, then idempotent bootstrap;
3. private network/directories/service users/firewall;
4. pinned text service with liveness/readiness plus the minimum speech services
   required by the accepted Stage 1 scope;
5. upgrade the existing AI Home LiteLLM with pinned local backends, semantic
   aliases, virtual keys, and privacy policies;
6. add Caddy TLS edge, request ID, route allow-list, streaming, and recovery;
7. logical registry and atomic switch/rollback tooling;
8. metadata logs, metrics, alerts, retention, disk/memory controls;
9. smoke, integration, fault, security, privacy, mixed-load and acceptance tests;
10. installation, operations, security, storage, models, HA, fallback, and
    troubleshooting documentation;
11. reboot, 24-hour soak, rollback, and clean rebuild rehearsal.

**Gate E / Stage 1 acceptance:** every applicable Stage 1 Must requirement has
passing evidence and no unaccepted high/critical residual risk.

## Phase F — Stage 2 Codex workflow

**Status: Blocked pending the pinned 0.145.0 cross-provider canary**

### Work allowed before unblock

- qualify complete GB10 Codex whole-session mode;
- maintain the inactive target-agent schema/template;
- build coding fixtures and metrics schema;
- run V-CODEX-VER-001 on installed 0.145.0, then rerun it before every client upgrade.

### Activation after unblock

1. Pin installed Codex 0.145.0 and record its binary/version; do not upgrade during qualification.
2. Run a cloud-parent → GB10-child canary that proves provider, initial assignment, follow-up, credentials and tools.
3. Implement planner, bounded task contract, local implementer, local retry,
   cloud implementer fallback, and cloud reviewer inside Codex.
4. Add explicit escalation codes and provider/fallback metrics.
5. Run V-CODEX-E2E-001, outage tests, real-repository corpus, and log canary.
6. Document installation/update/rollback and the accepted client versions.

**Gate F / Stage 2 acceptance:** all URS-CODEX requirements pass, including no
manual switching in the normal workflow. If 0.145.0 loses the task or cannot be
pinned safely, Stage 2 remains blocked until a later client passes rather than
being replaced by an unapproved harness.

## Phase G — Home Assistant, n8n, and speech migration

**Status: Pending Gate E and verified consumer exports**

1. Inventory active Home Assistant, Node-RED, n8n, search, and scheduler
   ownership; record components reused unchanged.
2. Migrate one low-risk public research workflow using `research`; normalize,
   deduplicate, compare prior state, and call the LLM only for meaningful change.
3. Migrate one private automation workflow using a private credential and
   `automation`; prove that local failure cannot reach cloud.
4. Integrate deterministic Home Assistant intents first, then restricted
   `home` tool reasoning.
5. Benchmark and deploy STT/TTS through supported Wyoming adapters; verify
   Danish, English, code-switching, mixed load, and text fallback when TTS fails.

**Gate G:** representative n8n and Home Assistant flows pass quality, privacy,
latency, recovery, and no-duplicate-scheduler checks without consumer knowledge
of model artifacts.

## Phase H — Meeting Assistant and Plaud

**Status: Pending Gate E, Meeting Assistant repository reconciliation, and an accepted Plaud import contract**

1. Reconcile and review the current Meeting Assistant worktree; define the
   separately owned implementation baseline.
2. Configure one `ai.home.arpa` Meeting Assistant account and bind rolling/final
   summary jobs to `meeting`; prove no silent cloud fallback.
3. Add explicit Plaud file import and durable original-audio retention to
   Meeting Assistant; do not use an undocumented private API.
4. Add immutable raw, speaker-attributed, and cleaned transcript artifacts with
   checksums and provenance.
5. Benchmark Whisper large-v3-turbo, large-v3, Danish Parakeet, Plaud's
   transcript reference, and pyannote or another qualified diarization candidate.
6. Add the versioned structured meeting schema and human-verifiable links to
   transcript layers.
7. Run failure/resume, backup/restore, retention/deletion, privacy-egress,
   mixed-load, and long-recording tests. Add a durable queue only if this
   evidence proves the existing app orchestration insufficient.

**Gate H:** V-MTG-001/002/003 pass on real representative Danish, English, and
mixed meetings, and original audio plus every derived layer survives failures
and restore.

## Phase I — Hermes personal assistant pilot

**Status: Deferred by owner decision (2026-09-04). Also pending Gate E, live AI
Home inventory, measured `ai-services-01` headroom, and the `agents` microVM
required by URS-PA-020 before any real or unauthored data reaches a sandbox.**

1. Decide whether the existing unverified AI Home Hermes Compose/profile files
   are discarded, migrated, or retained only as intent; do not deploy their
   floating image, shared master key, or assumed profile schema as-is.
2. Pin the supported Hermes, NemoClaw, OpenShell, sandbox image, and host tuple.
   Create one synthetic-data `owner` sandbox as its own Compose project on
   `ai-services-01`, with the URS-PA-019 isolation controls in place first.
3. Qualify Hermes first against direct GB10 vLLM and then through the existing
   `ai.home.arpa` LiteLLM path at the required 64K context. Test streaming, tools,
   memory headroom, restart persistence, snapshot/restore, upgrade, rollback,
   denied egress, and credential non-disclosure.
4. Optionally reproduce NVIDIA's direct DGX Spark NemoHermes playbook as a
   compatibility demo; do not promote its persistent state to production or
   add another LiteLLM/vLLM instance.
5. Add one allow-listed private Discord text bot/context using exact numeric
   identities and a profile virtual key, never the LiteLLM administrative key.
6. Add one read-only Home Assistant adapter and one n8n-to-Hermes signed event.
   Keep HA writes and agent-side n8n administration disabled.
7. Add a pending-proposal write tool and prove that n8n/application approval,
   not Hermes shell or OpenShell egress approval, controls execution.

**Gate I:** V-PA-001 and the single-sandbox portions of V-PA-003/004 pass. The
pilot contains no production email, banking, Plaud, private calendar, HA-write,
or broad LAN credentials.

## Phase J — Household isolation, personal data, and proactivity

**Status: Pending Gate I and an accepted canonical personal-store deployment decision**

1. Create separate `owner`, `partner`, and `family` OpenShell sandboxes, persistent
   states, Discord credentials, virtual keys, data roles, managed tool
   credentials, egress policies, quotas, and offline snapshot backups.
2. Implement the versioned personal-event API/store with mandatory
   `principal_scope`, `data_domain`, `visibility`, provenance, idempotency,
   retention, and filtered semantic
   retrieval. Keep Hermes memory and `state.db` non-canonical.
3. Prove all cross-sandbox negative cases, including Discord routing, API keys,
   memory/session search, embeddings/vector retrieval, caches, snapshots, logs,
   notification destinations, managed MCP credentials, and direct API calls.
4. Assign exactly one scheduler owner per proactive job. Start with synthetic
   and public topic monitoring, then calendar and consented Plaud outputs;
   measure notification precision, deduplication, rate limiting, and suppression.
5. Add read-only email and Home Assistant analytics only after the preceding
   controls pass. Add bank transactions last, after a separate privacy and
   threat review plus approval/restore evidence.
6. Add Discord voice through the qualified STT/TTS services only after text is
   stable. Treat the documented pause-during-TTS behavior as the baseline;
   barge-in is not accepted until demonstrated. Parakeet requires a maintained
   adapter and its own Danish/English/ARM64 latency tests.
7. Add cross-source reasoning only after every individual source has an owner,
   provenance contract, replay behavior, and passing access tests.

**Gate J:** V-PA-002 through V-PA-006 pass; each source has a named owner and
retention policy; three sandboxes pass a 24-hour mixed-load soak and offline
restore; a GB10/Hermes outage leaves home control and canonical data intact.

## Change control

Any change to runtime, gateway, model, quantization, template/parser, context,
priority, authentication, logging, exposed HA tools, Codex version, Hermes,
NemoClaw/OpenShell, sandbox/profile routing, personal-data schema, Discord
adapter, Plaud ingestion, meeting schema/storage, or speech/diarization artifact identifies
the affected requirements/risks/tests and reruns them. The last qualified
release remains available until the new 24-hour soak and rollback rehearsal
pass.
