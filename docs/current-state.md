# Current-state analysis

**Date:** 2026-09-26
**Status:** Live deployment observed on both hosts; canary and hardening gaps remain

## 2026-09-27 routing-refactor baseline addendum

The routing refactor rechecked reachable live state read-only before editing
source configuration. On `home-core`, LiteLLM, Caddy, PostgreSQL, n8n, and the
listed MCP/speech services were healthy; available memory was 35 GiB. The
configured LiteLLM version remains 1.99.1. The three locally available client
keys each listed only `automation-moe` at `/v1/models`. The administrative and
Hermes canary credentials were not available for this check.

`home-spark` reported 39 GiB available and 4 GiB swap in use. Docker status
could not be read because the SSH account lacks Docker socket access and
passwordless sudo. Therefore this addendum does not claim a newly observed
Spark process state. See the detailed
[routing baseline](model-routing-refactor-baseline-2026-09-27.md) for exact
commands, unavailable measurements, and rollback state.

The source tree now contains a canonical text model catalog and capability
route file, plus a deterministic LiteLLM renderer. It removes stopped Qwen3.8
aliases from the generated route list, maps the stable `automation` and `home`
aliases, and gives `home` and `automation` explicit request timeouts. It does
not restart LiteLLM, alter virtual keys, or modify n8n workflows. Thus the
live n8n path remains `automation-moe` until a separately staged migration.

Read-only n8n workflow inspection on 2026-09-27 found the HomeCompute
`automation-moe` node in the active Shopping list, Aula calendar sync, and
Notion AI automations workflows. Each node still has a 600,000 ms client
timeout and zero retries. The current Aula Collector & Analyze subworkflow
graph returned a Gemini model node, so the older inventory's blanket claim
that every published subworkflow uses the local model must be rechecked against
connections and production execution paths before serial migration.

## Scope and evidence rules

This inventory separates three evidence levels:

- **Present in source:** a configuration or implementation exists in a local repository.
- **Observed running:** a process, container, or host was inspected directly.
- **Unverified:** documentation describes it, but no live endpoint or exported configuration was available.

The current snapshot was obtained through direct SSH inspection of
`home-core` and `home-spark`, container health, rendered runtime commands, and
isolated n8n model executions. It supersedes the historical source-only
baseline retained later in this document.

## Observed live snapshot

| Host | Capability | Observed state on 2026-09-26 |
| --- | --- | --- |
| `home-core` | Gateway | Caddy 2.11.4, LiteLLM 1.99.1, and PostgreSQL 16.15 are healthy |
| `home-core` | Automations | n8n 2.38.3 plus Aula and Tilbudstrolden MCP services are healthy |
| `home-core` | Speech relays | Home Assistant-restricted Plapre `:10201` and Hviske `:10301` proxies are healthy |
| `home-core` | CPU model standby | Qwen3.6-35B-A3B `UD-Q4_K_M`, 22,134,528,992 bytes, is cached and stopped; a live maintenance cutover test passed |
| `home-core` | Hermes substrate | Commit `dbdef0519d080490dff122c9b92f37919ea6f81b` is deployed. The NixOS-owned agents VM definition and pinned guest tooling are present but safely disabled because off-host backup is not configured. |
| `home-spark` | n8n LLM | `unsloth/Qwen3.6-35B-A3B-NVFP4` revision `739af1e7aac320af1682ed1e0cce369af4c5265d` serves `automation-moe` at 64K and is healthy |
| `home-spark` | Home Assistant LLM | `google/gemma-4-E4B-it-qat-w4a16-ct` revision `6cd26aaa2357fb2bad8c51699a7558a4d1a965bb` serves `home-fast` at 32K and is healthy |
| `home-spark` | Danish STT | `syvai/hviske-v5.3` revision `5d1a09822018702dc51d763e3a867b62d26b3501` plus its Wyoming adapter are healthy |
| `home-spark` | Danish TTS | Plapre Nano v2 and its Wyoming adapter are healthy; the household tempo is `1.20x` |
| `home-spark` | General text | Qwen3.8-27B and its edge relay are stopped while the automation MoE is resident |

`home-core` had about 39 GiB available memory with the CPU standby stopped.
`home-spark` had about 45 GiB available with Qwen3.6 MoE, Gemma 4 E4B,
Hviske, and Plapre resident. These are point observations, not capacity or
mixed-load qualification results.

The dedicated compute link is not carrying the live canary routes. The
hardened `homecompute-compute-ssh-tunnel` service is active and forwards only:

