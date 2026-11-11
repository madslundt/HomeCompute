# Craft Anything toolbox

The toolbox is a standalone Ubuntu KVM guest on Home Core. It keeps Craft
Anything's experimental project execution away from the household Hermes and
OpenBot guests. It has a separate virtual disk, operator SSH identity, host-only
bridge, default-deny egress policy, and off-host recovery coverage.

The upstream project was at early alpha v0.6.0 when this pin was reviewed. The
Homepage card opens its native desktop app through noVNC at
`https://craft-anything.home.arpa/`. Caddy serves it only to approved LAN and
tailnet clients. The VNC password is required to control the desktop.

## Access and setup

Connect through Home Core, which is the only route to the guest:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 \
  -J home-core toolbox-operator@10.77.30.2
```

From a HomeCompute checkout in the guest, install the pinned CLI and desktop
UI. Copy these source files from the selected published release checkout:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 \
  -J home-core toolbox-operator@10.77.30.2 'mkdir -p ~/HomeCompute/config ~/HomeCompute/scripts'
scp -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 -o ProxyJump=home-core \
  config/craft-anything-toolbox.json toolbox-operator@10.77.30.2:HomeCompute/config/
scp -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 -o ProxyJump=home-core \
  scripts/install-craft-anything-toolbox.sh toolbox-operator@10.77.30.2:HomeCompute/scripts/
scp -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 -o ProxyJump=home-core \
  scripts/install-craft-anything-desktop.sh toolbox-operator@10.77.30.2:HomeCompute/scripts/
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 \
  -J home-core toolbox-operator@10.77.30.2
sudo apt-get update
sudo apt-get install -y curl jq
cd ~/HomeCompute
sudo ./scripts/install-craft-anything-toolbox.sh
# This installs the declared desktop packages and configures its systemd units.
sudo ./scripts/install-craft-anything-desktop.sh
craft-anything-cli doctor
craft-anything-cli mcp
systemctl status craft-anything-desktop.service craft-anything-websockify.service
```

Use the Homepage **Craft Anything** card to open the desktop. Its VNC password
is generated once and retained in guest state; retrieve it over the pinned SSH
route when needed:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519_ai-services-01 \
  -J home-core toolbox-operator@10.77.30.2 \
  'sudo cat /var/lib/craft-anything-desktop/vnc.password'
```

The desktop project directory is `~/Documents`. The password protects VNC
control; Caddy serves the browser bridge only over private HTTPS and the guest
VNC listener remains loopback-only. Do not expose port 6080 or 5900 on a public
interface. To rotate the VNC password, stop its unit, remove the two generated
password files, and rerun the installer to create a replacement:

```bash
sudo systemctl stop craft-anything-vnc.service
sudo rm /var/lib/craft-anything-desktop/vnc.password /var/lib/craft-anything-desktop/vnc.pass
sudo ./scripts/install-craft-anything-desktop.sh
```

Use the Craft Anything clean-room project defaults. Keep projects synthetic or
public, review approval prompts, and do not add Home Assistant actions, private
household files, SSH agent forwarding, Docker access, or credentials. The guest
has public TCP 80/443 egress only; it has no route to LAN, Tailscale, Home Assistant,
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

Only the control-plane Caddy edge (`172.28.200.2`) may initiate TCP 6080 to the
guest. That exact destination and source are allowed by the NixOS toolbox
firewall; other new inbound connections remain denied. Caddy's
`craft-anything.home.arpa` route is limited to approved LAN and tailnet source
ranges and forwards WebSocket traffic to the guest's noVNC listener. The
private DNS record is declared in
[`config/craft-anything-dns.json`](../../config/craft-anything-dns.json); apply
it to the existing UniFi private DNS and verify it with
`python3 scripts/verify-craft-anything-dns.py --check` from each client network.
Do not create public DNS, a port forward, or Tailscale Funnel.

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
