# HomeCompute execution plan

> **Deployment update:** ADR-016 makes the root NixOS flake authoritative for
> `ai-services-01`. Keep untrusted tools and personal-agent sandboxes outside
> the gateway host.

**Version:** 0.1  
**Date:** 2026-08-30  
**Status:** Ready for owner review; physical execution not started

## Purpose

This is the controlling plan for building the two-node HomeCompute platform. It
defines role-based names, execution order, dependencies, promotion gates, and
the point at which existing services may be migrated. The detailed node plans
contain the commands and acceptance checks.

The names do not depend on a hardware vendor:

| System role | Stable name | Initial hardware | Responsibility |
| --- | --- | --- | --- |
| AI compute node | `ai-compute-01` | NVIDIA GB10 or DGX Spark-class appliance | Rebuildable GPU inference: text, STT, TTS, and later diarization |
| AI services node | `ai-services-01` | Supported x86 NixOS host | Trusted Caddy/LiteLLM/PostgreSQL Compose stack and `/srv/state` |
| Application hosts | Existing or separately qualified hosts | Outside `ai-services-01` | n8n, MCP, browser workers, personal agents, CI, and experimental tools |

Suggested DNS is `ai-compute-01.home.arpa` and `ai-services-01.home.arpa`.
Consumers continue to use the
stable service alias `https://ai.home.arpa`; they do not depend on node hostnames.

## Final system boundary

```mermaid
flowchart LR
    Clients[Codex, Home Assistant, n8n,<br/>Meeting Assistant, approved agents]
    Alias[ai.home.arpa]

    subgraph Services[ai-services-01 - services node]
        Gateway[Caddy + LiteLLM + PostgreSQL]
        State[/srv/state/control-plane]
        Gateway --> State
    end

    subgraph Apps[Existing or separately qualified application hosts]
        Automation[n8n + MCP + durable services]
        Agents[isolated personal agents]
        Toolbox[CI + tools + frameworks]
        Automation -->|authenticated API| Gateway
        Agents -->|authenticated API| Gateway
        Toolbox -->|authenticated API| Gateway
    end

    subgraph Compute[ai-compute-01 - compute node]
        Inference[vLLM + qualified text model]
        Audio[STT + TTS + later diarization]
    end

    Clients -->|TLS + consumer credential| Alias --> Gateway
    Gateway -->|private 2.5GbE link| Inference
    Gateway -->|private 2.5GbE link| Audio
    Backup[Off-host encrypted backup] -.-> Gateway
    Backup -.-> Automation
```

## Information required before execution

Record these decisions in an operator-owned installation worksheet; never put
credentials in this repository:

1. Reserved LAN IPs for `ai-services-01`, `ai-compute-01`, and the current
   Home Assistant, automation, personal-agent, and AI Home hosts.
2. LAN gateway, DNS server, `home.arpa` records, administrator subnet, and any
   toolbox VLAN ID/gateway/DNS.
3. Private compute-link subnet. The proposed values are
   `10.77.10.2/24` for `ai-services-01` and `10.77.10.10/24` for
   `ai-compute-01`, with no gateway.
4. Off-host backup target, retention, encryption-key custody, and restore-test
   location.
5. UPS model and shutdown/restart method for both physical nodes.
6. Existing AI Home, n8n, Home Assistant, Node-RED, MCP, scheduler, database,
   and agent inventory.
7. Rollback owner and acceptable outage window for each migrated service.

## Execution order

### Step 1 — Approve the architecture and naming

- Approve this plan, ADR-001, ADR-011, ADR-013, and ADR-014.
- Reserve the role-based DNS names and IP addresses.
- Confirm that the GB10 is compute-only and the services node owns durable
  application state.
- Confirm that `ai.home.arpa` is the sole production client entry point.

**Exit:** Names, addresses, data ownership, and rollback owners are recorded.

### Step 2 — Inventory what already exists

- Export or inspect current AI Home, n8n, Home Assistant, Node-RED, MCP,
  databases, schedulers, monitoring, and agent services.
- Record versions, image digests/tags, ports, credentials by owner, volumes,
  backups, dependencies, and which services are actually used.
- Resolve duplicated schedulers and unused prototype services before migration.
- Take supported backups without committing private content or secrets here.

**Exit:** Every candidate migration has a source, owner, backup, restore method,
and keep/retire decision.

### Step 3 — Build the empty AI services node

Follow the [NixOS control-plane plan](nixos-control-plane-node-plan.md):

- prepare firmware, labelled filesystems, networking, and UPS;
- compare detected hardware modules with the committed host configuration;
- install the pinned NixOS flake on `ai-services-01`;
- configure the reviewed SSH key, Tailscale, sops age identity, and off-host backup;
- verify the NixOS generation, Home Manager activation, `/srv/state`, isolation,
  and an empty restore.

**Exit:** The empty services node is stable and recoverable. No production
service has moved.

### Step 4 — Build the AI compute node

Follow the [AI compute node plan](ai-compute-node-plan.md) through Gates C0 and
C1:

- inventory hardware, DGX OS, firmware, driver, CUDA, storage, and networking;
- configure `ai-compute-01` and its private Ethernet address;
- resolve all immutable runtime/model inputs;
- deploy the first vLLM tuple privately;
- pass health, Responses API, Codex tools/streaming, memory, latency, load,
  restart, and rollback tests.

