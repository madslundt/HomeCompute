#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/compose.yaml"
PRIMARY_SETUP="$SCRIPT_DIR/setup-compute-node.sh"
CACHE_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
EDGE_HELPER="$SCRIPT_DIR/tcp-edge-proxy.py"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  COMPUTE_NODE_NAME MODEL_DEPLOYMENT_ID VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION
  MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS COMPUTE_HOST_PORTS
  VLLM_HOST_PORT EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT TTS_HOST_PORT AUTOMATION_HOST_PORT WYOMING_TTS_HOST_PORT PLAPRE_WYOMING_PORT HVISKE_WYOMING_PORT GATEWAY_CIDR
  HF_TOKEN_FILE VLLM_API_KEY_FILE MIN_FREE_DISK_GIB VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS
  VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION VLLM_SHM_SIZE
  VLLM_ATTENTION_BACKEND VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER
  VLLM_SPECULATIVE_CONFIG VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS ALLOW_UNSUPPORTED_HOST
  AUTOMATION_MODEL_ID AUTOMATION_MODEL_REVISION AUTOMATION_TOKENIZER_REVISION AUTOMATION_CODE_REVISION
  AUTOMATION_MODEL_LICENSE_ID AUTOMATION_MODEL_QUANTIZATION AUTOMATION_CHAT_TEMPLATE_SHA256 AUTOMATION_ARTIFACT_MAX_BYTES
  AUTOMATION_ARTIFACT_MAX_FILES AUTOMATION_MAX_MODEL_LEN AUTOMATION_MAX_NUM_SEQS
  AUTOMATION_MAX_BATCHED_TOKENS AUTOMATION_GPU_MEMORY_UTILIZATION AUTOMATION_MOE_BACKEND AUTOMATION_FP8_MOE_BACKEND
  AUTOMATION_TOOL_CALL_PARSER AUTOMATION_SPECULATIVE_CONFIG AUTOMATION_DEFAULT_CHAT_TEMPLATE_KWARGS
  EMBEDDING_MODEL_ID EMBEDDING_MODEL_REVISION EMBEDDING_MODEL_LICENSE_ID EMBEDDING_GPU_MEMORY_UTILIZATION
  VISION_MODEL_ID VISION_MODEL_REVISION VISION_MODEL_LICENSE_ID VISION_GPU_MEMORY_UTILIZATION
  STT_MODEL_ID STT_MODEL_REVISION STT_MODEL_LICENSE_ID STT_GPU_MEMORY_UTILIZATION
  PIPER_IMAGE PIPER_VOICE_ID PIPER_VOICE_REVISION PIPER_VOICE_LICENSE_ID
  PIPER_MODEL_SHA256 PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256
  TTS_MODEL_ALIAS TTS_VOICE_ALIAS TTS_MAX_INPUT_CHARS HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES
  TEXT_ARTIFACT_MAX_BYTES TEXT_ARTIFACT_MAX_FILES EMBEDDING_ARTIFACT_MAX_BYTES EMBEDDING_ARTIFACT_MAX_FILES
  VISION_ARTIFACT_MAX_BYTES VISION_ARTIFACT_MAX_FILES STT_ARTIFACT_MAX_BYTES STT_ARTIFACT_MAX_FILES
)

COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1200

usage() {
  cat <<'USAGE'
Usage: setup-compute-automation-moe.sh COMMAND [OPTIONS]
Commands:
  validate    Validate the pinned opt-in Qwen3.6 MoE tuple and Compose profile
  prepare     Download and accept the pinned artifact without starting it
  install     Validate, pull, prepare, and record the staged candidate
  activate    Stop Qwen3.8-27B, start automation-moe, and run protocol smoke
  smoke       Test the active automation-moe listener
  status      Show primary and automation-moe container state
  logs        Show the last 200 automation-moe log lines
  deactivate  Stop automation-moe and restore Qwen3.8-27B
  help        Show this help
Options:
  --env FILE       Compute environment file (default: /etc/gb10-ai/gb10.env)
  --wait SECONDS   Bounded readiness timeout (default: 1200)

The candidate is never part of default startup. Activation is an explicit,
single-resident cold swap. Qwen3.8 Flash-Next is unaffected and remains off.
USAGE
}

