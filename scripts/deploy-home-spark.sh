#!/usr/bin/env bash
# Run from workstation via SSH: sudo bash -s -- FULL_COMMIT_SHA
set -Eeuo pipefail
revision="${1:-}"
[[ $# == 1 && "$revision" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Usage: sudo bash -s -- FULL_COMMIT_SHA\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname -s) == home-spark ]] || {
  printf 'Run as root on home-spark.\n' >&2; exit 1;
}
for executable in git docker flock python3; do command -v "$executable" >/dev/null || { printf 'Missing required command: %s\n' "$executable" >&2; exit 1; }; done
docker compose version >/dev/null
install -d -m 0755 /srv/homecompute/releases
install -d -m 0700 /var/lib/homecompute
exec 9>/var/lib/homecompute/home-spark-deploy.lock
flock -n 9 || { printf 'Another home-spark deployment is running.\n' >&2; exit 1; }
release="/srv/homecompute/releases/$revision"
if [[ ! -d "$release" ]]; then
  git clone --no-checkout https://github.com/madslundt/HomeCompute.git "$release"
fi
if ! git -C "$release" cat-file -e "$revision^{commit}" 2>/dev/null; then
  git -C "$release" fetch --no-tags origin "$revision"
fi
git -C "$release" cat-file -e "$revision^{commit}"

# A --no-checkout clone has an empty index and worktree, which Git reports as
# staged deletions for every tracked path. Populate only that empty state so a
# prior attempt by older versions of this wrapper is recoverable. Any checkout
# containing files or index entries remains protected by the dirty check.
if [[ -z $(git -C "$release" ls-files --cached) ]] &&
   [[ -z $(find "$release" -mindepth 1 -maxdepth 1 ! -name .git -print -quit) ]]; then
  git -C "$release" reset --hard "$revision"
else
  [[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
    printf 'Release checkout is dirty; preserving it: %s\n' "$release" >&2; exit 1;
  }
fi
git -C "$release" checkout --detach "$revision"
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Release checkout became dirty; refusing deployment.\n' >&2; exit 1;
}
# Validate against the selected release. The default text-primary in this
# Compose project is intentionally stopped in the current production roster;
# do not make an update implicitly start that additional GPU process.
"$release/scripts/setup-compute-node.sh" validate
if [[ -n $(docker ps --quiet --filter status=running --filter label=com.docker.compose.service=text-primary) ]]; then
  # When this exact baseline is already serving, use its guarded install path
  # for cache verification, readiness, and model smoke checks.
  "$release/scripts/setup-compute-node.sh" install
  "$release/scripts/setup-compute-node.sh" status
else
  printf 'text-primary is not running; validated release without starting the intentionally stopped baseline.\n'
fi

if [[ -L /srv/homecompute/current ]]; then
  previous=$(readlink -f /srv/homecompute/current)
  [[ "$previous" == "$release" ]] || ln -sfn "$previous" /srv/homecompute/previous
fi
temporary="/srv/homecompute/.current-$revision"
ln -sfn "$release" "$temporary"
mv -Tf "$temporary" /srv/homecompute/current
revision_file=/var/lib/homecompute/home-spark-deployed-revision
revision_tmp="$revision_file.tmp.$$"
printf '%s\n' "$revision" >"$revision_tmp"
chmod 0644 "$revision_tmp"
mv -f "$revision_tmp" "$revision_file"
printf 'Recorded home-spark application release: %s\n' "$revision"
printf 'DGX OS, drivers, CUDA, firmware, and NVIDIA Container Toolkit remain vendor-managed.\n'
