#!/usr/bin/env bash
# Run from a reviewed checkout on home-core: sudo bash scripts/deploy-home-core.sh SHA
set -Eeuo pipefail
revision="${1:-}"
[[ $# == 1 && "$revision" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Usage: sudo bash scripts/deploy-home-core.sh FULL_COMMIT_SHA\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Run as root on home-core.\n' >&2; exit 1;
}
for executable in git nixos-rebuild docker flock; do command -v "$executable" >/dev/null; done
install -d -m 0755 /srv/homecompute/releases
install -d -m 0700 /var/lib/homecompute
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }
# Restoring state and the identity is a separate operation; never initialize an
# empty replacement for the migrated n8n instance as a side effect of deployment.
for required_file in /var/lib/sops-nix/key.txt /srv/state/automation/n8n/database.sqlite /srv/state/automation/n8n/config; do
  [[ -s "$required_file" ]] || { printf 'Restore required file first: %s\n' "$required_file" >&2; exit 1; }
done
release="/srv/homecompute/releases/$revision"
if [[ ! -d "$release" ]]; then
  git clone https://github.com/madslundt/HomeCompute.git "$release"
fi
if ! git -C "$release" cat-file -e "$revision^{commit}" 2>/dev/null; then
  git -C "$release" fetch origin "$revision"
fi
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Release checkout is dirty; preserving it: %s\n' "$release" >&2; exit 1;
}
git -C "$release" checkout --detach "$revision"
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]]
(cd /var/lib/homecompute && nixos-rebuild build --flake "$release#home-core")
nixos-rebuild switch --flake "$release#home-core"
gateway=(docker compose --env-file /etc/homecompute/control-plane.env -f "$release/deploy/control-plane/compose.yaml")
automation=(docker compose --env-file /etc/homecompute/automation.env -f "$release/deploy/automation/compose.yaml" -f "$release/deploy/automation/production.yaml")
homepage=(docker compose --env-file /etc/homecompute/homepage.env -f "$release/deploy/homepage/compose.yaml")
"${gateway[@]}" config --quiet
"${automation[@]}" config --quiet
"${homepage[@]}" config --quiet
"${gateway[@]}" pull
"${automation[@]}" pull n8n
"${homepage[@]}" pull
"${automation[@]}" build aula-mcp
"${gateway[@]}" up -d --wait --wait-timeout 180
"${automation[@]}" up -d --wait --wait-timeout 180
"${homepage[@]}" up -d --wait --wait-timeout 180
if [[ -L /srv/homecompute/current ]]; then
  previous=$(readlink /srv/homecompute/current)
  if [[ "$previous" != "$release" ]]; then ln -sfn "$previous" /srv/homecompute/previous; fi
fi
ln -sfn "$release" /srv/homecompute/current
printf '%s\n' "$revision" > /var/lib/homecompute/deployed-revision
printf 'Healthy deployment: %s\n' "$revision"
printf 'Books importer is staged only; migration and source cutover are separate.\n'