- LiteLLM to `automation-moe` on local port 18005 and `home-fast` on 18006;
- the Plapre relay to local port 18201; and
- the Hviske relay to local port 18301.

## n8n local-model status

Four published workflows now use an OpenAI-compatible model node named
`HomeCompute automation model` with LiteLLM alias `automation-moe`:

- `Shopping list`;
- `Sub-Workflow: Aula Collector & Analyze`;
- `Aula calendar sync`; and
- `Notion AI automations`.

Their previous OpenAI and Gemini model nodes remain on the canvas with no
`ai_languageModel` connections. An isolated Danish response completed in
759 ms, and an isolated Danish tool call completed in 921 ms with the exact
requested arguments. No production workflow was manually executed for the
cutover.

## Known missing or incomplete work

1. **n8n failover does not yet cover the live alias.** The cached home-core
   standby protects the `automation` group, while n8n now calls
   `automation-moe`. A Spark outage or deliberate MoE stop can therefore hang
   or fail n8n until the workflows are rerouted or a fallback is added to the
   same alias.
2. **Dead upstreams can wait too long.** LiteLLM's current request timeout is
   600 seconds. A test against the stopped normal text route demonstrated that
   a TCP-accepting but non-responsive upstream does not fail over promptly.
3. **The stable general aliases are unavailable.** `auto`, `coding`,
   `automation`, `research`, `meeting`, and `assistant` still point at the
   stopped Qwen3.8-27B listener. The separate synthetic-only
   `assistant-canary` alias now routes to the live 64K `automation-moe` backend
   and passed content plus required-tool smokes through LiteLLM. Do not promote
   it or treat the other gateway model listings as availability.
4. **The private compute link is not in service.** The restricted SSH forwards
   are an accepted temporary transport, not the final topology.
5. **Production evidence is narrow.** The n8n model-only and tool-call smokes
   passed, but scheduled production runs, mixed load, retry/idempotency, and
   outage recovery have not been captured after cutover.
6. **Home Assistant acceptance is incomplete.** Plapre playback has been heard
   through Home Assistant, but the repository lacks a recorded Hviske
   transcription acceptance run and a complete Assist pipeline latency test.
7. **Backups remain incomplete.** No scheduled encrypted off-host backup and
   full restore drill covers the current gateway, n8n, credentials, and model
   routing state.
8. **Credential hygiene remains open.** Rotate the LiteLLM administrative key
   after coordinating all dependent clients; a diagnostic briefly placed it
   in a root-visible process argument. The production scripts now read it only
   from the mounted secret file.
9. **Heavy/general model qualification remains open.** Qwen3.8-27B mixed-load
   evidence, Flash-Next, SGLang/DFlash, and the later community-model
   candidates remain evaluation work rather than production routes.

## Historical baseline retained for provenance

The following sections describe the earlier source inventory and design
baseline. Statements that hosts or services were unobserved are historical and
must not be used as the present operational status.

### GB10 project (historical)

`HomeCompute` contains requirements, architecture, design, risks, verification,
ADRs, research, and an implementation plan. The final checkpoint roster now
selects Qwen3.8-27B as the workhorse, Flash-Next as an exclusive cold swap, and
two STT plus two TTS models. The guarded text scaffold pins the first
deployment stage—Qwen3.8-27B with native MTP on vLLM—while routing remains
disabled until live qualification. SGLang/DFlash, the four speech runtimes, and
Flash-Next still need independently pinned runtime images. The repository
contains no secrets, model artifacts, accepted release manifest, or observed
running GB10 service. See `setup-guide.md`.

The current documents already make several sound decisions:

- GB10 is a rebuildable inference appliance, not the home for application state.
- vLLM/native MTP is the first text-runtime baseline; SGLang/DFlash is the
  performance comparison.
- models and runtimes are hidden behind stable capability names;
- Home Assistant remains the authority for tools and physical actions;
- Codex keeps explicit, observable local/cloud selection and fallback;
- prompt, response, audio, transcript, and tool-body logging is off by default;
- production selection depends on real Danish, tool, mixed-load, memory, and
recovery measurements.

### Planned AI services node (historical)

The application host `home-core` has 48 GB RAM
and 1 TB NVMe, with NixOS 26.05 selected as its Git-first provisioning baseline.
This supersedes both the earlier Proxmox/VM design and the later uncommitted
Ubuntu bootstrap. The repository now contains a pinned NixOS flake, focused
host modules, integrated Home Manager and sops-nix, an immutable-input template,
and a restricted Caddy/LiteLLM/PostgreSQL Compose stack whose durable data lives
below `/srv/state`. This is design evidence only: host hardware, firmware, NICs,
storage, installation, backup target, and live migrations have not been
observed or qualified.

