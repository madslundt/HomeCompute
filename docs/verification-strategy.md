# Verification strategy

**Version:** 0.1  
**Date:** 2026-08-25  
**Status:** Approved test design; execution awaits the target GB10 and integrations

## 1. Verification policy

Verification uses inspection (I), analysis (A), automated test (T), and
demonstration (D). Each result records:

- test and requirement IDs;
- date, operator, repository commit, and fixture version;
- complete qualified platform/runtime/model tuple;
- exact command/configuration and raw-result artifact location;
- expected threshold, observed result, pass/fail/not-run status;
- deviations, defects, and approved risk acceptance.

**Not Run is not Pass.** A Must requirement cannot be accepted from design
intent, a vendor claim, or a different hardware/model measurement.

## 2. Test levels and order

```text
static inspection
  -> configuration/unit tests
  -> direct runtime protocol PoC
  -> edge/gateway contract PoC
  -> consumer integration PoCs
  -> role benchmarks
  -> mixed-load/fault/security tests
  -> 24-hour soak
  -> clean rebuild and rollback
  -> Stage 1 acceptance
  -> pinned Codex cross-provider canary
  -> Stage 2 end-to-end acceptance
```

A failed architecture gate stops dependent tests. For example, gateway testing
does not compensate for a failed direct Responses tool loop, and Stage 2 does
not start from source inspection alone or an unqualified Codex version.

## 3. Controlled test environments

| Environment | Purpose | Data policy |
| --- | --- | --- |
| Static/CI | Validate schemas, manifests, scripts, Compose, docs, and synthetic contracts | Synthetic only; no secrets |
| GB10 test listener | Direct runtime and destructive fault/compatibility tests | Synthetic repositories/prompts/audio |
| Integrated LAN test | Caddy/gateway and real clients with test credentials | Synthetic/redacted fixtures |
| Meeting integration test | Meeting Assistant gateway account, Plaud import, artifacts, and diarization | Consented/redacted audio; synthetic canaries |
| Hermes sandbox test | Pinned application host/OpenShell/NemoClaw/Hermes tuple, Discord test channels, 64K local inference and scoped APIs | Synthetic identities/data first; no production private sources |
| Acceptance | Final qualified tuple and production-like load | Controlled fixtures; Aula content remains synthetic/redacted |
| Production smoke | Non-destructive health and small canaries after switch | No content logging; no dangerous actions |

Real Aula content is not required to verify n8n summarization or privacy. The
acceptance fixture contains a unique synthetic canary and is sent only through
the existing n8n consumer path.

## 4. Phase C architecture gates

### V-CODEX-001 — Direct Responses compatibility

Against the pinned vLLM test listener, installed Codex 0.145.0 shall:

1. complete streamed text with exactly one terminal completion event;
2. execute a shell/read/write/apply-patch tool loop with valid call IDs/JSON;
3. complete multi-turn and parallel function calls;
4. use a real namespaced MCP tool and consume its result;
5. handle reasoning events expected by the model/client;
6. cancel and disconnect without leaked backend work or replay;
7. handle malformed tool output explicitly;
8. complete a long task that triggers the observed compaction behavior.

Any hang, missing terminal event, lost tool result, invalid patch schema, or
prompt/tool body in logs fails the gate.

### V-GW-001 — Edge equivalence

Repeat V-CODEX-001 through the combined Caddy and existing LiteLLM path at
`https://ai.home`, then compare event ordering, status/errors, cancellation,
actual model, TTFT and total latency with direct runtime. Gateway overhead must
meet URS-PERF-006. Authentication, virtual-key revocation, per-key alias
allow-lists, unknown consumer/model/path denial, request-ID propagation, and
metadata-only logs are part of this gate. Native backend success does not
qualify bridged/transformed behavior automatically.

### V-HA-001 — Home Assistant tool PoC

Using a restricted test area/entity set, Home Assistant Assist shall execute:

- deterministic native intent without LLM where applicable;
- one information query via `home`;
- one multi-entity allowed action;
- one denial for an unexposed entity/tool;
- one ambiguous/dangerous fixture requiring the configured safe behavior.

The model proposes tool calls; Home Assistant validates and executes. Direct
model access to device infrastructure fails the gate.

## 5. Phase D role benchmarks

### V-GEN-001 — General workload

Use versioned Danish/English summarization, extraction, classification,
research synthesis, Notion-state comparison, normalized web-result/change
detection, and JSON-schema fixtures. Score
schema validity, instruction/factual rubric, unsupported assertions, latency,
throughput, context behavior, memory, and sensitive-log canary. Pass thresholds
are URS-GEN-003.

