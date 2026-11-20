#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

action="${1:-}"
if [[ $# != 1 || ! "$action" =~ ^(install|reconfigure|status|verify|stop|(enable|disable)-(hermes|openbot))$ ]]; then
  printf 'Usage: sudo bash scripts/setup-nod-guest.sh install|reconfigure|status|verify|stop|enable-hermes|disable-hermes|enable-openbot|disable-openbot\n' >&2
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
base_compose=(docker compose --project-name homecompute-nod
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

if [[ $action == reconfigure || $action == enable-* || $action == disable-* ]]; then
  exec 9>"$state/deploy.lock"
  flock -n 9
fi

validate_token() {
  local file="$1" name="$2"
  [[ -f "$file" && ! -L "$file" && $(stat -c '%a:%u:%h' "$file") == 600:0:1 ]] &&
    [[ $(wc -c < "$file") -eq $(("${#name}" + 49)) ]] &&
    LC_ALL=C grep -qxE "$name=nod_[A-Za-z0-9_-]{43}" "$file"
}

configure_bridge() {
  local hermes="$1" openbot="$2"
  compose=("${base_compose[@]}")
  export NOD_DELEGATION_HERMES_ENV=/dev/null NOD_DELEGATION_OPENBOT_ENV=/dev/null
  if [[ $hermes == no && $openbot == no ]]; then
    return
  fi
  [[ -d "$bridge" && ! -L "$bridge" && $(stat -c '%a:%u' "$bridge") == 700:0 ]]
  if [[ $hermes == yes ]]; then
    validate_token "$bridge/token.env" HOMECOMPUTE_NOD_BRIDGE_TOKEN
    NOD_DELEGATION_HERMES_ENV="$bridge/token.env"
  fi
  if [[ $openbot == yes ]]; then
    validate_token "$bridge/openbot.env" HOMECOMPUTE_NOD_OPENBOT_TOKEN
    NOD_DELEGATION_OPENBOT_ENV="$bridge/openbot.env"
  fi
  if [[ $hermes == yes && $openbot == yes ]] &&
     cmp -s <(cut -d= -f2 "$bridge/token.env") <(cut -d= -f2 "$bridge/openbot.env"); then
    printf 'Nod delegates require distinct credentials.\n' >&2
    exit 1
  fi
  compose+=(-f "$release/deploy/nod/hermes.override.yaml")
}

recreate_server() {
  "${compose[@]}" config --quiet &&
    "${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 server
}

prior_hermes=no prior_openbot=no
[[ ! -f "$bridge/enabled" ]] || prior_hermes=yes
[[ ! -f "$bridge/openbot-enabled" ]] || prior_openbot=yes
hermes="$prior_hermes" openbot="$prior_openbot"
case "$action" in
  enable-hermes) hermes=yes; marker="$bridge/enabled" ;;
  disable-hermes) hermes=no; marker="$bridge/enabled" ;;
  enable-openbot) openbot=yes; marker="$bridge/openbot-enabled" ;;
  disable-openbot) openbot=no; marker="$bridge/openbot-enabled" ;;
esac
if [[ $action != enable-* && $action != disable-* ]]; then
  configure_bridge "$hermes" "$openbot"
fi

case "$action" in
  install)
    COMPOSE_PROJECT_NAME=homecompute-nod sh "$project/setup.sh"
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --build --wait --wait-timeout 300
    ;;
  reconfigure)
    recreate_server
    ;;
  enable-hermes|enable-openbot)
    configure_bridge "$hermes" "$openbot"
    temporary=$(mktemp -d "$bridge/.render.XXXXXX")
    trap 'rm -rf -- "$temporary"' EXIT
    bridge_files=(src/server/app.ts src/server/access.ts src/server/thread.ts src/runtime/index.ts)
    for file in "${bridge_files[@]}" src/ports/agent.ts; do
      mkdir -p "$temporary/$(dirname "$file")"
      git -C "$upstream" show "$revision:$file" > "$temporary/$file"
    done
    (cd "$temporary" && git apply --unidiff-zero --check "$release/deploy/nod/hermes-bridge.patch" &&
      git apply --unidiff-zero "$release/deploy/nod/hermes-bridge.patch")
    install -m 0444 "$release/deploy/nod/homecompute-hermes.ts" "$temporary/src/server/homecompute-hermes.ts"
    for file in "${bridge_files[@]}" src/server/homecompute-hermes.ts; do
      if [[ $prior_hermes == yes || $prior_openbot == yes ]]; then
        cmp -s "$temporary/$file" "$bridge/$file" || {
          printf 'Applied Nod bridge differs from this release. Disable it from its originating release before upgrading.\n' >&2
          exit 1
        }
      else
        install -D -m 0444 "$temporary/$file" "$bridge/$file"
      fi
    done
    if ! recreate_server; then
      configure_bridge "$prior_hermes" "$prior_openbot"
      recreate_server
      exit 1
    fi
    install -m 0600 /dev/null "$marker"
    ;;
  disable-hermes|disable-openbot)
    if [[ -f "$marker" ]]; then
      configure_bridge "$hermes" "$openbot"
      if ! recreate_server; then
        configure_bridge "$prior_hermes" "$prior_openbot"
        recreate_server
        exit 1
      fi
      rm -- "$marker"
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
const delegates = [
  ['hermes', process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN, process.env.HOMECOMPUTE_NOD_OPENBOT_TOKEN],
  ['openbot', process.env.HOMECOMPUTE_NOD_OPENBOT_TOKEN, process.env.HOMECOMPUTE_NOD_BRIDGE_TOKEN],
];
for (const [delegate, bridge, other] of delegates) {
  if (!bridge) continue;
  const list = (token) => fetch(`http://127.0.0.1:4310/mcp/${delegate}-nod`, {
    method: 'POST',
    headers: {
      Host: 'homecompute-capabilities.home.arpa',
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  });
  assert.equal((await list()).status, 401);
  assert.equal((await list(process.env.NOD_AUTH_TOKEN)).status, 401);
  if (other) assert.equal((await list(other)).status, 401);
  const response = await list(bridge);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).result.tools.map(({name}) => name), ['nod_submit', 'nod_status']);
  console.log(`Nod ${delegate} bridge verified independent authentication and the bounded tool list.`);
}
JS
    [[ $(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 --header "Host: nod.home.arpa" http://10.77.60.2:4310/) == 200 ]]
    printf 'Nod web app answered on its private guest address.\n'
    ;;
  stop)
    "${compose[@]}" stop
    ;;
esac
