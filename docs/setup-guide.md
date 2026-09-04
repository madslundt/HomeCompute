# HomeCompute setup guide

> **Control-plane update:** ADR-016 selects the Git-first NixOS configuration
> in this repository for `home-core`. The compute node keeps its supported
> DGX OS baseline.

**Scope:** build the services-node and compute-node baselines, connect them,
prepare the gateway and model-qualification work, and optionally pilot Hermes
as an isolated Compose project on `home-core` (ADR-017).

This is the main operator path. You can complete the steps below without
reading the detailed plans. Each stage links to its plan for design rationale,
edge cases, and full acceptance evidence.

## What you will build

| Name | Type | Responsibility |
| --- | --- | --- |
| `home-core` | x86 NixOS host | Runs the trusted gateway Compose stack; owns gateway state and backups |
| Gateway Compose project | Containers on `home-core` | Caddy, one LiteLLM worker, and dedicated PostgreSQL |
| `home-spark` | NVIDIA GB10 or DGX Spark-class appliance | Runs rebuildable text, STT, TTS, and later diarization inference |
| Automation project | Separate Compose project on `home-core` | Runs n8n and its workflow state after migration off the Home Assistant add-on |
| Hermes `owner` pilot | OpenShell sandbox in its own Compose project on `home-core` | Runs the optional personal agent; sends inference through `ai.home.arpa` to `home-spark` |

The production request path is:

```text
clients -> https://ai.home.arpa -> home-core -> private link -> home-spark
```

Automations, agents, and Home Assistant all use `https://ai.home.arpa`; none of
them get direct access to compute ports. The private link stays restricted to
LiteLLM even for workloads that share the host with it.

## What this guide does not automate

The flake does not partition disks, invent site-specific network addresses or
SSH keys, create an off-host backup repository, install DGX OS, install
NemoClaw/Hermes, or migrate live data.

Those operations depend on your network, storage, existing services, and
credentials. This guide states when to perform them and what must be true
before you continue.

## Setup at a glance

1. Record the network, backup, credential, and rollback decisions.
2. Inventory and back up the services that may later move.
3. Install the pinned NixOS configuration on `home-core`.
4. Deploy the trusted gateway stack, then configure and restore-test off-host backup.
5. Install and verify supported DGX OS on `home-spark`.
6. Deploy and smoke-test the first pinned text tuple on loopback.
7. Connect the private link and restrict compute access to `home-core`.
8. Install the gateway and expose semantic aliases at `https://ai.home.arpa`.
9. Benchmark models by use case and promote only passing tuples.
10. Migrate automations and other consumers one at a time with rollback.
11. Optionally pilot one isolated Hermes sandbox as its own Compose project on
    `home-core`.

The rest of this guide expands these steps and provides the commands and
checkpoints. The detailed plans are optional unless a check fails or you need
the design rationale.

## Models and use cases

Clients use logical aliases, never model names. The gateway can change the
model behind an alias after the replacement passes the same tests.

| Alias or route | Use case | Initial candidate or comparison set |
| --- | --- | --- |
| `coding` | Codex editing, tools, builds, and tests | Qwen3.6 integration baseline; Qwen3.8 first quality candidate; Nemotron 3.5 performance candidate; Ornith conditional challenger |
| `automation` | n8n, structured output, and approved tools | Qwen3.6 integration baseline; Qwen3.8 first quality candidate; Nemotron 3.5 performance comparison |
| `research` | Private, source-bounded synthesis | Qwen3.6 integration baseline; Qwen3.8 first quality candidate |
| `home` | Danish/English conversation and safe Home Assistant tool proposals | Qwen3.6 shared baseline; smaller Qwen controls only if latency requires them |
| `meeting` | Transcript cleanup, summaries, decisions, and actions | Qwen3.6 integration baseline; Qwen3.8 quality candidate; Gemma only for a measured multilingual/factuality gap |
| `assistant` | Isolated personal-agent sessions and tools | Qwen3.6 integration baseline at 64K or more; Qwen3.8 quality candidate; Nemotron 3.5 performance candidate |
| STT route | Danish, English, and mixed-language transcription | Danish Parakeet, Whisper large-v3-turbo, and Whisper large-v3 |
| TTS route | Danish speech for Home Assistant and agents | Piper Danish baseline; Røst naturalness challengers |
| Retrieval route | Private document search | Deferred Qwen3 Embedding and Reranker candidates |

No production winner exists yet. The shipped compute configuration starts one
pinned NVIDIA Qwen3.6 candidate at 32K context for protocol and smoke testing.
That does not qualify every alias or the 64K personal-agent use case.

## Before you start