### V-CODE-001 — Coding workload

Use clean, resettable representative repositories/tasks for .NET, Python, Vue
3, and React/TypeScript. Every candidate receives the same bounded spec,
allowed tools, retry rule, tests, context budget, and sampling policy. Record
build/tests, cloud fallback, frontier review findings/severity, duration, tokens,
TTFT/throughput, and whether cloud reimplementation was needed. Pass threshold
is URS-CODE-002; tool/protocol requirements are URS-CODE-001.

### V-HA-002 — Home Assistant model benchmark

Run normal, ambiguous, negative, and dangerous-action fixtures at 10, 25, 50,
and 100+ exposed entities. Score exact tool, exact entity set, arguments, call
count, hallucinated executed entities, safe denial/confirmation, TTFT, and final
speech response. Pass thresholds are URS-HA-003/004 and URS-PERF-001.

### V-STT-001 — Danish/English ASR

Use speaker-balanced clean, near-field, far-field/noisy, number/time, room/entity
name, and Danish/English code-switch audio. Benchmark Danish Parakeet, Whisper
large-v3-turbo, and Whisper large-v3; include Plaud's transcript as a reference
where available. Record WER, command semantic accuracy,
punctuation, first partial/final latency, RTF, peak resources, and concurrent
behavior. Pass thresholds are URS-STT-002 and URS-PERF-003.

### V-TTS-001 — Danish voice

Use a blinded/randomized listening set covering Danish names, numbers, times,
rooms, compounds, English names, short confirmations, and long sentences.
Record pronunciation pass, mean naturalness, first-audio latency, RTF,
stability, voice consistency, resources, and concurrency. Pass thresholds are
URS-TTS-002 and URS-PERF-003/004.

### Benchmark controls

- Warm-up and cold-start runs are labeled separately.
- Report p50/p95/p99 where sample size supports them; never infer p99 from a tiny set.
- Same-model runtime comparisons hold revision, template/tokenizer, precision or equivalent quantization, prompt/output distribution, sampling, context, concurrency, OS/driver/power, and background load constant.
- For models with native MTP, compare MTP on and off. Record speculative tokens
  proposed and accepted, output tokens/s, TTFT, end-to-end task time, peak
  memory, retries, and any long-context, vision, tool, or concurrency regressions.
- Treat Q4, Q8, FP8, and NVFP4 as different artifact tuples. Bit count alone
  does not establish either quality or speed across quantization schemes.
- Capture runtime startup output or telemetry proving the selected attention,
  MoE/GEMM, activation, KV-cache, and speculative-decoding paths. A model-card
  precision label is not evidence that the expected GB10 kernel executed.
- For every ambiguous role, publish the tested alternatives, their measured
  advantages/disadvantages, the winner rationale, and the threshold that
  would have selected a different candidate.
- Raw results are retained under a non-sensitive result artifact location; summaries go in `docs/benchmarks/`.
- Leaderboards/vendor examples can select candidates but cannot satisfy acceptance.

## 6. Meeting and Plaud verification

### V-MTG-001 — Import and artifact provenance

Import the same consented Plaud export twice. The first import shall persist and
checksum original audio before inference; the second shall be detected as the
same source or create an explicitly linked revision rather than silently
duplicating work. Force failure after each pipeline stage and verify that the
original plus every completed artifact remain readable and the job can resume.

Verify that raw STT and speaker-attributed transcript files never change when
cleanup or summary regeneration runs. Each derived artifact records input
checksums, schema version, inference release, and creation time.

### V-MTG-002 — Language, diarization, and cleanup

Use Danish-only, English-only, and code-switched meetings with two, three, and
overlapping speakers, quiet/noisy rooms, names, acronyms, and technical terms.
Measure WER, speaker error/attribution error, language preservation,
punctuation, and latency. Compare raw and cleaned text for unsupported factual
changes. Unknown speakers remain generic labels; identity is never inferred
without explicit input.

### V-MTG-003 — Structured output and privacy

Validate the versioned summary schema for summary, decisions, action items,
topics, participants/speaker labels, and languages. Kill local STT,
diarization, and text services separately while network egress monitoring is
active. The meeting remains pending/failed, original audio remains available,
and zero audio/transcript bytes reach a cloud provider.

## 7. Capacity and concurrency verification

### V-PERF-001 — Mixed workload

Run at least these scenarios after single-workload baselines:

