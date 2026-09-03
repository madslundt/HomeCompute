# Home Manager integration for NixOS 26.05

Verified: 2026-09-03

## Recommendation

Use Home Manager only as a module inside the existing `nixosConfigurations`
definition. For this repository, the requested boundary is sound:

- NixOS owns the machine: users, login shell selection, networking, firewall,
  Docker, SSH server and authorized keys, Tailscale, storage, backups, sops-nix
  secret materialization, system services, and `/srv/state`.
- Home Manager owns `mads`' interactive environment: shell startup files, Git
  client configuration, tmux, aliases, non-privileged CLI packages, non-secret
  environment variables, and dotfiles below `/home/mads`.
- Docker Compose owns application workloads and their application-level
  configuration. It should consume runtime secrets and persistent paths
  provisioned by the system layer; Home Manager should not operate Compose
  workloads or `/srv/state`.

This is simpler than maintaining a second standalone Home Manager output and
activation workflow. Do not add a parallel `homeConfigurations.mads` output
unless independent home activation becomes an explicit requirement: it would
create a second entry point that can drift from the configuration applied by
`nixos-rebuild`. The Home Manager manual explicitly says that the NixOS-module
form builds user profiles with the system under `nixos-rebuild` and shows
`home-manager.nixosModules.home-manager` inside `nixosSystem.modules`.
[Home Manager: NixOS flake module](https://nix-community.github.io/home-manager/nix-flakes/nixos.html)

## Compatible inputs and module wiring

As of the verification date, the official
[`release-26.05` Home Manager branch](https://github.com/nix-community/home-manager/tree/release-26.05)
and [`nixos-26.05` Nixpkgs branch](https://github.com/NixOS/nixpkgs/tree/nixos-26.05)
both exist. Home Manager's release branch identifies itself as release 26.05
and its own flake targets `github:NixOS/nixpkgs/nixos-26.05`.
[Home Manager 26.05 flake source](https://github.com/nix-community/home-manager/blob/release-26.05/flake.nix)

Use this input shape:

```nix
inputs = {
  nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  home-manager = {
    url = "github:nix-community/home-manager/release-26.05";
    inputs.nixpkgs.follows = "nixpkgs";
  };
};
```

The `follows` assignment avoids a second Nixpkgs input and gives Home Manager
the same pinned Nixpkgs source revision. It does **not** by itself give Home
Manager the same evaluated `pkgs` value; `home-manager.useGlobalPkgs = true`
does that. With `useGlobalPkgs`, overlays and `nixpkgs.config` belong at the
NixOS system level because per-user Home Manager `nixpkgs.*` options are
disabled. [Home Manager: Nix flakes](https://nix-community.github.io/home-manager/nix-flakes.html)

The corresponding system-module shape is:

```nix
modules = [
  # Existing host and NixOS modules...
  home-manager.nixosModules.home-manager
  {
    home-manager = {
      useGlobalPkgs = true;
      useUserPackages = true;
      users.mads = ./home/mads;
    };
  }
];
```

`useUserPackages = true` installs the generated Home Manager environment via
`users.users.mads.packages`, placing its profile under
`/etc/profiles/per-user/mads`; it does not make those packages global. The
official NixOS flake template enables it, and the manual notes that it is
needed for configurations used with `nixos-rebuild build-vm`. Keeping it on is
a good fit for a system-coupled, Git-first server. If it is left false, user
packages instead live in the user's own profile; system-coupled activation
still works, but the profile location and session-variable path differ.
[Official NixOS flake template](https://github.com/nix-community/home-manager/blob/release-26.05/templates/nixos/flake.nix),
[NixOS module installation notes](https://github.com/nix-community/home-manager/blob/release-26.05/docs/manual/installation/nixos.md),
[NixOS Home Manager options](https://nix-community.github.io/home-manager/options/nixos/home-manager.html)

Keep the default `home-manager.enableLegacyProfileManagement = false`. The
option documentation says that the legacy standalone-style per-user profile
is typically undesirable when Home Manager is embedded in the system.
[NixOS Home Manager options](https://nix-community.github.io/home-manager/options/nixos/home-manager.html)

Do not set `programs.home-manager.enable = true` merely out of habit from a
standalone `home.nix`. In an embedded submodule, Home Manager's source
deliberately suppresses installing the standalone command, so the setting does
not provide another supported activation path.
[Home Manager program module source](https://github.com/nix-community/home-manager/blob/release-26.05/modules/programs/home-manager.nix)

## User module layout

Use one small aggregation module and focused leaf modules:

```text
home/mads/
  default.nix
  shell.nix
  git.nix
  tmux.nix
```

`default.nix` should import the three leaf modules and contain the small amount
of truly cross-cutting user configuration: `home.stateVersion`, CLI packages,
non-secret `home.sessionVariables`, and any simple `home.file` or
`xdg.configFile` declarations. Keep aliases with the shell that interprets
them. Do not create a generic shared Home Manager layer for one user; the
official `home-manager.sharedModules` mechanism is useful only for settings
that genuinely apply to every configured Home Manager user.
[Home Manager NixOS-module options](https://nix-community.github.io/home-manager/options/nixos/home-manager.html)

In the NixOS-module form, Home Manager derives `home.username`,
`home.homeDirectory`, and UID from `users.users.mads`. Prefer that system user
declaration as the single source of truth instead of repeating the values in
`home/mads/default.nix`.
[Home Manager NixOS module source](https://github.com/nix-community/home-manager/blob/release-26.05/nixos/common.nix),
[Home options](https://nix-community.github.io/home-manager/options/home-manager/home.html)

This four-file split is reasonable because the boundaries are stable and each
leaf uses a distinct Home Manager program module. If any leaf remains only a
few trivial lines, merging it back into `default.nix` would be more
maintainable than introducing deeper `modules/`, `profiles/`, or
`shared/` hierarchies prematurely. The Home Manager project itself recommends
starting with a small, simple configuration and growing it gradually.
[Home Manager README](https://github.com/nix-community/home-manager#words-of-warning)

## State versions

For a Home Manager configuration first introduced on 26.05, set:

```nix
home.stateVersion = "26.05";
```

This is distinct from `system.stateVersion`. It selects compatibility defaults
for Home Manager-managed state and should remain at the release first used for
this home configuration. Updating the Home Manager input later is not a reason
to change it; only change it after reading the intervening release notes and
performing any required migration.
[Home Manager upgrade guidance](https://nix-community.github.io/home-manager/usage/upgrading.html),
[`home.stateVersion` option](https://nix-community.github.io/home-manager/options/home-manager/home.html#home.stateVersion)

## Existing dotfiles and activation safety

The safest steady-state behavior is Home Manager's default: abort activation
when an unmanaged file would be overwritten. Before the first switch, inspect
the existing shell, Git, and tmux files, encode wanted content in Git, and move
the unmanaged originals aside deliberately.

If automated first-time migration is required, configure the NixOS-level
option, not standalone CLI flags:

```nix
home-manager.backupFileExtension = "hm-backup";
```

This moves a colliding unmanaged file to the same name with that extension.
Activation aborts if that backup already exists. A `backupCommand` is the
alternative when a command should handle each colliding path; when both are
configured, the command wins. Avoid `home-manager.overwriteBackup = true`
because it permits clobbering existing backups, and use per-file `force = true`
only after confirming that deletion is safe. Prefer treating a backup setting
as an explicit migration policy, then remove it once every managed target is
clean.
[Home Manager: resolving file collisions](https://nix-community.github.io/home-manager/usage/dotfiles.html#sec-file-conflicts)

## Activation and reproducibility

With `home-manager.nixosModules.home-manager` and
`home-manager.users.mads = ./home/mads`, a normal system switch applies both
the NixOS and user configurations:

```console
sudo nixos-rebuild switch --flake .#HOSTNAME
```

Home Manager activates configured users through
`home-manager-mads.service` during rebuild and boot by default. Keep
`home-manager.startAsUserService = false` unless the home directory is
unavailable before login (for example, with `pam_mount`); the alternative mode
is documented as experimental for other uses.
[Home Manager NixOS installation](https://github.com/nix-community/home-manager/blob/release-26.05/docs/manual/installation/nixos.md),
[`home-manager.startAsUserService`](https://nix-community.github.io/home-manager/options/nixos/home-manager.html#home-manager.startAsUserService)

Commit `flake.lock` together with `flake.nix`: the lock file records a locked
revision for each input, while `follows` makes Home Manager share the top-level
Nixpkgs lock node.
[Nix reference: `nix flake lock`](https://releases.nixos.org/nix/nix-2.34.8/manual/command-ref/new-cli/nix3-flake-lock.html)

Also add every imported `.nix` and dotfile source to Git before evaluating the
flake. A Git-backed local flake sees tracked files, so an untracked newly added
module can appear as a missing path during `nixos-rebuild`.
[Official NixOS Wiki: `nixos-rebuild` with flakes](https://wiki.nixos.org/wiki/Nixos-rebuild#With_Flakes)

Recommended rollout checks are:

```console
nix flake check
sudo nixos-rebuild build --flake .#HOSTNAME
sudo nixos-rebuild switch --flake .#HOSTNAME
systemctl status home-manager-mads.service
```

## Scope guardrails and pitfalls

- `home.packages` is for tools used by `mads`. Recovery and administration
  tools that must exist without that user's profile stay in NixOS
  `environment.systemPackages`.
- Home Manager may generate the user's shell rc files, but NixOS still declares
  the user, groups, login shell, and any machine-wide shell integration.
- Keep `services.openssh`, authorized keys, firewall ports, Docker daemon and
  group membership, Tailscale, mounts, backup timers, and sops-nix ownership or
  permissions in NixOS modules.
- Never place secret values in `home.sessionVariables`, generated dotfile text,
  or flake arguments: evaluated/generated content can enter the world-readable
  Nix store. Let sops-nix materialize runtime files with system-declared owner
  and mode, and have user tools read those runtime paths when necessary.
  [Nix reference: secrets](https://releases.nixos.org/nix/nix-2.33.1/manual/store/secrets.html),
  [sops-nix](https://github.com/Mic92/sops-nix#how-it-works)
- Keep Compose manifests and `/srv/state` lifecycle outside Home Manager. A
  Home Manager activation must remain safe even if application workloads are
  stopped or application state is absent.
- `home-manager.extraSpecialArgs` is the official mechanism when Home Manager
  modules truly need flake-level values. Do not pass the complete `inputs`
  attrset by default when no home module consumes it; narrower arguments make
  dependencies clearer.
  [Home Manager: NixOS flake module](https://nix-community.github.io/home-manager/nix-flakes/nixos.html)

## Design verdict

The requested architecture should not be replaced with standalone Home
Manager. Its main simplification opportunity is to keep exactly one activation
graph and one source of user identity: `nixos-rebuild` owns activation,
`users.users.mads` owns account facts, and `home/mads/default.nix` owns only the
user environment. The proposed four Home Manager files are a sensible maximum
for the initial scope; expand the tree only when actual reusable or
host-specific user policy appears.
