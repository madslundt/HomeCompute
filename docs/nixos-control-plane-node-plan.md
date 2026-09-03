# NixOS control-plane node plan

**Target:** `ai-services-01`, x86-64, NixOS 26.05

**Decision:** ADR-016

Use this plan for first installation. After installation, follow the
[NixOS operations guide](nixos-operations.md) for routine apply, rollback,
updates, and extensions.

## Configuration boundary

One flake rebuild owns the host and the `mads` home environment:

```text
flake.nix + flake.lock
  -> hosts/ai-services-01
     -> modules/nixos: system, network, firewall, Docker, SSH, Tailscale,
        storage, backups, sops-nix
     -> home/mads: Bash, Git, tmux, aliases, CLI tools, environment, dotfiles

deploy/control-plane/compose.yaml
  -> Caddy + LiteLLM + PostgreSQL
  -> /srv/state/control-plane
```

Home Manager does not declare users, login shells, groups, SSH authorization,
services, mounts, firewall rules, Docker, backups, or secrets. Compose does not
install or configure the host.

## Before installation

1. Verify the NixOS 26.05 installer checksum and boot it on the intended host.
2. Record firmware, disk identity, NIC names, MAC addresses, and the dedicated
   compute-link topology.
3. Partition as UEFI with an EFI filesystem labelled `ESP` and an ext4 root
   filesystem labelled `nixos`, matching `hardware-configuration.nix`.
4. Run `nixos-generate-config --root /mnt` and compare the detected initrd and
   kernel modules with the committed hardware module. Do not blindly replace
   the repository's host and storage policy.
5. Add `mads`' reviewed public SSH key to the host configuration. Never commit
   its private key or a password hash generated from a reusable password.
6. Create a persistent age identity, back it up offline, add its public
   recipient to `.sops.yaml`, and commit only an encrypted file such as
   `secrets/ai-services-01.sops.yaml`.
7. Configure the actual off-host Restic repository and password secret plus an
   application-consistent PostgreSQL dump/snapshot preparation command, then
   enable `homecompute.backups`.

## Build and install

From the checked-out, reviewed repository:

```bash
nix flake check
sudo nixos-rebuild build --flake .#ai-services-01
sudo nixos-install --flake .#ai-services-01
```

After reboot, check the exact generation and activate subsequent changes with:

```bash
sudo nixos-rebuild switch --flake .#ai-services-01
systemctl status home-manager-mads.service
```

Flakes ignore untracked imported files in a Git checkout. Stage new Nix modules
before building them, and commit `flake.lock` with every intentional input
update.

## Network and access rollout

The initial configuration uses DHCP so it is independent of unverified NIC
names. It exposes SSH and HTTPS only on `tailscale0`; SSH password and root
login are disabled. The `mads` wheel account uses passwordless sudo because it
has no reusable password; possession of its reviewed SSH key is therefore an
administrative credential. Use the local console for the initial Tailscale
enrollment.

Before assigning static LAN or private-compute addresses:

1. replace DHCP with explicit `systemd-networkd` configuration using observed
   interface names and addresses;
2. add only the required ingress to the NixOS firewall;
3. keep the compute link non-routed and allow only the LiteLLM path;
4. verify allowed and denied paths after every Docker or firewall change;
5. keep Compose bound to `127.0.0.1` until that verification passes.

## Secrets and workloads

Set `homecompute.secrets.defaultSopsFile` to the encrypted host file and enable
the module only after the age identity exists. sops-nix then materializes the
control-plane credentials under `/run/secrets/control-plane`. Secret values,
decrypted environment files, and age private keys never enter the repository or
Nix store.

Render Compose before starting it:

```bash
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml config --quiet
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml up -d
```

The environment file contains non-secret settings and secret file paths. The
containers write durable data only below `/srv/state/control-plane`.

## Acceptance gates

- `nix flake check` and `nixos-rebuild build` pass from a clean Git checkout.
- Reboot selects the expected generation and `home-manager-mads.service`
  succeeds.
- SSH accepts the reviewed key only through the intended Tailscale policy.
- No container publishes a wildcard host address; only Caddy publishes a port.
- All five control-plane secrets are sops-managed and absent from the Nix
  store, logs, and rendered Compose output.
- `/srv/state/control-plane` survives rebuild and workload recreation.
- An encrypted off-host backup and isolated restore of `/srv/state` pass before
  migration.
- The previous service remains available until gateway equivalence and rollback
  tests pass.