| Scenario | Active work |
| --- | --- |
| M1 | One `home` request |
| M2 | P1 Codex generation + P0 `home` |
| M3 | P2 n8n automation request + P0 `home` |
| M4 | P1 Codex + P2 n8n + P0 voice turn |
| M5 | P4 meeting transcription/diarization + repeated P0/P1 arrivals |
| M6 | Three 64K-capable Hermes sessions with scheduled analysis + P0 voice + P1 Codex arrival |

Measure queue time, TTFT, end-to-end voice time, throughput, priority order,
starvation, memory/KV pressure, temperature/power, errors, and recovery. M4 must
meet URS-PERF-001/002/005.

### V-MEM-001 — Memory envelope

Measure idle, model-load, qualified context/concurrency, M4 peak, and forced
allocation failure. Validate the documented budget and 10% headroom. Confirm a
failed allocation produces an explicit error/readiness change and bounded
recovery rather than a crash loop.

## 8. Security and privacy verification

| Test | Procedure and pass condition |
| --- | --- |
| V-SEC-001 Network exposure | Scan from client LAN and an untrusted segment. Only approved edge paths/listener are reachable; runtime, metrics, admin, and container interfaces are not. |
| V-SEC-002 TLS | Verify trusted chain/name, supported protocol/ciphers, client failure on invalid trust, and documented renewal/rotation. |
| V-SEC-003 Authentication | Test each Codex, public/private n8n, Home Assistant, and Meeting Assistant credential; revocation, cross-consumer/alias access, spoofed header, missing/invalid token, and log redaction. The LiteLLM master key is rejected as a consumer configuration. |
| V-SEC-004 Allow-list | Unknown model/path/method/content type and oversize text/audio are rejected without internal details. |
| V-SEC-005 HA authority | Unexposed/admin/direct-device operations cannot be executed; allowed operations are auditable by Home Assistant. |
| V-SEC-006 Log canary | Send unique strings in prompt, response instruction, tool argument/result, transcript, audio metadata, authorization, meeting, and synthetic Aula/n8n content. Search edge, router, runtime, container, journal, metrics and error outputs; zero forbidden occurrence passes. |
| V-SEC-009 Private-route egress | Force local failure for `home`, `meeting`, and private `automation`. Packet/provider audit shows no cloud request and the client receives an explicit local-unavailable result. |
| V-SEC-007 Artifact provenance | Verify image digest, model/tokenizer revision, template/parser hash, license record, secret scan, least-privilege container and mount/capability policy. |
| V-SEC-008 Telemetry cardinality | Send adversarial request IDs/header/entity values; none become metric label names/values outside the allow-list. |

## 9. Reliability, recovery, and operations verification

| Test | Fault/action | Pass condition |
| --- | --- | --- |
| V-OPS-001 Reboot | Clean host reboot | All required services autostart; readiness returns in documented order/time |
| V-OPS-002 Process kill | Kill edge, text, STT, and TTS separately | Explicit unavailable state; bounded restart or alert within 5 minutes; no hidden model swap |
| V-OPS-003 Dependency failure | Break model mount, private DNS/network, certificate, and telemetry separately | Correct readiness/error class; inference not blocked solely by telemetry failure |
| V-OPS-004 Stream fault | Disconnect client/upstream before and after first event | No replay after streaming; backend work cancels/bounds; request remains diagnosable |
| V-OPS-005 Disk pressure | Fill a dedicated test volume through 80/90% thresholds | Alert/change freeze/cleanup procedure works; no broad destructive cleanup |
| V-OPS-006 Upgrade/rollback | Deploy a candidate, fail post-switch test, restore prior manifest | Alias/DNS unchanged; prior qualified release restored without client edits |
| V-OPS-007 Clean rebuild | Rebuild supported host from source-controlled inputs and separately supplied secrets/artifacts | Equivalent manifest/configuration and acceptance smoke pass |
| V-OPS-008 Soak | 24-hour representative mixed workload plus scheduled faults | No unhandled failure, sensitive log canary, unrecovered OOM, or lost readiness |
| V-OPS-009 Control-plane outage | Stop Caddy and LiteLLM separately | Explicit failure and bounded recovery; no direct runtime bypass; Codex cloud planning/review remains available through its separately configured provider |

## 10. Stage 2 verification

### V-CODEX-VER-001 — Versioned cross-provider capability gate

