# Hermes personal assistant platform verification

**Research date:** 2026-08-25  
**Status:** Verified against current Nous Research, NVIDIA, Home Assistant, and
n8n primary sources  
**Scope:** Hermes Agent, NemoClaw/OpenShell, local inference, Discord, Home
Assistant, n8n, memory, user isolation, ARM64/GB10, credentials, and approval
boundaries. No setup or configuration was changed.

## Outcome

The handoff is now technically credible, but it should be merged as a staged
**personal-agent application layer**, not as a replacement for the repository's
inference-appliance architecture.

The strongest new evidence is that NVIDIA now treats Hermes as a first-class
NemoClaw agent and lists both Hermes and single-node DGX Spark as tested. The
dedicated `nemohermes` path can configure an OpenShell sandbox, managed Discord,
managed credentials, and local vLLM. NVIDIA nevertheless says Hermes is suitable
for evaluation and documented onboarding while production parity with OpenClaw
is not asserted; its DGX Spark playbook also calls the result a demo rather than
a production-ready solution. [NemoClaw platform support](https://docs.nvidia.com/nemoclaw/user-guide/hermes/reference/platform-support),
[NVIDIA DGX Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemoclaw/README.md)

The recommended merge is therefore:

```text
Discord identities / profile-specific API clients
                         |
                         v
        three separately isolated Hermes runtimes
             owner | partner | family
                         |
                         v
              OpenShell inference.local
                         |
                         v
       existing ai.home.arpa edge + qualified LiteLLM
                         |
                         v
                GB10 pinned vLLM/model

Existing n8n / Home Assistant / durable personal stores remain on their
application hosts and cross narrow authenticated interfaces.
```

To preserve ADR-001 and the current architecture, run the Hermes/OpenShell
sandboxes on an application host and use the GB10 for inference. Running
NemoHermes directly on the GB10 is officially supported for a pilot, but it
creates durable Hermes memory, sessions, messaging state, and lifecycle backups
there. Promoting that topology would explicitly supersede the current
inference-only and durable-state decisions rather than merely extend them.
NemoClaw documents that Hermes state includes `state.db`, memories, platform
sessions, and logs in a persistent sandbox volume. [NemoClaw Hermes state](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/state-and-backups/understand-sandbox-state)

## Claim-by-claim verdict

| Handoff claim | Verdict | Required revision |
| --- | --- | --- |
| Hermes can be the always-on personal-assistant runtime. | **Accurate.** Hermes runs one agent core across CLI, gateway, API, TUI, and desktop; it persists sessions, memory, skills, and scheduled jobs. [Hermes documentation](https://hermes-agent.nousresearch.com/docs/) | Treat it as an application runtime with durable state, not an inference component. Pin a release and qualify it before sensitive use. |
| `NemoClaw / OpenShell -> Hermes` is the supported relationship. | **Accurate now.** OpenShell is the general sandbox/runtime; NemoClaw supplies the opinionated blueprint and Hermes-specific integration. Hermes has a dedicated `nemohermes` CLI and tested DGX Spark path. [NemoClaw ecosystem](https://docs.nvidia.com/nemoclaw/user-guide/hermes/about/ecosystem), [Hermes quickstart](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/quickstart) | Do not describe NemoClaw and OpenShell as interchangeable. Do not claim production maturity; NVIDIA explicitly withholds Hermes/OpenClaw production-parity assurance. |
| Hermes works on GB10/ARM64. | **Accurate.** Hermes lists Linux aarch64 and its Docker image as Tier 1; NemoClaw lists DGX OS on Spark as tested. [Hermes platform support](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/getting-started/platform-support.md), [NemoClaw prerequisites](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/prerequisites) | Use the supported installer or architecture-pinned container. Do not assume arbitrary Python wheels, NIM images, or third-party tool binaries have ARM64 builds. NVIDIA specifically warns that some NIM images lack `linux/arm64` manifests. [NIM on NemoClaw](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/local-inference/set-up-nvidia-nim) |
| Hermes can use local vLLM and the proposed Qwen model. | **Accurate with a new hard gate.** Hermes accepts vLLM and other OpenAI-compatible servers. NemoClaw's validated DGX Spark managed-vLLM default is `nvidia/Qwen3.6-35B-A3B-NVFP4`. [NemoClaw inference providers](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/learn-and-choose/choose-inference-provider), [Hermes providers](https://hermes-agent.nousresearch.com/docs/integrations/providers) | Hermes currently requires at least a 64K context per active session. The repository's earlier 16K/32K qualification baseline is insufficient for Hermes. Re-test memory headroom, tool calling, concurrency, and mixed STT/TTS load at 64K before promotion. [Hermes quickstart](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart/) |
| A second LiteLLM and managed vLLM should be deployed with Hermes on GB10. | **Does not fit.** NemoClaw can route to an already-running OpenAI-compatible endpoint through `inference.local`; it does not require its own model server. [NemoClaw compatible endpoint](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/inference/custom-endpoints/set-up-openai-compatible-endpoint) | Reuse the already-qualified `ai.home.arpa`/LiteLLM/vLLM path. Keep direct vLLM as the qualification baseline and add the existing LiteLLM only after its Chat Completions, streaming, tools, privacy, and latency path passes for Hermes. Do not deploy another gateway. |
| Discord text and proactive messaging are available. | **Accurate.** Hermes supports DMs, server channels, allowlists, sessions, outbound delivery, and cron-driven notifications. NemoClaw lists Discord as tested and can store its token through OpenShell. [Hermes sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions/), [NemoClaw messaging](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/messaging-channels/choose-messaging-channels) | Start with one private text bot and exact numeric user IDs. Add a separate bot credential per private profile unless profile routing has been explicitly tested. |
| Discord can provide continuous voice conversation. | **Mostly accurate, separately gated.** Hermes can join a Discord voice channel, transcribe each user's stream, run the agent, and speak a TTS reply. [Hermes voice mode](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/voice-mode.md) | The documented Discord VC path uses Whisper providers and pauses listening during TTS for echo prevention. Do not claim Discord barge-in/full duplex or direct Parakeet support yet. Hermes can disable automatic STT and pass the audio path to a custom pipeline, so Parakeet requires an adapter/plugin and its own latency test. |
| Hermes integrates with Home Assistant. | **Accurate, but too permissive for the proposed security model.** Hermes has a WebSocket event adapter plus REST tools that can list entities and call HA services. [Hermes Home Assistant integration](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/homeassistant.md) | The native setup uses one Home Assistant long-lived token and allows `ha_call_service` directly; its blocked-domain list is not a general approval boundary. Keep HA authoritative. Initially expose read-only analytics or narrowly authorized scripts/intents through an adapter/MCP/API, and route writes through the existing approval workflow. |
| n8n remains the deterministic workflow engine and can invoke Hermes. | **Accurate.** Hermes exposes authenticated OpenAI-compatible and webhook APIs; Hermes can also consume MCP. n8n supports bearer-auth HTTP requests and a configurable OpenAI base URL. [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), [Hermes webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks/), [n8n OpenAI credential source](https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/credentials/OpenAiApi.credentials.ts) | Prefer n8n -> profile-specific Hermes API or signed webhook for relevant events. Let n8n retain ingestion, retries, normalization, and action approvals. Hermes also has an n8n MCP catalog entry, but agent-side workflow administration should stay disabled until a concrete, least-privilege need exists. [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) |
| Hermes memory should not be the personal source of truth. | **Accurate and important.** Hermes' profile memory is curated agent context; its state database also stores sessions/messages. It is not an event warehouse. [Hermes file roles](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/which-file-does-what.md), [Hermes session storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage) | Keep canonical email, transaction, Plaud, calendar, and event data in the existing application data layer. Keep Hermes memory limited to preferences, relationships, goals, and useful summaries. Do not move PostgreSQL/pgvector/Redis to GB10 merely for Hermes. |
| `owner`, `partner`, and `family` can be isolated Hermes profiles. | **Accurate at the application layer.** Profiles have separate config, keys, memory, sessions, skills, cron, and state. Hermes also supports deterministic `profile_routes` and per-profile API keys. [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/), [multi-profile gateways](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways), [Hermes API authentication](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) | For email and banking, use **three OpenShell sandboxes**, not only three profiles inside one process/container. Each sandbox gets its own Discord credential, network policy, managed MCP providers, and data-store authorization. A single Hermes instance can isolate conversation sessions, but its profile memory is shared by all users routed to that profile. |
| Human approval before every consequential action is already supplied by Hermes/OpenShell. | **Premature.** Hermes approvals primarily guard dangerous shell commands; OpenShell approval controls network egress. Neither is a universal business-action approval engine for `send_email`, HA service calls, calendar changes, or financial operations. [Hermes security](https://hermes-agent.nousresearch.com/docs/user-guide/security/), [OpenShell policies](https://github.com/NVIDIA/OpenShell) | Implement consequential actions as proposal records handled by n8n/application services: validate principal/data-domain scope, show the exact action to the authenticated owner, record approval, then execute with a credential the agent never receives. |

## Security and credential model

The handoff's requirement that authorization live below the model is correct.
OpenShell supplies real enforcement: a restricted non-root child, Landlock
filesystem policy, seccomp, a network namespace, a policy proxy, and managed
inference routing. Network and inference rules are hot-reloadable; filesystem
and process policy are fixed at sandbox creation. [OpenShell sandbox architecture](https://github.com/NVIDIA/OpenShell/blob/main/architecture/sandbox.md),
[OpenShell overview](https://docs.nvidia.com/openshell/about/overview)

NemoClaw-managed inference, messaging, and HTTPS MCP credentials can remain in
the OpenShell gateway and be substituted only at an approved egress boundary.
This is materially safer than a direct Hermes installation, where provider and
integration secrets normally live in the profile `.env`. [NemoClaw credential storage](https://docs.nvidia.com/nemoclaw/user-guide/hermes/security/credential-storage),
[managed MCP design](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/mcp-servers/about-managed-mcp-servers),
[Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/)

This protection is not automatic for every custom skill or native integration.
For example, the documented native Home Assistant path expects `HASS_TOKEN` in
the Hermes profile. Until a NemoClaw-managed provider/placeholder path is proven
for a particular integration, treat that secret as visible to the sandboxed
agent and prefer an authenticated narrow HTTP/MCP facade instead.

Use one sandbox per trust domain:

| Sandbox | Allowed data/API scopes | Never attach |
| --- | --- | --- |
| `owner` | `principal_scope = 'owner'` plus own private and explicitly shared household projections | Partner-private credentials/data |
| `partner` | `principal_scope = 'partner'` plus own private and explicitly shared household projections | Owner-private credentials/data |
| `family` | `data_domain = 'household' AND visibility = 'shared'` | Either person's private email, banking, recordings, or private conversations |

Enforce the scope again in every database/API query. Discord routing, a profile
name, system instructions, and model-selected tool arguments are not sufficient
authorization.

## Integration changes to merge

1. Add Hermes/NemoClaw/OpenShell as an **optional Phase I application layer**,
   with household isolation in Phase J, after the current text-runtime and
   API-gateway gates—not to the Phase C GB10 infrastructure.
2. Preserve the existing Home Assistant, n8n, Meeting Assistant, PostgreSQL,
   queues, and canonical personal data stores on their current application
   hosts. Hermes consumes narrow APIs/events; it does not become their database.
3. Reuse the existing `ai.home.arpa` model path. Qualify Hermes first against direct
   vLLM, then repeat through the existing LiteLLM. The critical additional gate
   is one 64K-context agent session plus realistic concurrent profiles.
4. Pilot one `owner` NemoHermes sandbox with local inference, Discord text, no
   private data, no HA writes, and no cloud fallback. Verify restart, snapshot,
   restore, denied egress, credential non-disclosure, and tool-call correctness.
5. Add read-only Home Assistant and one n8n -> signed-Hermes event. Keep the
   model out of the deterministic trigger/action path.
6. Create separate `partner` and `family` sandboxes only after cross-sandbox
   negative tests prove that memory, sessions, credentials, APIs, and database
   scopes cannot cross.
7. Add Discord voice last. Benchmark Danish/English code-switching, Opus
   dependencies on ARM64, STT/TTS contention, interruption behavior, and whether
   a custom Parakeet adapter is worth maintaining.
8. Keep email read-only first and banking last. Consequential actions require a
   separate authenticated approval service or n8n workflow.

## Promotion gates

- Pin Hermes, NemoClaw, OpenShell, sandbox image, vLLM image, model revision,
  tool parser, and 64K context settings as one tested tuple.
- Pass a 24-hour soak with three profiles/sandboxes, scheduled jobs, Discord
  reconnects, and mixed inference/speech load.
- Prove default-deny LAN egress and verify that each sandbox reaches only its
  assigned API/MCP endpoints. OpenShell warns that controls vary by platform and
  Landlock is best-effort, so inspect the effective live policy rather than
  assuming the blueprint is the result. [NemoClaw security posture](https://docs.nvidia.com/nemoclaw/user-guide/hermes/security/best-practices)
- Restore every sandbox from an offline backup. NemoClaw snapshots are private
  local data and `destroy` deletes the persistent volume. [NemoClaw snapshots](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/state-and-backups/create-and-restore-snapshots)
- Demonstrate adversarial cross-profile tests for Discord identity routing,
  API keys, memory/session search, database principal/domain/visibility scope, MCP credentials, and
  notification destinations.
- Demonstrate that email sending, HA writes, calendar mutation, deletion, and
  financial actions fail closed without a request-bound human approval.

## Final recommendation

Merge the handoff's **Hermes + NemoClaw/OpenShell**, profile model, Discord text,
proactive scheduling, memory/personal-data distinction, and below-model access
control requirements. Revise the deployment so Hermes is an application layer
outside the GB10 inference appliance, reuse the existing LiteLLM/vLLM path, and
represent each person/shared context as a separate OpenShell sandbox. Treat
Discord voice, native Home Assistant writes, Parakeet integration, and all
private email/banking ingestion as gated follow-on work. This preserves the
current architecture while adding a credible path to a local personal
assistant.