Create an operator-owned worksheet outside the repository. Record:

1. LAN/VLAN and private-link addresses for both physical nodes.
2. LAN gateway, DNS server, search domain, and administrator network.
3. A private compute subnet with no default gateway.
4. An off-host backup target, retention, encryption-key owner, and restore site.
5. Public SSH keys and the owners of all credentials.
6. Existing gateway, n8n, Home Assistant, database, scheduler, and agent state.
7. A rollback owner and acceptable outage window for each later migration.

The examples use this private subnet:

| Endpoint | Example address |
| --- | --- |
| `home-core` private NIC | `10.77.10.2/24` |
| `home-spark` private NIC | `10.77.10.10/24` |

Choose different values if these overlap a LAN, VPN, or container network.

Also prepare:

- local console access to both physical nodes;
- a UPS for both nodes;
- two network ports on each node for the proposed direct-link design;
- a verified NixOS 26.05 installer image and checksum;
- supported DGX OS and its recovery procedure for the compute appliance;
- exact container and model revisions plus the required model-registry token.

## Step 1 — Inventory before changing anything

Record the current AI gateway, n8n, Home Assistant, Node-RED, MCP services,
databases, schedulers, monitoring, and agents.

For each service, capture its version, image, ports, data locations, credential
owner, backup method, dependencies, and whether it is still used. Take a
supported backup before planning its migration.

Do not disable or migrate a live service during the node-baseline steps.

**Checkpoint:** every later migration has an owner, source, backup, restore
method, and keep-or-retire decision.

