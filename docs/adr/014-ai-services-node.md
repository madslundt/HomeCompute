# ADR-014: `home-core` as the Proxmox services node

> **Superseded by [ADR-016](016-nixos-control-plane-host.md).** This file is a
> historical decision record, not an installation path.

## Context

`home-spark` is ARM64/GB10, optimized for local AI inference, and intentionally
rebuildable. The platform also needs an always-on x86 host for the shared AI
control plane, automations, browser/tool execution, databases, and later
personal-agent sandboxes. Some of those tools have better x86 container and
package support, and their durable state must not share the compute-node failure and
rebuild boundary.

The initial `home-core` hardware has 48 GB RAM, one 1 TB NVMe, three M.2 slots, dual
2.5GbE, USB4, and OCuLink. It is consumer hardware without ECC, redundant power,
or an out-of-band management controller.

## Decision

Use `home-core` as a single-node Proxmox VE 9 host. Keep the hypervisor minimal and
run application workloads in three Debian 13 VMs and one Ubuntu 24.04 VM:

1. `ai-gateway-01` owns the Caddy/LiteLLM edge and is the only guest attached to a
   private, non-routed `home-spark` bridge.
2. `automation-01` owns n8n, MCP, browser workers, queues, and workflow state.
3. `agents-01` owns separately sandboxed personal-agent runtimes and
   principal/domain-partitioned agent state on a dedicated restricted VLAN.
4. `toolbox-01` owns rebuildable x86 development, CI, framework, and experimental
   tool workloads on the most restricted network.

Use the first physical NIC for the trusted LAN and Proxmox management. Use the
second as a dedicated 2.5GbE `home-spark` link. Do not use OCuLink or USB4 as the
machine-to-machine data path.

Use full VMs rather than installing Docker on Proxmox or nesting Docker in LXC.
Use ext4/LVM-thin on the supplied single SSD and require encrypted off-host
backups plus tested restores. Treat UPS, post-power-loss boot, SMART monitoring,
MFA, and management-network restriction as promotion gates.

## Alternatives

- Install Debian and Docker directly: simpler, but loses VM isolation,
  independent rollback, and clean future HAOS support.
- Run every service in one VM: lower overhead, but combines trusted gateway,
  durable automation, and untrusted tool workloads into one failure domain.
- Install Docker directly on Proxmox: rejected because application packages
  and networking would contaminate the hypervisor lifecycle.
- Use LXCs: efficient, but nested-container and kernel-sharing trade-offs are
  not worthwhile for this first security boundary.
- Use single-disk ZFS: checksumming is useful, but it provides no disk
  redundancy and complicates the 1 TB baseline. Revisit when adding matched
  NVMe devices.
- Replace the gateway boundary with direct client-to-compute access: rejected
  for production because consumer identity, policy, aliases, and explicit
  cloud fallback remain control-plane responsibilities.

## Consequences

The x86/ARM64 split improves tool compatibility and lets `home-spark` dedicate its
memory and CPU/GPU budget to inference. VM separation limits credentials and
blast radius, but consumes several GB of RAM and adds guest patching and backup
operations. The agent VM uses a fixed 16 GB allocation; the toolbox is stopped
by default so the 48 GB host retains safe hypervisor and cache headroom.

One `home-core` node is not high availability. Its failure stops the gateway and
automations even though `home-spark` may still run. Consumer hardware and a single
SSD make UPS, monitoring, off-host backup, restore drills, and retained old-host
rollback mandatory before moving critical workflows.

## Status

Superseded by ADR-016 after the owner selected a direct Git-first NixOS
control-plane installation.

## Evidence

- `docs/adr/015-personal-data-domains-and-memory.md`
- `docs/adr/001-gb10-inference-only.md`
- `docs/adr/011-reuse-ai-home-control-plane.md`
- `docs/adr/013-hermes-personal-agent-layer.md`