The target does not invalidate the existing-host inventory. AI Home and other
live services remain on their current hosts until `home-core` passes host,
container, backup/restore, service-equivalence, and rollback gates documented
in `nixos-control-plane-node-plan.md`.

### Existing AI Home Hub (historical)

The separate `ai_home` repository is a one-commit Docker Compose design for an
always-on Mac Mini. Source contains:

| Capability | Source evidence | Runtime status | Reuse decision |
| --- | --- | --- | --- |
| LiteLLM Proxy | `config/litellm/config.yaml` and Compose service | Unverified | Reuse as the single shared model control plane after qualification |
| Cloud providers | Anthropic, OpenAI, and Gemini entries | Unverified | Preserve only providers and models still used and valid |
| Household agents | Two intended Hermes services using floating images and hand-written profile files | Unverified | Do not deploy as-is; replace with a pinned NemoClaw/OpenShell pilot in its own Compose project on `home-core` |
| Scheduler | Ofelia jobs for briefings/news/reminders | Unverified | Do not duplicate on GB10; compare with n8n/HA schedules before enabling |
| Web UI | LibreChat | Unverified | Out of GB10 scope |
| Development orchestrator | OpenClaw plus tmux/Codeman design | Unverified | Do not couple GB10 deployment to it; Codex remains the accepted harness |
| Persistence | PostgreSQL, MongoDB, Qdrant, Redis volumes | Unverified | Reuse infrastructure only with separate users/databases and proven ownership; do not add copies by default |
| Monitoring | Uptime Kuma | Unverified | Reuse for availability; add metrics storage only when the acceptance signals require it |
| Network | Tailscale-only intent | Unverified | Preserve VPN/LAN boundary; still require service authentication and TLS |

The existing LiteLLM configuration routes concrete cloud aliases and uses one
master key. Hermes and LibreChat are configured to use that administrative
master key directly. OpenClaw instead references `OPENCLAW_MASTER_KEY`, but the
checked LiteLLM configuration does not provision that value as a virtual key;
the credential contract is inconsistent or incomplete. The gateway has no GB10
backend or task-semantic aliases.

The Compose design publishes several ports on all host interfaces and uses
floating tags including `main-stable` and `latest`. Documentation saying access
is Tailscale-only does not itself create a host firewall rule. Ofelia also
mounts the Docker socket; a read-only socket mount still grants powerful Docker
API access and is not equivalent to a read-only filesystem. These are prototype
choices, not a production baseline.

## Hermes handoff reconciliation

Current primary-source verification is recorded in
`research/hermes-personal-assistant-verification.md`.
Hermes now fits the strategy as a personal-agent application layer: NVIDIA
lists Hermes and DGX Spark/ARM64 as tested through NemoClaw/OpenShell, supports
an existing OpenAI-compatible local endpoint, and provides managed Discord and
credentials. This validates a real pilot path, but not the current AI Home
Compose assumptions or production placement on GB10.

The existing `ai_home` Hermes files are design intent, not a qualified install:

- they use a floating `nousresearch/hermes-agent:latest` image and explicitly
  say the image/profile schema must be verified;
- both consumers use the LiteLLM administrative master key;
- profiles are separated only by containers/volumes and are not proven
  OpenShell security domains with default-deny egress;
- native Home Assistant tokens/tools, scheduled Docker commands, Discord
  identity mapping, state backup/restore, and consequential-action approval
  have not passed live tests;
- no `family` trust domain exists.

Hermes requires at least a 64K context for each active session. Any earlier
16K/32K model qualification therefore remains useful for other roles but does
not qualify the assistant workload. Discord voice exists, but the documented
path pauses listening during TTS and uses Whisper-compatible STT; the selected
Hviske route and barge-in require separate adapters and evidence.

## Existing Meeting Assistant

The separate `meeting-assistant` repository already implements most of the
meeting domain that the handoff proposes to build:

- live microphone and system-audio capture;
- on-device Whisper as the default transcription backend;
- an explicit OpenAI-compatible account backend with no silent local-to-cloud
  transcription fallback;
- independent bindings for live answer, rolling summary, and final summary;
- bounded rolling summaries while preserving the complete raw transcript for
  the final summary;
