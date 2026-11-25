#!/usr/bin/env bash
# Apply only the T3 Code project from a published HomeCompute release.
set -Eeuo pipefail

revision=${1:-}
scope=${2:-}
[[ $# -le 2 && "$revision" =~ ^[a-f0-9]{40}$ && ( -z "$scope" || "$scope" == --github-projects-only ) ]] || {
  printf 'Usage: sudo bash scripts/deploy-t3code.sh FULL_COMMIT_SHA [--github-projects-only]\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core && $(uname -m) == x86_64 ]] || {
  printf 'Run as root on x86_64 home-core.\n' >&2
  exit 1
}

release="/srv/homecompute/releases/$revision"
script="$release/scripts/deploy-t3code.sh"
[[ $(realpath -- "${BASH_SOURCE[0]}") == "$script" &&
   $(git -C "$release" rev-parse HEAD) == "$revision" &&
   -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Run the installer from the clean checkout at %s.\n' "$release" >&2
  exit 1
}
git -C "$release" fetch --quiet https://github.com/madslundt/HomeCompute.git master
git -C "$release" merge-base --is-ancestor "$revision" FETCH_HEAD || {
  printf 'The selected commit must be published on master.\n' >&2
  exit 1
}

exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }

python3 "$release/deploy/t3code/github-projects.py" prepare
export T3CODE_RELEASE="$revision"
compose=(docker compose -f "$release/deploy/t3code/compose.yaml")
"${compose[@]}" config --quiet
"${compose[@]}" pull github-projects
if [[ "$scope" == --github-projects-only ]]; then
  "${compose[@]}" up -d --no-deps --no-build --pull never --wait --wait-timeout 90 github-projects
  "${compose[@]}" ps github-projects
  exit 0
fi

state=/srv/state/t3code
for directory in "$state" "$state/projects" "$state/.codex-homecompute" "$state/.hermes-code"; do
  if [[ -e "$directory" || -L "$directory" ]]; then
    [[ -d "$directory" && ! -L "$directory" &&
       $(stat -c '%u:%g:%a' -- "$directory") == 12073:12073:700 ]] || {
      printf 'Unexpected state directory ownership, mode or type: %s\n' "$directory" >&2
      exit 1
    }
  else
    install -d -m 0700 -o 12073 -g 12073 -- "$directory"
  fi
done

# Build first so a failed artifact verification leaves the old workspace running.
"${compose[@]}" build
"${compose[@]}" run --rm --no-deps --pull never -T --entrypoint t3 t3code \
  policy check /etc/t3code/policy.json
"${compose[@]}" run --rm --no-deps --pull never -T \
  -e HERMES_HOME=/data/.hermes-code --entrypoint hermes-code-review t3code --check
"${compose[@]}" run --rm --no-deps --pull never -T --entrypoint curl t3code \
  --fail --silent --show-error --max-time 10 --cacert /run/homecompute/home-core-root.crt \
  https://ai.home.arpa:8443/v1/models | jq -e 'any(.data[]; .id == "automation-moe")' >/dev/null
docker run --rm --interactive --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --security-opt "seccomp=$release/deploy/paperclip/codex-seccomp.json" \
  --tmpfs /tmp:mode=1777,nosuid,nodev,size=512m --workdir /tmp \
  --tmpfs /data:mode=0700,uid=12073,gid=12073,nosuid,nodev \
  "homecompute/t3code:$revision" sh -s < "$release/deploy/paperclip/verify-codex-sandbox.sh"
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 90
"${compose[@]}" ps
