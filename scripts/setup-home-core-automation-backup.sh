#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/control-plane/compose.yaml"
NORMAL_LITELLM_CONFIG="$REPO_ROOT/deploy/control-plane/litellm-config.yaml"
BACKUP_LITELLM_CONFIG="$REPO_ROOT/deploy/control-plane/litellm-config-automation-backup.yaml"
ENV_FILE="${CONTROL_PLANE_ENV_FILE:-/etc/homecompute/control-plane.env}"
STATE_ROOT=/srv/state/automation-backup
MODEL_DIR="$STATE_ROOT/models"
MODEL_FILE=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
MODEL_PATH="$MODEL_DIR/$MODEL_FILE"
MODEL_PARTIAL="$MODEL_PATH.partial"
MODEL_REPO=unsloth/Qwen3.6-35B-A3B-GGUF
MODEL_REVISION=a483e9e6cbd595906af30beda3187c2663a1118c
MODEL_SIZE=22134528992
MODEL_SHA256=ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61
IMAGE=ghcr.io/ggml-org/llama.cpp:server@sha256:b74a168a10b13129ce8973582a5c699fadecde45945a8b8b004b79e34f4ff1ab
COMMAND="${1:-help}"

usage() {
  cat <<'USAGE'
Usage: setup-home-core-automation-backup.sh COMMAND
Commands:
  validate           Validate the pinned profile without changing state
  prepare            Download and verify the pinned Qwen3.6 Q4 model and image
  maintenance-start  Load/test standby, then route automation to home-core
  smoke              Re-run the direct content/tool smokes
  status             Show the standby container state and memory footprint
  maintenance-stop   Restore Spark routing, then stop the standby
  logs               Show the last 100 container log lines

The standby is intentionally stopped by default. Start it and wait for smoke
success before a planned home-spark model swap. It does not provide instant
recovery from an unplanned outage while stopped.
USAGE
}

log() { printf '[automation-backup] %s\n' "$*"; }
die() { printf '[automation-backup] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "Rerun this state-changing command with sudo"; }
compose() {
  docker compose --profile automation-backup --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

compose_with_litellm_config() {
  local config_file=$1
  shift
  LITELLM_CONFIG_FILE="$config_file" compose "$@"
}

validate_model() {
  [[ -f "$MODEL_PATH" ]] || die "Model is not prepared: $MODEL_PATH"
  [[ $(stat -c %s "$MODEL_PATH") == "$MODEL_SIZE" ]] || die "Model size verification failed"
  [[ $(sha256sum "$MODEL_PATH" | awk '{print $1}') == "$MODEL_SHA256" ]] || die "Model SHA-256 verification failed"
}

validate() {
  [[ $(hostname) == home-core ]] || die "This profile is only for home-core"
  [[ -f "$ENV_FILE" ]] || die "Control-plane environment not found: $ENV_FILE"
  [[ -f "$NORMAL_LITELLM_CONFIG" ]] || die "Normal LiteLLM config not found: $NORMAL_LITELLM_CONFIG"
  [[ -f "$BACKUP_LITELLM_CONFIG" ]] || die "Maintenance LiteLLM config not found: $BACKUP_LITELLM_CONFIG"
  command -v docker >/dev/null || die "docker is required"
  command -v curl >/dev/null || die "curl is required"
  command -v jq >/dev/null || die "jq is required"
  command -v sha256sum >/dev/null || die "sha256sum is required"
  compose config --quiet
  local rendered
  rendered="$(compose config --format json)"
  jq -e --arg image "$IMAGE" '
    .services["automation-backup"].image == $image and
    .services["automation-backup"].profiles == ["automation-backup"] and
    .services["automation-backup"].ports == null and
    .services["automation-backup"].read_only == true and
    .services["automation-backup"].mem_limit == "30064771072" and
    .services["automation-backup"].memswap_limit == "30064771072" and
    .networks["automation-backup"].internal == true
  ' <<<"$rendered" >/dev/null || die "Rendered standby profile violates the reviewed envelope"
  log "Pinned model, image, isolation, and resource envelope are valid"
}

prepare() {
  require_root
  validate
  install -d -m 0755 "$STATE_ROOT"
  install -d -m 0755 "$MODEL_DIR"
  if [[ -f "$MODEL_PATH" ]]; then
    validate_model
    log "Pinned model is already present and verified"
  else
    local url
    url="https://huggingface.co/$MODEL_REPO/resolve/$MODEL_REVISION/$MODEL_FILE?download=true"
    log "Downloading the pinned 22.1 GB Qwen3.6 Q4 artifact; partial downloads resume"
    curl --fail --location --retry 5 --retry-all-errors --continue-at - --output "$MODEL_PARTIAL" "$url"
    [[ $(stat -c %s "$MODEL_PARTIAL") == "$MODEL_SIZE" ]] || die "Downloaded model has the wrong size"
    [[ $(sha256sum "$MODEL_PARTIAL" | awk '{print $1}') == "$MODEL_SHA256" ]] || die "Downloaded model has the wrong SHA-256"
    chmod 0444 "$MODEL_PARTIAL"
    mv -f -- "$MODEL_PARTIAL" "$MODEL_PATH"
    log "Pinned model download verified"
  fi
  docker pull "$IMAGE"
  log "CPU standby is prepared on disk and remains stopped"
}

smoke() {
  validate
  validate_model
  local response
  # Expansion inside this single-quoted script belongs to the container.
  # shellcheck disable=SC2016
  response="$(compose exec -T automation-backup sh -ec '
    key="$(cat /run/secrets/compute_api_key)"
    curl --fail --silent --show-error --max-time 300 \
      --header "Authorization: Bearer $key" \
      --header "Content-Type: application/json" \
      --data-binary @- http://127.0.0.1:8080/v1/chat/completions
  ' <<'JSON'
{"model":"automation-backup","messages":[{"role":"system","content":"Svar kort på dansk. Tænk ikke højt."},{"role":"user","content":"Svar kun med ordet KLAR."}],"temperature":0,"max_tokens":32}
JSON
)"
  jq -e '.choices[0].message.content | ascii_upcase | contains("KLAR")' <<<"$response" >/dev/null ||
    die "Danish content smoke failed"

  # Expansion inside this single-quoted script belongs to the container.
  # shellcheck disable=SC2016
  response="$(compose exec -T automation-backup sh -ec '
    key="$(cat /run/secrets/compute_api_key)"
    curl --fail --silent --show-error --max-time 300 \
      --header "Authorization: Bearer $key" \
      --header "Content-Type: application/json" \
      --data-binary @- http://127.0.0.1:8080/v1/chat/completions
  ' <<'JSON'
{"model":"automation-backup","messages":[{"role":"system","content":"Du skal bruge det relevante værktøj og ikke besvare spørgsmålet direkte."},{"role":"user","content":"Hvad er temperaturen i køkkenet?"}],"tools":[{"type":"function","function":{"name":"get_temperature","description":"Læs temperaturen i et rum","parameters":{"type":"object","properties":{"room":{"type":"string"}},"required":["room"],"additionalProperties":false}}}],"tool_choice":"required","temperature":0,"max_tokens":96}
JSON
)"
  jq -e '
    .choices[0].message.tool_calls[0].function as $f |
    $f.name == "get_temperature" and
    (($f.arguments | fromjson).room | ascii_downcase | contains("køkken"))
  ' <<<"$response" >/dev/null || die "Danish tool-call smoke failed"
  log "Danish content and structured tool-call smokes passed"
}

