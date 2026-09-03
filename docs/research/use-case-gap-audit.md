# Use-case coverage and gap audit

**Verified:** 2026-08-26  
**Scope:** the repository against the owner's stated Home Assistant, Node-RED,
n8n/Notion/Aula, Codex, Hermes, speech, email/calendar/message, and financial
use cases. Pricing and discounts are deliberately out of scope.  
**Sources:** repository documents plus first-party NVIDIA, ASUS, Home Assistant,
n8n, Notion, Nous Research, Qwen, and vLLM material. Hardware/model claims are
not inferred from retailer listings or community benchmarks.

## Executive verdict

The repository covers the **platform shape and safety boundaries very well**.
In particular, it correctly keeps HAOS, Node-RED, n8n, MCP servers, source
repositories, and durable personal state off the rebuildable GB10; keeps Home
Assistant authoritative for physical actions; defines local-only private
routes; separates owner/partner/family trust domains; and makes cloud planning and
review explicit rather than pretending a local coding model is automatically a
frontier-model replacement. See the [requirements](../requirements.md),
[architecture](../architecture.md), [implementation plan](../implementation-plan.md),
and [risk analysis](../risk-analysis.md).

The important gaps are mostly **application contracts**, not another model or
runtime:

1. The exact `AI automations` Notion state machine is not documented or tested.
2. n8n's own execution database, credentials, risky nodes, SSRF surface, and
   HAOS backup/restore are outside the current privacy gates.
3. Web pages, email, Aula, messages, MCP results, and Notion content are not yet
   modeled as hostile prompt-injection inputs.
4. Email/calendar/banking are sensibly gated, but concrete source contracts,
   retention, deletion, and failure behavior are not defined; general
   "messages" are not explicitly covered.
5. Voice inference is covered, but microphone/satellite/wake-word topology and
   degraded local voice when GB10 is unavailable are not.
6. Multimodal document/OCR and camera-image use cases are absent even though the
   first NVIDIA Qwen3.6 Spark NIM supports image input.
7. The purchase assumptions hard-code a 1 TB SSD although both products can be
   sold in other capacities. At-rest host protection, UPS/graceful shutdown,
   and the always-on energy policy also need explicit decisions.

These gaps do **not** justify changing the Phase C model choice. They justify an
application inventory and additional Phase G/J contracts before private data is
connected.

## Hardware purchase conclusion

