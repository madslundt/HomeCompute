# Hermes household rollout plan

**Date:** 2026-09-26
**Status:** Proposed plan; no Hermes runtime has been installed
**Scope:** Four private household assistants (owner, spouse, daughter A, and
daughter B) plus an optional shared family assistant

## Recommendation

Build Hermes as a household service, but do not model the household as four
users of one agent process. The production target should be:

- one private Hermes/OpenShell sandbox per person;
- a separate messaging identity, LiteLLM virtual key, state database, memory,
  skills, schedules, backup, and egress policy for every sandbox; and
- an optional fifth `family` sandbox containing only explicitly shared
  household context.

Hermes profiles do separate configuration, memory, sessions, skills, cron, and
`state.db`, but multiplexed profiles still share a gateway process and service
domain. Profiles are useful organization; they are not the household privacy
boundary. The existing ADR-013 choice of separate OpenShell sandboxes remains
the safer design and should be expanded from `owner` / `partner` / `family` to
four private principals plus `family`.

Run the agent layer in a KVM guest on `home-core`, not directly beside the
gateway containers and not on `home-spark`. Use Ubuntu 24.04 for the guest unless
a separately recorded qualification proves the NixOS path: NVIDIA currently
calls Linux with Docker its tested path and gives Ubuntu 24.04 host-level
validation, while explicitly saying NixOS may work but is not validated.
Hermes' own NixOS module is useful, but Nix/NixOS is Tier 2 and the native module
does not replace the OpenShell boundary.

This changes ADR-017's implementation detail from “a `microvm.nix` NixOS
guest” to “a separately kernel-isolated KVM guest managed by `home-core`.” The
security outcome stays the same; the guest OS follows the upstream-supported
NemoClaw path. Record that amendment before implementation.

```text
private channel A -> owner Hermes sandbox ------\
private channel B -> spouse Hermes sandbox ------+-> one OpenShell gateway
private channel C -> daughter A Hermes sandbox --+   with sandbox-specific policy
private channel D -> daughter B Hermes sandbox --/   and injected credential
shared channel ----> family Hermes sandbox (optional)          |
                                                               |
                                        https://ai.home.arpa / LiteLLM
                                                               |
                                           home-spark local model
```

## Why this differs from the inspirational setup

The quoted household setup is a good product goal: local inference, one stable
router, inspectable memory, and no metered provider dependency. Three details
should not be copied literally:

1. Current Hermes memory is not only plain files. `SOUL.md`, `USER.md`, and
   `MEMORY.md` are inspectable, but sessions and messages also live in
   `state.db`; current NemoClaw snapshots also preserve other manifest-defined
   state. Backup, export, and deletion must cover both.
2. A 131K context is not required to begin. Hermes requires at least 64K. The
   live `automation-moe` service is already configured for 64K and four
   concurrent sequences. Start at 64K, test four household sessions alongside
   n8n and voice load, and consider 131,072 only if KV memory, latency, and
   compaction evidence justify it.
3. Do not fine-tune a household member's voice or writing during initial
   rollout. Start with explicit per-person context files and reviewed memories.
   A later fine-tune needs separate consent, provenance, deletion, and factuality
   evaluation.

## Hard prerequisites

These gates should be completed before a real household message enters Hermes:

1. **Backup and restore:** a same-host encrypted Restic repository may be used
   only as an explicitly labelled `local-bootstrap` safeguard while the empty
   or synthetic-data guest is staged. It protects against accidental state
   deletion but not loss of `home-core` or its disk. Before any real household
   message enters Hermes, replace it with encrypted off-host backup and complete
   a restore drill, including the agents guest and Hermes snapshots.

   The staged NixOS configuration implements this as the separate
   `agents-vm-bootstrap` Restic job, not as `homecompute.backups`. It stores only
   `/srv/state/agents-vm` in `/srv/backup/restic-homecompute`, requires explicit
   risk acceptance, and is accepted by the VM gate only when the guest is
   classified `synthetic-only`. The default `household` classification still
   requires the off-host backup contract.
