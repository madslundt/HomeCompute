# NixOS operations and extension guide

This is the day-to-day guide for `ai-services-01`. The initial disk and host
installation remains in the [NixOS control-plane plan](nixos-control-plane-node-plan.md).

## Ownership rules

Put a change where it is activated and owned:

| Change | Location | Applied by |
| --- | --- | --- |
| Boot, users, login shell, packages needed for recovery, networking, firewall, Docker, SSH, Tailscale, storage, backups, secrets, system services | `modules/nixos/` or `hosts/<host>/` | `nixos-rebuild` |
| Interactive shell configuration, Git, tmux, aliases, user CLI tools, non-secret environment variables, user dotfiles | `home/<user>/` | Home Manager inside `nixos-rebuild` |
| Application containers, networks, limits, health checks, and runtime mounts | `deploy/<workload>/` | Docker Compose |
| Encrypted secret values | `secrets/*.sops.yaml` | sops-nix during NixOS activation |
| Durable application data | `/srv/state/<workload>` | Application, with NixOS provisioning and backup policy |

Do not add a standalone `homeConfigurations` output or run the standalone
`home-manager switch` command. The NixOS generation is the only system and
user activation unit.

## Prepare a change

Work from a clean checkout when possible. Imported files must be added to the
Git index before a Git-backed flake can see them:

```bash
git status --short
git add path/to/new-file.nix
./scripts/validate-repository.sh
git diff --check
git diff --cached --check
git diff --cached
```

Do not stage runtime environment files, decrypted secrets, age identities, or
anything below `/srv/state`. Commit `flake.lock` whenever an input revision is
changed intentionally.

## Apply a NixOS change

Run these commands from the repository checkout on `ai-services-01`. Build
before activation so evaluation or build failures cannot affect the running
generation:

```bash
git pull --ff-only
nix flake check
sudo nixos-rebuild build --flake .#ai-services-01
sudo nixos-rebuild test --flake .#ai-services-01
systemctl --failed
systemctl status home-manager-mads.service
sudo nixos-rebuild switch --flake .#ai-services-01
```

`test` activates the candidate without making it the boot default. Use the
console for changes that can disrupt SSH, Tailscale, firewall, storage, or
networking. For a risky change that should activate only at the next attended
reboot, use:

```bash
sudo nixos-rebuild boot --flake .#ai-services-01
sudo reboot
```

After switching, verify the affected service and the expected generation:

```bash
sudo nixos-rebuild list-generations
systemctl --failed
systemctl status home-manager-mads.service
```

Home Manager activation is part of the same generation. There is no separate
Home Manager apply step.

## Roll back a NixOS change

If the current session still has administrative access, switch to the previous
generation:

```bash
sudo nixos-rebuild switch --rollback
```

If networking or boot is broken, choose the previous NixOS generation from the
boot menu at the local console. A rollback changes declarative system and Home
Manager state together; it does not roll back application data in `/srv/state`
or Compose image/data migrations. Those require the workload's tested restore
procedure.

After recovery, revert or fix the Git change, validate it, and create a new
generation. Do not treat an imperative rollback as the final repository state.

## Apply a Compose workload change

NixOS provisions Docker, runtime secret files, firewall policy, and
`/srv/state`. It deliberately does not start application Compose projects.
Create a root-owned runtime environment file once, using the tracked example
as a starting point:

```bash
sudo install -d -m 0750 /etc/homecompute
sudo install -m 0600 config/control-plane.env.example \
  /etc/homecompute/control-plane.env
sudoedit /etc/homecompute/control-plane.env
```

Replace every placeholder with reviewed non-secret settings and immutable
image digests. Secret values stay in the sops-nix files referenced by the
environment file. Render and review before applying:

```bash
sudo docker compose \
  --env-file /etc/homecompute/control-plane.env \
  -f deploy/control-plane/compose.yaml config --quiet
sudo docker compose \
  --env-file /etc/homecompute/control-plane.env \
  -f deploy/control-plane/compose.yaml up -d
sudo docker compose \
  --env-file /etc/homecompute/control-plane.env \
  -f deploy/control-plane/compose.yaml ps
```

Use the same explicit files for `logs`, `down`, and later updates. Back up and
restore-test application state before an image or schema migration.

## Extend the existing host

For a new system responsibility:

1. Add a focused file under `modules/nixos/`, named for the responsibility.
2. Import it from `hosts/ai-services-01/default.nix`.
3. Keep host-specific values in the host module and reusable policy in the
   focused module.
4. Add an assertion when activation without a site-specific value would be
   unsafe.
5. Extend `scripts/validate-repository.sh` when the boundary or invariant can
   be checked statically.

For a new user-level capability:

1. Add it to the closest file under `home/mads/`.
2. Create and import another focused Home Manager file only when it represents
   a stable responsibility; keep trivial settings in `default.nix`.
3. Keep secrets, system services, groups, login-shell selection, mounts, and
   privileged operations out of Home Manager.

For a new Compose workload:

1. Create `deploy/<workload>/compose.yaml` and a non-secret example under
   `config/`.
2. Provision its durable directories below `/srv/state/<workload>` in the
   NixOS storage module.
3. Declare its runtime secrets in the sops-nix module.
4. Add rendering and security-boundary checks to repository validation.
5. Document backup consistency, restore, health, update, and rollback before
   migration.

## Add another NixOS host or user

Add a host under `hosts/<hostname>/` with its generated and reviewed hardware
configuration, then add another `nixosConfigurations.<hostname>` entry in
`flake.nix`. Keep the flake explicit while there is only one host; extract a
small host constructor only after a second host reveals genuinely shared
wiring.

NixOS must declare every login user. If another user's environment should be
managed, add `home/<user>/`, declare `users.users.<user>` in NixOS, and add
`home-manager.users.<user>` to the embedded module configuration. Do not add a
standalone Home Manager output.

## Add encrypted secrets and backups

Create the host age identity outside Git, back it up offline, and configure its
public recipient in `.sops.yaml`. Commit only encrypted files matching
`secrets/*.sops.yaml`. Then set the host's encrypted file and enable secrets:

```nix
homecompute.secrets = {
  enable = true;
  defaultSopsFile = ../../secrets/ai-services-01.sops.yaml;
};
```

Enable backups only after configuring an off-host Restic repository, the
sops-managed password path, and an application-consistent preparation command.
The configuration intentionally fails with an assertion if any of these are
missing. A timer running successfully is not acceptance; complete an isolated
restore test.

## Update pinned inputs

Update one dependency at a time when practical:

```bash
nix flake update nixpkgs
nix flake update home-manager
nix flake update sops-nix
git diff -- flake.lock
./scripts/validate-repository.sh
sudo nixos-rebuild build --flake .#ai-services-01
```

Keep Nixpkgs and Home Manager on matching release branches. Read their release
notes before changing branches. Do not change `system.stateVersion` or
`home.stateVersion` merely because the inputs were updated; those versions
preserve migration compatibility.

Apply the candidate with `test`, verify it, then use `switch`. Retain the prior
bootable generation until the new one has passed the required soak and recovery
checks.
