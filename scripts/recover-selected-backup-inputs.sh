#!/usr/bin/env bash
# Restore and retain the exact inputs referenced by the deployed backup unit.
set -Eeuo pipefail
base=bf3431d53538f446fe799cc6b7651a7d25bef462
release="/srv/homecompute/releases/$base"
gcroot=/var/lib/homecompute/selected-backup-source
config=/etc/homecompute/nas-recovery-backup.json
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $# == 0 && $EUID == 0 && $(hostname) == home-core ]] || die "Run on home-core as root."
[[ $(cat /var/lib/homecompute/deployed-revision) == "$base" ]] || die "Deployed revision changed."
[[ $(git -C "$release" rev-parse HEAD) == "$base" &&
   -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || die "Expected the clean deployed release."

helper=$(jq -er '.immichDbBackupScript' "$config")
compose=$(jq -er '.immichComposeFile' "$config")
source=${helper%/scripts/immich-db-backup.sh}
[[ $source =~ ^/nix/store/[a-z0-9]{32}-source$ &&
   $helper == "$source/scripts/immich-db-backup.sh" &&
   $compose == "$source/deploy/immich/compose.yaml" ]] || die "Unexpected active backup inputs."
if [[ -e $gcroot || -L $gcroot ]]; then
  [[ -L $gcroot && $(readlink "$gcroot") == "$source" ]] || die "Unexpected GC root."
fi

metadata=$(nix flake metadata --json "$release")
[[ $(jq -r '.revision' <<< "$metadata") == "$base" &&
   $(jq -r '.path' <<< "$metadata") == "$source" ]] || die "The active store source does not match the deployed revision."
for input in scripts/immich-db-backup.sh deploy/immich/compose.yaml; do
  [[ -f "$source/$input" && ! -L "$source/$input" ]]
  cmp -- "$source/$input" "$release/$input"
done
nix-store --realise "$source" --add-root "$gcroot" --indirect >/dev/null
[[ $(readlink "$gcroot") == "$source" ]]
printf 'Retained the active Immich backup inputs at %s.\n' "$gcroot"