2. **A working assistant inference lane:** the current `assistant` alias points
   at stopped Qwen3.8. Qualify the already-resident Qwen3.6 model for Hermes and
   expose it through a dedicated `assistant-canary` alias, or restore another
   qualified assistant backend. Do not give Hermes the `automation-moe` client
   identity even if both aliases temporarily reach the same model.
3. **KVM and capacity preflight:** verify `/dev/kvm`, CPU virtualization, free
   disk, and sustained host headroom on `home-core`. NVIDIA's current minimum is
   4 vCPU, 8 GB RAM, and 20 GB free; its recommendation is 4+ vCPU, 16 GB RAM,
   and 40 GB free for one NemoClaw host. Size the guest for several sandboxes
   from measured use rather than multiplying those figures blindly.
4. **Household privacy agreement:** record that the infrastructure
   administrator can access guest disks and backups. Agree on each person's
   retention, export, deletion, and shared-memory rules before onboarding.
5. **Daughters' interface and permissions:** choose an age-appropriate client
   and authentication method. Do not assume Discord is suitable. The initial
   child policy should have no shell, broad web browsing, external messaging,
   Home Assistant writes, purchases, or unattended proactive jobs.

## Phase 0 — Pin the design and release tuple

1. Amend ADR-017 for the Ubuntu KVM guest and expand ADR-013/URS-PA-004 from
   three to five possible principals.
2. Record the exact tuple: Ubuntu image checksum, Docker version, NemoClaw tag
   or reviewed full commit, OpenShell version, managed sandbox-image digest,
   Hermes version/image digest, inference alias, model revision, tool parser,
   and 64K context setting.
3. Use NemoClaw `v0.0.129`, commit
   `26922313bba96184e65c3663b351683ebae9504d`, as the current pilot candidate.
   Preserve its managed Hermes `0.21.3` and OpenShell `0.0.116` versions as one
   tested tuple. Do not substitute the newer standalone Hermes `0.21.5` or
   upstream OpenShell `0.1.1` independently.
4. Re-resolve the tag and record immutable image digests when implementation
   starts. Do not make `latest`, `main`, or the mutable `lkg` channel the
   deployment record.

**Exit gate:** the complete tuple and rollback tuple are in source control, and
upstream installers/scripts have been reviewed before execution.

## Phase 1 — Build the agents guest

1. Add a private KVM guest on `home-core` using a checksum-pinned Ubuntu 24.04
   image. Give it a dedicated persistent disk; do not use host bind mounts for
   Hermes state.
2. Start with 4+ vCPU, 16 GB RAM, and at least 80 GB disk, then adjust from
   observed multi-sandbox use. Preserve enough host memory and CPU for Caddy,
   LiteLLM, PostgreSQL, n8n, backups, and recovery.
3. Give the guest a private host-only interface. Default-deny guest-to-LAN
   traffic. Permit only DNS/NTP needed by the platform, pinned install/update
   sources during maintenance, the host-only `ai.home.arpa` endpoint, and later
   the specifically selected messaging service.
4. Keep the OpenShell gateway, Hermes API, and dashboard on guest loopback.
   Reach them with SSH forwarding; publish no `8642`, dashboard, Docker, or
   OpenShell ports to the LAN.
5. Keep the guest and its state in the encrypted off-host backup set. Test a
   guest-disk restore before adding real data.

**Exit gate:** guest compromise tests cannot reach the control-plane Docker
socket or secrets from the guest; the guest has no route to the private compute
link; reboot and restore evidence exists.

## Phase 2 — Prepare the local inference contract

1. Add `assistant-canary` in LiteLLM, initially backed by the active Qwen3.6
   35B-A3B service if its Hermes tool-calling tests pass.
2. Create a distinct LiteLLM virtual key for the owner canary, limited to that
   alias and an explicit budget/rate policy. Never use the LiteLLM master key.
3. Configure the supported authenticated private endpoint directly as
   `https://ai.home.arpa/v1`, with `ai.home.arpa` in
   `NEMOCLAW_TRUSTED_PRIVATE_HOSTS`. Install the HomeCompute CA in the guest and
   pass the same PEM through `NEMOCLAW_CORPORATE_CA_BUNDLE` so the managed
   sandbox and OpenShell proxy trust it too. OpenShell injects the
   per-principal LiteLLM key at egress; the raw key is not given to Hermes.
