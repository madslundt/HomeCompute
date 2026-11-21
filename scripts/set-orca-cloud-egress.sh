#!/usr/bin/env bash
# Apply an independently selected provider allowlist from the active release.
set -Eeuo pipefail
[[ $# == 2 && ( $1 == codex || $1 == claude ) && ( $2 == enable || $2 == disable ) &&
   $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Usage on home-core: sudo bash set-orca-cloud-egress.sh codex|claude enable|disable\n' >&2
  exit 2
}
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
revision=${release##*/}
[[ "$release" == /srv/homecompute/releases/"$revision" && "$revision" =~ ^[0-9a-f]{40}$ ]]
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9
[[ $(readlink -f /srv/homecompute/current) == "$release" ]]
[[ $(git -C "$release" rev-parse HEAD) == "$revision" ]]
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]]
git -C "$release" merge-base --is-ancestor "$revision" origin/master

export ORCA_RELEASE="$revision"
before=(docker compose -f "$release/deploy/orca/compose.yaml")
after=("${before[@]}")
directory=/var/lib/homecompute/orca
[[ -d "$directory" && ! -L "$directory" && $(stat -c %u:%a "$directory") == 0:700 ]]
for provider in codex claude; do
  marker="$directory/$provider-egress-enabled"
  [[ ! -L "$marker" && ( ! -e "$marker" || -f "$marker" ) ]]
  overlay=(-f "$release/deploy/orca/$provider.compose.yaml")
  if [[ -f "$marker" ]]; then
    before+=("${overlay[@]}")
  fi
  if [[ ( "$provider" == "$1" && "$2" == enable ) || ( "$provider" != "$1" && -f "$marker" ) ]]; then
    after+=("${overlay[@]}")
  fi
done

marker="$directory/$1-egress-enabled"
args=(up -d --no-deps --wait --wait-timeout 90 orca-egress)
if ! "${after[@]}" "${args[@]}"; then
  "${before[@]}" "${args[@]}"
  exit 1
fi
if [[ $2 == enable ]]; then
  record=(install -m 0600 /dev/null "$marker")
else
  record=(rm -f -- "$marker")
fi
if ! "${record[@]}"; then
  "${before[@]}" "${args[@]}"
  exit 1
fi
printf 'Orca %s egress selection recorded.\n' "$1"
