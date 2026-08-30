# AI services node (`ai-services-01`) plan

**Version:** 0.1  
**Date:** 2026-08-30  
**Status:** Provisioning design ready; hardware and live migrations not yet verified

## Outcome

The GMKtec K15 is assigned the stable role name `ai-services-01` and becomes
the always-on x86 services node. Proxmox VE remains a minimal hypervisor, three
Debian virtual machines own application workloads, and `ai-compute-01` remains
a rebuildable inference appliance. `ai-services-01` does not become a second AI
inference server unless a later, measured requirement adds an external GPU.

The first installation creates infrastructure only. It does not migrate live
n8n, Home Assistant, AI Home, Hermes, Meeting Assistant, or database state.
Each service moves only after its source inventory, backup, restore, and
rollback checks pass.

## Target layout

| ID | Guest | Initial allocation | Responsibility | Durable state |
| --- | --- | --- | --- | --- |
| 110 | `ai-gateway-01` | 4 vCPU, 8 GB max / 6 GB balloon, 120 GB | Caddy, LiteLLM, dedicated PostgreSQL/Redis, metadata-only monitoring | Gateway configuration, keys, routing metadata |
| 120 | `automation-01` | 6 vCPU, 16 GB max / 10 GB balloon, 300 GB | n8n, MCP servers, queues, browser workers, later qualified agent sandboxes | Workflows, application databases, agent state |
| 130 | `toolbox-01` | 6 vCPU, 12 GB max / 8 GB balloon, 300 GB | CI, builds, x86-only frameworks, experimental and untrusted tools | Rebuildable caches and checked-out repositories only |

The maximum guest allocation is 36 GB. Balloon minimums total 24 GB, leaving
room for Proxmox, filesystem cache, and bursts on a nominal 48 GB system. CPU
allocation is intentionally overcommitted because the workloads are bursty;
memory must not be overcommitted beyond this baseline until it is measured.

Use full VMs, not Docker on the Proxmox host and not nested Docker in LXC.
This costs a little RAM but gives clearer kernel, backup, firewall, and trust
boundaries. The guest baseline uses Debian 13 and distribution-packaged Docker
and Compose. Application container images still require immutable digests.

## Execution summary

| Order | Step | Exit gate |
| --- | --- | --- |
| 1 | Record hardware, update firmware, enable virtualization and power recovery, attach UPS | S0 hardware ready |
| 2 | Install Proxmox as `ai-services-01`, update it, enable MFA, restrict management | S0 hypervisor ready |
| 3 | Create `vmbr0` for LAN and `vmbr1` for the private compute link | S0 network ready |
| 4 | Configure off-host backup and capacity/SMART monitoring | S0 recovery ready |
| 5 | Run the guarded script to create the Debian template and three VMs | S1 guests created |
| 6 | Verify cloud-init, SSH, Docker, isolation, startup order, backup, and restore | S1 passed |
| 7 | Inventory and migrate the AI gateway with the old gateway retained | S2 passed |
| 8 | Migrate automations from low to high risk, eliminating duplicate schedules | S3 passed |
| 9 | Add restricted toolbox workloads and separately gated agents | S4 passed |
| 10 | Optionally migrate HAOS only after its own inventory and restore test | S5 passed |

## S0.1 — Physical preparation

Before installing:

1. Record the K15 serial number, current BIOS/firmware version, SSD model and
   firmware, RAM layout, and MAC addresses.
2. Update only to a GMKtec-supported stable BIOS and retain the old version and
   recovery instructions.
3. Enable Intel VT-x and VT-d/IOMMU in firmware.
4. Enable automatic power-on after AC loss. Disable decorative RGB if the
   firmware allows it.
5. Connect the machine to a UPS. Test that it shuts down cleanly and returns
   after power is restored.
6. Use the first 2.5GbE port for management and the trusted LAN. Reserve the
   second port for a direct, private link to `ai-compute-01`.

OCuLink is not networking and is not used in this design. The K15 vendor also
marks it as non-hot-pluggable, so any later OCuLink change happens only while
the machine is powered off.

## S0.2 — Proxmox installation

Install the current, supported Proxmox VE 9 ISO interactively. As of this
document's date the current x86 installer is 9.2; verify the release and ISO
SHA-256 again immediately before writing the installer. Do not automate disk
erasure or the management bridge.