4. Test Chat Completions first. Select Responses only if the full streaming and
   tool probe passes; NemoClaw deliberately defaults custom endpoints to Chat
   Completions because superficially compatible Responses implementations can
   lose system prompts or tools.
5. From the guest, exercise 64K context, streaming, multi-step tool calls,
   cancellation, malformed tool arguments, gateway failure, Spark failure, and
   the known LiteLLM dead-upstream timeout case.

**Exit gate:** the canary key can call only `assistant-canary`; prompts and
outputs do not enter infrastructure logs; model identity and context are
observed rather than inferred; failure is bounded.

## Phase 3 — Owner synthetic-data canary

1. Install the pinned NemoClaw/OpenShell tuple in the guest using the documented
   headless-server path, then onboard one `owner-canary` Hermes sandbox without
   messaging, web search, MCP servers, host mounts, or consequential tools.
2. Use an exact managed-image digest and the locked-down/restricted policy. Do
   not substitute a custom Dockerfile during the first qualification.
3. Connect it to the owner-specific LiteLLM key through OpenShell's private
   endpoint and credential injection. Confirm that the raw key cannot be read
   from the sandbox.
4. Use the loopback dashboard or terminal through SSH forwarding for the first
   conversations. Populate only synthetic `SOUL.md`, `USER.md`, and memory.
5. Test effective filesystem, process, network, inference, and credential
   denial—not only the intended configuration.
6. Snapshot, stop, start, rebuild, upgrade, roll back, destroy a disposable
   clone, and restore it. Include `state.db`, context files, messaging state
   when added, and the default kanban database; separately inventory state the
   upstream snapshot manifest does not include.
7. Run a 24-hour soak with scheduled health checks but no autonomous external
   actions.

**Exit gate:** V-PA-001 passes and a clean rebuild from the pinned tuple plus an
offline snapshot reproduces the agent without exposing a credential.

## Phase 4 — Give each adult a private agent

1. Promote the owner canary to a new real-data sandbox only after Phase 3 and
   the backup prerequisite pass. Do not reuse synthetic sessions as production
   history.
2. Create the spouse sandbox independently under the same OpenShell gateway:
   separate sandbox state, LiteLLM key, messaging bot/API token, exact user
   allowlist, snapshot set, notification target, policy, and persona/memory
   files.
3. Verify that provider credentials and policy bindings remain sandbox-specific
   in the pinned managed tuple. If the current gateway cannot preserve that
   property, stop household expansion and review a per-gateway design rather
   than weakening credential isolation. Follow the documented manual headless
   reboot recovery for the pilot; do not invent an unsupported service unit.
4. Test prompt-mediated and direct attempts to cross-read sessions, memories,
   logs, snapshots, API keys, tools, and notification destinations.
5. Start with conversation and explicitly saved preferences. Add read-only
   calendar or household views only after the data source enforces principal
   scope below the model.

**Exit gate:** both adults can export and delete their own data; neither agent
can read or message through the other's identity; administrator visibility is
documented and accepted.

## Phase 5 — Add the daughters one at a time

1. Create a full separate sandbox for each daughter, not a session in an adult
   profile and not one shared “children” memory.
2. Begin with a smaller tool surface than the adult agents: local conversation,
   reviewed memory writes, and optionally curated read-only resources.
3. Use exact authenticated-user allowlists. A display name, channel name, or
   voice identity never chooses the profile or grants permission.
4. Make memory visible and correctable. Do not retain inferred sensitive traits,
   private health information, or conflicts without an explicit household
   policy and appropriate consent.
5. Decide and document parental administration, retention, quiet hours,
   proactive notifications, web access, and deletion separately for each child.
6. Repeat isolation, backup, outage, and restore tests after each addition.

**Exit gate:** each daughter has an independent identity and data lifecycle;
the child policies remain enforced when prompts ask the model to bypass them.

## Phase 6 — Optional shared family agent

Create `family` only after all four private agents pass isolation tests. It gets
its own sandbox and receives only records explicitly projected as
`visibility=shared`. It does not search private agent memory, and private agents
do not gain access to one another through family summaries. Suitable first
uses are a shared shopping list, meal planning, household reminders, and
read-only shared-calendar summaries. Keep family messaging disabled initially:
the current NemoClaw-managed Discord contract documents one exact
`DISCORD_USER_ID`, not a four-person allowlist. Enable it only after a managed
multi-user ingress or a separately reviewed authenticated adapter is qualified.

