# HomeCompute setup guide

**Scope:** build the services-node and compute-node baselines, connect them,
prepare the gateway and model-qualification work, and optionally pilot Hermes
on the services node.

This is the main operator path. You can complete the steps below without
reading the detailed plans. Each stage links to its plan for design rationale,
edge cases, and full acceptance evidence.

## What you will build

| Name | Type | Responsibility |
| --- | --- | --- |
| `ai-services-01` | x86 Proxmox host | Runs the gateway, automation, and toolbox VMs; owns durable state and backups |
| `ai-gateway-01` | VM 110 | Exposes `https://ai.home`; owns authentication, aliases, routing, and gateway data |
| `automation-01` | VM 120 | Runs n8n, MCP services, queues, and separately approved agent services |
| `toolbox-01` | VM 130 | Runs CI, builds, experiments, and restricted tools |
| `ai-compute-01` | NVIDIA GB10 or DGX Spark-class appliance | Runs rebuildable text, STT, TTS, and later diarization inference |
| Hermes `owner` pilot | OpenShell sandbox on `automation-01` | Runs the optional personal agent; sends inference through `ai.home` to `ai-compute-01` |

The production request path is:

```text
clients -> https://ai.home -> ai-gateway-01 -> private link -> ai-compute-01
```

`automation-01` and `toolbox-01` also use `https://ai.home`. They do not get
direct access to compute ports.

## What this guide does not automate

The scripts do not install Proxmox or DGX OS, edit physical network bridges,
configure a backup target, deploy the gateway applications, install
NemoClaw/Hermes, or migrate live data.

Those operations depend on your network, storage, existing services, and
credentials. This guide states when to perform them and what must be true
before you continue.

## Setup at a glance

1. Record the network, backup, credential, and rollback decisions.
2. Inventory and back up the services that may later move.
3. Install Proxmox and the two network bridges on `ai-services-01`.
4. Configure off-host backup and provision the three empty service VMs.
5. Install and verify supported DGX OS on `ai-compute-01`.
6. Deploy and smoke-test the first pinned text tuple on loopback.
7. Connect the private link and restrict compute access to `ai-gateway-01`.
8. Install the gateway and expose semantic aliases at `https://ai.home`.
9. Benchmark models by use case and promote only passing tuples.
10. Migrate automations and other consumers one at a time with rollback.
11. Optionally pilot one isolated Hermes sandbox on `automation-01`.

The rest of this guide expands these steps and provides the commands and
checkpoints. The detailed plans are optional unless a check fails or you need
the design rationale.

## Models and use cases

Clients use logical aliases, never model names. The gateway can change the
model behind an alias after the replacement passes the same tests.

| Alias or route | Use case | Initial candidate or comparison set |
| --- | --- | --- |
| `coding` | Codex editing, tools, builds, and tests | Qwen3.6 integration baseline; Qwen3-Coder-Next primary specialist; Nemotron 3.5, Gemma 4, Qwen3.8, and Devstral controls |
| `automation` | n8n, structured output, and approved tools | Qwen3.6 shared baseline; Nemotron 3.5, Gemma 4, Qwen3.8, Devstral, and gpt-oss comparisons |
| `research` | Private, source-bounded synthesis | Qwen3.6 shared baseline; later agent/general challengers |
| `home` | Danish/English conversation and safe Home Assistant tool proposals | Qwen3.6 shared baseline; smaller Qwen controls only if latency requires them |
| `meeting` | Transcript cleanup, summaries, decisions, and actions | Qwen3.6 shared baseline; dense challengers only if they improve factuality |
| `assistant` | Isolated personal-agent sessions and tools | Qwen3.6 integration baseline at 64K or more; Nemotron 3.5 agent challenger |
| STT route | Danish, English, and mixed-language transcription | Danish Parakeet, Whisper large-v3-turbo, and Whisper large-v3 |
| TTS route | Danish speech for Home Assistant and agents | Piper Danish baseline; Røst naturalness challengers |
| Retrieval route | Private document search | Deferred Qwen3 Embedding and Reranker candidates |