Record the exact installed Codex binary/hash/version. For the first candidate,
0.145.0 source must apply the custom role's `model_provider`, and the actual
desktop/CLI path must prove all of the following: correct GB10 child provider
metadata, a unique initial assignment visible to the child, a unique follow-up
visible to the same child, and correct returned results. Provider metadata with
an empty/encrypted-only task fails. Repeat this gate for every client upgrade;
0.149.1 is expected to fail the provider-override prerequisite. Until a version
passes, all remaining automatic cross-provider tests are **Blocked**, not waived.

### V-CODEX-E2E-001 — Automatic workflow

After V-CODEX-VER-001:

1. Developer starts with normal `codex` interaction.
2. Trace proves cloud planner use and a bounded approved specification.
3. Trace proves GB10 `coding` implementation and local model execution.
4. A passing task proceeds to cloud diff review.
5. A forced first failure retries locally with compiler/test evidence.
6. A forced second failure selects cloud implementation automatically.
7. A forced GB10 outage keeps cloud planning/review usable and records fallback/block reason.
8. No manual provider/model switching occurs.
9. Metrics contain required task outcomes but no source/prompt content.

One representative end-to-end feature must pass build/tests and review; the
full corpus must meet URS-CODE-002.

## 11. Personal assistant verification

### V-PA-001 — Runtime, inference, policy and recovery pilot

On the pinned application-host/OpenShell/NemoClaw/Hermes tuple, create one `owner`
sandbox with synthetic data. Prove local model/tool operation first against
direct GB10 vLLM and then the existing Caddy/LiteLLM path at 64K context. Record
TTFT, throughput, unified-memory/KV pressure, effective model, streaming/tool
behavior, restart persistence, upgrade/rollback, snapshot/offline restore, and
24-hour reconnect/schedule behavior. Deny unlisted filesystem paths, processes,
LAN destinations, inference endpoints and credentials. A GB10, edge, Hermes,
or application-host outage shall not interrupt deterministic home control or
damage canonical data.

### V-PA-002 — Cross-sandbox and personal-data isolation

Create `owner`, `partner`, and `family` sandboxes with unique state, Discord bot,
virtual key, event-API/database role, managed providers, notification target,
and backup. For each principal, attempt direct and prompt-mediated access to
the other principal's memory, sessions, APIs, records, embeddings/vector
search, cache, snapshots, logs, MCP credentials, tools, and notification
destinations. `owner` sees only `owner/shared`, `partner` only `partner/shared`, and `family`
only `shared`; every other attempt is denied below the model and audited
without content leakage.

### V-PA-003 — Discord identity and delivery

Test allow-listed/unknown users, DMs, private/shared server channels, spoofed
names, forwarded text, cross-bot tokens, reconnects, duplicate events, edits,
rate limits, quiet/suppressed notifications, and credential revocation. The
numeric authenticated identity/channel selects exactly one sandbox before
dispatch. Proactive messages reach only their assigned destination and include
a bounded source/reason without private content in infrastructure logs.

### V-PA-004 — Consequential-action approval

For email send, calendar mutation, HA write, deletion and a synthetic financial
action, verify Hermes can create a proposal but cannot execute it. The external
approval path binds authenticated owner, exact immutable arguments, expiry and
a one-time token. Test denial, expiry, argument modification, wrong-owner,
replay, executor outage, and direct-tool bypass. Hermes/OpenShell shell or
egress approval alone shall never authorize the business action.

### V-PA-005 — Discord voice

After text acceptance, test Danish, English and code-switched Discord voice,
participant stream separation, Opus dependencies on ARM64, STT/TTS latency,
pause-during-TTS behavior, attempted interruption, reconnect and mixed load.
Treat half duplex as the documented baseline. Parakeet is accepted only through
a versioned adapter that passes the same corpus; speaker recognition never
changes the authorized profile.

### V-PA-006 — Canonical events and proactive behavior

Replay duplicate/out-of-order source events and verify stable source identity,
provenance, `owner_scope`, processing state, retention and idempotency. Delete
or restore Hermes memory and prove canonical history is unchanged. Inventory
one scheduler owner per job, then test meaningful-change suppression,
deduplication, rate limits, quiet hours, failure/retry and notification audit.
Private email/Home analytics start read-only; banking remains Not Run until all
preceding privacy, isolation, approval and restore gates pass.

## 12. Requirements traceability