Details: [platform execution plan](platform-execution-plan.md#step-2--inventory-what-already-exists).

## Step 2 — Build `home-core`

### 2.1 Prepare the x86 host

1. Record the host, firmware, CPU, RAM, storage, NICs, and MAC addresses.
2. Update supported firmware and retain the recovery instructions.
3. Enable CPU virtualization and IOMMU in firmware.
4. Enable automatic power-on after AC loss.
5. Connect the UPS and both network ports.
6. Confirm local console access before changing networking.

### 2.2 Prepare host-specific inputs

Boot the verified NixOS installer and create an EFI filesystem labelled `ESP`
plus an ext4 root filesystem labelled `nixos`. Mount them below `/mnt`, clone
this repository, and compare `nixos-generate-config --root /mnt` with the
committed `hosts/home-core/hardware-configuration.nix`.

Before remote activation:

1. add `mads`' reviewed public SSH key to the host configuration;
2. replace DHCP with explicit networking only after recording NIC names;
3. create and back up the host's age identity;
4. add its public recipient and commit only encrypted sops data;
5. configure a real off-host Restic repository and test credentials.

### 2.3 Validate and install NixOS

Run from the repository checkout:

```bash
nix flake check
sudo nixos-rebuild build --flake .#home-core
sudo nixos-install --flake .#home-core
```

After reboot, subsequent system and user changes use the same activation:

```bash
sudo nixos-rebuild switch --flake .#home-core
systemctl status home-manager-mads.service
```

### 2.4 Configure recovery and verify the baseline

Enable the guarded sops-nix and Restic modules only after supplying their real
encrypted and off-host inputs. Confirm:

- the expected NixOS generation boots and can roll back from the boot menu;
- SSH accepts only the reviewed key through the declared Tailscale policy;
- time synchronization, DNS, Docker, Compose, SMART monitoring, and trimming work;
- `/srv/state/control-plane` exists with the declared ownership and mode;
- Home Manager applies Bash, Git, tmux, aliases, and CLI tools for `mads`;
- a backup of `/srv/state` restores on an isolated target.

**Checkpoint:** the NixOS services node is stable and recoverable. No production
state has moved.

Details: [NixOS control-plane plan](nixos-control-plane-node-plan.md).

## Step 3 — Build `home-spark`

### 3.1 Prepare the appliance

Use an NVIDIA GB10 or DGX Spark-class appliance with its supported DGX OS.
The repository does not replace the GPU driver, CUDA, Docker, or NVIDIA
Container Toolkit.

1. Record hardware, firmware, storage, MAC addresses, and recovery information.
2. Connect the UPS, management network, and private-compute network.
3. Set hostname `home-spark.home.arpa` and the correct timezone.
4. Assign reserved management and private addresses.
5. Apply vendor-supported firmware and DGX OS updates, then reboot.
6. Verify `nvidia-smi`, container GPU access, NTP, disk health, DNS, and space.
7. Enable SSH keys and restrict management to the administrator network.

Keep runtime ports on loopback at first. Do not expose them to the LAN or the
internet.

### 3.2 Initialize external configuration and secrets

Copy this repository to the appliance. From the repository root, run:

```bash
sudo ./scripts/setup-compute-node.sh init --env /etc/gb10-ai/gb10.env
sudoedit /etc/gb10-ai/gb10.env
sudoedit /etc/gb10-ai/secrets/hf_token
```

`init` creates the service account, storage tree, configuration, and secret
files. It is safe to rerun and preserves operator settings.

In `/etc/gb10-ai/gb10.env`, resolve and verify:

1. the exact Linux ARM64 runtime image digest;
2. full model, tokenizer, and remote-code commits;
3. provenance URL, license, format, quantization, and template hash;
4. parser, backend, context, concurrency, and memory settings;
5. loopback bind `127.0.0.1` for the first deployment;
6. at least 100 GB of storage headroom.

Keep secrets outside the repository. Never use `latest`, branch names, floating
model revisions, wildcard binds, or unverified templates.

### 3.3 Validate and install the first text tuple

Run the read-only and offline checks:

```bash
sudo ./scripts/setup-compute-node.sh preflight --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh validate --env /etc/gb10-ai/gb10.env
```

Resolve every warning or error that affects acceptance. Then install:

```bash
sudo ./scripts/setup-compute-node.sh install \
  --env /etc/gb10-ai/gb10.env \
  --wait 1200
```

The installer pulls the exact image, verifies GPU access, starts the service,
waits for health, checks the six aliases, sends a minimal Responses request,
and writes a secret-free release manifest.

### 3.4 Check health and rollback

Run:

```bash
sudo ./scripts/setup-compute-node.sh status --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh smoke --env /etc/gb10-ai/gb10.env
sudo ./scripts/setup-compute-node.sh logs --env /etc/gb10-ai/gb10.env
```

A smoke test proves startup and a minimal API call only. It does not qualify
coding tools, Danish quality, long context, concurrency, stability, or recovery.

Retain the last known-good environment and manifest. Prove that a prior pinned
tuple can be restored:

```bash
sudo ./scripts/setup-compute-node.sh rollback \
  --env /etc/gb10-ai/releases/previous.env \
  --wait 1200
```

Use the actual retained environment path; `previous.env` is illustrative.

**Checkpoint:** direct loopback inference is healthy and a tested rollback path
exists. No ordinary client can reach it.

Details: [compute-node plan](ai-compute-node-plan.md).

## Step 4 — Connect the private compute link

1. Cable the two private interfaces.
2. Confirm that the private subnet has no gateway or internet route.
3. Test reachability between `home-core` and `home-spark`.
4. Add a compute-node firewall rule allowing only the gateway private address
   to the qualified inference port.
5. Prove that ordinary LAN clients cannot reach the compute port directly.

On the compute node, edit the release environment:

```text
GB10_BIND_ADDRESS=10.77.10.10
GATEWAY_CIDR=10.77.10.2/32
FIREWALL_CONFIRMED=true
```

Use your worksheet values if they differ. Re-run `validate` and `install`, then
test health and Responses calls from `home-core`.

Pull the private cable and verify a clear unavailable response. Private aliases
must not silently switch to a cloud provider.

**Checkpoint:** only the private interface on `home-core` can reach the compute service.

## Step 5 — Install the AI gateway

Use the guarded `deploy/control-plane` stack on `home-core`. It contains
Caddy, one LiteLLM worker, and a dedicated PostgreSQL database. Redis is absent
until scaling or an approved cache design requires it. n8n, browsers, agent
sandboxes, and Home Assistant stay outside this deployment.

Resolve fixed-version image digests in the tracked environment template,
materialize credentials with sops-nix, and deploy on loopback first. For a LAN
or Tailscale address, declare and rebuild the exact NixOS firewall policy and
verify both allowed and denied clients after Compose starts.

Add the private compute endpoint as a TLS backend. Plain HTTP is an explicit
exception limited to the dedicated non-routed link. Expose only semantic
aliases; never expose the concrete model, compute address, LiteLLM management
API, PostgreSQL, or Docker API to clients.

Verify TLS, authentication, streaming, tool calls, cancellation, rate limits,
logging privacy, backend failure, and rollback. Keep the old gateway available
until the replacement passes equivalence tests.

**Checkpoint:** approved clients can use `https://ai.home.arpa`, and the old gateway
can still be restored within the agreed window.

Details: [platform plan, Step 6](platform-execution-plan.md#step-6--install-and-qualify-the-ai-gateway).

## Step 6 — Qualify models before promotion

Test one immutable model/runtime tuple at a time. Record the image, model,
tokenizer, template, parser, quantization, context, flags, and result together.

Run use-case fixtures for:

- Codex Responses streaming, tools, edits, builds, tests, and recovery;
- Danish/English automation and strict structured output;
- Home Assistant entity selection, confirmations, and denied actions;
- private research with source bounds and injection tests;
- meeting factuality, decisions, actions, STT, and diarization;
- personal-agent isolation, tools, and at least 64K context;
- cold/warm latency, concurrency, memory, thermals, restart, and mixed load;
- STT word error rate and TTS pronunciation, naturalness, and first audio.

Require at least 10% production memory headroom. Promote only the exact tuple
that passes its alias or route. Publisher benchmarks are candidate evidence,
not local acceptance.

Details: [model recommendation](research/llm-installation-recommendation.md) and
[verification strategy](verification-strategy.md).

## Step 7 — Migrate services one at a time

Use this order:

1. AI gateway, with the old gateway retained.
2. A sanitized n8n workflow, then one low-risk real workflow.
3. Remaining automations from low to high risk.
4. Restricted toolbox workloads.
5. Home Assistant voice and tools after deterministic safety and Danish tests.
6. Meeting Assistant after its import, storage, privacy, and restore path works.
7. Optional Home Assistant VM relocation only after a separate migration plan.

For every migration, back up first, test restore, prevent duplicate schedules,
observe a soak period, and retain rollback until the exit gate passes.

## Step 8 — Optionally pilot Hermes

> **Deferred (2026-09-04).** Hermes is not being built yet. Keeping the agent
> layer absent is the single largest risk reduction available on a consolidated
> host, because it removes the prompt-injectable component that holds tool
> credentials. This step is retained as the plan for when that changes.

Hermes runs on `home-core` in its own Compose project, never on the
GX10/GB10 compute appliance. ADR-017 accepts container isolation here in place
of the separate host the requirements originally specified, so the isolation
steps below are the boundary rather than a second layer behind one.

Before a sandbox handles any real personal data, or any input the household did
not author, URS-PA-020 requires moving it into the `agents` microVM — a
separate kernel on the same machine. Synthetic-data piloting on the host kernel
is acceptable; production is not. Start only after `ai.home.arpa` and the
`assistant` inference alias work.

1. Set explicit memory and CPU limits from measured gateway, n8n, and database
   usage, so a sandbox cannot starve the gateway sharing its kernel.
2. Install a pinned NemoClaw/OpenShell release and onboard Hermes using the
   release-matched NVIDIA instructions. Run every container as a non-root user
   with dropped capabilities and no Docker socket mount.
3. Give Hermes a dedicated gateway credential and point its inference provider
   at `https://ai.home.arpa`; do not expose a direct GB10 endpoint to the sandbox.
4. Create one synthetic-data `owner` sandbox with Restricted policy,
   deny-by-default egress, no host bind mounts, and no credentials capable of
   email, calendar, Home Assistant, deletion, or financial actions. Put it on
   its own Docker network with no route to the gateway's `internal` network,
   give it a `/srv/state/hermes` subtree the gateway's runtime user cannot
   read, and materialize its secrets from a sops group separate from the
   gateway, database, and compute credentials.
5. Back up and restore the sandbox state, then test reboot recovery, policy
   denials, streaming, tools, 64K context, gateway/GB10 outages, and mixed-load
   behavior.
6. Create separate `partner` and `family` sandboxes only after cross-sandbox
   isolation tests pass. Never use profiles inside one shared process as the
   privacy boundary.

The repository intentionally does not freeze install commands before a release
tuple is selected. Use the exact commands for that pinned release and record
them in the operator runbook. See the
[NemoClaw placement and Hermes setup note](research/nemoclaw-machine-placement.md),
[Hermes verification plan](research/hermes-personal-assistant-verification.md),
and [ADR-013](adr/013-hermes-personal-agent-layer.md).

**Checkpoint:** one recoverable synthetic Hermes sandbox runs in its own
Compose project on `home-core`, reaches inference only through
`ai.home.arpa`, and cannot cross its declared network, credential, filesystem,
or data boundaries — verified from inside the sandbox, not from the host.

## Completion checklist

The platform is ready for production only when:

- both nodes are reproducible, patched, monitored, and on UPS power;
- the services node has tested encrypted off-host restore;
- the compute node has no canonical application state;
- only the gateway reaches private compute ports;
- clients use TLS, individual credentials, and semantic aliases;
- every promoted model/runtime tuple has use-case and mixed-load evidence;
- private aliases fail closed and logs contain no private payloads;
- power, network, process, gateway, database, update, and restore failures have
  been exercised;
- old services remain recoverable until their rollback windows expire.

For the complete phase gates and rationale, see the
[platform execution plan](platform-execution-plan.md).