- crash-durable stable transcript and segment-summary writes;
- a meeting library containing `transcript.md`, `summaries.md`, `summary.md`,
  and `meta.json`;
- editable titles, participant estimates, projects, and summary regeneration.

It currently uses Chat Completions for LLM jobs and can call an OpenAI-style
audio-transcription endpoint. It does **not** currently provide:

- Plaud audio import;
- preservation of imported original audio as a managed meeting artifact;
- speaker diarization or speaker-attributed transcript segments;
- an immutable raw-STT artifact distinct from a cleaned transcript;
- the complete structured meeting schema proposed in the handoff;
- a verified `ai.home.arpa` gateway account and task aliases.

The app has extensive uncommitted work. It is therefore an integration target,
not a repository to modify from this design project without a separately
reviewed task.

## Home Assistant, Node-RED, n8n, MCP, and search

The GB10 documents say Home Assistant, Node-RED, n8n, Notion state, and MCP
servers remain on existing hosts. The Aula MCP source repository is present.
No Home Assistant configuration export, Node-RED flow export, n8n workflow
export, search-provider configuration, or live HAOS inventory was available in
this workspace. Their detailed topology, versions, authentication, schedules,
and active integrations remain unverified.

Until those exports or live access are available, implementation shall not:

- migrate or recreate existing workflows;
- assume n8n and Node-RED run on HAOS;
- select a web-search provider;
- introduce another scheduler;
- change the Aula MCP boundary;
- claim an existing Home Assistant voice/STT/TTS integration.

## Technical debt not to carry forward

1. Floating container tags and unpinned model/provider identifiers.
2. A single shared LiteLLM master key for every downstream consumer.
3. Direct host-port publication without an authenticated TLS edge.
4. Concrete vendor/model names embedded in consumer configuration.
5. Potentially overlapping Ofelia, n8n, and Home Assistant schedules without an
   ownership inventory.
6. Databases and Redis being treated as generically reusable without separate
   credentials, databases, retention, backup, and failure ownership.
7. A new meeting worker/database that duplicates Meeting Assistant's domain and
   durable meeting library.
8. A second LiteLLM instance on GB10 when the existing control plane can be
   qualified and extended.
9. Treating published Compose ports as Tailscale-only without verified host
   firewall/bind-address controls.
10. Giving a scheduler the Docker socket when a narrower trigger mechanism can
    meet the job requirement.
11. Inconsistent LiteLLM consumer credentials and direct use of its
    administrative master key.

## Revised reuse boundary

```text
Mac Mini / existing control plane
  Caddy TLS edge -> existing LiteLLM -> cloud providers or GB10 vLLM
  existing Uptime Kuma / PostgreSQL / Redis only where qualified

home-core, separate Compose projects on the same kernel (ADR-017)
  automation project -> n8n + workflow state, migrated off the HAOS add-on
  agent project -> three OpenShell sandboxes -> Hermes owner / partner / family
  profile-specific Discord/API/model/data/tool credentials
  encrypted sandbox snapshots and canonical personal event API/store

GB10 inference appliance
  vLLM + text model
  STT runtime
  TTS runtime
  diarization runtime when Phase H requires it
  inference metrics only

Existing consumers
  Home Assistant / Node-RED / n8n / Hermes / Meeting Assistant / Codex
  retain workflow, authorization, and durable domain state
```

The direct Codex-to-vLLM path remains a test baseline. Production consumers use
the shared authenticated control plane after it passes the same Responses,
streaming, tool, privacy, and latency corpus.

## Required evidence before implementation

1. Export or inspect active Home Assistant, Node-RED, and n8n topology without
   including credentials or private payloads.
2. Verify whether the `ai_home` stack is deployed, which services are active,
   and which are still desired.
3. Inventory current schedulers and search providers by owner and workflow.
4. Decide the durable storage/backup location for imported Plaud audio.
5. Define a supported Plaud ingestion path; do not depend on an undocumented
   private API or scraper.
6. Reconcile Meeting Assistant's current uncommitted work before planning its
   gateway/Plaud changes.
7. Verify `home-core` meets the OpenShell/NemoClaw prerequisites, and
   measure gateway, database, and n8n usage before setting the per-project
   resource limits ADR-017 requires. The host is decided; its headroom is not.
8. Inventory the live Hermes/Telegram/Discord deployment, if any, before
   replacing or importing state from the `ai_home` prototype.
9. Decide the canonical personal event-store host and API authorization model;
   do not infer that the existing LibreChat PostgreSQL/Qdrant/Redis stores are
   reusable or appropriately isolated.
