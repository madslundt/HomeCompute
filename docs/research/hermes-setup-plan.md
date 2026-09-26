# Hermes household-agent setup plan

**Research date:** 2026-09-26
**Status:** Plan based on current Nous Research, NVIDIA NemoClaw, and OpenShell
primary sources; no service or host configuration was changed
**Scope:** One private personal agent for each adult and each of two children,
plus an optional shared-family agent, using the existing `home-core` and
`home-spark` architecture.

## Recommendation

Use **NemoClaw-managed Hermes in a dedicated Ubuntu 24.04 KVM guest on
`home-core`**, with one OpenShell sandbox per person and an optional fifth
sandbox for shared-family context. Keep inference on `home-spark` behind the
existing `https://ai.home.arpa` LiteLLM edge.

Do not deploy the upstream Hermes Docker image directly, do not run NemoClaw on
the NixOS host, and do not model household members as profiles inside one
Hermes process. Those paths are technically possible, but they give up either
the repository's required OpenShell boundary or NVIDIA's validated generic
Linux path.

The rollout should be a pilot, not an immediate production migration.
NVIDIA's current platform matrix calls Hermes a tested first-class NemoClaw
agent but says it is suitable for evaluation and documented onboarding;
production parity with OpenClaw is not asserted. The same matrix validates
Ubuntu 24.04 at host level while listing NixOS among distributions that may
work but are not validated. [NemoClaw platform support](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/reference/platform-support),
[NemoClaw prerequisites](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/prerequisites)

## Current release snapshot

The versions must be treated as one tested tuple rather than independently
upgraded components:

