#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/compose.yaml"
PRIMARY_SETUP="$SCRIPT_DIR/setup-compute-node.sh"
CACHE_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  COMPUTE_NODE_NAME MODEL_DEPLOYMENT_ID VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION
  MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS COMPUTE_HOST_PORTS
  VLLM_HOST_PORT EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT TTS_HOST_PORT AUTOMATION_HOST_PORT
  WYOMING_TTS_HOST_PORT PLAPRE_WYOMING_PORT HVISKE_WYOMING_PORT GATEWAY_CIDR HF_TOKEN_FILE VLLM_API_KEY_FILE
  MIN_FREE_DISK_GIB VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION
  VLLM_SHM_SIZE VLLM_ATTENTION_BACKEND VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER
  VLLM_SPECULATIVE_CONFIG VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS ALLOW_UNSUPPORTED_HOST
  AUTOMATION_MODEL_ID AUTOMATION_MODEL_REVISION AUTOMATION_TOKENIZER_REVISION AUTOMATION_CODE_REVISION
  AUTOMATION_MODEL_LICENSE_ID AUTOMATION_MODEL_QUANTIZATION AUTOMATION_CHAT_TEMPLATE_SHA256
  AUTOMATION_ARTIFACT_MAX_BYTES AUTOMATION_ARTIFACT_MAX_FILES AUTOMATION_MAX_MODEL_LEN AUTOMATION_MAX_NUM_SEQS
  AUTOMATION_MAX_BATCHED_TOKENS AUTOMATION_GPU_MEMORY_UTILIZATION AUTOMATION_MOE_BACKEND AUTOMATION_FP8_MOE_BACKEND
  AUTOMATION_TOOL_CALL_PARSER AUTOMATION_SPECULATIVE_CONFIG AUTOMATION_DEFAULT_CHAT_TEMPLATE_KWARGS
  NVIDIA_AUTOMATION_VLLM_IMAGE NVIDIA_AUTOMATION_MODEL_ID NVIDIA_AUTOMATION_MODEL_REVISION
  NVIDIA_AUTOMATION_TOKENIZER_REVISION NVIDIA_AUTOMATION_CODE_REVISION NVIDIA_AUTOMATION_MODEL_LICENSE_ID
  NVIDIA_AUTOMATION_MODEL_QUANTIZATION NVIDIA_AUTOMATION_MODEL_PROVENANCE_URL NVIDIA_AUTOMATION_MODEL_WEIGHT_FORMAT
  NVIDIA_AUTOMATION_CHAT_TEMPLATE_SHA256 NVIDIA_AUTOMATION_ARTIFACT_MAX_BYTES NVIDIA_AUTOMATION_ARTIFACT_MAX_FILES
  NVIDIA_AUTOMATION_HOST_PORT NVIDIA_AUTOMATION_MAX_MODEL_LEN NVIDIA_AUTOMATION_MAX_NUM_SEQS
  NVIDIA_AUTOMATION_MAX_BATCHED_TOKENS NVIDIA_AUTOMATION_GPU_MEMORY_UTILIZATION NVIDIA_AUTOMATION_ATTENTION_BACKEND
  NVIDIA_AUTOMATION_MOE_BACKEND NVIDIA_AUTOMATION_REASONING_PARSER NVIDIA_AUTOMATION_TOOL_CALL_PARSER
  NVIDIA_AUTOMATION_SPECULATIVE_CONFIG NVIDIA_AUTOMATION_DEFAULT_CHAT_TEMPLATE_KWARGS
  EMBEDDING_MODEL_ID EMBEDDING_MODEL_REVISION EMBEDDING_MODEL_LICENSE_ID EMBEDDING_GPU_MEMORY_UTILIZATION
  VISION_MODEL_ID VISION_MODEL_REVISION VISION_MODEL_LICENSE_ID VISION_GPU_MEMORY_UTILIZATION
  STT_MODEL_ID STT_MODEL_REVISION STT_MODEL_LICENSE_ID STT_GPU_MEMORY_UTILIZATION
  PIPER_IMAGE PIPER_VOICE_ID PIPER_VOICE_REVISION PIPER_VOICE_LICENSE_ID PIPER_MODEL_SHA256
  PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256 TTS_MODEL_ALIAS TTS_VOICE_ALIAS TTS_MAX_INPUT_CHARS
  HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES TEXT_ARTIFACT_MAX_BYTES TEXT_ARTIFACT_MAX_FILES
  EMBEDDING_ARTIFACT_MAX_BYTES EMBEDDING_ARTIFACT_MAX_FILES VISION_ARTIFACT_MAX_BYTES
  VISION_ARTIFACT_MAX_FILES STT_ARTIFACT_MAX_BYTES STT_ARTIFACT_MAX_FILES
)

COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1200
MANIFEST=""
ROLLBACK_STATE=""

usage() {
  cat <<'USAGE'
Usage: setup-compute-automation-nvidia.sh COMMAND [OPTIONS]
Commands:
  validate    Validate the pinned NVIDIA candidate and isolated Compose profile
  prepare     Fetch and accept its immutable artifact without starting it
  install     Pull the pinned vLLM image and stage NVIDIA weights
  activate    Cold-swap the resident text model to NVIDIA for qualification
  smoke       Run auth, Responses, and basic required/automatic tool smokes
  status      Show source and NVIDIA candidate container state
  logs        Show NVIDIA candidate runtime logs
  deactivate  Stop NVIDIA and restore the exact pre-activation text model
Options:
  --env FILE       Compute environment file (default: /etc/gb10-ai/gb10.env)
  --wait SECONDS   Bounded readiness timeout (default: 1200)

Activation is an operator-started, single-resident qualification window.
While it runs, the prior text endpoint is stopped. It never promotes a route.
USAGE
}

log() { printf '[automation-nvidia] %s\n' "$*"; }
die() { printf '[automation-nvidia] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "Run this state-changing command with sudo"; }
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
manifest_path() { printf '%s/manifests/accepted-automation-nvidia-cache.json' "$GB10_ROOT"; }
rollback_path() { printf '%s/manifests/automation-nvidia-rollback.env' "$GB10_ROOT"; }

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
  MANIFEST="$(manifest_path)"
  ROLLBACK_STATE="$(rollback_path)"
}

validate_candidate() {
  "$PRIMARY_SETUP" validate --env "$ENV_FILE"
  load_env
  python3 "$SCRIPT_DIR/modelctl.py" validate --deployment automation-spark-nvidia-candidate --prefix NVIDIA_AUTOMATION_ ||
    die "NVIDIA candidate environment differs from its catalog tuple"
  [[ "$NVIDIA_AUTOMATION_HOST_PORT" == "$AUTOMATION_HOST_PORT" ]] ||
    die "NVIDIA candidate must use the reserved automation listener during its exclusive cold swap"
  compose --profile prepare-automation-nvidia --profile automation-nvidia config --quiet
  local services
  services="$(compose --profile prepare-automation-nvidia --profile automation-nvidia config --services)"
  for service in automation-nvidia-fetch automation-nvidia-edge automation-nvidia-primary; do
    [[ $'\n'"$services"$'\n' == *$'\n'"$service"$'\n'* ]] || die "Compose profile is missing $service"
  done
  log "Pinned NVIDIA Qwen3.6 candidate tuple matches the catalog"
}

install_runtime_helper() {
  local destination temporary
  destination="$GB10_ROOT/runtime/model-cache-integrity.py"
  temporary="$(mktemp "$GB10_ROOT/runtime/.model-cache-integrity.XXXXXX")"
  install -m 0440 -o root -g gb10-ai "$CACHE_HELPER" "$temporary"
  mv -fT "$temporary" "$destination"
}

verify_cache() {
  config_path_is_trusted "$MANIFEST" 0 "Accepted NVIDIA automation cache manifest" ||
    die "Accepted NVIDIA model cache is missing or untrusted; run prepare first"
  "$CACHE_HELPER" verify --cache-root "$GB10_ROOT/cache/huggingface" \
    --repo-id "$NVIDIA_AUTOMATION_MODEL_ID" --revision "$NVIDIA_AUTOMATION_MODEL_REVISION" \
    --revision "$NVIDIA_AUTOMATION_TOKENIZER_REVISION" --revision "$NVIDIA_AUTOMATION_CODE_REVISION" \
    --manifest "$MANIFEST"
}