log() { printf '[automation-moe] %s\n' "$*"; }
die() { printf '[automation-moe] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun it with sudo"; }

parse_options() {
  while (($#)); do
    case "$1" in
      --env) (($# >= 2)) || die "--env requires a file"; ENV_FILE="$2"; shift 2 ;;
      --wait)
        (($# >= 2)) || die "--wait requires seconds"
        [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "--wait must be a positive integer"
        WAIT_SECONDS="$((10#$2))"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
}

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 "Compute-node configuration" "${CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}

compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
manifest_path() { printf '%s/manifests/accepted-automation-cache.json' "$GB10_ROOT"; }

validate_candidate() {
  "$PRIMARY_SETUP" validate --env "$ENV_FILE"
  load_env
  python3 "$SCRIPT_DIR/modelctl.py" validate --deployment automation-spark-primary --prefix AUTOMATION_ ||
    die "Automation candidate differs from its catalog deployment"
  [[ "$PLAPRE_WYOMING_PORT:$HVISKE_WYOMING_PORT" == 10201:10301 ]] || die "Speech listener ports changed"
  [[ "$COMPUTE_HOST_PORTS" == 8000,8001,8002,8003,8004,8005,10200,10201,10301 ]] || die "Firewall port set omits a qualified listener"
  compose --profile prepare-automation --profile automation-moe config --quiet
  local services
  services="$(compose --profile prepare-automation --profile automation-moe config --services)"
  for service in automation-fetch automation-edge automation-primary; do
    [[ $'\n'"$services"$'\n' == *$'\n'"$service"$'\n'* ]] || die "Compose profile is missing $service"
  done
  log "Pinned opt-in Qwen3.6 MoE tuple is valid"
}

install_runtime_helpers() {
  local source destination temporary
  for source in "$CACHE_HELPER" "$EDGE_HELPER"; do
    destination="$GB10_ROOT/runtime/$(basename -- "$source")"
    temporary="$(mktemp "$GB10_ROOT/runtime/.$(basename -- "$source").XXXXXX")"
    install -m 0440 -o root -g gb10-ai "$source" "$temporary"
    mv -fT "$temporary" "$destination"
  done
}

verify_cache() {
  local manifest
  manifest="$(manifest_path)"
  config_path_is_trusted "$manifest" 0 "Accepted automation cache manifest" || die "Accepted automation cache manifest is missing or untrusted"
  "$CACHE_HELPER" verify --cache-root "$GB10_ROOT/cache/huggingface" \
    --repo-id "$AUTOMATION_MODEL_ID" --revision "$AUTOMATION_MODEL_REVISION" \
    --revision "$AUTOMATION_TOKENIZER_REVISION" --revision "$AUTOMATION_CODE_REVISION" \
    --manifest "$manifest"
}

prepare_candidate() {
  require_root; validate_candidate; install_runtime_helpers
  log "Fetching the pinned public automation-moe artifact; no model is started"
  compose --profile prepare-automation run --rm automation-fetch
  local manifest temporary
  manifest="$(manifest_path)"
  if [[ ! -e "$manifest" && ! -L "$manifest" ]]; then
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-automation-cache.XXXXXX")"
    if ! "$CACHE_HELPER" create --cache-root "$GB10_ROOT/cache/huggingface" \
      --repo-id "$AUTOMATION_MODEL_ID" --revision "$AUTOMATION_MODEL_REVISION" \
      --revision "$AUTOMATION_TOKENIZER_REVISION" --revision "$AUTOMATION_CODE_REVISION" >"$temporary"; then
      rm -f -- "$temporary"; die "Could not create the automation cache manifest"
    fi
    chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
    if ! ln -- "$temporary" "$manifest"; then rm -f -- "$temporary"; die "Automation manifest appeared during acceptance"; fi
    rm -f -- "$temporary"
  fi
  verify_cache
  log "Pinned automation-moe artifact is staged and verified"
}

wait_health() {
  local port="$1" deadline=$((SECONDS + WAIT_SECONDS))
  until curl --fail --silent --max-time 3 "http://${GB10_BIND_ADDRESS}:${port}/health" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || return 1
    sleep 5
  done
}

smoke_candidate() (
  load_env
  local base="http://${GB10_BIND_ADDRESS}:${AUTOMATION_HOST_PORT}" code models response auth_header
  auth_header="$(mktemp)"; trap 'rm -f -- "$auth_header"' EXIT
  printf 'Authorization: Bearer %s\n' "$(<"$VLLM_API_KEY_FILE")" >"$auth_header"; chmod 0600 "$auth_header"
  curl --fail --silent --max-time 10 "$base/health" >/dev/null
  code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "$base/v1/models")"
  [[ "$code" == 401 || "$code" == 403 ]] || die "Unauthenticated model listing was not denied"
  models="$(curl --fail --silent --max-time 30 --header "@$auth_header" "$base/v1/models")"
  jq -e '.data | any(.id == "automation-moe")' <<<"$models" >/dev/null || die "automation-moe alias is missing"
  response="$(curl --fail --silent --max-time 300 --header "@$auth_header" -H 'Content-Type: application/json' \
    --data '{"model":"automation-moe","input":"Svar kun med ordet KLAR.","max_output_tokens":32}' "$base/v1/responses")"
  jq -e '.status == "completed" and ([.output[]?.content[]?.text // empty] | join(" ") | length > 0)' <<<"$response" >/dev/null ||
    die "Responses smoke did not complete"
  python3 "$SCRIPT_DIR/tool_call_smoke.py" --base-url "$base" --model automation-moe \
    --api-key-file "$VLLM_API_KEY_FILE" --choices required auto || die "Tool-call smoke failed"
  log "automation-moe health, auth denial, alias, Danish Responses, and required/automatic tool smokes passed"
)

write_release_record() {
  local timestamp record temporary
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  record="$GB10_ROOT/manifests/automation-candidate-${timestamp}-${AUTOMATION_MODEL_REVISION:0:12}.env"
  temporary="$(mktemp "$GB10_ROOT/manifests/.automation-candidate.XXXXXX")"
  {
    printf 'INSTALLED_AT=%s\n' "$timestamp"
    printf 'MODEL_ID=%s\n' "$AUTOMATION_MODEL_ID"
    printf 'MODEL_REVISION=%s\n' "$AUTOMATION_MODEL_REVISION"
    printf 'VLLM_IMAGE=%s\n' "$VLLM_IMAGE"
    printf 'MAX_MODEL_LEN=%s\n' "$AUTOMATION_MAX_MODEL_LEN"
    printf 'MOE_BACKEND=%s\n' "$AUTOMATION_MOE_BACKEND"
    printf 'TOOL_CALL_PARSER=%s\n' "$AUTOMATION_TOOL_CALL_PARSER"
    printf 'SPECULATIVE_CONFIG=disabled\n'
    printf 'DEFAULT_THINKING=disabled\n'
    printf 'DEFAULT_STARTUP=false\n'
    printf 'QUALIFICATION_STATUS=pending\n'
    printf 'CACHE_MANIFEST_SHA256=%s\n' "$(sha256sum "$(manifest_path)" | cut -d ' ' -f 1)"
  } >"$temporary"
  chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
  mv -fT "$temporary" "$record"
  log "Wrote secret-free staged release record: $record"
}

install_candidate() {
  require_root; validate_candidate
  compose --profile prepare-automation --profile automation-moe pull automation-fetch automation-edge automation-primary
  prepare_candidate
  write_release_record
  log "Candidate installed but not loaded; use activate for an explicit cold-swap qualification run"
}

restore_primary() {
  compose --profile automation-moe stop automation-primary automation-edge >/dev/null 2>&1 || true
  compose up -d text-edge text-primary
  wait_health "$VLLM_HOST_PORT" || die "Primary model did not recover within ${WAIT_SECONDS}s"
}

activate_candidate() {
  require_root; validate_candidate; verify_cache
  compose stop text-primary text-edge
  if ! compose --profile automation-moe up -d automation-edge automation-primary || ! wait_health "$AUTOMATION_HOST_PORT"; then
    restore_primary
    die "automation-moe failed to start; primary model restored"
  fi
  if ! smoke_candidate; then
    restore_primary
    die "automation-moe smoke failed; primary model restored"
  fi
  log "automation-moe is active as an explicit qualification lane; the ordinary automation alias is unchanged"
}

deactivate_candidate() {
  require_root; load_env; restore_primary
  log "automation-moe stopped and Qwen3.8-27B restored"
}

show_status() {
  load_env
  compose --profile automation-moe ps text-edge text-primary automation-edge automation-primary
}

parse_options "$@"
case "$COMMAND" in
  validate) validate_candidate ;;
  prepare) prepare_candidate ;;
  install) install_candidate ;;
  activate) activate_candidate ;;
  smoke) smoke_candidate ;;
  status) show_status ;;
  logs) load_env; compose --profile automation-moe logs --tail 200 automation-primary automation-edge ;;
  deactivate) deactivate_candidate ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