For these use cases, DGX Spark and ASUS Ascent GX10 are the same compute class:
both use GB10, a 20-core Arm CPU, 128 GB coherent unified memory, the NVIDIA AI
stack/DGX OS path, 10 GbE, and ConnectX-7. NVIDIA documents 273 GB/s memory
bandwidth and up to 1 PFLOP at FP4 **with sparsity**; those are hardware-format
figures, not proof of coding, Danish, or agent quality. ASUS likewise states the
same GB10, 128 GB, and up-to-200B-model class. [NVIDIA hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html),
[ASUS GX10 product page](https://www.asus.com/us/networking-iot-servers/desktop-ai-supercomputer/ultra-small-ai-supercomputers/asus-ascent-gx10/).

Therefore, choose on effective price, local warranty/support, exact SSD, return
terms, acoustics/thermals, and update/recovery ownership—not an expectation that
one chassis will run a better model. ASUS states that GX10 uses a validated
ASUS-adapted DGX OS image, while NVIDIA owns the first-party DGX Spark recovery
and release path. [ASUS DGX OS FAQ](https://www.asus.com/us/support/faq/1056142/),
[NVIDIA DGX OS](https://docs.nvidia.com/dgx/dgx-spark/dgx-os.html),
[NVIDIA system recovery](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html).

Purchase checks:

- Confirm the exact SSD SKU. NVIDIA lists 1 TB and 4 TB Spark variants; ASUS
  lists 1/2/4 TB GX10 variants. The repository currently hard-codes 1 TB in the
  [requirements](../requirements.md#reliability-and-operations-requirements),
  [storage ADR](../adr/005-storage-strategy.md), and
  [design](../design-specification.md#12-gb10-storage-design). If a discounted
  unit has 2 or 4 TB, make capacity an inventory variable and regenerate the
  quota plan. [NVIDIA hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html),
  [ASUS specifications](https://www.asus.com/bt/networking-iot-servers/desktop-ai-supercomputer/ultra-small-ai-supercomputers/asus-ascent-gx10/techspec/).
- Prefer 4 TB when the premium is modest. The repository's active+rollback
  policy is workable on 1 TB, but separate NVFP4/FP8/GGUF weights, container
  images, compile caches, speech models, and temporary downloads make 1 TB an
  operating constraint. It is still correct to keep canonical personal data
  off the GB10.
- Verify the shipped OEM OS/firmware and the exact NGC/NIM profile for every
  service. GB10 is Arm64 and NVIDIA explicitly says to confirm Spark-compatible
  NIM images/profiles; an x86-only image is not portable by assumption.
  [NVIDIA NGC guidance](https://docs.nvidia.com/dgx/dgx-spark/ngc.html).
- Confirm at-rest protection rather than assuming that "self-encrypting" means
  enabled. NVIDIA lists self-encrypting NVMe and documents Secure Boot and TCG
  storage controls; ASUS's public specification does not make the same storage
  security guarantee for every SKU. [NVIDIA hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html),
  [DGX Spark UEFI security](https://docs.nvidia.com/dgx/dgx-spark-uefi/security-tab.html).

## Coverage matrix

| Stated scenario | Coverage | Repository evidence | Remaining work |
| --- | --- | --- | --- |
| Keep Home Assistant, Node-RED, and n8n on HAOS | **Covered by design** | URS-AI-001/PA-001 and [ADR-001](../adr/001-gb10-inference-only.md) keep applications and durable state off GB10. | Export the live HAOS/n8n/Node-RED topology and prove backup/restore. The current host is explicitly unverified in [current state](../current-state.md#home-assistant-node-red-n8n-mcp-and-search). |
| Run the Notion `AI automations` database on daily/weekly schedules | **Partial** | `automation`/`research`, n8n-owned retry/idempotency, explicit search, meaningful-change suppression, and Notion as source of truth are present in URS-GEN-001–005 and Phase G. | The real property schema, due-date calculation, time zone/DST, run locking, partial-write recovery, comments, success evaluation, conflict detection, and exact next-run behavior are absent. |
| Research the web and update Notion content | **Partial** | URS-GEN-004 keeps search in the calling workflow; URS-GEN-005 normalizes/deduplicates; V-GEN-001 tests research synthesis and state comparison. | Define the search provider, citations/source ledger, hostile-content boundary, retrieval limits, stale-source behavior, and safe targeted Notion mutation. |
| Add a short Notion change comment and complete fulfilled tasks | **Not concretely covered** | Generic structured-output and success-quality fixtures exist. | Comment capability, duplicate-comment prevention, a completion evidence schema, and an independent/deterministic verifier are needed. Do not let the same untrusted generation both perform and conclusively approve a consequential state transition. |
| Daily Aula summary through the existing Aula MCP | **Covered at boundary; runtime unverified** | Aula remains inside n8n, with no new route/state, in [requirements](../requirements.md#purpose-and-scope), [dependency graph](../dependency-graph.md#consumer-dependencies), and R-020. | Export the workflow/MCP contract, apply prompt-injection fixtures, test failure/staleness/duplicates, and verify n8n execution retention. |
| Local TTS for Home Assistant | **Strong design, not deployed** | URS-TTS-001/002, P0 voice priority, Wyoming, Danish evaluation, mixed-load and fallback tests are defined. | Inventory satellites/microphones/wake word/media players; decide whether loss of GB10 means text-only or a lightweight HAOS Piper fallback. |
| Local STT and conversational home control | **Covered beyond stated ask** | Deterministic Assist first, exact exposed-entity tests, Danish/English STT, and HA-only tool execution are well covered. | Do not use the official OpenAI HA integration as a local-compatible proxy: Home Assistant says it supports only the official OpenAI endpoint. Use a qualified local conversation integration or supported adapter while retaining the built-in Assist API boundary. [Home Assistant OpenAI integration](https://www.home-assistant.io/integrations/openai_conversation/). |
| Replace GPT-5.6 Luna for Codex implementation while keeping cloud planning/review | **Strong design, not yet proven** | URS-CODE-001/002 and all Codex Stage 2 requirements, V-CODEX-001/E2E, ADR-006/007/010, and the coding corpus directly match this workflow. | No official model card proves Luna equivalence. Pass the user's own repo tasks, builds/tests, cloud diff review, tool protocol, cancellation, compaction, and exact Codex-version canary before promotion. vLLM now documents Codex integration, but client/runtime/model-parser compatibility is still a tuple. [vLLM Codex integration](https://docs.vllm.ai/en/stable/serving/integrations/codex/). |
| Use local models in n8n | **Covered at platform level** | Per-consumer keys, private/public automation separation, aliases, explicit fallback, schema benchmarks, and mixed-load priority are present. | Add per-workflow data classification, input/output caps, execution-data retention, untrusted-input policy, and evaluation sets from prior successful runs. |
| Hermes household profiles | **Strong design, placement blocked** | Separate OpenShell sandboxes, profile credentials, principal/data-domain enforcement, external approval, snapshots, 64K context, and cross-profile negative tests are in URS-PA-002–019 and Phases I/J. | Select a separate personal-agent host and trust domain, deploy one synthetic `owner` pilot, then add `partner`. Hermes profiles alone are not OS/filesystem isolation; keep the separate-sandbox rule. Hermes supports local OpenAI-compatible endpoints and stores memory locally. [Hermes FAQ](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/faq.md), [NemoClaw overview](https://docs.nvidia.com/nemoclaw/user-guide/hermes/home). |
| Calendar, email, bank transactions | **Covered as staged intent** | URS-PA-006/007/011/013, Phase J, V-PA-004/006, and R-030–036 correctly make calendar/Plaud/email gradual, email read-only first, and banking last. | Name each provider/API, owner scope, raw retention, deletion, provenance, reconciliation, backup, and outage behavior. Financial ingestion remains blocked until isolation and restore evidence exists. |
| Messages | **Gap** | Messaging is mostly treated as a Hermes interface (Discord), not as email/SMS/iMessage/WhatsApp-style source data. | Define each channel separately. Add sender/thread identity, edit/delete/tombstone behavior, attachments, retention, private/shared scope, and whether the assistant may only summarize/draft or also send. |
| Family proactive summaries | **Covered structurally** | Phase J includes scheduler ownership, public topics, calendar, Plaud, email/home analytics, deduplication, quiet hours, and notification isolation. | Define a shared-family projection that deliberately excludes spouse-private data; measure whether alerts are useful, not merely deliverable. |
| Work meetings/Plaud | **Covered beyond stated ask** | Phase H, URS-MTG, and V-MTG define source preservation, local-only speech, diarization, provenance, and restore. | Only proceed if this reflects an actual work need and consent/retention rules are accepted. Do not duplicate the existing Meeting Assistant. |

## P0 gaps to close before private production data

### 1. Specify the Notion automation as a state machine

The generic n8n tests should be supplemented with a fixture matching the real
database. A safe run should look like:

```text
eligible active page
  -> acquire page/run lock
  -> snapshot page ID + last_edited_time + normalized input hash
  -> retrieve complete recursive content
  -> retrieve/search with bounded read-only capabilities
  -> generate structured proposal + sources + completion evidence
  -> validate schema/facts/success evidence
  -> re-read edit time/hash; stop as needs_review on human conflict
  -> targeted content update
  -> page-level change comment
  -> property update (last successful run/status/next run)
  -> release lock and record terminal result
```

Add properties or an external run record for at least: `run_id`, `run_state`,
`last_attempt_at`, `last_success_at`, `next_run_at`, `input_hash`,
`model_release`, `source_set_hash`, `result_summary`, `completion_evidence`,
`error_class`, and `retry_count`. Keep `last AI run` semantics explicit: an
attempt that failed before Notion was updated is not a successful run.

Notion-specific acceptance cases:

- Recursively page through all children. Notion returns at most 100 children per
  response and nested blocks require further calls; a shallow read silently
  omits content. [Working with page content](https://developers.notion.com/guides/data-apis/working-with-page-content),
  [retrieve block children](https://developers.notion.com/reference/get-block-children).
- Handle an average limit of three requests/second, HTTP 429, and
  `Retry-After`; retry-safe append/comment operations need a run ID so recovery
  cannot create duplicate output. [Notion request limits](https://developers.notion.com/reference/request-limits).
- Enable only the required connection capabilities. Page/block comments require
  insert-comment capability, which is off by default. [Create comment](https://developers.notion.com/reference/create-a-comment).
- Prefer targeted Markdown `update_content` search-and-replace when possible,
  and reject ambiguous matches. If full replacement is used, first reject
  `truncated=true` or `unknown_block_ids`, preserve child pages/databases, and
  retain a recoverable prior snapshot. [Update page content as Markdown](https://developers.notion.com/reference/update-page-markdown).
- Re-read `last_edited_time` and the content hash before writing. Notion exposes
  edit timestamps, but the documented update request has no client-supplied
  compare-and-swap precondition; conflict detection therefore belongs in the
  workflow. This is an inference from the [page object](https://developers.notion.com/reference/page)
  and [update page request](https://developers.notion.com/reference/patch-page).
- Test the pinned `Notion-Version`. The current API uses `2026-03-11`; custom
  HTTP/MCP code must not silently carry older `database`, `archived`, or append
  request shapes. [Notion version changes](https://developers.notion.com/reference/changes-by-version).

For free-form success criteria, have the model return a typed verdict with
quoted evidence/source IDs and an uncertainty/review outcome. Use deterministic
checks where possible (date reached, field present, source count, exact value),
and reserve automatic `complete` for a passing verifier. Otherwise set
`needs_review`; a plausible self-judgment is not success evidence.

### 2. Protect the n8n application layer, not only model logs

The repository forbids content in edge/gateway/runtime logs, but n8n execution
history can itself contain full Notion pages, Aula messages, email, financial
records, tool results, and credentials. n8n recommends saving only necessary
execution data and pruning finished runs; current defaults prune by age after
14 days or count above 10,000, but the actual HAOS configuration must be
inspected. [n8n execution-data management](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/configure-n8n/scaling/manage-execution-data.md).

Required controls:

- Set and back up the same explicit `N8N_ENCRYPTION_KEY` as the database; without
  it, restored encrypted credentials are unusable. [n8n encryption key](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/configure-n8n/basic-configuration/configuration-examples/set-a-custom-encryption-key.md).
- Disable saving successful/manual data where operationally possible; set a
  deliberate prune period/count. Enterprise deployments can redact execution
  payloads while preserving metadata; Community users should not assume that
  feature is available. [n8n redaction](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/configure-n8n/security/redact-execution-data.md).
- Run `n8n audit` and review unprotected webhooks, custom/community nodes,
  filesystem/command/code nodes, unused credentials, and missing security
  settings. [n8n security audit](https://docs.n8n.io/hosting/securing/security-audit/).
- Enable SSRF protection on a supported n8n release and narrowly allow-list the
  internal `ai.home.arpa`, Home Assistant, and approved MCP hosts. n8n's default SSRF
  block ranges include private and loopback networks when enabled, so blindly
  enabling it will break legitimate local integrations; hostname allow-lists
  take precedence. Network/VLAN/firewall rules remain the primary control.
  [n8n SSRF protection](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md).
- If workflows execute Code nodes, use the supported external task-runner mode
  and do not expose the HAOS host or Docker socket. [n8n task-runner hardening](https://github.com/n8n-io/n8n-docs/blob/main/docs/deploy/host-n8n/configure-n8n/security/harden-task-runners.md).
- Export versioned workflow definitions and test a HAOS restore containing n8n,
  Node-RED, the Aula MCP, database, encryption key, and schedules. A backup that
  restores Home Assistant but loses the workflow encryption key is a failed
  platform restore.

### 3. Treat retrieved text as hostile data

Web pages, Notion content, Aula messages, email, calendar descriptions,
attachments, bank descriptions, and MCP results can contain instructions aimed
at the model. Local inference prevents disclosure to a cloud model; it does not
make prompt injection harmless.

Add a shared untrusted-input contract:

- retrieved text is quoted/tagged as data and cannot modify system/tool policy;
- research workers receive read-only search/fetch and bounded output, not broad
  LAN, filesystem, email-send, calendar-write, HA-write, or financial tools;
- tool arguments are validated against deterministic allow-lists and schemas;
- fetched URLs, redirects, file sizes/types, and private address ranges are
  constrained before model access;
- citations refer to retrieved source IDs, and unsupported claims fail the run;
- write actions produce a pending proposal whose exact arguments are approved
  outside the model, as already designed for Hermes in URS-PA-011 and V-PA-004.

Apply the same corpus to the Notion research workflow and Aula MCP before adding
email, messages, or bank data.

## P1 platform and operations gaps

1. **End-to-end data map.** Mark every copy of content: Notion cloud, web-search
   provider, n8n execution DB, queues, LiteLLM, GB10 memory/temp/coredumps,
   application logs, Hermes memory/snapshots, notification channels, and
   backups. "Local model" does not make Notion or search local.
2. **At-rest and host hardening.** Add requirements for Secure Boot state,
   activated disk encryption/SED policy, swap/hibernation and coredump policy,
   OS accounts/SSH, unused Wi-Fi/Bluetooth, physical placement, recovery media,
   and secret rotation. NVIDIA documents Secure Boot as enabled by default on
   Spark, but acceptance should inspect effective state. [DGX Spark UEFI](https://docs.nvidia.com/dgx/dgx-spark/uefi-settings.html).
3. **Power-loss policy.** Add a suitable UPS, graceful shutdown, restart after
   power return, and interrupted model-download/update tests. DGX Spark is
   specified with a 240 W supply and 140 W SoC TDP; size the UPS from measured
   full-system draw, not TDP alone. [NVIDIA hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html).
4. **Always-on versus scheduled inference.** Local HA voice and always-reachable
   Hermes imply warm GB10 service. If energy/noise argues for scheduled shutdown,
   document cold-start UX and retain deterministic HA control plus a lightweight
   local voice fallback. NVIDIA reports 38 W idle and 233.2 W maximum for DGX
   Spark under its EU test disclosure; measure the purchased chassis and chosen
   power mode rather than transferring those numbers to GX10. [NVIDIA compliance](https://docs.nvidia.com/dgx/dgx-spark/compliance.html).
5. **Actual client inventory.** The platform cannot be considered scenario-
   complete until live versions, endpoints, credentials, schedules, search
   provider, HA exposed entities, voice satellites, n8n database/retention, and
   backups are captured. The repository already identifies this blocker but no
   dedicated acceptance artifact exists.
6. **Source freshness and degraded summaries.** A daily briefing should say
   which sources succeeded, failed, or are stale. Partial Aula/calendar/email
   data must not be presented as a complete day.
7. **Model change regression sets.** Save sanitized expected results from real
   prior automations and representative repositories. Re-run them whenever the
   model, quantization, parser, prompt template, runtime, Codex, or gateway
   changes. The existing immutable-tuple policy is strong; the missing part is
   user-specific fixtures.

## Ranked local-model ideas

The ranking favors usefulness, privacy benefit, and reuse of the current
architecture. Every item keeps deterministic ingestion/scheduling in n8n/HA or
an application service and uses the model for interpretation, synthesis, or a
proposal.

| Rank | Idea | Why it is useful | Safe first version |
| ---: | --- | --- | --- |
| 1 | **Make `AI automations` a versioned local evaluation harness** | Improves the workflow already delivering value and provides the acceptance corpus for every future model. | Replay sanitized historical pages against local/cloud models; compare citations, diffs, schema validity, success verdict, latency, and cost. Do not write to production during evaluation. |
| 2 | **Private cited search over family and work documents** | Finds appliance manuals, insurance/house records, warranties, receipts, school PDFs, policies, project docs, and meeting notes without shipping the corpus to a model provider. | Separate `owner`, `partner`, `shared-family`, and work collections; return excerpts and source links; no autonomous edits. Qwen publishes multilingual embedding/reranker models in 0.6B/4B/8B sizes with 32K context, but Danish retrieval still needs a local corpus. [Qwen3 Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B). |
| 3 | **Private and shared daily briefings** | Combines Aula, each person's calendar, weather/transport, selected mail, tasks, and HA state into actionable morning/evening summaries. | Generate Owner-private, Partner-private, and deliberately shared-family projections. List failed/stale sources, deduplicate, use quiet hours, and never infer that private inbox items are shareable. |
| 4 | **Local coding worker plus CI/review triage** | Extends the planned Codex implementation path to test failure explanation, repetitive refactors, migration drafts, issue reproduction, log analysis, and release-note summaries while keeping source local. | Local implementation in a clean worktree; builds/tests required; cloud Sol review remains final for risky work. Add acceptance by language/repository rather than one global pass rate. |
| 5 | **Local voice as a household interface** | Makes HA control, timers, briefings, shopping capture, and family queries usable away from a screen. | Keep closed Speech-to-Phrase/deterministic Assist for control; use Whisper/open STT plus local LLM for open questions; use Wyoming for external STT/TTS. Home Assistant officially defines this pipeline and external-service boundary. [Fully local Assist](https://www.home-assistant.io/voice_control/voice_remote_local_assistant), [Wyoming](https://www.home-assistant.io/integrations/wyoming/). |
| 6 | **Email/message triage and drafting** | Local classification can rank urgency, extract dates/actions, group threads, and draft replies without sending raw content to an LLM provider. | Read-only ingestion, per-person scopes, citations back to message IDs, draft only, and an explicit send approval. Start with one low-risk mailbox/folder, not the whole family inbox. |
| 7 | **Meeting-to-workflow assistant** | Converts work/family meeting audio into searchable transcripts, decisions, actions, and follow-ups; the repository already has a mature Plaud/Meeting Assistant design. | Consented import; immutable audio/raw transcript; local STT/diarization; human review before creating tasks or sending notes. |
| 8 | **Home energy and maintenance analyst** | Explains unusual consumption, heating inefficiency, battery/solar behavior, device dropouts, humidity patterns, and appliance anomalies. | Aggregate/downsample deterministic HA history; model explains and recommends; HA automations remain authoritative. No automatic thermostat/lock/alarm changes. |
| 9 | **Local document/photo understanding** | Useful for letters, invoices, receipts, warranty labels, error screens, school PDFs, parcel labels, and visual home-maintenance questions. | A private upload inbox with deletion/retention and source provenance; extract structured data and ask for confirmation before filing. NVIDIA's Spark Qwen3.6 VLM NIM supports text, image input, and function calling, so the first text candidate can be evaluated before adding a second VLM. [NVIDIA Qwen3.6 Spark VLM API](https://docs.nvidia.com/nim/vision-language-models/1.7.0/examples/qwen3.6/api-dgx-spark.html). |
| 10 | **Camera-event summaries** | Can reduce false notifications and describe a door/driveway event locally. | Opt-in cameras only, event snapshots rather than continuous surveillance, short retention, no face identity inference, and no autonomous security action. Treat direct image URLs as SSRF input; base64/local upload is safer. |
| 11 | **Read-only financial categorization and review** | Locally categorizes merchants, finds subscriptions/duplicates, explains budget changes, and prepares questions. | Import read-only exported transactions; deterministic account reconciliation; model suggestions never modify the ledger or initiate payments. Keep last in rollout, as Phase J already requires. |

## Voice-specific additions

The current voice strategy is sound. Home Assistant's Assist pipeline is wake
word → STT → intent → TTS; Wyoming can connect external Speech-to-Phrase,
Whisper, Piper, and wake-word services. The built-in Assist API exposes only
Assist-equivalent intents and exposed entities and cannot perform administrative
tasks. [Assist pipeline](https://developers.home-assistant.io/docs/voice/pipelines/),
[Home Assistant LLM API](https://developers.home-assistant.io/docs/core/llm/).

Add these test scenarios:

- satellite/microphone placement, television/kitchen noise, echo, Danish names,
  child/adult voices, and simultaneous household speech;
- wake-word false activation and missed activation, including TV/music;
- first command after model cold start and after an n8n/Codex generation;
- STT success but LLM unavailable; TTS unavailable after action success;
- short deterministic spoken confirmation versus a long generated answer;
- local-only verification with network egress monitoring;
- a small Piper fallback if audible home control must survive GB10 outage.

NVIDIA Speech TTS NIM now lists a DGX Spark profile for Chatterbox Multilingual,
including Danish, but the published Spark profile uses roughly 44.6 GiB at
batch eight. It is a quality challenger, not an obvious always-resident service
beside a large LLM. Benchmark it against Piper for Danish quality, time to first
audio, and mixed-load memory before inclusion. [NVIDIA TTS support matrix](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/tts.html).

## Hermes-specific additions

The repository's separate OpenShell sandbox per trust domain is stronger than
running multiple Hermes profiles in one OS identity and should remain. NVIDIA's
NemoClaw stack provides managed inference, network policy, integrations, and
lifecycle operations around supported agents; Hermes itself supports local
OpenAI-compatible endpoints, persistent memory/skills, messaging, and cron.
[NemoClaw ecosystem](https://docs.nvidia.com/nemoclaw/user-guide/hermes/about/ecosystem),
[Hermes on DGX Spark](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/hermes-agent/README.md).

Do not enable Hermes cron for jobs already owned by n8n or HA. Prefer n8n to
ingest/normalize/retry and send a bounded signed event to the appropriate
sandbox. Keep canonical email, transaction, calendar, meeting, and message data
outside Hermes memory. Back up each sandbox before upgrades; NVIDIA says
snapshots are private local data and preserve declared state, including Hermes
cron/script state. [NemoClaw snapshots](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/state-and-backups/create-and-restore-snapshots).

One useful current integration to evaluate is Home Assistant's supported MCP
Server boundary: registered LLM APIs, including built-in Assist, can be exposed
over Streamable HTTP. This may reduce custom adapter code, but it must still be
fronted by the per-profile, least-privilege authentication and denial tests in
Phase I; it is not permission to give Hermes an HA administrator token.
[Home Assistant LLM/MCP API](https://developers.home-assistant.io/docs/core/llm/),
[MCP Server integration](https://www.home-assistant.io/integrations/mcp_server).

## Optional tools that should not change the core architecture

- **NVIDIA NIM:** useful where a model has a validated Spark profile and the
  packaged health/observability/security lifecycle is worth it. Developer use
  needs authenticated NGC access; production support/lifecycle can require AI
  Enterprise. Check each support matrix. [NVIDIA NGC guidance](https://docs.nvidia.com/dgx/dgx-spark/ngc.html),
  [NIM architecture](https://docs.nvidia.com/nim/large-language-models/latest/reference/architecture.html).
- **NVIDIA AI Workbench:** useful as an optional remote, Git-versioned,
  containerized development location for local experiments and sensitive-code
  prototypes. It is not needed to operate the headless Compose inference
  appliance and should not become a second production orchestrator.
  [AI Workbench overview](https://docs.nvidia.com/ai-workbench/user-guide/latest/overview/introduction.html),
  [remote locations](https://docs.nvidia.com/ai-workbench/user-guide/latest/locations/locations.html).
- **n8n self-hosted AI starter kit:** useful as reference only. n8n calls it a
  proof-of-concept that is not fully optimized for production; it should not
  replace this repository's more deliberate Caddy/LiteLLM/vLLM, HAOS, and
  durable-state separation. [n8n starter kit](https://github.com/n8n-io/self-hosted-ai-starter-kit).

## Recommended documentation changes

No Phase C implementation change is warranted before hardware access. The next
documentation pass should add, in order:

1. A sanitized live-inventory artifact for HAOS, Node-RED, n8n, Aula MCP,
   Notion schema, search provider, schedules, execution retention, and backups.
2. A Notion `AI automations` contract and verification suite covering the full
   state machine and partial-write/conflict cases above.
3. An n8n application-security and data-retention requirement section, with
   audit, encryption-key backup, execution pruning/redaction, SSRF, risky-node,
   and restore tests.
4. A shared untrusted-input/prompt-injection requirement and corpus for web,
   Notion, Aula, email, messages, attachments, and MCP results.
5. A hardware inventory variable for SSD capacity/OEM and acceptance checks for
   Secure Boot, at-rest protection, UPS, recovery media, and energy policy.
6. A voice endpoint/satellite/degraded-mode inventory and explicit decision on
   local Piper fallback during GB10 outage.
7. A multimodal `document` or internal vision capability only after a real
   consumer contract exists; do not add a public alias merely because the
   selected model supports images.
8. A per-source Phase J template covering owner scope, credentials, raw
   retention, edit/delete/tombstone events, provenance, replay, notification,
   and allowed read/draft/write behavior. Apply it to calendar, email, each
   message channel, and banking separately.

## Final assessment

The repository has covered the major scenarios and made the right foundational
choices. It is not missing a second generic gateway, another agent framework,
or a larger model shortlist. It is missing the detailed contracts around the
systems that already contain the family's real data. Closing those contracts—
especially Notion/n8n atomicity, retention, prompt injection, restore, and
per-person source authorization—will produce more value and reduce more risk
than installing additional models on day one.