prepare_candidate() {
  require_root; validate_candidate; install_runtime_helper
  log "Fetching the pinned NVIDIA artifact; no model is started"
  compose --profile prepare-automation-nvidia run --rm automation-nvidia-fetch
  if [[ ! -e "$MANIFEST" && ! -L "$MANIFEST" ]]; then
    local temporary
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-automation-nvidia-cache.XXXXXX")"
    if ! "$CACHE_HELPER" create --cache-root "$GB10_ROOT/cache/huggingface" \
      --repo-id "$NVIDIA_AUTOMATION_MODEL_ID" --revision "$NVIDIA_AUTOMATION_MODEL_REVISION" \
      --revision "$NVIDIA_AUTOMATION_TOKENIZER_REVISION" --revision "$NVIDIA_AUTOMATION_CODE_REVISION" >"$temporary"; then
      rm -f -- "$temporary"; die "Could not create the NVIDIA accepted-cache manifest"
    fi
    chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
    if ! ln -- "$temporary" "$MANIFEST"; then
      rm -f -- "$temporary"; die "NVIDIA cache manifest appeared during acceptance"
    fi
    rm -f -- "$temporary"
  fi
  verify_cache
  log "NVIDIA model is staged and its cache manifest is verified"
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
  local base="http://${GB10_BIND_ADDRESS}:${NVIDIA_AUTOMATION_HOST_PORT}" code models response auth_header
  auth_header="$(mktemp)"; trap 'rm -f -- "$auth_header"' EXIT
  printf 'Authorization: Bearer %s\n' "$(<"$VLLM_API_KEY_FILE")" >"$auth_header"; chmod 0600 "$auth_header"
  curl --fail --silent --max-time 10 "$base/health" >/dev/null
  code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "$base/v1/models")"
  [[ "$code" == 401 || "$code" == 403 ]] || die "Unauthenticated model listing was not denied"
  models="$(curl --fail --silent --max-time 30 --header "@$auth_header" "$base/v1/models")"
  jq -e '.data | (any(.id == "automation-moe-nvidia") and any(.id == "automation-moe"))' <<<"$models" >/dev/null ||
    die "NVIDIA candidate or stable automation alias is missing"
  response="$(curl --fail --silent --max-time 300 --header "@$auth_header" -H 'Content-Type: application/json' \
    --data '{"model":"automation-moe-nvidia","input":"Svar kun med ordet KLAR.","max_output_tokens":32}' "$base/v1/responses")"
  jq -e '.status == "completed" and ([.output[]?.content[]?.text // empty] | join(" ") | length > 0)' <<<"$response" >/dev/null ||
    die "NVIDIA Responses smoke did not complete"
  python3 "$SCRIPT_DIR/tool_call_smoke.py" --base-url "$base" --model automation-moe-nvidia \
    --api-key-file "$VLLM_API_KEY_FILE" --choices required auto || die "NVIDIA tool-call smoke failed"
  log "NVIDIA health, auth, alias, Responses, and basic tool-call smokes passed; full 64-tool gates remain required"
)

running_services() {
  compose --profile automation-moe --profile automation-nvidia ps --status running --services
}

capture_source() {
  local services automation=false text=false
  services="$(running_services)"
  if printf '%s\n' "$services" | awk '$0 == "automation-primary" {found=1} END {exit !found}'; then automation=true; fi
  if printf '%s\n' "$services" | awk '$0 == "text-primary" {found=1} END {exit !found}'; then text=true; fi
  [[ "$automation:$text" != true:true ]] || die "Both source text models are running; refusing a two-model cold swap"
  [[ "$automation:$text" != false:false ]] || die "No supported source text model is running to restore"
  if [[ "$automation" == true ]]; then printf 'SOURCE=automation\n'; else printf 'SOURCE=text\n'; fi
}

write_rollback_state() {
  local state temporary
  [[ ! -e "$ROLLBACK_STATE" && ! -L "$ROLLBACK_STATE" ]] || die "Rollback state already exists; inspect it and deactivate/restore before retrying"
  state="$(capture_source)"
  temporary="$(mktemp "$GB10_ROOT/manifests/.automation-nvidia-rollback.XXXXXX")"
  printf '%s' "$state" >"$temporary"
  chown root:root "$temporary"; chmod 0600 "$temporary"
  if ! ln -- "$temporary" "$ROLLBACK_STATE"; then
    rm -f -- "$temporary"; die "Rollback state appeared during activation"
  fi
  rm -f -- "$temporary"
}

read_rollback_source() {
  config_path_is_trusted "$ROLLBACK_STATE" 0 "NVIDIA rollback state" || return 1
  local -a lines
  mapfile -t lines <"$ROLLBACK_STATE"
  [[ ${#lines[@]} == 1 ]] || return 1
  case "${lines[0]}" in
    SOURCE=automation) printf 'automation\n' ;;
    SOURCE=text) printf 'text\n' ;;
    *) return 1 ;;
  esac
}

