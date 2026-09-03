# Current-state analysis

**Date:** 2026-08-25  
**Status:** Repository evidence complete; live-host verification blocked by unavailable host resolution

## Scope and evidence rules

This inventory separates three evidence levels:

- **Present in source:** a configuration or implementation exists in a local repository.
- **Observed running:** a process, container, or host was inspected directly.
- **Unverified:** documentation describes it, but no live endpoint or exported configuration was available.

The MacBook Docker context contained no running `ai_home` containers. The SSH
aliases `mac-mini` and `haos` did not resolve from the inspected machine, so the
Mac Mini and HAOS runtime state is **unverified**, not absent.

## GB10 project

`gb10setup` contains requirements, architecture, design, risks, verification,
ADRs, research, and an implementation plan. It now also contains editable D2
platform/installation diagrams and a guarded Phase C scaffold: an idempotent
setup script, immutable-input template, and private vLLM Compose definition.
The scaffold contains placeholders rather than a qualified image digest/model
commit, and it deliberately refuses to deploy until they and the security
inputs are resolved. The repository still contains no secrets, model artifacts,
accepted release manifest, or observed running GB10 service. See
`setup-guide.md`.

The current documents already make several sound decisions:

- GB10 is a rebuildable inference appliance, not the home for application state.
- vLLM is the first text-runtime PoC, using an NVIDIA-qualified GB10 artifact.
- models and runtimes are hidden behind stable capability names;
- Home Assistant remains the authority for tools and physical actions;
- Codex keeps explicit, observable local/cloud selection and fallback;
- prompt, response, audio, transcript, and tool-body logging is off by default;
- production selection depends on real Danish, tool, mixed-load, memory, and
recovery measurements.

## Planned AI services node

The project owner has assigned `ai-services-01` to a GMKtec K15 with 48 GB RAM
and 1 TB NVMe and selected NixOS 26.05 as its Git-first provisioning baseline.
This supersedes both the earlier Proxmox/VM design and the later uncommitted
Ubuntu bootstrap. The repository now contains a pinned NixOS flake, focused
host modules, integrated Home Manager and sops-nix, an immutable-input template,
and a restricted Caddy/LiteLLM/PostgreSQL Compose stack whose durable data lives
below `/srv/state`. This is design evidence only: the K15, firmware, NICs,
storage, installation, backup target, and live migrations have not been
observed or qualified.

The target does not invalidate the existing-host inventory. AI Home and other
live services remain on their current hosts until `ai-services-01` passes host,
container, backup/restore, service-equivalence, and rollback gates documented
in `nixos-control-plane-node-plan.md`.

## Existing AI Home Hub

The separate `ai_home` repository is a one-commit Docker Compose design for an
always-on Mac Mini. Source contains:

| Capability | Source evidence | Runtime status | Reuse decision |
| --- | --- | --- | --- |
| LiteLLM Proxy | `config/litellm/config.yaml` and Compose service | Unverified | Reuse as the single shared model control plane after qualification |
| Cloud providers | Anthropic, OpenAI, and Gemini entries | Unverified | Preserve only providers and models still used and valid |
| Household agents | Two intended Hermes services using floating images and hand-written profile files | Unverified | Do not deploy as-is; replace with a pinned NemoClaw/OpenShell pilot on an application host |
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
path pauses listening during TTS and uses Whisper-compatible STT; Parakeet and
barge-in require separate adapters and evidence.

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

Always-on application host
  three OpenShell sandboxes -> Hermes owner / partner / family
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
7. Identify the always-on application host and verify its OpenShell/NemoClaw
   prerequisites, storage encryption, firewall, and offline backup target.
8. Inventory the live Hermes/Telegram/Discord deployment, if any, before
   replacing or importing state from the `ai_home` prototype.
9. Decide the canonical personal event-store host and API authorization model;
   do not infer that the existing LibreChat PostgreSQL/Qdrant/Redis stores are
   reusable or appropriately isolated.