| Component | Current upstream state on 2026-09-26 | Pilot rule |
| --- | --- | --- |
| Hermes Agent | `0.21.5`, tag `v2026.9.24`; the matching direct Docker tag is `nousresearch/hermes-agent:v2026.9.24` | Do not substitute this into a NemoClaw sandbox. It is newer than NemoClaw's managed Hermes pin. [Hermes 0.21.5 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24) |
| NemoClaw | `v0.0.129`, tag commit `26922313bba96184e65c3663b351683ebae9504d` | Candidate pilot release. Pin the full commit, not `latest`, `main`, or the mutable `lkg` tag. `v0.0.129` also supports installing Hermes with deferred onboarding. [NemoClaw v0.0.129 release note](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/release-notes/2026/9/23), [tag](https://github.com/NVIDIA/NemoClaw/releases/tag/v0.0.129) |
| NemoClaw-managed Hermes | Hermes `0.21.3`, tag `v2026.9.14` | Keep this managed pin. NemoClaw patches and qualifies this exact runtime. [NemoClaw update contract](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/operate-sandboxes/update-sandboxes) |
| NemoClaw-managed OpenShell | OpenShell `0.0.116` | Keep this managed pin even though upstream OpenShell is newer. Do not run `openshell self-update`. [NemoClaw update contract](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/operate-sandboxes/update-sandboxes) |
| OpenShell upstream | `v0.1.1`, released 2026-09-26 | Not part of the candidate tuple until a future reviewed NemoClaw release selects it. [OpenShell v0.1.1 release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.1) |

Before implementation, re-resolve the candidate tag to a full commit and
re-read its release note. Record the NemoClaw commit, managed image digest,
Hermes version, OpenShell version, guest image digest/checksum, and effective
policy together in the acceptance record.

## Why an Ubuntu guest changes the existing design slightly

ADR-017 correctly requires a separate `agents` kernel before Hermes handles
real personal data or any input the household did not author. Its current
implementation wording assumes a `microvm.nix` guest and separate Compose
projects. Stock NemoClaw instead owns the OpenShell gateway and sandbox
containers, and NVIDIA has not validated NixOS installer assumptions.

The smallest architecture correction is:

1. Keep the accepted separate-kernel requirement.
2. Implement it as an Ubuntu 24.04 KVM guest managed by the NixOS host.
3. Let NemoClaw/OpenShell own containers inside that guest rather than wrapping
   them in repository-authored Compose projects.
4. Keep each household member in a separate OpenShell sandbox with a distinct
   state, provider credential, messaging credential, and network policy.

This needs a narrow ADR-017/URS-PA-019 amendment before implementation. It does
not change ADR-001: `home-spark` remains inference-only.

Direct Hermes is a viable fallback if an Ubuntu guest is rejected. Its official
Docker image supports amd64 and arm64, persistent `/opt/data`, a non-root
runtime, and s6 supervision, while native installs can create systemd services.
However, choosing it would remove the repository's intended OpenShell network,
filesystem, process, and credential boundary and would therefore require a
security ADR, not merely a different install command. [Hermes platform support](https://hermes-agent.nousresearch.com/docs/getting-started/platform-support),
[Hermes Docker guide](https://hermes-agent.nousresearch.com/docs/user-guide/docker)

## Household identity topology

Use a default Hermes profile inside each sandbox. Do not use a shared
multiplexed Hermes gateway or `profile_routes` for private agents.

| Sandbox name | Intended users | Initial tools/data | Messaging policy |
| --- | --- | --- | --- |
| `agent-owner` | One adult | Synthetic data, inference, memory; later narrowly scoped owner APIs | Dedicated bot token; exact numeric owner user ID only |
| `agent-partner` | The other adult | Synthetic data, inference, memory; later narrowly scoped partner APIs | Dedicated bot token; exact numeric partner user ID only |
| `agent-child1` | First child | Synthetic data and a deliberately small, read-only tool set | Dedicated bot token; exact numeric child user ID; no role or channel-wide grant |
| `agent-child2` | Second child | Synthetic data and a deliberately small, read-only tool set | Dedicated bot token; exact numeric child user ID; no role or channel-wide grant |
| `agent-family` | All four household members | Shared household data only; never any person's private projection | Messaging deferred until a managed four-identity ingress is qualified; never omit `DISCORD_USER_ID` to simulate sharing |

The neutral infrastructure names avoid putting children's names into container,
log, snapshot, and monitoring metadata. OpenShell `0.0.116` restricts sandbox
names to 1-19 lowercase alphanumeric/hyphen characters, with no consecutive
hyphens; all names above comply. [NemoClaw update contract](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/operate-sandboxes/update-sandboxes)

### Profiles are not sandboxes

Hermes profiles are real state partitions: each has its own configuration,
keys, memory, sessions, skills, cron jobs, and state database. They are useful
for separate assistants inside one trust domain. [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)

They are not the household authorization boundary. NemoClaw's trusted-computing
base explicitly says the OpenShell-managed topology does not create gateway or
agent user-ID isolation, and mutable agent configuration/state remains
untrusted. [NemoClaw trusted computing base](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/security/trusted-computing-base)

For this household, use five sandboxes rather than five profiles in one
sandbox. A host administrator can still inspect guest disks, OpenShell state,
and backups. The design isolates normal agent compromise and accidental
cross-person access; it does not promise privacy from the infrastructure
administrator.

### Children require policy, not a different prompt

The official projects do not document a child-specific or parental-control
mode. A child-safe deployment must therefore be enforced below the model:

- start without shell, browser, web search, arbitrary MCP, host mounts,
  proactive cron, email, calendar, banking, Home Assistant writes, or deletion
  credentials;
- expose only explicitly reviewed, read-only APIs and enable one capability at
  a time after a denial test;
- keep the OpenShell network policy deny-by-default and use a distinct
  LiteLLM key with child-specific quotas;
- select the messaging platform only after the household makes its age,
  account, and supervision decision; if Discord is used, authorize the exact
  numeric user ID rather than a mutable role;
- make any parent access to a child's bot an explicit household decision.
  Hermes warns that an authorized messaging user has the agent's full tool
  capability, so additional authorized identities are not read-only oversight;
- test the selected local model's content behavior separately. OpenShell can
  restrict actions and data, but it does not make model output age-appropriate.

Hermes' direct gateway denies users by default when no access policy is
configured. NemoClaw's managed Discord setup has a different sharp edge:
without `DISCORD_USER_ID`, any member of the configured server can message the
bot. Always set the user ID for a private agent. [Hermes Discord authorization](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord),
[NemoClaw Discord setup](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/messaging-channels/set-up-discord)

The current NemoClaw Discord guide documents a single `DISCORD_USER_ID`, not a
multi-ID managed allowlist. Keep family-agent messaging disabled until either a
later managed contract supports all four exact identities or a narrow external
ingress authenticates all four and calls the family sandbox. Direct Hermes can
accept multiple `DISCORD_ALLOWED_USERS`, but bypassing NemoClaw's managed
channel contract solely for that feature needs its own credential and rebuild
review. When a family ingress is accepted, keep Hermes'
`group_sessions_per_user` behavior enabled so users in a common channel receive
separate conversational sessions, but treat the family sandbox's memory,
skills, state, logs, and backups as shared. Per-user sessions inside a shared
profile are not private personal storage.

## Blocking prerequisites in the current repository

The first implementation task is not installing Hermes. It is closing these
preconditions:

1. **Make `assistant` real.** `docs/current-state.md` records that `assistant`
   still targets the stopped Qwen3.8 listener. NemoClaw onboarding will probe
   and reject an unhealthy route. Point `assistant` at a qualified 64K-capable
   model, with streaming and tool calls enabled, before onboarding.
2. **Create the guest boundary.** Real or unauthored data remains prohibited
   until the `agents` KVM guest exists. A host-kernel synthetic demo is not a
   shortcut to production.
3. **Finish off-host backup/restore.** Current-state evidence says scheduled
   encrypted off-host backup is incomplete. Hermes adds sensitive state and
   must not be the first workload relying on an untested restore path.
4. **Amend the topology records.** Replace three `owner`/`partner`/`family`
   sandboxes with four private sandboxes plus an optional family sandbox, and
   reconcile `microvm.nix`/Compose wording with the Ubuntu/NemoClaw path.
5. **Measure `home-core` headroom.** The latest observation shows about 39 GiB
   available, but that is not a capacity reservation. Record gateway/n8n peaks
   and leave explicit host headroom before allocating the guest.

## Target layout

```text
home-core (NixOS host)
  |
  +-- control-plane Compose: Caddy -> LiteLLM -> home-spark vLLM
  |
  +-- agents KVM guest (Ubuntu 24.04)
        +-- NemoClaw CLI + one OpenShell gateway
        +-- OpenShell sandbox: agent-owner
        +-- OpenShell sandbox: agent-partner
        +-- OpenShell sandbox: agent-child1
        +-- OpenShell sandbox: agent-child2
        +-- OpenShell sandbox: agent-family (optional)

Each sandbox -> inference.local -> OpenShell credential injection
             -> https://ai.home.arpa/v1 -> dedicated LiteLLM key
             -> assistant alias -> home-spark model at >=64K context
```

Start the guest at 4 vCPU, 16 GiB RAM, and 80 GiB disk for the one-sandbox
pilot. NVIDIA's generic minimum is 4 vCPU, 8 GB RAM, and 20 GB free, with 16 GB
RAM and 40 GB free recommended; the managed sandbox image alone is roughly
2.4 GB compressed. Five sandboxes, local snapshots, and image generations need
more disk than the one-sandbox recommendation. Increase CPU/RAM only from
measured pressure, and do not let guest allocation consume the gateway's
required headroom. [NemoClaw prerequisites](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/get-started/prerequisites)

Give the guest a dedicated host-only bridge, for example `br-hc-agents`, not a
direct attachment to the home LAN. Publish the Caddy edge on that bridge and
allow only the guest address to reach TCP 443. Inside the guest, map
`ai.home.arpa` to that bridge address through the controlled resolver. At the
host boundary, reject all other private/Tailscale/compute destinations and
allow only DNS/NTP plus reviewed public HTTPS destinations. OpenShell then
enforces the narrower per-sandbox policy.

The dashboard, OpenShell gateway, and Hermes API remain on loopback. Reach them
through an SSH tunnel via `home-core`; do not publish them to the LAN or
Tailscale. NVIDIA's headless guide uses loopback as the normal remote-access
boundary. [NemoClaw headless deployment](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/deployment/deploy-to-headless-server)

## Ordered implementation plan

### Phase 0 — Record the release and architecture decision

1. Amend ADR-017 and the Hermes requirements for an Ubuntu 24.04 KVM guest,
   NemoClaw-managed containers, and the four-private-plus-family topology.
2. Record the reviewed full NemoClaw commit and all managed component/image
   identities. For the 2026-09-26 candidate, the release commit is
   `26922313bba96184e65c3663b351683ebae9504d`.
3. Do not independently install upstream Hermes `0.21.5` or OpenShell `0.1.1`
   into that tuple.
4. Define a rollback checkpoint for the guest configuration before adding any
   runtime.

**Exit:** the version tuple and guest boundary are reviewable without executing
a remote installer.

### Phase 1 — Build the isolated guest and network

1. Create the Ubuntu 24.04 guest with fixed CPU, memory, disk, and a dedicated
   service account. Do not grant normal household accounts guest shell access.
2. Install only the documented prerequisites: Node.js 22.19+, npm 10+, trusted
   system Python 3, Docker Engine, `binutils`, and required base utilities.
3. Put only the dedicated guest operator in the Docker group. Docker group
   membership has root-level host impact.
4. Add `artifacts/home-core-root.crt` as a standalone trusted CA in the guest.
   Pass the same PEM through `NEMOCLAW_CORPORATE_CA_BUNDLE` during onboarding so
   the managed sandbox and OpenShell proxy also trust `ai.home.arpa`; installing
   it only in the guest is insufficient. [NemoClaw CA trust](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/security/configure-corporate-ca-trust)
5. Implement and negatively test the host-only bridge and egress policy before
   giving the guest any application credential.
6. Keep Docker and the VM boot-enabled, but do not invent a NemoClaw systemd
   recovery service. NVIDIA explicitly says reboot recovery is manual and says
   not to use an unofficial service unit as a substitute. [NemoClaw headless deployment](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/deployment/deploy-to-headless-server)

**Exit:** the empty guest can reach only package sources needed for the install,
DNS/NTP, and the `ai.home.arpa` bridge endpoint; it cannot reach the private
compute link, gateway state network, n8n, Home Assistant, or the general LAN.

### Phase 2 — Make the inference contract pass before onboarding

1. Route the `assistant` alias to a live model served at a context length of at
   least 64K. Keep the existing direct vLLM path available only for a controlled
   comparison; the steady-state sandbox receives only the LiteLLM URL.
2. Enable and validate the model-specific vLLM tool parser and automatic tool
   choice. A model that chats but cannot reliably emit tool calls does not
   qualify Hermes.
3. Create one revocable LiteLLM virtual key for `agent-owner`; never use the
   LiteLLM administrative key. Later sandboxes each receive their own key and
   quota.
4. From the guest, verify TLS, `GET /v1/models`, Chat Completions streaming,
   tool calling, timeout behavior, and a 64K request through
   `https://ai.home.arpa/v1`.

NemoClaw accepts an authenticated private OpenAI-compatible endpoint when its
hostname is explicitly listed in `NEMOCLAW_TRUSTED_PRIVATE_HOSTS`. The sandbox
calls `inference.local`; OpenShell forwards to the configured endpoint and
injects the API key at egress, so the raw key is not given to the agent.
[NemoClaw compatible endpoint](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/inference/custom-endpoints/set-up-openai-compatible-endpoint),
[endpoint security](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/inference/custom-endpoints/custom-endpoint-security)

**Exit:** the owner key works only for the intended alias and rate limits, and
the model route passes the Hermes-specific 64K/tool/streaming tests.

### Phase 3 — Install one synthetic owner sandbox

Use the release-matched headless instructions at implementation time. The
shape should be:

```bash
export NEMOCLAW_INSTALL_REF="26922313bba96184e65c3663b351683ebae9504d"

curl -fsSL \
  "https://raw.githubusercontent.com/NVIDIA/NemoClaw/${NEMOCLAW_INSTALL_REF}/install.sh" |
  NEMOCLAW_INSTALL_REF="${NEMOCLAW_INSTALL_REF}" \
  NEMOCLAW_AGENT=hermes \
  bash -s -- --defer-onboarding
```

Then onboard the custom endpoint with secrets supplied only to the process:

```bash
export NEMOCLAW_SANDBOX_NAME=agent-owner
export NEMOCLAW_PROVIDER=custom
export NEMOCLAW_ENDPOINT_URL=https://ai.home.arpa/v1
export NEMOCLAW_MODEL=assistant
export NEMOCLAW_TRUSTED_PRIVATE_HOSTS=ai.home.arpa
export NEMOCLAW_CORPORATE_CA_BUNDLE=/path/to/home-core-root.crt
export COMPATIBLE_API_KEY='<dedicated agent-owner virtual key>'

nemohermes onboard --non-interactive
```

Do not copy the placeholder secret or these illustrative commands into an
unreviewed runbook. Recheck the pinned release's environment names first.
NemoClaw holds submitted provider credentials only long enough to register them
with OpenShell; OpenShell is the system of record and does not let the CLI read
values back. [NemoClaw credential storage](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/security/credential-storage)

Start with Restricted/deny-by-default network policy, no web search, no
messaging, no host mounts, no custom plugins, no MCP servers, no private data,
and no credentials capable of external side effects. NemoClaw supports
read-only host mounts on Linux Docker, but every sandbox process can read the
entire mounted tree; narrow APIs are safer and remain the project default.
[NemoClaw sandbox state and mounts](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/state-and-backups/understand-sandbox-state)

**Exit:** `agent-owner` is Ready, uses the exact managed image and versions,
and has no path to any undeclared LAN or internet destination.

### Phase 4 — Qualify the sandbox and operating model

For the owner sandbox, capture all of the following:

- `nemoclaw doctor` passes host/gateway checks;
- `openshell sandbox list` shows the expected single sandbox;
- `nemohermes agent-owner status` reports Ready and healthy inference through
  the in-sandbox `inference.local` route;
- `nemohermes inference get` reports the intended provider/model;
- `nemohermes agent-owner connect --probe-only` passes;
- a short prompt, streamed response, and bounded tool call succeed;
- attempts to reach the home LAN, compute link, n8n, Home Assistant, another
  sandbox, arbitrary internet sites, host filesystem, and Docker socket fail;
- the raw LiteLLM key cannot be obtained from the sandbox environment, files,
  logs, generated configuration, or agent tools;
- gateway and GB10 outage tests fail clearly and recover without duplicating a
  tool action;
- CPU, RAM, PIDs, disk, and inference concurrency remain within limits during a
  24-hour synthetic soak.

`status` is the authoritative operational probe because it tests the same
in-sandbox route Hermes uses and rejects malformed or provider-error responses,
not merely failed TCP connections. [NemoClaw inference verification](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/inference/validate-inference/verify-inference-route),
[sandbox monitoring](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/monitoring/monitor-sandbox-activity)

Use a host/guest timer only for read-only health checks and alerting. Do not let
monitoring mutate policy or auto-rebuild a sandbox. Hermes itself can export
content-free gateway/platform health over OTLP, but add that only after the
basic status probe works and only to an approved collector. [Hermes gateway monitoring](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-monitoring)

### Phase 5 — Add private text messaging

Create a dedicated bot for `agent-owner`. If Discord is chosen, set all of:

```text
DISCORD_BOT_TOKEN=<owner-bot-token>
DISCORD_SERVER_ID=<private-server-id>
DISCORD_USER_ID=<exact-owner-user-id>
DISCORD_REQUIRE_MENTION=1
```

Let NemoClaw register the token through OpenShell. Do not save it in the
sandbox's `.env`, an image, the repository, shell history, or a snapshot.
Verify denial for a second household account and an unlisted server member.
Keep the bot in text mode; voice remains a separate later qualification.

**Exit:** only the intended identity can trigger the agent, and the bot cannot
broaden the sandbox's tool or network policy.

### Phase 6 — Prove backup, reboot recovery, update, and rollback

1. Create a named NemoClaw snapshot. Snapshots include manifest-declared Hermes
   state such as `SOUL.md`, `state.db`, kanban data, cron execution history, and
   Discord replay state, and live under
   `~/.nemoclaw/rebuild-backups/<sandbox>/`. Treat them as private personal
   data. [NemoClaw snapshots](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/state-and-backups/create-and-restore-snapshots)
2. Remember what they omit: raw provider credentials are not in the snapshot,
   and a replacement gets a newly generated Hermes API key. Recreate
   credentials from the dedicated secret source during restore.
3. Export the current secret-free NemoClaw v1alpha1 configuration and store it
   with the release manifest. Back up snapshots and required OpenShell/registry
   state through encrypted off-host backup; a snapshot remaining only inside
   the guest is not a backup.
4. Restore into a clean replacement sandbox and prove sessions/memory return,
   credentials are reattached rather than exposed, and canonical external data
   is unchanged.
5. Reboot the guest and perform NVIDIA's documented manual sequence: start and
   verify Docker, inspect status, start a stopped sandbox when indicated, run
   `connect --probe-only`, then use `recover` only if the agent/forward remains
   unhealthy. Alert until this recovery is complete.
6. For an update, snapshot first, pin a newly reviewed 40-character NemoClaw
   commit, update the host CLI, run `nemohermes upgrade-sandboxes --check`, and
   rebuild one sandbox. Do not update Hermes inside the sandbox or OpenShell
   independently. Retain the previous commit and snapshot until rollback is
   demonstrated. [NemoClaw headless updates and recovery](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/deployment/deploy-to-headless-server),
   [sandbox updates](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/manage-sandboxes/operate-sandboxes/update-sandboxes)

**Exit:** a clean restore, guest reboot, planned upgrade, and rollback all have
captured evidence. Until NVIDIA publishes an automatic boot-persistence
contract, an operator runbook and post-reboot alert are part of normal service
ownership.

### Phase 7 — Add the remaining household sandboxes serially

Use the same tested tuple, but never clone credentials or personal state.

1. Add `agent-partner` with a new LiteLLM key, bot token, allowlist, state, and
   snapshot chain. Repeat all cross-sandbox denial tests.
2. Add `agent-child1`, then `agent-child2`, initially with the restricted child
   policy above. Run output/content tests with the household before enabling
   either bot for routine use.
3. Add `agent-family` only after a shared-data contract exists. Its API may
   return household-shared projections only and must not infer sharing from the
   caller's membership in a family channel. Keep messaging disabled until a
   four-identity managed ingress is qualified.
4. Run concurrent 64K sessions incrementally. Five configured sandboxes do not
   justify five simultaneous model contexts; set admission/concurrency limits
   from measured GB10 memory and preserve priority for Home Assistant voice and
   production automations.
5. Run a 24-hour mixed-load soak and restore drill with all enabled sandboxes.

## Integration order after the base platform passes

Add capabilities in this order:

1. synthetic/public topic work;
2. read-only shared household information for `agent-family`;
3. narrowly scoped personal read APIs for each adult;
4. bounded n8n-to-Hermes events with one scheduler owner;
5. read-only Home Assistant analytics;
6. consented calendar/meeting sources;
7. voice;
8. private email and financial information last, after a separate threat and
   retention review.

No sandbox receives a credential that can send email, mutate a calendar,
control Home Assistant, delete records, or execute a financial action. Hermes
may create a proposal, but execution remains in the existing authenticated
n8n/application approval flow. OpenShell egress approval and Hermes shell
approval are not business-action authorization.

## Final acceptance gates

Do not call the setup complete until:

- the Ubuntu guest and host-only network are reproducible from the repository;
- the full NemoClaw/Hermes/OpenShell/image/model tuple is immutable and recorded;
- `assistant` is live at 64K or more and passes streaming and tool calling;
- every sandbox has its own LiteLLM key, messaging token, exact user allowlist,
  state, snapshot chain, quotas, and effective policy evidence;
- private sandboxes cannot read one another's memory, sessions, files,
  credentials, logs, notifications, API scopes, or backups;
- the family sandbox can read shared projections only;
- child sandboxes pass the reduced-tool policy and household content review;
- dashboards and APIs remain loopback-only;
- policy, credential non-disclosure, restart, guest reboot, gateway/GB10 outage,
  backup/restore, update, and rollback tests pass;
- five-sandbox mixed load does not violate home-core or home-spark resource and
  priority budgets;
- deterministic Home Assistant operation remains independent of Hermes,
  `home-core`, and `home-spark`.

## Conclusion

The best next step is to build the `agents` VM boundary and repair the live
`assistant` route, not to run a Hermes installer on the NixOS host. Once those
two prerequisites and off-host restore are ready, onboard one synthetic
`agent-owner` sandbox with the pinned NemoClaw tuple, qualify it end to end,
and expand serially to the other adult, both children, and finally the optional
shared-family sandbox.
