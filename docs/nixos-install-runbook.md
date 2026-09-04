# NixOS installation runbook

**Target:** `ai-services-01`, GMKtec K15, x86-64, NixOS 26.05

This is the bare-metal procedure for a fresh install. It implements the
contract in the [control-plane plan](nixos-control-plane-node-plan.md); read
that first for the configuration boundary and acceptance gates. After the host
is reachable, switch to the [operations guide](nixos-operations.md).

## 1. Firmware

Set these before booting the installer:

| Setting | Value | Reason |
| --- | --- | --- |
| Secure Boot | Disabled | Stock NixOS does not sign its bootloader |
| Boot mode | UEFI, CSM disabled | `systemd-boot` with `canTouchEfiVariables` |
| Restore on AC power loss | Enabled | Always-on node; a promotion gate in ADR-014 |

Record firmware version, disk identity, both NIC names, and their MAC
addresses. Note which of the two 2.5GbE ports is cabled; the dedicated compute
link depends on telling them apart later.

## 2. Partition and mount

Boot the NixOS 26.05 minimal ISO after verifying its checksum. Confirm the
disk rather than assuming a device name:

```bash
lsblk -o NAME,SIZE,MODEL
```

`hardware-configuration.nix` mounts by label, so `ESP` and `nixos` must match
exactly. FAT labels are uppercase.

```bash
parted /dev/nvme0n1 -- mklabel gpt
parted /dev/nvme0n1 -- mkpart ESP fat32 1MiB 2GiB
parted /dev/nvme0n1 -- set 1 esp on
parted /dev/nvme0n1 -- mkpart root ext4 2GiB 100%
mkfs.fat -F32 -n ESP /dev/nvme0n1p1
mkfs.ext4 -L nixos /dev/nvme0n1p2
mount /dev/disk/by-label/nixos /mnt
mkdir -p /mnt/boot
mount -o umask=077 /dev/disk/by-label/ESP /mnt/boot
```

The ESP is 2 GiB because `systemd-boot` keeps a kernel and initrd per
generation, at roughly 100 MB each. `boot.loader.systemd-boot.configurationLimit`
bounds that at ten entries; 2 GiB leaves headroom above the resulting ~1 GB. A
512 MiB ESP fills up and then blocks the rebuild that would have fixed it.

## 3. Reconcile the detected hardware configuration

Print the detected configuration instead of writing it. Plain
`nixos-generate-config --root /mnt` overwrites files under `/mnt/etc/nixos`:

```bash
nixos-generate-config --root /mnt --show-hardware-config > /tmp/detected.nix
```

Compare `boot.initrd.availableKernelModules` and `boot.initrd.kernelModules`
with the committed host module and merge **only** those lists. The detected
output identifies filesystems by UUID; this repository uses labels
deliberately, so do not copy its `fileSystems` block or replace the file
wholesale.

Any difference must reach Git before the build, because a Git-backed flake
cannot see uncommitted work. Prefer editing on the workstation and pushing, so
the permanent checkout and the installed generation agree.

## 4. Build, then install

```bash
nix-shell -p git    # only if git is absent from the ISO
git clone https://github.com/madslundt/HomeCompute /tmp/HomeCompute
cd /tmp/HomeCompute
git status --short
nix --extra-experimental-features 'nix-command flakes' flake check
nixos-rebuild build --flake .#ai-services-01
```

Flakes ignore untracked imported files. If `git status` lists a modified or
new Nix file, stage it; otherwise the build silently uses the committed
version. Build before installing so an evaluation or build failure surfaces
before the disk is written.

```bash
nixos-install --flake /tmp/HomeCompute#ai-services-01
```

`nixos-install` prompts for a **root** password. Set one you can retrieve; the
next step needs it.

## 5. First console session

`mads` has no password and `users.mutableUsers` is true, so that account
cannot log in until root sets one. SSH and HTTPS are reachable on `tailscale0`
only. Complete this at the console, in order:

```bash
passwd mads
tailscale up
```

Then verify remote access from the workstation **while console access is still
available**:

```bash
ssh ai-services-01
```

Password authentication and root login are disabled, so a reviewed key over
Tailscale is the only remote path. Do not leave the machine until this
succeeds.

## 6. Confirm the generation

```bash
sudo nixos-rebuild list-generations
systemctl --failed
systemctl status home-manager-mads.service
```

Clone the permanent, user-owned checkout as `mads` and use it for every later
change. The operations guide assumes a checkout the interactive account can
edit, not a root-owned `/etc/nixos`.

## Remaining gates

The host is installed and reachable at this point, but no workload runs yet.
Secrets, the gateway, backups, networking, and the n8n migration each have
their own blockers and exit gates, in a required order.

That sequence is the [`ai-services-01` rollout plan](ai-services-01-rollout-plan.md),
which is the single authority for it. This runbook ends at "the host is
reachable over SSH"; the rollout plan starts there.