No production winner exists yet. The shipped compute configuration starts one
pinned NVIDIA Qwen3.6 candidate at 32K context for protocol and smoke testing.
That does not qualify every alias or the 64K personal-agent use case.

## Before you start

Create an operator-owned worksheet outside the repository. Record:

1. LAN addresses for both physical nodes and all three VMs.
2. LAN gateway, DNS server, search domain, and administrator network.
3. A private compute subnet with no default gateway.
4. An off-host backup target, retention, encryption-key owner, and restore site.
5. Public SSH keys and the owners of all credentials.
6. Existing gateway, n8n, Home Assistant, database, scheduler, and agent state.
7. A rollback owner and acceptable outage window for each later migration.

The examples use this private subnet:

| Endpoint | Example address |
| --- | --- |
| `ai-gateway-01` private NIC | `10.77.10.2/24` |
| `ai-compute-01` private NIC | `10.77.10.10/24` |

Choose different values if these overlap a LAN, VPN, or container network.

Also prepare:

- local console access to both physical nodes;
- a UPS for both nodes;
- two network ports on each node for the proposed direct-link design;
- a verified Proxmox installer and Debian 13 cloud-image checksum;
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

## Step 2 — Build `ai-services-01`

### 2.1 Prepare the x86 host

1. Record the host, firmware, CPU, RAM, storage, NICs, and MAC addresses.
2. Update supported firmware and retain the recovery instructions.
3. Enable CPU virtualization and IOMMU in firmware.
4. Enable automatic power-on after AC loss.
5. Connect the UPS and both network ports.
6. Confirm local console access before changing networking.

### 2.2 Install Proxmox

Install the supported Proxmox VE 9 release interactively on the intended disk.
Verify the installer checksum before writing it.

During installation:

1. Set the hostname to `ai-services-01.home.arpa`.
2. Assign a reserved management address, gateway, and DNS.
3. Set the correct timezone and verify time synchronization.
4. Keep management on wired Ethernet.
5. Review the selected disk and storage layout before confirming erasure.

After the first boot:

1. Select the appropriate official Proxmox repository policy.
2. Apply updates and reboot when the kernel or microcode changes.
3. Create a named administrator, enable MFA, and keep root for recovery.
4. Restrict SSH and the Proxmox UI to the administrator network.
5. Enable the `Snippets` content type on the `local` storage.
6. Do not install application services or Docker on the Proxmox host.

### 2.3 Create the two bridges

Create the bridges from the local console or Proxmox UI after confirming the
physical interface names:

```text
management NIC -> vmbr0 -> LAN, host management, and VM LAN interfaces
private NIC    -> vmbr1 -> no host address and no gateway
```

Only `ai-gateway-01` receives a VM interface on `vmbr1`. Do not guess interface
names or edit remote networking without console recovery.

### 2.4 Configure recovery first

Configure encrypted backup to a different physical system. A second disk or VM
inside `ai-services-01` is not an off-host backup.

Monitor SMART/NVMe health and storage capacity. Keep the backup encryption key
and a sanitized Proxmox configuration copy outside this node.

### 2.5 Prepare the services-node configuration

Copy this repository to the Proxmox host. From the repository root, run:

```bash
sudo ./scripts/setup-services-node.sh init
sudoedit /etc/ai-platform/services-node.env
```

Replace every `REPLACE_...` value. Check storage names, bridge names, VM IDs,
VM addresses, gateway, DNS, public-key path, and resource allocations.

Download the Debian cloud image's published checksum using a trusted channel.
Put the exact 128-character SHA-512 value in `CLOUD_IMAGE_SHA512`.

Leave `START_VMS=false` for the first provisioning run.

### 2.6 Validate and provision the VMs

Run the read-only checks first:

```bash
sudo ./scripts/setup-services-node.sh validate
sudo ./scripts/setup-services-node.sh preflight
```

Resolve every error before continuing. Then run the mutating stages one at a
time:

```bash
sudo ./scripts/setup-services-node.sh host-packages
sudo ./scripts/setup-services-node.sh create-template
sudo ./scripts/setup-services-node.sh provision
sudo ./scripts/setup-services-node.sh status
```

