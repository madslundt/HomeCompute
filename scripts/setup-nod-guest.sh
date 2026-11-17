#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

action="${1:-}"
if [[ $# != 1 || ! "$action" =~ ^(install|reconfigure|status|verify|stop|enable-hermes|disable-hermes)$ ]]; then
  printf 'Usage: sudo bash scripts/setup-nod-guest.sh install|reconfigure|status|verify|stop|enable-hermes|disable-hermes\n' >&2
  exit 2
fi
if [[ $EUID != 0 || $(hostname) != homecompute-nod ]]; then
  printf 'Run as root in the dedicated homecompute-nod guest.\n' >&2
  exit 1
fi

release="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
upstream=/opt/homecompute-nod
state=/var/lib/homecompute-nod
project="$upstream/deployment/self-host"
revision=e697ec36504826bb3151907f457e4498fb95cedc
bridge="$state/hermes-bridge"
base_override="$release/deploy/nod/compose.override.yaml"
compose=(docker compose --project-name homecompute-nod
  --env-file "$project/.env" --env-file "$state/model.env"
  -f "$project/compose.yaml" -f "$base_override")

if [[ $action == install ]]; then
  install -d -m 0700 "$state"
  exec 9>"$state/deploy.lock"
  flock -n 9
  [[ -f "$state/model.env" && ! -L "$state/model.env" && $(stat -c '%a:%u' "$state/model.env") == 600:0 ]]
  [[ -s "$state/gateway-ca.crt" && ! -L "$state/gateway-ca.crt" ]]
  if ! command -v docker >/dev/null || ! docker compose version >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git ca-certificates
  fi
  systemctl start docker.service
  if [[ ! -e "$upstream" ]]; then
    scratch="$(mktemp -d /opt/.homecompute-nod.XXXXXX)"
    trap 'rm -rf -- "$scratch"' EXIT
    git clone --depth 1 --branch nod-v0.7.6 https://github.com/n8n-io/nod.git "$scratch/source"
    [[ $(git -C "$scratch/source" rev-parse HEAD) == "$revision" ]]
    mv "$scratch/source" "$upstream"
  fi
fi

[[ $(git -C "$upstream" rev-parse HEAD) == "$revision" ]]
[[ -z $(git -C "$upstream" status --porcelain --untracked-files=no) ]]
unset NOD_ENV_FILE NOD_BIND_ADDRESS NOD_PUBLIC_URL NOD_IMAGE_TAG PORT
unset LLM_PROVIDER LLM_MODEL OPENAI_COMPATIBLE_BASE_URL OPENAI_COMPATIBLE_API_KEY
export PORT=4310 NOD_BIND_ADDRESS=10.77.60.2 NOD_PUBLIC_URL=https://nod.home.arpa

if [[ $action == enable-hermes || $action == disable-hermes ]]; then
  exec 9>"$state/deploy.lock"
  flock -n 9
  [[ -d "$bridge" && ! -L "$bridge" && $(stat -c '%a:%u' "$bridge") == 700:0 ]]
fi

if [[ $action == enable-hermes || ( $action != disable-hermes && -f "$bridge/enabled" ) ]]; then
  [[ -f "$bridge/token.env" && ! -L "$bridge/token.env" && $(stat -c '%a:%u' "$bridge/token.env") == 600:0 ]]
  [[ $(wc -c < "$bridge/token.env") -eq 77 ]]
  LC_ALL=C grep -qxE 'HOMECOMPUTE_NOD_BRIDGE_TOKEN=nod_[A-Za-z0-9_-]{43}' "$bridge/token.env"
  compose+=(-f "$release/deploy/nod/hermes.override.yaml")
fi

case "$action" in
  install)
    COMPOSE_PROJECT_NAME=homecompute-nod sh "$project/setup.sh"
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --build --wait --wait-timeout 300
    ;;
  reconfigure)
    exec 9>"$state/deploy.lock"
    flock -n 9
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 server
    ;;
  enable-hermes)
    temporary=$(mktemp -d "$bridge/.render.XXXXXX")
    trap 'rm -rf -- "$temporary"' EXIT
    mkdir -p "$temporary/src/server"
    git -C "$upstream" show "$revision:src/server/app.ts" > "$temporary/src/server/app.ts"
    (cd "$temporary" && git apply --check "$release/deploy/nod/hermes-bridge.patch" &&
      git apply "$release/deploy/nod/hermes-bridge.patch")
    if [[ -f "$bridge/enabled" ]]; then
      cmp -s "$temporary/src/server/app.ts" "$bridge/app.ts"
      cmp -s "$release/deploy/nod/homecompute-hermes.ts" "$bridge/homecompute-hermes.ts"
    else
      install -m 0444 "$temporary/src/server/app.ts" "$bridge/app.ts"
      install -m 0444 "$release/deploy/nod/homecompute-hermes.ts" "$bridge/homecompute-hermes.ts"
    fi
    "${compose[@]}" config --quiet
    if ! "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 server; then
      if [[ ! -f "$bridge/enabled" ]]; then
        base=("${compose[@]:0:${#compose[@]}-2}")
        "${base[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 server
      fi
      exit 1
    fi
    install -m 0600 /dev/null "$bridge/enabled"
    ;;
  disable-hermes)
    if [[ -f "$bridge/enabled" ]]; then
      "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 server
      rm -- "$bridge/enabled"
    fi
    ;;
  status)
    "${compose[@]}" ps
    ;;
  verify)
    "${compose[@]}" exec -T server node --input-type=module - <<'JS'
import assert from 'node:assert/strict';
const base = process.env.OPENAI_COMPATIBLE_BASE_URL;
const headers = { Authorization: `Bearer ${process.env.OPENAI_COMPATIBLE_API_KEY}` };
const models = await fetch(`${base}/models`, { headers });
assert.equal(models.status, 200);
assert.deepEqual((await models.json()).data.map(({id}) => id), ['automation-moe']);
assert.equal((await fetch(`${base}/models`)).status, 401);
assert.equal((await fetch(new URL('/healthz', base), { headers })).status, 404);
console.log('Nod server container verified TLS, restricted model discovery and unauthenticated denial.');
const n8n = 'https://n8n.home.arpa';
const discovery = await fetch(`${n8n}/.well-known/oauth-protected-resource/mcp-server/http`);
assert.equal(discovery.status, 200);
const metadata = await discovery.json();
assert.equal(metadata.resource, `${n8n}/mcp-server/http`);
assert.ok(metadata.scopes_supported.includes('workflow:read'));
assert.equal((await fetch(`${n8n}/mcp-server/http`)).status, 401);
assert.equal((await fetch(`${n8n}/rest/settings`)).status, 404);
console.log('Nod server verified private n8n OAuth discovery, MCP authentication and editor isolation.');
const bridge = process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN;
if (bridge) {
  const list = (token) => fetch('http://127.0.0.1:4310/mcp/hermes-nod', {
    method: 'POST',
    headers: {
      Host: 'nod.home.arpa',
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  });
  assert.equal((await list()).status, 401);
  assert.equal((await list(process.env.NOD_AUTH_TOKEN)).status, 401);
  const response = await list(bridge);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).result.tools.map(({name}) => name), ['nod_submit', 'nod_status']);
  console.log('Nod bridge verified independent authentication and the bounded tool list.');
}
JS
    [[ $(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 --header "Host: nod.home.arpa" http://10.77.60.2:4310/) == 200 ]]
    printf 'Nod web app answered on its private guest address.\n'
    ;;
  stop)
    "${compose[@]}" stop
    ;;
esac
