# Hermes live deployment readiness audit

**Observed:** 2026-09-26 22:52–22:56 CEST
**Scope:** read-only checks of `home-core`, `home-spark`, the existing
LiteLLM/Caddy path, and the repository configuration. Two minimal inference
requests were made to confirm tool-calling behavior. No service, package,
secret, firewall rule, or household application data was created or changed;
the probes may have added routine service logs or counters.

## Decision

The host can support the first Hermes pilot, but the household deployment is
**not yet safe to activate**. Hardware virtualization, current capacity, the
private CA, and one 64K tool-capable inference route are present. The hard
gates that remain are:

1. configure encrypted off-host backup and complete an isolated restore;
2. install and declaratively configure the QEMU/KVM runtime and guest network;
3. add an exact firewall grant from that guest network to Caddy only;
4. create a dedicated LiteLLM virtual key and working assistant canary alias;
5. resolve the host memory conflict between the proposed guest and the 28 GiB
   CPU automation standby; and
6. deploy from a reviewed commit rather than the older commit currently active
   on `home-core`.

The maximum safe production activation before these gates close is an **inert
virtualization substrate**: QEMU/KVM packages and service configuration with no
guest autostart, no Hermes credentials, no personal data, and no persistent
Hermes state. Repository implementation, flake evaluation, and closure builds
are safe to continue now. Do not create the production agents guest or any
household sandbox yet.

## Evidence summary

| Area | Live observation | Readiness |
|---|---|---|
| SSH | `ssh home-core` and `ssh home-spark` both succeeded with the configured keys. `mads` has non-interactive sudo on `home-core`; the `home-spark` account does not. | Pass |
| NixOS | `home-core` runs NixOS `26.05.20260901.a311611` on Linux `6.18.48`; systemd reports `running`. | Pass |
| CPU virtualization | `/dev/kvm` exists; CPU exposes `vmx`; `kvm` and `kvm_intel` are loaded. | Pass |
| Virtualization runtime | No `qemu-system-x86_64`, `qemu-kvm`, `virsh`, `virt-install`, `cloud-localds`, `libvirtd`, or `virtqemud` was found. No guest bridge exists. | Blocked |
| `home-core` capacity | 14 logical CPUs; 46 GiB RAM, 38 GiB available; no swap; 805 GiB free on `/`; low current load. | Pass with budgeting caveat |
| `home-spark` capacity | 20 logical CPUs; 121 GiB unified memory, 45 GiB available; 8.8 GiB of 15 GiB swap used; 646 GiB disk free. | Pass for current route only |
| Backups | No Restic unit or timer, no Restic secret, no off-host backup mount, and `/srv/state/control-plane/backups` is empty. Repository config explicitly has `homecompute.backups.enable = false`. | Hard block |
| Control plane | Pinned Caddy 2.11.4, LiteLLM 1.99.1, and PostgreSQL 16.15 containers are healthy. | Pass |
| Gateway auth | `https://ai.home.arpa/healthz` returned 200 with CA verification; unauthenticated `/v1/models` returned 401; authenticated `/v1/models` returned 200. | Pass |
| Assistant alias | The alias is advertised, but a minimal completion timed out after 20 seconds with no response. No local port `18000` listener exists. | Hard block for `assistant` |
| 64K canary route | `automation-moe` returned a required `get_temperature` function call through both `/v1/chat/completions` and `/v1/responses`. | Pass as a synthetic canary only |
| Guest-to-gateway path | The CA is ready, but the present `HC-CADDY-LAN` chain does not permit a future VM bridge. | Blocked |
| Deployment provenance | `/srv/homecompute/current` is commit `aaa2e1b83e9fc67c43b2e29268d8769712099067`; local HEAD `7ac719989cb3eb3bb5a3b876de054dda0b57d7ca` is three commits ahead. | Review before switch |

## Virtualization and capacity details

`home-core` has the hardware and kernel side of KVM ready:

```text
/dev/kvm: crw-rw-rw- root:kvm
CPU virtualization flag: vmx
modules: kvm_intel, kvm
```

The NixOS source already loads `kvm-intel`, but it does not enable a
virtual-machine manager. The live host has only physical, Tailscale, and Docker
bridges. A dedicated agents bridge and deterministic addressing therefore need
to be designed and deployed declaratively.

At the observed load, a 4-vCPU/16-GiB pilot VM fits. It cannot be budgeted in
isolation, however. The existing CPU automation fallback is configured for a
28-GiB memory limit, while the host has 46 GiB total and no swap. Starting that
standby while a 16-GiB agents guest is resident could exceed safe host headroom
once the control plane and other services are included. Before guest autostart,
choose and enforce one of:

