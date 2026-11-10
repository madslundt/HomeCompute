# Craft Anything toolbox

The toolbox is a standalone Ubuntu KVM guest on Home Core. It keeps Craft
Anything's experimental project execution away from the household Hermes and
OpenBot guests. It has a separate virtual disk, operator SSH identity, host-only
bridge, default-deny egress policy, and off-host recovery coverage.

The upstream project was at early alpha v0.6.0 when this pin was reviewed. The
Homepage card links to the upstream project; Craft Anything does not expose a
HomeCompute web dashboard.

## Access and setup

Connect through Home Core, which is the only route to the guest:

```bash
ssh -J mads@home-core toolbox-operator@10.77.30.2
```

From a HomeCompute checkout in the guest, install the pinned CLI (the headless
guest does not install the desktop UI). Copy these two source files from the
published release checkout:

```bash
ssh -J mads@home-core toolbox-operator@10.77.30.2 'mkdir -p ~/HomeCompute/config ~/HomeCompute/scripts'
scp -o ProxyJump=mads@home-core config/craft-anything-toolbox.json toolbox-operator@10.77.30.2:HomeCompute/config/
scp -o ProxyJump=mads@home-core scripts/install-craft-anything-toolbox.sh toolbox-operator@10.77.30.2:HomeCompute/scripts/
ssh -J mads@home-core toolbox-operator@10.77.30.2
sudo apt-get update
sudo apt-get install -y curl jq
cd ~/HomeCompute
sudo ./scripts/install-craft-anything-toolbox.sh
craft-anything-cli doctor
craft-anything-cli mcp
```

Use the Craft Anything clean-room project defaults. Keep projects synthetic or
public, review approval prompts, and do not add Home Assistant actions, private
household files, SSH agent forwarding, Docker access, or credentials. The guest
has public TCP 80/443 only; it has no route to LAN, Tailscale, Home Assistant,
the private compute network, or the host's application state.

Hermes remains separately managed in the agents guest. Do not copy the toolbox
MCP server into the household Hermes profile; that would place arbitrary
project-execution tools beside household capabilities. If Hermes is added to
this stack later, install and run it inside this same guest and connect through
the local stdio MCP process with a reviewed tool allowlist. Do not expose a
network MCP listener or connect a household Hermes instance.

## Lifecycle

The host owns the guest through `homecompute-toolbox-vm.service`. The NixOS
module pins the Ubuntu cloud image, allocates 4 vCPU, 8 GiB RAM, and a 100 GiB
grow-only qcow2 disk. Cloud-init installs only the reviewed operator SSH key.
State lives at `/srv/state/toolbox-vm`.

The NAS application recovery archive stops the guest, archives its disk and
immutable boot seed, and records the seed hash in the manifest. Verify the
recovery archive before relying on it. The recovery verifier checks qcow2
structure, not a booted restore; restore boot remains a separate disaster
recovery exercise.

Inspect state with:

```bash
systemctl status homecompute-toolbox-vm.service
ip addr show br-hc-toolbox
iptables -S HC-TOOLBOX-EGRESS
```

Rollback by selecting the prior published HomeCompute revision and rebuilding
Home Core. Preserve `/srv/state/toolbox-vm` unless intentionally retiring the
guest; a code rollback does not roll back guest data.
