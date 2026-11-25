#!/usr/bin/env bash
# Reconcile the private Paseo project from a published HomeCompute revision.
set -Eeuo pipefail

revision=${1:-}
action=${2:-apply}
[[ $# -ge 1 && $# -le 2 && $revision =~ ^[a-f0-9]{40}$ &&
   ( $action == apply || $action == retire ) ]] || {
  printf 'Usage: sudo bash scripts/deploy-paseo.sh FULL_SHA [apply|retire]\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core && $(uname -m) == x86_64 ]] || exit 1
release=/srv/homecompute/releases/$revision
[[ $(realpath -- "${BASH_SOURCE[0]}") == "$release/scripts/deploy-paseo.sh" &&
   $(git -C "$release" rev-parse HEAD) == "$revision" &&
   -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Use the clean published release checkout.\n' >&2
  exit 1
}
git -C "$release" fetch --quiet origin master
git -C "$release" merge-base --is-ancestor "$revision" origin/master
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }

state=/var/lib/homecompute/paseo
app_state=/srv/state/paseo
tailnet_name=home-core.tail479ad.ts.net
export PASEO_RELEASE=$revision
compose=(docker compose -f "$release/deploy/paseo/compose.yaml")
gateway=(docker compose --env-file /etc/homecompute/control-plane.env -f "$release/deploy/control-plane/compose.yaml")
homepage=(docker compose --env-file /etc/homecompute/homepage.env -f "$release/deploy/homepage/compose.yaml")

require_directory() {
  local directory=$1 uid=$2 gid=$3
  if [[ ! -e $directory && ! -L $directory ]]; then
    install -d -m 0700 -o "$uid" -g "$gid" -- "$directory"
  fi
  [[ -d $directory && ! -L $directory &&
     $(stat -c '%u:%g:%a' -- "$directory") == "$uid:$gid:700" ]]
}

ensure_tailnet() {
  jq -e --arg host "$tailnet_name" '
    .BackendState == "Running" and (.Self.DNSName | rtrimstr(".")) == $host
  ' < <(tailscale status --json) >/dev/null
  jq -e --arg endpoint "$tailnet_name:18797" '
    (.AllowFunnel[$endpoint] // false) == false and
    ((.Web[$endpoint] // {"Handlers": {"/": {"Proxy": "http://127.0.0.1:6767"}}})
      == {"Handlers": {"/": {"Proxy": "http://127.0.0.1:6767"}}})
  ' < <(tailscale serve status --json) >/dev/null
}

active_compose_source() {
  local path
  path=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$1")
  [[ $path =~ ^/srv/homecompute/releases/[0-9a-f]{40}/deploy/$2/compose\.yaml$ && -f $path ]] || {
    printf 'Unexpected active %s Compose sources. Reconcile the owning lifecycle.\n' "$2" >&2
    return 1
  }
  printf '%s\n' "$path"
}

verify_checkout() {
  "${compose[@]}" exec -T paseo node -e '
    const assert = require("node:assert/strict");
    (async () => {
      const models = await fetch("http://inference:18080/v1/models", {
        headers: {authorization: "Bearer local"}
      }).then(r => r.json());
      for (const id of ["local-only", "automation-moe", "stt-danish", "omnivoice"])
        assert(models.data.some(model => model.id === id), "Missing expected route");
      const app = await fetch("http://127.0.0.1:6767/api/health");
      assert(app.ok, "Paseo health check failed");
    })().catch(() => process.exit(1));
  '
  "${compose[@]}" exec -T paseo python3 /etc/paseo/register-projects.py
}

retire() {
  ensure_tailnet
  verify_checkout
  [[ -s $state/github-projects/credentials/token ]] || {
    printf 'Verify the migrated GitHub Projects credential before retirement.\n' >&2
    return 1
  }
  local old=homecompute-t3code old_state=/srv/state/t3code
  local archive=/srv/state/.retired/t3code migration_state=/var/lib/homecompute/t3code
  local target
  require_directory /srv/state/.retired 0 0
  if [[ -e $old_state ]]; then
    [[ -d $old_state && ! -L $old_state && ! -e $archive && ! -L $archive ]]
  fi
  # Refuse to remove a container unless it belongs to the retired Compose project.
  mapfile -t containers < <(docker ps -aq --filter label=com.docker.compose.project="$old")
  if (( ${#containers[@]} )); then
    docker stop "${containers[@]}" >/dev/null
    docker rm "${containers[@]}" >/dev/null
  fi
  if [[ -e $old_state ]]; then mv -T -- "$old_state" "$archive"; fi
  for target in "$old"_default "$old"-ingress; do
    if docker network inspect "$target" >/dev/null 2>&1; then docker network rm "$target" >/dev/null; fi
  done
  if [[ -e $migration_state ]]; then
    [[ -d $migration_state && ! -L $migration_state && ! -e "$state/retired-projects" ]]
    mv -T "$migration_state" "$state/retired-projects"
    # The live sidecar now owns the credential. Do not retain a second live copy.
    rm -f -- "$state/retired-projects/github-projects/credentials/token"
  fi
  while IFS= read -r image; do
    [[ $image == 'homecompute/t3code:<none>' ]] || docker image rm "$image" >/dev/null
  done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' homecompute/t3code)
  printf 'Retired T3 Code containers and networks. Retained the private rollback archive.\n'
}

if [[ $action == retire ]]; then
  retire
  exit
fi

for marker in active-image.json active-config.json; do
  [[ ! -e /var/lib/homecompute/homepage-maintenance/$marker ]] || {
    printf 'Reconcile the active Homepage maintenance override first.\n' >&2
    exit 1
  }
done
ensure_tailnet
old_gateway=$(active_compose_source homecompute-control-plane-caddy-1 control-plane)
old_homepage=$(active_compose_source homecompute-homepage-homepage-1 homepage)
# This scoped rollout may only change the private proxy and the dashboard card.
old_gateway_release=${old_gateway%/deploy/control-plane/compose.yaml}
old_homepage_release=${old_homepage%/deploy/homepage/compose.yaml}
git -C "$release" diff --exit-code "${old_gateway_release##*/}" "$revision" -- deploy/control-plane \
  ':(exclude)deploy/control-plane/Caddyfile' ':(exclude)deploy/control-plane/compose.yaml' \
  ':(exclude)deploy/control-plane/*.md' >/dev/null
git -C "$release" diff --exit-code "${old_homepage_release##*/}" "$revision" -- deploy/homepage \
  ':(exclude)deploy/homepage/config/services.yaml' ':(exclude)deploy/homepage/*.md' >/dev/null
require_directory "$state" 0 0
require_directory "$app_state" 12074 12074
for directory in projects .paseo .codex .hermes; do
  require_directory "$app_state/$directory" 12074 12074
done
if [[ ! -e $state/runtime.env && ! -L $state/runtime.env ]]; then
  (umask 077; set -o noclobber; printf 'PASEO_PASSWORD=%s\n' "$(python3 -c "import secrets; print(secrets.token_hex(32))")" > "$state/runtime.env")
fi
[[ -f $state/runtime.env && ! -L $state/runtime.env &&
   $(stat -c '%u:%g:%a' "$state/runtime.env") == 0:0:600 ]]
grep -Eq '^PASEO_PASSWORD=[a-f0-9]{64}$' "$state/runtime.env"
python3 -B "$release/deploy/paseo/github-projects.py" prepare
previous_token=/var/lib/homecompute/t3code/github-projects/credentials/token
token=$state/github-projects/credentials/token
if [[ ! -e $token && -f $previous_token && ! -L $previous_token &&
      $(stat -c '%u:%g:%a' "$previous_token") == 0:10001:640 ]]; then
  install -m 0640 -o 0 -g 10001 -- "$previous_token" "$token"
fi
python3 -B "$release/scripts/setup-paseo.py"
"${compose[@]}" config --quiet
"${gateway[@]}" config --quiet
"${homepage[@]}" config --quiet
"${compose[@]}" pull inference github-projects
"${compose[@]}" build paseo
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --security-opt "seccomp=$release/deploy/paperclip/codex-seccomp.json" \
  --tmpfs /tmp:mode=1777,nosuid,nodev,size=128m \
  --tmpfs /home/paseo:mode=700,uid=12074,gid=12074,nosuid,nodev \
  --workdir /tmp --entrypoint sh "homecompute/paseo:$revision" \
  -c 'mkdir -p "$CODEX_HOME"; exec sh -s' < "$release/deploy/paperclip/verify-codex-sandbox.sh"

gateway_changed=false
homepage_changed=false
rollback_edge() {
  if [[ $homepage_changed == true ]]; then
    docker compose --env-file /etc/homecompute/homepage.env -f "$old_homepage" \
      up -d --no-deps --pull never --wait homepage || true
  fi
  if [[ $gateway_changed == true ]]; then
    docker compose --env-file /etc/homecompute/control-plane.env -f "$old_gateway" \
      up -d --no-deps --pull never --wait caddy || true
  fi
}
trap rollback_edge ERR
if ! git -C "$release" diff --quiet "${old_gateway_release##*/}" "$revision" -- \
  deploy/control-plane/Caddyfile deploy/control-plane/compose.yaml; then
  gateway_changed=true
  "${gateway[@]}" up -d --no-deps --pull never --wait --wait-timeout 90 caddy
fi
"${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 120
verify_checkout
if ! git -C "$release" diff --quiet "${old_homepage_release##*/}" "$revision" -- \
  deploy/homepage/compose.yaml deploy/homepage/config/services.yaml; then
  homepage_changed=true
  "${homepage[@]}" up -d --no-deps --pull never --wait --wait-timeout 90 homepage
fi
tailscale serve --bg --yes --https=18797 http://127.0.0.1:6767
ensure_tailnet
trap - ERR
printf 'Paseo deployed at https://%s:18797/\n' "$tailnet_name"
