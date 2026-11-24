#!/usr/bin/env bash
# Deploy just the NixOS-generated selected NAS services on home-core.
set -Eeuo pipefail
umask 077
export PATH=/run/current-system/sw/bin

operation=${1:-}
revision=${2:-}
[[ $# == 2 && "$operation" =~ ^(apply|rollback|retire)$ && "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ $EUID == 0 && $(hostname -s) == home-core ]]
release="/srv/homecompute/releases/$revision"
[[ $(realpath -- "$release") == "$release" && $(git -C "$release" rev-parse HEAD) == "$revision" ]]
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]]
git -C "$release" merge-base --is-ancestor "$revision" origin/master
[[ $(realpath -- "${BASH_SOURCE[0]}") == "$release/scripts/setup-selected-nas-recovery.sh" ]]

state=/var/lib/homecompute/nas-recovery-backup
overlays=/etc/systemd/system.control
root="$state/selected-units-$revision"
units=(homecompute-nas-recovery-backup.service homecompute-nas-recovery-check.service)
for path in "$state" "$overlays"; do
  [[ -d "$path" && ! -L "$path" && $(stat -c %u "$path") == 0 ]]
done
[[ -f "$state/backup-authorized" && ! -L "$state/backup-authorized" &&
   $(stat -c '%u:%a' "$state/backup-authorized") == 0:600 ]]
exec 9>"$state/lifecycle.lock"
flock -n 9
exec 8>/var/lib/homecompute/deploy.lock
flock -n 8

for unit in "${units[@]}"; do
  case "$(systemctl show "$unit" -p ActiveState --value)" in
    inactive|failed) ;;
    *) echo "Wait for the current NAS operation to settle." >&2; exit 1 ;;
  esac
  [[ -z $(systemctl show "$unit" -p DropInPaths --value) ]]
done
[[ ! -e "$state/resume.json" && ! -e /run/homecompute/application-recovery-in-progress ]]

verify_owned() {
  [[ -L "$root" && $(readlink -- "$root") =~ ^/nix/store/[a-z0-9]{32}-homecompute-selected-nas-units$ ]]
  for unit in "${units[@]}"; do
    [[ -L "$overlays/$unit" && $(readlink -- "$overlays/$unit") == "$root/$unit" ]]
  done
}

if [[ "$operation" != apply ]]; then
  verify_owned
  for unit in "${units[@]}"; do
    if [[ "$operation" == retire ]]; then
      cmp -- "$root/$unit" "/etc/systemd/system/$unit"
    fi
  done
  for unit in "${units[@]}"; do rm -- "$overlays/$unit"; done
  systemctl daemon-reload
  rm -- "$root"
  printf 'Selected NAS service overlay %s complete. Retained the credential and backup repository.\n' "$operation"
  exit 0
fi

if [[ -L "$root" ]]; then
  verify_owned
  echo "Selected NAS services already installed from this revision."
  exit 0
fi
for unit in "${units[@]}"; do
  [[ ! -e "$overlays/$unit" && ! -L "$overlays/$unit" ]]
  [[ $(systemctl show "$unit" -p FragmentPath --value) == "/etc/systemd/system/$unit" ]]
done

bundle=$(nix --extra-experimental-features 'nix-command flakes' build --no-link --print-out-paths +  "$release#nixosConfigurations.home-core.config.system.build.selectedNasRecovery")
[[ "$bundle" =~ ^/nix/store/[a-z0-9]{32}-homecompute-selected-nas-units$ ]]
systemd-analyze verify "${units[@]/#/$bundle/}"
for unit in "${units[@]}"; do
  grep -Fq -- '-selected-nas-recovery-backup.py --config /nix/store/' "$bundle/$unit"
done

credential="$state/hermes-ssh-key"
temporary=$(mktemp "$state/.hermes-ssh-key.XXXXXX")
installed=()
clean_up() {
  local result=$?
  rm -f -- "$temporary"
  if (( result != 0 )); then
    for unit in "${installed[@]}"; do rm -- "$overlays/$unit"; done
    if (( ${#installed[@]} )); then systemctl daemon-reload; fi
    if [[ -L "$root" ]]; then rm -- "$root"; fi
  fi
}
trap clean_up EXIT
SOPS_AGE_KEY_FILE=/var/lib/sops-nix/key.txt sops decrypt --output-type json "$release/secrets/home-core.sops.yaml" |
  jq -er '."hermes-backup"."ssh-key" | select(type == "string")' >"$temporary"
expected_key=$(cut -d ' ' -f 1,2 "$release/config/hermes-backup-authorized-key.pub")
[[ $(ssh-keygen -y -f "$temporary" | cut -d ' ' -f 1,2) == "$expected_key" ]]
if [[ -e "$credential" || -L "$credential" ]]; then
  [[ -f "$credential" && ! -L "$credential" && $(stat -c '%u:%a:%h' "$credential") == 0:600:1 ]]
  cmp -- "$credential" "$temporary"
else
  mv -T -- "$temporary" "$credential"
fi
install -d -m 0700 /srv/backups/homecompute-selected-state
nix-store --realise "$bundle" --add-root "$root" --indirect >/dev/null
for unit in "${units[@]}"; do
  ln -s -- "$root/$unit" "$overlays/$unit"
  installed+=("$unit")
done
systemctl daemon-reload
for unit in "${units[@]}"; do
  [[ $(realpath -- "$(systemctl show "$unit" -p FragmentPath --value)") == "$(realpath -- "$root/$unit")" ]]
done
printf 'Selected NAS backup and check installed from %s.\n' "$revision"