| Requirement(s) | Primary verification |
| --- | --- |
| URS-AI-001, URS-AI-002, URS-AI-008 | V-SEC-001, V-OPS-007, architecture/config inspection |
| URS-AI-003, URS-AI-004, URS-AI-005 | Alias contract and V-OPS-006 replacement test |
| URS-AI-006, URS-AI-007 | V-CODEX-001, V-GW-001, existing-client integration |
| URS-AI-009, URS-AI-010 | V-GW-001, V-SEC-003, V-SEC-008 |
| URS-AI-011 | Current-state inspection, V-GW-001, V-OPS-009 |
| URS-AI-012, URS-AI-013 | V-GW-001, V-PERF-001, V-SEC-003, V-SEC-009 |
| URS-GEN-001, URS-GEN-002, URS-GEN-003, URS-GEN-004, URS-GEN-005 | V-GEN-001, workflow/architecture/state inspection, V-SEC-006 |
| URS-CODE-001, URS-CODE-002, URS-CODE-003 | V-CODE-001, selection-record inspection |
| URS-HA-001, URS-HA-002, URS-HA-003, URS-HA-004, URS-HA-005 | V-HA-001, V-HA-002, V-SEC-005 |
| URS-STT-001, URS-STT-002, URS-STT-003 | V-STT-001 and Home Assistant/Meeting Assistant integration |
| URS-TTS-001, URS-TTS-002 | V-TTS-001 and Home Assistant integration |
| URS-PA-001, URS-PA-002, URS-PA-003 | V-PA-001, V-PERF-001 M6, V-MEM-001, V-SEC-001/003/009 |
| URS-PA-004, URS-PA-005, URS-PA-006, URS-PA-008 | V-PA-002, V-PA-003, V-SEC-003/006 |
| URS-PA-007, URS-PA-012, URS-PA-013 | V-PA-006, phase-gate and scheduler-ownership inspection |
| URS-PA-009 | V-PA-003 |
| URS-PA-010 | V-PA-005 |
| URS-PA-011 | V-PA-004 |
| URS-PA-014 | V-PA-001, V-SEC-009 |
| URS-MTG-001, URS-MTG-002, URS-MTG-003, URS-MTG-004, URS-MTG-005, URS-MTG-006, URS-MTG-007, URS-MTG-008 | V-MTG-001, V-MTG-002, V-MTG-003, architecture/source inspection |
| URS-PERF-001, URS-PERF-002, URS-PERF-005 | V-HA-002, V-PERF-001 M4 |
| URS-PERF-003, URS-PERF-004 | V-STT-001, V-TTS-001 |
| URS-PERF-006 | V-GW-001 direct/proxied A/B |
| URS-PERF-007, URS-PERF-008 | V-MEM-001 |
| URS-SEC-001 | V-SEC-001 |
| URS-SEC-002 | V-SEC-002 |
| URS-SEC-003, URS-SEC-004 | V-SEC-003, V-SEC-004 |
| URS-SEC-005 | V-SEC-005, V-HA-001, V-HA-002 |
| URS-SEC-006, URS-SEC-007 | V-SEC-006 and retention/access inspection |
| URS-SEC-008 | V-SEC-007, V-OPS-006, V-OPS-007 |
| URS-OPS-001, URS-OPS-002, URS-OPS-003, URS-OPS-004 | V-OPS-001, V-OPS-002, V-OPS-003 |
| URS-OPS-005, URS-OPS-006 | Metrics/config inspection, V-SEC-008, V-OPS-008 |
| URS-OPS-007 | V-OPS-005, V-OPS-007 and storage inspection |
| URS-OPS-008, URS-OPS-009 | V-OPS-006, V-OPS-007 |
| URS-OPS-010 | V-OPS-008 |
| URS-CODEX-001, URS-CODEX-002 | V-CODEX-001 and workflow demonstration |
| URS-CODEX-003, URS-CODEX-004 | V-CODEX-VER-001 and configuration inspection |
| URS-CODEX-005, URS-CODEX-006, URS-CODEX-007 | V-CODEX-E2E-001, V-SEC-006 |
| URS-CODEX-008 | Current-mode documentation/UX inspection |
| URS-MNT-001, URS-MNT-002, URS-MNT-003 | Documentation and artifact audit |
| URS-MNT-004 | V-OPS-006 |
| URS-MNT-005 | CI/config validation and deployment test |

## 13. Acceptance records

Stage 1 and Stage 2 receive separate signed/dated summaries listing every Must
requirement and evidence link. Stage 1 can pass while Stage 2 remains blocked by
V-CODEX-VER-001. The summary clearly distinguishes design completion, PoC pass,
benchmark selection, deployment qualification, and operational acceptance.
Hermes Phase I and household Phase J receive separate acceptance records so a
single synthetic sandbox cannot be misrepresented as private multi-user or
financial-data acceptance.
