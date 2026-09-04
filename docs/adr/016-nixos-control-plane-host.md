# ADR-016: NixOS on the control-plane machine

## Context

ADR-014 proposed Proxmox and role-separated VMs for `home-core`. A later
working-tree design replaced that with an Ubuntu bootstrap. The project owner
has now selected a Git-first NixOS installation and requested that the Ubuntu
work be removed.

The trusted gateway still needs a small host boundary around Docker Compose,
persistent application state, secrets, networking, firewall policy, SSH,
Tailscale, and backups. User shell configuration should be reproducible without
letting a user-level tool own machine policy.

## Decision

Install NixOS 26.05 directly on `home-core`. The root `flake.nix` and
`flake.lock` are the only system activation entry point. NixOS modules own boot,
users, networking, firewall, Docker, SSH, Tailscale, storage, backups, and
sops-nix secret materialization.

Integrate Home Manager's matching `release-26.05` branch as a NixOS flake
module. It shares the system's pinned Nixpkgs input and configures only `mads`'
shell, Git client, tmux, aliases, CLI tools, environment variables, and
dotfiles. `nixos-rebuild` applies both system and home configuration; there is
no standalone Home Manager activation path.

Application workloads remain Docker Compose projects. Durable application data
lives below `/srv/state`; encrypted secrets are committed only as sops files
and materialized at runtime outside the Nix store.

`home-spark` remains on its vendor-supported DGX OS baseline. This decision
does not imply NixOS support for the GB10 appliance.

## Consequences

- Host and user configuration are pinned and reviewed with application changes
  in one repository.
- A new deployment requires the target age identity, an authorized SSH public
  key, and verified hardware module lists before remote activation.
- The key-only `mads` administrator uses passwordless sudo; its private key
  must be protected as a root-equivalent credential.
- Backup and control-plane activation stay disabled until real off-host and
  encrypted inputs replace the guarded defaults.
- Docker group membership is not granted to `mads`; Compose operations use
  `sudo` because Docker daemon access is root-equivalent.
- The old Ubuntu bootstrap, Proxmox provisioning script, cloud-init templates,
  and their active installation instructions are removed.

## Alternatives

- Ubuntu bootstrap scripts: rejected in favor of one declarative NixOS system
  activation graph.
- Standalone Home Manager: rejected because it creates a second activation and
  package-evaluation path.
- Separate NixOS repository: rejected while one owner changes host policy,
  Compose workloads, storage, and operations together.
- NixOS on `home-spark`: rejected until NVIDIA supports and qualifies that
  platform for the appliance.

## Status

Accepted. Supersedes ADR-014 for new `home-core` installation work and
replaces the uncommitted Ubuntu host design.

## Evidence

- `flake.nix`
- `hosts/home-core/`
- `modules/nixos/`
- `home/mads/`
- `docs/research/home-manager-nixos-26.05-integration.md`