For the supplied single 1 TB NVMe, use the installer default ext4/LVM-thin
layout. Single-disk ZFS provides checksumming but no device redundancy and adds
operational complexity. A later two-disk, same-size NVMe mirror is a valid
upgrade, but it does not replace an off-host backup.

During the installer:

- use `ai-services-01.home.arpa`, not a vendor-based name or IP literal;
- assign a reserved management address and correct LAN gateway/DNS;
- use `Europe/Copenhagen` and verify NTP after first boot;
- keep the host on wired Ethernet; Wi-Fi is not a hypervisor uplink;
- install only to the intended NVMe and retain a screenshot of the storage
  layout before confirming the destructive step.

After first boot:

1. Choose either the supported enterprise repository with a subscription or
   the official no-subscription repository. The repository script deliberately
   does not make this policy decision.
2. Apply Proxmox updates in a maintenance window and reboot if the kernel or
   microcode changed.
3. Create a named Proxmox administrator, enable MFA, and retain the root account
   only for recovery. Do not publish ports 22 or 8006 to the internet.
4. Enable the `Snippets` content type on the `local` storage.
5. Keep the Proxmox host free of Docker, application databases, Tailscale exit
   routing, development runtimes, and application repositories.

## S0.3 — Network design

`vmbr0` is the trusted LAN/management bridge backed by the first physical NIC.
It carries the Proxmox management IP and the first NIC of all three VMs.

`vmbr1` is backed by the second physical NIC and has no gateway. It connects
directly to the `ai-compute-01` private link. Only `ai-gateway-01` receives a
virtual NIC on this bridge:

| Endpoint | Address |
| --- | --- |
| `ai-gateway-01` private NIC | `10.77.10.2/24` |
| `ai-compute-01` private NIC | `10.77.10.10/24` |

Use a different subnet if it overlaps an existing LAN, VPN, or container
network. Do not bond the two physical ports: one 2.5GbE link is ample for LLM API
traffic and separating management from inference is more useful than aggregate
bandwidth.

Create `vmbr1` from the local console or Proxmox UI after confirming which
Realtek interface is the unused physical port. The conceptual configuration is:

```text
physical NIC 1 (manual) -> vmbr0 (static management address + only gateway)
physical NIC 2 (manual) -> vmbr1 (no host address and no gateway)
```

Never paste guessed interface names into `/etc/network/interfaces` over a
remote-only session. The provisioning script validates the bridges but does
not create or edit them.

When VLAN-capable switching/routing is available, place `toolbox-01` on a
restricted VLAN and set `TOOLBOX_VLAN_TAG`. Until then, apply both Proxmox VM
firewall rules and a guest firewall. The intended policy is:

- Proxmox SSH/8006: administrator network only;
- `ai-gateway-01` TCP 443: approved LAN/VPN consumers;
- `ai-gateway-01` to `ai-compute-01`: only qualified inference/audio ports;
- `automation-01`: no direct compute route; it calls `https://ai.home`;
- `toolbox-01`: no inbound service exposure, no household credentials, and
  allow-listed outbound access where practical;
- compute runtime ports: private link only, never ordinary client LAN or internet.

## S0.4 — Storage and backups

The 1 TB baseline is adequate for the three thin-provisioned VM disks, but
thin provisioning is not capacity. Alert at 75%, stop nonessential image/cache
growth at 85%, and freeze migrations at 90%.

Guest ownership rules:

- `ai-gateway-01`: only gateway-owned databases and metadata;
- `automation-01`: workflow databases, queues, agent state, and application
  configuration;
- `toolbox-01`: disposable build caches and repositories; no canonical data;
- `ai-compute-01`: models, runtime caches, and bounded telemetry only.

Back up to another physical system. A Proxmox Backup Server or NFS target on a
NAS is preferred; a backup VM or second SSD inside the same physical node is not an
off-host backup. Start with:

| Guest | Schedule | Retention |
| --- | --- | --- |
| `ai-gateway-01` | nightly | 7 daily, 4 weekly, 3 monthly |
| `automation-01` | nightly after application DB export | 7 daily, 4 weekly, 3 monthly |
| `toolbox-01` | weekly or rebuild from source | 2 weekly |

Keep backup encryption keys and a sanitized copy of `/etc/pve` outside the
services node. Perform one isolated restore before migrating a live service and repeat a
restore test at least quarterly. Snapshots are short-lived change checkpoints,
not backups.

## S1 — Provision and verify the empty guests