The script refuses to overwrite an existing template or VM ID. If one already
exists, inspect it and choose deliberate IDs instead of deleting it blindly.

Inspect every VM before starting it:

```bash
qm config 110
qm config 120
qm config 130
sudo ./scripts/setup-services-node.sh start
```

### 2.7 Verify the empty services baseline

Confirm all of the following:

- all three VMs boot and cloud-init completes;
- the QEMU guest agent reports their addresses;
- SSH public-key login works and password/root login fails;
- time synchronization, DNS, package access, Docker, and Compose work;
- `ai-gateway-01` has LAN and private-compute interfaces;
- `automation-01` and `toolbox-01` cannot reach the compute subnet directly;
- the Proxmox host runs no application containers;
- one empty VM can be backed up and restored in isolation.

**Checkpoint:** the empty services node is stable, isolated, and recoverable.
No production state has moved.

Details: [services-node plan](ai-services-node-plan.md).

## Step 3 — Build `ai-compute-01`

### 3.1 Prepare the appliance

Use an NVIDIA GB10 or DGX Spark-class appliance with its supported DGX OS.
The repository does not replace the GPU driver, CUDA, Docker, or NVIDIA
Container Toolkit.

1. Record hardware, firmware, storage, MAC addresses, and recovery information.
2. Connect the UPS, management network, and private-compute network.
3. Set hostname `ai-compute-01.home.arpa` and the correct timezone.
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
3. Test reachability between `ai-gateway-01` and `ai-compute-01`.
4. Add a compute-node firewall rule allowing only the gateway private address
   to the qualified inference port.
5. Prove that the LAN, `automation-01`, and `toolbox-01` cannot reach the port.

On the compute node, edit the release environment:

```text
GB10_BIND_ADDRESS=10.77.10.10
GATEWAY_CIDR=10.77.10.2/32
FIREWALL_CONFIRMED=true
```

Use your worksheet values if they differ. Re-run `validate` and `install`, then
test health and Responses calls from `ai-gateway-01`.

Pull the private cable and verify a clear unavailable response. Private aliases
must not silently switch to a cloud provider.

**Checkpoint:** only `ai-gateway-01` can reach the compute service.

## Step 5 — Install the AI gateway

The repository does not yet include a turnkey gateway stack. Build the reviewed
gateway configuration on `ai-gateway-01` with:

- Caddy for TLS and the public `https://ai.home` boundary;
- LiteLLM for authentication, aliases, routing, and explicit fallback policy;
- dedicated PostgreSQL and Redis instances for gateway-owned state;
- separate, revocable keys for each client;
- pinned images and metadata-only logs;
- monitoring that excludes prompts, outputs, audio, and credentials.

Add the private compute endpoint as a backend. Expose only the semantic aliases
and fixed audio routes; never expose the concrete model or compute address to
clients.

Verify TLS, authentication, streaming, tool calls, cancellation, rate limits,
logging privacy, backend failure, and rollback. Keep the old gateway available
until the replacement passes equivalence tests.

**Checkpoint:** approved clients can use `https://ai.home`, and the old gateway
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

Hermes is an application workload on the K15 services node, not a service on
the GX10/GB10 compute appliance. Start only after `automation-01`, `ai.home`,
and the `assistant` inference alias are working.

1. Allocate 16 GB of available RAM to the `automation-01` pilot and verify that
   concurrent n8n, queue, browser, and database work still has safe headroom.
2. Install a pinned NemoClaw/OpenShell release on `automation-01` and onboard
   Hermes using the release-matched NVIDIA instructions.
3. Give Hermes a dedicated gateway credential and point its inference provider
   at `https://ai.home`; do not expose a direct GB10 endpoint to the sandbox.
4. Create one synthetic-data `owner` sandbox with Restricted policy,
   deny-by-default egress, no host bind mounts, and no credentials capable of
   email, calendar, Home Assistant, deletion, or financial actions.
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

**Checkpoint:** one recoverable synthetic Hermes sandbox runs on
`automation-01`, reaches inference only through `ai.home`, and cannot cross its
declared network, credential, filesystem, or data boundaries.

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