## Phase 7 — Integrations and proactivity

Add capabilities in this order:

1. one signed n8n-to-Hermes informational event;
2. read-only Home Assistant state through a narrow adapter;
3. read-only shared calendar data;
4. bounded proactive summaries with one scheduler owner, deduplication, quiet
   hours, and a visible reason/source;
5. draft-only email for adults;
6. explicitly approved writes through an external proposal/approval executor;
7. voice only after text identity and privacy behavior are stable.

Hermes shell approvals and OpenShell egress approval are not authorization to
send email, change a calendar, unlock a door, purchase something, or delete
data. Those actions remain outside Hermes and require an authenticated,
one-time approval bound to exact arguments.

## Household acceptance test

Before declaring the setup complete:

- run four simultaneous 64K Hermes sessions plus a representative n8n job and
  Home Assistant voice request; record latency, KV pressure, queueing, and
  failure behavior;
- verify the active vLLM limit of four concurrent sequences is acceptable or
  deliberately change and re-qualify it before enabling the fifth family
  sandbox;
- perform direct and prompt-injected cross-sandbox access attempts for every
  pair of principals;
- revoke each LiteLLM and messaging credential independently;
- reboot `home-core`, the agents guest, and `home-spark` independently and
  confirm deterministic home control remains available;
- restore every sandbox to an isolated target from encrypted off-host backup;
- verify one person's deletion is not resurrected by memory indexes, caches,
  summaries, or restored backups; and
- run the repository validation suite and capture the release/acceptance record.

## Expected repository work

Implementation should add small, reviewable pieces rather than one installer:

- an ADR amendment for the KVM guest and five-principal topology;
- a `home-core` virtualization/network module and guest resource limits;
- a pinned guest-image/bootstrap definition;
- a host-only agents bridge, firewall policy, private-host declaration, and CA
  trust for `ai.home.arpa`;
- per-principal encrypted secret names and a safe one-time registration flow;
- a repeatable NemoClaw/OpenShell onboarding wrapper that requires pinned
  versions and never accepts a floating image;
- health, snapshot, restore, and cross-sandbox verification scripts;
- backup coverage for guest disks and exported NemoClaw snapshots; and
- updated requirements, risk rows, Phase I/J gates, and current-state docs.

## Primary sources checked

- [Hermes v0.21.5 release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24)
- [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Hermes multi-profile gateways](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways)
- [Hermes session storage](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage)
- [Hermes minimum context and custom endpoints](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)
- [Hermes Nix/NixOS support](https://hermes-agent.nousresearch.com/docs/getting-started/nix-setup)
- [NemoClaw prerequisites and tested platforms](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/prerequisites)
- [NemoClaw Hermes quickstart](https://docs.nvidia.com/nemoclaw/user-guide/hermes/get-started/quickstart)
- [NemoClaw headless deployment](https://docs.nvidia.com/nemoclaw/user-guide/hermes/deployment/deploy-to-headless-server)
- [NemoClaw custom OpenAI-compatible endpoint](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/custom-endpoints/set-up-openai-compatible-endpoint)
- [NemoClaw custom-endpoint security](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/custom-endpoints/custom-endpoint-security)
- [NemoClaw multiple sandboxes and gateways](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/operate-sandboxes/run-sandboxes)
- [NemoClaw external gateway lifecycle authority](https://docs.nvidia.com/nemoclaw/user-guide/hermes/deployment/gateway-lifecycle-authority)
- [NemoClaw security controls](https://docs.nvidia.com/nemoclaw/user-guide/hermes/security/best-practices)
- [NemoClaw credential storage](https://docs.nvidia.com/nemoclaw/user-guide/hermes/security/credential-storage)
- [NemoClaw sandbox state](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/state-and-backups/understand-sandbox-state)
- [NemoClaw snapshot and restore](https://docs.nvidia.com/nemoclaw/user-guide/hermes/manage-sandboxes/state-and-backups/create-and-restore-snapshots)