Copy this repository to the new Proxmox host, then:

```bash
sudo ./scripts/setup-services-node.sh init
sudoedit /etc/ai-platform/services-node.env

sudo ./scripts/setup-services-node.sh validate
sudo ./scripts/setup-services-node.sh preflight
sudo ./scripts/setup-services-node.sh host-packages
sudo ./scripts/setup-services-node.sh create-template
sudo ./scripts/setup-services-node.sh provision
sudo ./scripts/setup-services-node.sh status
```

Before `create-template`, download the Debian image's published `SHA512SUMS`
over HTTPS, verify its signed provenance when available, and put the exact
128-hex checksum in the environment file. The script rejects a checksum
mismatch and refuses to overwrite any existing template or VM ID.

The VMs are not started by default. Inspect each configuration first:

```bash
qm config 110
qm config 120
qm config 130
sudo ./scripts/setup-services-node.sh start
```

Cloud-init updates Debian, disables password/root SSH, installs the QEMU guest
agent, Docker, Compose, Git, curl, jq, rsync, and basic diagnostics, and applies
bounded Docker logs. The administrator is not placed in the `docker` group
because that group is root-equivalent; use `sudo docker ...`.

## Service installation and migration order

### Gate S0 — Hypervisor baseline

Pass when firmware/BIOS inventory exists; Proxmox is current; both bridges,
SMART, NTP, UPS behavior, MFA, and management firewall are verified; and no
critical hardware errors appear during a 24-hour host soak.

### Gate S1 — Empty guest baseline

Pass when all guests boot, cloud-init finishes, QEMU guest agent reports IPs,
SSH key login works, password login fails, Docker runs, time is synchronized,
the toolbox cannot reach the compute private address, and a backup/restore of one
empty guest succeeds.

### Gate S2 — AI gateway

Inventory the existing `ai_home` deployment before moving anything. Build
Caddy, LiteLLM, dedicated PostgreSQL/Redis, virtual consumer keys, pinned
images, metadata-only logs, and monitoring on `ai-gateway-01`. Qualify the direct
private `ai-compute-01` route first, then `https://ai.home`. Keep the old gateway
available for rollback until equivalence and recovery tests pass.

### Gate S3 — Automations

Export n8n workflows and credentials using supported methods, inventory every
scheduler, and take application-consistent database backups. Install n8n and
MCP services with separate identities/databases on `automation-01`, import a
sanitized test workflow, test retries/idempotency, then migrate one low-risk
workflow before any household-critical job.

Hermes/OpenShell, browser workers, and other agent runtimes are separate
sub-gates. They do not inherit n8n or Home Assistant credentials, and the model
never grants tool authority.

### Gate S4 — Toolbox

Install development frameworks only when a real workload needs them, preferably
as pinned containers or devcontainers. CI credentials are short-lived and
scoped. The toolbox may call `ai.home`; it does not receive direct compute access
or production automation databases.

### Gate S5 — Optional Home Assistant migration

Home Assistant stays where it is until the active HAOS, radio, add-on, backup,
and network topology is inventoried. If migration is later approved, create a
separate HAOS VM (suggested ID 140, 4 vCPU, 6 GB RAM, 64 GB disk) and pass
through only the required USB radio. This VM is intentionally absent from the
initial script.

## Recovery rules

- A Proxmox host failure is recovered by reinstalling the documented host,
  recreating bridges, restoring guests from off-host backup, and restoring
  backup encryption keys from their separate location.
- A bad guest change rolls back through an application rollback or a recent
  short-lived snapshot; database recovery uses tested exports/backups.
- An `ai-compute-01` outage leaves `ai-services-01` running. Private aliases
  fail closed; only explicitly public aliases may use configured cloud fallback.
- An `ai-services-01` outage leaves direct compute qualification possible, but
  production consumers lose the `ai.home` control plane. This is accepted for a
  single-node design and is not Proxmox HA.

## Primary references

- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf)
- [Current Proxmox VE downloads and ISO checksum](https://proxmox.com/en/downloads/proxmox-virtual-environment)
- [Proxmox network configuration](https://pve.proxmox.com/wiki/Network_Configuration)
- [GMKtec K15 specifications](https://www.gmktec.com/products/gmktec-k15-minipc-intel-core-ultra-5-125u)
- [NVIDIA DGX Spark hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [Debian 13 Docker Compose package](https://packages.debian.org/trixie/docker-compose)
