#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

action="${1:-}"
if [[ $# != 1 || ! "$action" =~ ^(install|status|verify|stop)$ ]]; then
  printf 'Usage: sudo bash scripts/setup-nod-guest.sh install|status|verify|stop\n' >&2
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
compose=(docker compose --project-name homecompute-nod
  --env-file "$project/.env" --env-file "$state/model.env"
  -f "$project/compose.yaml" -f "$release/deploy/nod/compose.override.yaml")

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
export PORT=4310 NOD_BIND_ADDRESS=127.0.0.1 NOD_PUBLIC_URL=http://localhost:4310

case "$action" in
  install)
    COMPOSE_PROJECT_NAME=homecompute-nod sh "$project/setup.sh"
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --build --wait --wait-timeout 300
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
JS
    [[ $(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 http://127.0.0.1:4310/) == 200 ]]
    printf 'Nod web app answered on guest loopback.\n'
    ;;
  stop)
    "${compose[@]}" stop
    ;;
esac
