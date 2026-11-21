#!/usr/bin/env bash
# Apply the published opt-in Codex network policy to the running Orca project.
set -Eeuo pipefail
[[ $# == 1 && ( $1 == enable || $1 == disable ) && $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Usage on home-core: sudo bash set-orca-codex-egress.sh enable|disable\n' >&2
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
compose=(docker compose -f "$release/deploy/orca/compose.yaml")
overlay=(-f "$release/deploy/orca/codex.compose.yaml")
marker=/var/lib/homecompute/orca/codex-egress-enabled
[[ -d ${marker%/*} && ! -L ${marker%/*} && $(stat -c %u:%a "${marker%/*}") == 0:700 ]]
[[ ! -L "$marker" ]]
if [[ $1 == enable ]]; then
  # Roll back an unsuccessful first enable to the default allowlist.
  if ! "${compose[@]}" "${overlay[@]}" up -d --no-deps --wait --wait-timeout 90 orca-egress; then
    if [[ ! -e "$marker" ]]; then
      "${compose[@]}" up -d --no-deps --wait --wait-timeout 90 orca-egress
    fi
    exit 1
  fi
  install -m 0600 /dev/null "$marker"
else
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 90 orca-egress
  rm -f -- "$marker"
fi
printf 'Orca Codex egress selection recorded.\n'