restore_source() {
  local source port
  source="$(read_rollback_source)" || { log "Rollback marker is missing or invalid; leaving it for manual recovery"; return 1; }
  compose --profile automation-nvidia stop automation-nvidia-primary automation-nvidia-edge >/dev/null 2>&1 || true
  if [[ "$source" == automation ]]; then
    compose --profile automation-moe up -d automation-edge automation-primary || return 1
    port="$AUTOMATION_HOST_PORT"
  else
    compose up -d text-edge text-primary || return 1
    port="$VLLM_HOST_PORT"
  fi
  if ! wait_health "$port"; then log "Source model did not recover on port $port"; return 1; fi
  rm -f -- "$ROLLBACK_STATE"
  log "Restored the prior $source text service"
}

record_candidate() {
  local timestamp record temporary
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  record="$GB10_ROOT/manifests/automation-nvidia-candidate-${timestamp}-${NVIDIA_AUTOMATION_MODEL_REVISION:0:12}.env"
  temporary="$(mktemp "$GB10_ROOT/manifests/.automation-nvidia-candidate.XXXXXX")"
  {
    printf 'INSTALLED_AT=%s\n' "$timestamp"
    printf 'MODEL_ID=%s\n' "$NVIDIA_AUTOMATION_MODEL_ID"
    printf 'MODEL_REVISION=%s\n' "$NVIDIA_AUTOMATION_MODEL_REVISION"
    printf 'VLLM_IMAGE=%s\n' "$NVIDIA_AUTOMATION_VLLM_IMAGE"
    printf 'MAX_MODEL_LEN=%s\n' "$NVIDIA_AUTOMATION_MAX_MODEL_LEN"
    printf 'MOE_BACKEND=%s\n' "$NVIDIA_AUTOMATION_MOE_BACKEND"
    printf 'ATTENTION_BACKEND=%s\n' "$NVIDIA_AUTOMATION_ATTENTION_BACKEND"
    printf 'TOOL_CALL_PARSER=%s\n' "$NVIDIA_AUTOMATION_TOOL_CALL_PARSER"
    printf 'SPECULATIVE_CONFIG=%s\n' "$NVIDIA_AUTOMATION_SPECULATIVE_CONFIG"
    printf 'DEFAULT_THINKING=disabled\nDEFAULT_STARTUP=false\nQUALIFICATION_STATUS=pending\n'
    printf 'CACHE_MANIFEST_SHA256=%s\n' "$(sha256sum "$MANIFEST" | cut -d ' ' -f 1)"
  } >"$temporary"
  chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"; mv -fT "$temporary" "$record"
  log "Wrote secret-free candidate record: $record"
}

install_candidate() {
  require_root; validate_candidate
  compose --profile prepare-automation-nvidia --profile automation-nvidia pull automation-nvidia-fetch automation-nvidia-primary
  prepare_candidate
  record_candidate
  log "Candidate installed but stopped. Run activate only during an approved qualification window."
}

activate_candidate() {
  require_root; validate_candidate; verify_cache
  write_rollback_state
  compose --profile automation-moe --profile automation-nvidia stop automation-primary automation-edge text-primary text-edge >/dev/null 2>&1 || true
  if ! compose --profile automation-nvidia up -d automation-nvidia-edge automation-nvidia-primary ||
    ! wait_health "$NVIDIA_AUTOMATION_HOST_PORT"; then
    restore_source || die "NVIDIA failed and automatic source restoration failed; inspect $ROLLBACK_STATE"
    die "NVIDIA failed to start; the prior service was restored"
  fi
  if ! smoke_candidate; then
    compose --profile automation-nvidia stop automation-nvidia-primary automation-nvidia-edge >/dev/null 2>&1 || true
    restore_source || die "NVIDIA smoke failed and automatic source restoration failed; inspect $ROLLBACK_STATE"
    die "NVIDIA smoke failed; the prior service was restored"
  fi
  log "NVIDIA is serving automation-moe-nvidia and automation-moe. Gateway routing can now be promoted."
}

deactivate_candidate() {
  require_root; load_env
  [[ -e "$ROLLBACK_STATE" && ! -L "$ROLLBACK_STATE" ]] || die "No NVIDIA activation rollback state exists"
  restore_source || die "Could not restore the prior service; inspect $ROLLBACK_STATE"
}

show_status() {
  load_env
  compose --profile automation-moe --profile automation-nvidia ps text-edge text-primary automation-edge automation-primary automation-nvidia-edge automation-nvidia-primary
}

parse_options "$@"
case "$COMMAND" in
  validate) validate_candidate ;;
  prepare) prepare_candidate ;;
  install) install_candidate ;;
  activate) activate_candidate ;;
  smoke) smoke_candidate ;;
  status) show_status ;;
  logs) load_env; compose --profile automation-nvidia logs --tail 200 automation-nvidia-primary automation-nvidia-edge ;;
  deactivate) deactivate_candidate ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