**Exit:** Direct private inference works, but ordinary clients still do not use
the compute node directly.

### Step 5 — Connect the nodes

- Cable the dedicated 2.5GbE ports directly or through an appropriate switch.
- Confirm the private subnet has no default gateway or internet route.
- Allow only `ai-services-01` to reach the compute service ports.
- Deny application hosts, ordinary LAN clients, and the internet.
- Test MTU, packet loss, sustained transfer, inference latency, and behavior
  during cable and compute-node failures.

**Exit:** The gateway-to-compute path is stable, restricted, and documented.

### Step 6 — Install and qualify the AI gateway

- Rebuild the desired existing Caddy/LiteLLM configuration on
  `ai-services-01` using pinned images and dedicated PostgreSQL ownership.
- Create distinct consumer credentials and task-semantic aliases.
- Add the local `ai-compute-01` backend and only explicitly approved cloud
  routes.
- Keep private home, meeting, and personal-agent aliases fail-closed.
- Verify TLS, authentication, Responses API behavior, tool streaming, logging
  privacy, rate limits, retry policy, backend outage behavior, and rollback.
- Keep the previous AI Home gateway available until equivalence passes.

**Exit:** `https://ai.home.arpa` passes Gate C3 and can be switched back to the old
gateway within the agreed recovery time.

### Step 7 — Select the production inference tuples

- Run the benchmark and verification suite for candidate text runtimes/models.
- Test coding, Danish/general use, n8n automation, Home Assistant tools, meeting
  summaries, long context, mixed load, and recovery.
- Compare vLLM with required baselines/challengers using identical fixtures.
- Promote only immutable tuples that meet quality, latency, memory-headroom,
  privacy, and stability thresholds.
- Add STT and TTS as separate fixed-path services only after text is stable.

**Exit:** Each production alias maps to a qualified and reversible tuple.

### Step 8 — Migrate automations in risk order

- Install the approved n8n, MCP, database, queue, and browser-worker stacks on
  a separately qualified application host with pinned images and identities.
- Import one sanitized test workflow, then one low-risk real workflow.
- Test idempotency, retries, scheduling, credential isolation, database restore,
  and compute/gateway outage handling.
- Migrate remaining workflows by owner and risk class.
- Disable an old scheduler only after the new job has completed successfully
  for its agreed soak period.

**Exit:** Automations are backed up, observable, non-duplicated, and reversible.

### Step 9 — Add restricted tools and agents

- Install development frameworks on a separately qualified toolbox host only
  for measured workloads, preferably as pinned containers/devcontainers.
- Give the toolbox no production database or household credentials and no
  direct compute route.
- Pilot one Hermes/OpenShell sandbox on a separately isolated application host; test default-deny
  egress, managed credentials, approvals, snapshot/restore, 64K context, and
  session isolation before adding people/profiles.
- Keep Home Assistant as the sole authority for physical home actions.

**Exit:** Tools and agents operate within explicit network, credential, data,
and approval scopes.

### Step 10 — Add optional consumers one at a time

- Home Assistant voice/tools after deterministic tool restrictions and Danish
  STT/TTS tests pass.
- Meeting Assistant after its current worktree is reconciled and the supported
  Plaud import/storage contract is defined.
- HAOS relocation only after its radio/add-on/topology inventory and a separate
  VM restore test; relocation is not part of the initial build.

**Exit:** Each consumer has its own credential, privacy policy, acceptance
evidence, and rollback path.

### Step 11 — Production acceptance and handover

- Run a 72-hour mixed-load soak followed by the agreed longer production soak.
- Test power loss, UPS shutdown, automatic boot, service start order, compute
  outage, gateway outage, failed upgrade, database restore, and full host
  restore.
- Verify patch, monitoring, alerting, capacity, log retention, backup retention,
  secret rotation, and quarterly restore schedules.
- Record the installed hardware/firmware/software manifest and the exact
  promoted container/model tuples.
- Remove old services only after the rollback window expires and final backups
  are verified.

**Exit:** The platform is accepted for production; unresolved gates remain
explicitly Not Run or Blocked rather than being inferred as passed.

## Critical dependencies

```text
Inventory ───────────────┐
                        ├─> AI gateway migration ─> automation migration
Services node S0/S1 ────┤
Compute node C0/C1 ─────┘

Gateway + compute qualification ─> model selection ─> optional consumers
Off-host backup + restore test ───> any durable-state migration
```

Work on the two physical nodes may proceed in parallel through their empty-host
baselines. Gateway integration requires both. No durable migration starts
before the NixOS services-node restore gate.

## Definition of done

The project is complete only when:

- role names and DNS are stable;
- no production consumer calls a concrete model or compute-node runtime port;
- both physical nodes are reproducibly configured and monitored;
- gateway and automation state has encrypted off-host backup with tested
  restore;
- promoted model/runtime tuples are immutable and have measured evidence;
- private aliases fail closed and logging excludes private payloads;
- power, network, compute, gateway, database, update, and restore failures have
  been exercised;
- each retired service has a final backup and recorded rollback-window expiry.