- make the agents VM and 28-GiB standby mutually exclusive;
- reduce one or both reservations after measured qualification; or
- move/remove the standby requirement.

This must be an enforced lifecycle rule, not an operator convention.

## Backup and restore gate

All live evidence agrees with the repository configuration that backup is not
implemented:

- `homecompute.backups.enable = false`;
- no `restic-backups-*` unit or timer exists;
- `/run/secrets/restic/password` is absent;
- there is no off-host/NFS/CIFS backup mount; and
- `/srv/state/control-plane/backups` contains no artifact.

An empty VM or inert hypervisor package can be staged without a data backup.
The production guest, OpenShell state, Hermes `state.db`, user memory, sandbox
snapshots, and provider/messaging credentials must not be created until an
encrypted off-host backup has run and an isolated restore has succeeded.

## Gateway, CA, and inference evidence

The workstation copy of `artifacts/home-core-root.crt` exactly matches the live
Caddy root certificate file:

```text
file SHA-256: 74525679f0876ba929c26a2b8b83bb639c68c176a30c641917c0175d8d11487b
certificate SHA-256 fingerprint:
27:9C:BA:A0:59:6A:E3:CA:24:C4:4D:A2:8A:C6:58:C5:57:E3:C2:A8:66:4F:81:D8:D3:C8:D0:34:C0:75:3A:56
valid: 2026-09-04 through 2036-07-13
```

TLS verification against `ai.home.arpa` succeeded from both the workstation
and `home-core`. The authenticated model list currently advertises:

```text
assistant, auto, automation, automation-moe, coding, home, meeting, research
```

Advertising is not health. The `assistant` request received zero bytes and
timed out after 20 seconds. The generic text relay port is absent while only the
automation and Home Assistant relays (`18005`, `18006`) listen.

The active `automation-moe` route is suitable for the **synthetic owner canary**:

- model: `unsloth/Qwen3.6-35B-A3B-NVFP4`;
- pinned revision: `739af1e7aac320af1682ed1e0cce369af4c5265d`;
- context: 65,536;
- concurrency: 4 sequences;
- batched tokens: 8,192;
- reasoning parser: `qwen3`;
- tool parser: `qwen3_coder`; and
- required function calls passed via both supported gateway APIs.

This does not authorize reusing the automation consumer credential for Hermes.
Provision a separate LiteLLM virtual key restricted to a dedicated canary alias,
with its own budget and revocation path. Do not provide the LiteLLM master key
to the guest.

## Network policy gap

The gateway is correctly exposed through Caddy rather than exposing LiteLLM or
the compute node directly. The current LAN ingress chain permits only:

```text
192.168.10.0/24 via enp44s0
192.168.30.30/32 via enp44s0
172.28.201.2/32 via br-hc-n8n
tailscale0
```

It rejects all other sources. Consequently a new host-only agents bridge would
not be able to call `192.168.30.122:443` without an explicit rule. Add an exact
source subnet/address and destination-port rule for the agents guest. Do not
give the guest a route to `10.77.10.0/24` or to the Docker control-plane bridge.
Install the current Caddy root CA in the guest and keep LiteLLM credentials in
OpenShell's credential provider.

## Safe deployment sequence from this state

1. Finish the NixOS virtualization, resource-budget, and firewall design in the
   repository. Evaluate and build it without activation.
2. Configure encrypted off-host Restic storage for the existing host and prove
   an isolated restore of application-consistent state.
3. Activate only the inert virtualization substrate with no guest autostart,
   then verify SSH, Caddy, LiteLLM, n8n, speech relays, and rollback.
4. Create the agents bridge with deny-by-default policy and only the reviewed
   DNS/NTP/package-source and `ai.home.arpa:443` paths needed for bootstrap.
5. Create a dedicated `assistant-canary` alias and virtual key; repeat both
   tool-call tests without the master key.
6. Create the checksum-pinned Ubuntu guest with synthetic data only. Keep all
   external messaging and household data disabled.
7. Install the pinned NemoClaw release tuple, create only the owner canary
   sandbox, and pass isolation, reboot, snapshot, restore, outage, and
   credential-revocation tests.
8. Add real household users one at a time only after the pilot gates pass.

## Audit command classes

The audit used only SSH inventory commands (`hostname`, `nixos-version`,
`lsmod`, `free`, `df`, `systemctl`, `ss`, `ip`, `iptables-save`, `docker ps`),
certificate hashes/validation, HTTP health/auth probes, and two minimal
tool-forcing inference requests. Secret files were read only inside root-owned
remote shells to construct Authorization headers; secret values were never
printed or copied.