gateway_smoke() {
  # Keep the gateway credential inside the container; never place it in argv.
  compose exec -T litellm python3 - <<'PY'
import json
import sys
import urllib.request

with open("/run/secrets/litellm_master_key", encoding="utf-8") as handle:
    key = handle.read().strip()

request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/chat/completions",
    data=json.dumps({
        "model": "automation",
        "messages": [
            {"role": "system", "content": "Svar kort på dansk. Tænk ikke højt."},
            {"role": "user", "content": "Svar kun med ordet KLAR."},
        ],
        "temperature": 0,
        "max_tokens": 32,
    }).encode(),
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=300) as response:
    document = json.load(response)

content = document["choices"][0]["message"]["content"]
if "KLAR" not in content.upper():
    print("Stable automation alias returned an unexpected response", file=sys.stderr)
    raise SystemExit(1)
print("Stable automation alias reached the home-core standby")
PY
}

switch_gateway() {
  local config_file=$1
  compose_with_litellm_config "$config_file" up -d --no-deps --force-recreate --wait --wait-timeout 180 litellm
}

maintenance_start() {
  require_root
  validate
  validate_model
  if systemctl is-active --quiet homecompute-agents-vm.service; then
    die "Refusing to start the 28 GiB standby while the 16 GiB agents VM is running; stop the agents VM first"
  fi
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "Pinned image is missing; run prepare first"
  compose up -d --no-build --wait --wait-timeout 1200 automation-backup
  smoke
  log "Switching the stable automation alias to home-core; active gateway requests may be interrupted"
  if ! switch_gateway "$BACKUP_LITELLM_CONFIG" || ! gateway_smoke; then
    log "Maintenance route failed; attempting to restore the normal Spark route"
    switch_gateway "$NORMAL_LITELLM_CONFIG" || true
    compose stop automation-backup || true
    die "Could not activate and verify the maintenance route"
  fi
  log "Standby is warm and the stable automation alias now prefers home-core"
}

maintenance_stop() {
  require_root
  validate
  log "Restoring the stable automation alias to prefer home-spark"
  switch_gateway "$NORMAL_LITELLM_CONFIG"
  compose stop automation-backup
  log "Normal routing restored; standby stopped and its model/image remain cached"
}

case "$COMMAND" in
  validate) validate ;;
  prepare) prepare ;;
  maintenance-start) maintenance_start ;;
  smoke) smoke ;;
  status) validate; compose ps automation-backup; docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' homecompute-control-plane-automation-backup-1 2>/dev/null || true ;;
  maintenance-stop) maintenance_stop ;;
  logs) validate; compose logs --tail 100 automation-backup ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
