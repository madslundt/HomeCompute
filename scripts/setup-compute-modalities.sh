#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
shopt -s nullglob dotglob

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/compose.yaml"
CACHE_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
FIREWALL_HELPER="$SCRIPT_DIR/configure-compute-firewall.sh"
TTS_ADAPTER="$SCRIPT_DIR/openai-wyoming-tts.py"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  COMPUTE_NODE_NAME VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION
  MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS VLLM_HOST_PORT COMPUTE_HOST_PORTS
  EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT TTS_HOST_PORT WYOMING_TTS_HOST_PORT GATEWAY_CIDR
  HF_TOKEN_FILE VLLM_API_KEY_FILE MIN_FREE_DISK_GIB VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS
  VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION VLLM_SHM_SIZE VLLM_ATTENTION_BACKEND
  VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER VLLM_SPECULATIVE_CONFIG
  ALLOW_UNSUPPORTED_HOST EMBEDDING_MODEL_ID EMBEDDING_MODEL_REVISION EMBEDDING_MODEL_LICENSE_ID
  EMBEDDING_GPU_MEMORY_UTILIZATION VISION_MODEL_ID VISION_MODEL_REVISION VISION_MODEL_LICENSE_ID
  VISION_GPU_MEMORY_UTILIZATION STT_MODEL_ID STT_MODEL_REVISION STT_MODEL_LICENSE_ID
  STT_GPU_MEMORY_UTILIZATION PIPER_IMAGE PIPER_VOICE_ID PIPER_VOICE_REVISION
  PIPER_VOICE_LICENSE_ID PIPER_MODEL_SHA256 PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256
  TTS_MODEL_ALIAS TTS_VOICE_ALIAS TTS_MAX_INPUT_CHARS HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES
  TEXT_ARTIFACT_MAX_BYTES TEXT_ARTIFACT_MAX_FILES EMBEDDING_ARTIFACT_MAX_BYTES EMBEDDING_ARTIFACT_MAX_FILES
  VISION_ARTIFACT_MAX_BYTES VISION_ARTIFACT_MAX_FILES STT_ARTIFACT_MAX_BYTES STT_ARTIFACT_MAX_FILES
)
MODALITY_SERVICES=(embedding-primary vision-primary stt-primary tts-primary tts-openai-adapter)
HTTP_SERVICES=(embedding-primary vision-primary stt-primary tts-openai-adapter)
COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1200
MODALITY="${GB10_MODALITY:-}"
SELECTED_PROFILE=""
SELECTED_SERVICES=()
SELECTED_HTTP_SERVICES=()

usage() {
  cat <<'USAGE'
Usage: setup-compute-modalities.sh COMMAND [OPTIONS]
Commands:
  validate    Read-only validation of pins, identity, paths, secret, and Compose profiles
  preflight   Read-only host and runtime prerequisite checks
  prepare     Fetch pinned public artifacts and atomically accept three cache manifests
  install     Pull, prepare, firewall, start, wait, smoke, and record a staged release
  up          Verify accepted caches and start the selected modality
  smoke       Exercise authentication and the selected modality protocol
  status      Show only the selected modality service state and health
  logs        Show the last 200 lines from the selected modality services
  down        Stop only the selected modality services; retain artifacts and text
  help        Show this help
Options:
  --env FILE       Compute environment file (default: /etc/gb10-ai/gb10.env)
  --wait SECONDS   Bounded readiness timeout (default: 1200)
  --modality NAME  embedding, vision, stt, tts, or explicit mixed-load value all
USAGE
}
log() { printf '[compute-modalities] %s\n' "$*"; }
warn() { printf '[compute-modalities] WARNING: %s\n' "$*" >&2; }
die() { printf '[compute-modalities] ERROR: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }
require_root() { [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun it with sudo"; }

parse_options() {
  while (($#)); do
    case "$1" in
      --env) (($# >= 2)) || die "--env requires a file"; ENV_FILE="$2"; shift 2 ;;
      --wait)
        (($# >= 2)) || die "--wait requires seconds"
        if [[ ! "$2" =~ ^[0-9]+$ ]] || ((10#$2 <= 0)); then die "--wait must be a positive integer"; fi
        WAIT_SECONDS="$((10#$2))"; shift 2 ;;
      --modality) (($# >= 2)) || die "--modality requires a value"; MODALITY="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
}

select_modality() {
  case "$MODALITY" in
    embedding) SELECTED_PROFILE=embedding; SELECTED_SERVICES=(embedding-primary); SELECTED_HTTP_SERVICES=(embedding-primary) ;;
    vision) SELECTED_PROFILE=vision; SELECTED_SERVICES=(vision-primary); SELECTED_HTTP_SERVICES=(vision-primary) ;;
    stt) SELECTED_PROFILE=stt; SELECTED_SERVICES=(stt-primary); SELECTED_HTTP_SERVICES=(stt-primary) ;;
    tts) SELECTED_PROFILE=tts; SELECTED_SERVICES=(tts-primary tts-openai-adapter); SELECTED_HTTP_SERVICES=(tts-openai-adapter) ;;
    all) SELECTED_PROFILE=modalities; SELECTED_SERVICES=("${MODALITY_SERVICES[@]}"); SELECTED_HTTP_SERVICES=("${HTTP_SERVICES[@]}") ;;
    '') die "Select one modality with --modality embedding|vision|stt|tts; use --modality all only for mixed-load qualification" ;;
    *) die "Unknown modality: $MODALITY" ;;
  esac
}
load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 "Compute-node configuration" "${CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
require_value() { local value="${!1:-}"; [[ -n "$value" && "$value" != *REPLACE_WITH* && "$value" != *CHANGEME* && "$value" != *TODO* ]] || die "Missing or placeholder setting: $1"; }
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
file_links() { stat -c '%h' "$1" 2>/dev/null || stat -f '%l' "$1"; }
sha256_file() { sha256sum "$1" | cut -d ' ' -f 1; }

check_secret() {
  local path="$VLLM_API_KEY_FILE" parent mode
  [[ "$path" == /etc/gb10-ai/secrets/vllm_api_key ]] || die "Unexpected API-key path"
  [[ -f "$path" && -s "$path" && ! -L "$path" ]] || die "VLLM_API_KEY_FILE must be a non-empty regular file"
  [[ "$(file_links "$path")" == 1 ]] || die "VLLM_API_KEY_FILE must not have additional hard links"
  mode="$(file_mode "$path")"; [[ "$mode" == 440 ]] || die "VLLM_API_KEY_FILE must have mode 0440"
  [[ "$(stat -c '%u:%g' "$path")" == "0:$(id -g gb10-ai)" ]] || die "VLLM_API_KEY_FILE must be owned by root:gb10-ai"
  parent="$(dirname -- "$path")"
  [[ -d "$parent" && ! -L "$parent" && "$(file_mode "$parent")" == 750 ]] || die "Secret parent must be a non-symlink directory with mode 0750"
  [[ "$(stat -c '%u:%g' "$parent")" == "0:$(id -g gb10-ai)" ]] || die "Secret parent must be owned by root:gb10-ai"
}
check_fixed_value() { [[ "${!1}" == "$2" ]] || die "$1 must be exactly $2"; }
check_revision() { [[ "$2" =~ ^[0-9a-f]{40}$ ]] || die "$1 must be a lowercase full commit revision"; }
check_hash() { [[ "$2" =~ ^[0-9a-f]{64}$ ]] || die "$1 must be a lowercase SHA-256"; }
check_directory() {
  local path="$1" identity="$2" mode="$3" label="$4"
  [[ -d "$path" && ! -L "$path" ]] || die "$label must be a non-symlink directory"
  [[ "$(stat -c '%u:%g' "$path")" == "$identity" ]] || die "$label has the wrong numeric identity"
  [[ "$(file_mode "$path")" == "$mode" ]] || die "$label must have mode 0$mode"
}
check_piper_directory() {
  local path="$GB10_ROOT/models/piper"
  [[ ! -e "$path" && ! -L "$path" ]] && return
  [[ -d "$path" && ! -L "$path" ]] || die "Piper model path must be absent or a non-symlink directory"
  [[ "$(stat -c '%u:%g' "$path")" == "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" ]] || die "Piper model directory identity must match gb10-ai"
  [[ "$(file_mode "$path")" == 750 ]] || die "Piper model directory must have mode 0750"
}
provision_piper_directory() {
  check_piper_directory
  install -d -m 0750 -o gb10-ai -g gb10-ai "$GB10_ROOT/models/piper"
  check_piper_directory
}
runtime_program_path() { printf '%s/runtime/%s' "$GB10_ROOT" "$1"; }
install_runtime_programs() {
  local source name destination temporary
  for source in "$CACHE_HELPER" "$TTS_ADAPTER"; do
    name="$(basename -- "$source")"; destination="$(runtime_program_path "$name")"
    temporary="$(mktemp "$GB10_ROOT/runtime/.${name}.XXXXXX")"
    install -m 0440 -o root -g gb10-ai "$source" "$temporary"
    mv -fT "$temporary" "$destination"
  done
}
verify_runtime_program() {
  local source="$1" destination
  destination="$(runtime_program_path "$(basename -- "$source")")"
  config_path_is_trusted "$destination" 0 "Installed runtime program" ||
    die "Installed runtime program is missing or untrusted: $destination"
  cmp -s "$source" "$destination" ||
    die "Installed runtime program differs from this release; run install"
}
verify_runtime_programs() { verify_runtime_program "$CACHE_HELPER"; verify_runtime_program "$TTS_ADAPTER"; }

validate_config() {
  local key services service
  load_env
  for key in COMPUTE_NODE_NAME GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS VLLM_HOST_PORT COMPUTE_HOST_PORTS \
    EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT TTS_HOST_PORT WYOMING_TTS_HOST_PORT \
    VLLM_IMAGE VLLM_API_KEY_FILE EMBEDDING_MODEL_ID EMBEDDING_MODEL_REVISION EMBEDDING_MODEL_LICENSE_ID \
    EMBEDDING_GPU_MEMORY_UTILIZATION VISION_MODEL_ID VISION_MODEL_REVISION VISION_MODEL_LICENSE_ID \
    VISION_GPU_MEMORY_UTILIZATION STT_MODEL_ID STT_MODEL_REVISION STT_MODEL_LICENSE_ID \
    STT_GPU_MEMORY_UTILIZATION PIPER_IMAGE PIPER_VOICE_ID PIPER_VOICE_REVISION PIPER_VOICE_LICENSE_ID \
    PIPER_MODEL_SHA256 PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256 TTS_MODEL_ALIAS TTS_VOICE_ALIAS \
    TTS_MAX_INPUT_CHARS MIN_FREE_DISK_GIB HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES EMBEDDING_ARTIFACT_MAX_BYTES \
    EMBEDDING_ARTIFACT_MAX_FILES VISION_ARTIFACT_MAX_BYTES VISION_ARTIFACT_MAX_FILES STT_ARTIFACT_MAX_BYTES \
    STT_ARTIFACT_MAX_FILES; do require_value "$key"; done
  [[ ! -L "$ENV_FILE" && "$(file_mode "$ENV_FILE")" =~ ^(400|440|600|640)$ ]] || die "Environment file must be a trusted non-symlink and not world-accessible"
  [[ "$GB10_ROOT" == /srv/gb10-ai ]] || die "GB10_ROOT must be /srv/gb10-ai"
  [[ "$COMPUTE_NODE_NAME" == home-spark && "$(hostname -s)" == home-spark ]] || die "Modality lifecycle must run on configured host home-spark"
  [[ -d "$GB10_ROOT" && ! -L "$GB10_ROOT" ]] || die "GB10_ROOT must be an initialized non-symlink directory"
  check_directory "$GB10_ROOT/cache/huggingface" "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" 750 "Hugging Face cache directory"
  check_directory "$GB10_ROOT/manifests" "0:$GB10_RUNTIME_GID" 750 "Manifest directory"
  check_directory "$GB10_ROOT/runtime" "0:$GB10_RUNTIME_GID" 750 "Runtime program directory"
  [[ "$GB10_RUNTIME_UID" =~ ^[0-9]+$ && "$GB10_RUNTIME_GID" =~ ^[0-9]+$ ]] || die "Runtime identity must be numeric"
  id gb10-ai >/dev/null 2>&1 || die "gb10-ai service account is missing"
  [[ "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" == "$(id -u gb10-ai):$(id -g gb10-ai)" ]] || die "Runtime identity does not match gb10-ai"
  check_directory "$GB10_ROOT/models" "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" 750 "Model directory"
  check_piper_directory
  case "$GB10_BIND_ADDRESS" in
    127.0.0.1) ;;
    10.77.10.10)
      require_value GATEWAY_CIDR
      [[ "$GATEWAY_CIDR" == 10.77.10.2/32 ]] || die "Gateway source must be exactly 10.77.10.2/32"
      ;;
    *) die "Modality listeners must use loopback or the qualified private address 10.77.10.10" ;;
  esac
  check_fixed_value VLLM_HOST_PORT 8000; check_fixed_value EMBEDDING_HOST_PORT 8001
  check_fixed_value VISION_HOST_PORT 8002; check_fixed_value STT_HOST_PORT 8003
  check_fixed_value TTS_HOST_PORT 8004; check_fixed_value WYOMING_TTS_HOST_PORT 10200
  check_fixed_value COMPUTE_HOST_PORTS 8000,8001,8002,8003,8004,10200
  check_fixed_value VLLM_IMAGE 'nvcr.io/nvidia/vllm@sha256:d049bead397430803ac5b705d50094492f23782716677d746ab721e43f054cf6'
  check_fixed_value PIPER_IMAGE 'rhasspy/wyoming-piper:2.4.3@sha256:f7aaafe325d18979f43a9fe8bb4db494ba28ce8c109cb982983157817705ff87'
  check_fixed_value EMBEDDING_MODEL_ID Qwen/Qwen3-VL-Embedding-2B
  check_fixed_value EMBEDDING_MODEL_REVISION 9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda
  check_fixed_value EMBEDDING_MODEL_LICENSE_ID Apache-2.0
  check_fixed_value VISION_MODEL_ID microsoft/Phi-4-multimodal-instruct
  check_fixed_value VISION_MODEL_REVISION 93f923e1a7727d1c4f446756212d9d3e8fcc5d81
  check_fixed_value VISION_MODEL_LICENSE_ID MIT
  check_fixed_value STT_MODEL_ID openai/whisper-large-v3-turbo
  check_fixed_value STT_MODEL_REVISION 41f01f3fe87f28c78e2fbf8b568835947dd65ed9
  check_fixed_value STT_MODEL_LICENSE_ID MIT
  check_revision EMBEDDING_MODEL_REVISION "$EMBEDDING_MODEL_REVISION"; check_revision VISION_MODEL_REVISION "$VISION_MODEL_REVISION"
  check_revision STT_MODEL_REVISION "$STT_MODEL_REVISION"; check_revision PIPER_VOICE_REVISION "$PIPER_VOICE_REVISION"
  check_fixed_value EMBEDDING_GPU_MEMORY_UTILIZATION 0.10
  check_fixed_value VISION_GPU_MEMORY_UTILIZATION 0.18
  check_fixed_value STT_GPU_MEMORY_UTILIZATION 0.06
  check_fixed_value PIPER_VOICE_ID da_DK-talesyntese-medium
  check_fixed_value PIPER_VOICE_REVISION 1162a9173d0ce503555aed757976b7a9912eae4c
  [[ "$PIPER_VOICE_LICENSE_ID" == CC0-1.0 ]] || die "Piper voice dataset license must be exactly CC0-1.0"
  check_fixed_value PIPER_MODEL_SHA256 b9271efd25f7b8494bbd28d48dd675c8c119daa284f3ee488008935f515f1241
  check_fixed_value PIPER_CONFIG_SHA256 89fe13bd251406cc0088570d103ea7ac35823211d8466faf913268ab8506f41b
  check_fixed_value PIPER_MODEL_CARD_SHA256 b5b7a51d4d815a307594619a519eb389b2e17839b30c0eb69a39d3ade5b1ed65
  check_hash PIPER_MODEL_SHA256 "$PIPER_MODEL_SHA256"; check_hash PIPER_CONFIG_SHA256 "$PIPER_CONFIG_SHA256"; check_hash PIPER_MODEL_CARD_SHA256 "$PIPER_MODEL_CARD_SHA256"
  check_fixed_value TTS_MODEL_ALIAS tts; check_fixed_value TTS_VOICE_ALIAS danish-default; check_fixed_value TTS_MAX_INPUT_CHARS 2000
  [[ "$MIN_FREE_DISK_GIB" =~ ^[1-9][0-9]*$ ]] || die "MIN_FREE_DISK_GIB must use canonical positive decimal notation"
  [[ "$ALLOW_UNSUPPORTED_HOST" == true ]] || ((MIN_FREE_DISK_GIB >= 200)) || die "Production MIN_FREE_DISK_GIB cannot be lower than 200"
  [[ "$EMBEDDING_ARTIFACT_MAX_BYTES:$EMBEDDING_ARTIFACT_MAX_FILES:$VISION_ARTIFACT_MAX_BYTES:$VISION_ARTIFACT_MAX_FILES:$STT_ARTIFACT_MAX_BYTES:$STT_ARTIFACT_MAX_FILES:$HF_CACHE_MAX_BYTES:$HF_CACHE_MAX_FILES" == 5000000000:32:25000000000:64:7000000000:32:536870912000:50000 ]] || die "Modality/cache acquisition limits differ from the qualified tuple"
  check_secret
  require_command docker; require_command jq; require_command python3; require_command sha256sum
  docker compose version >/dev/null
  compose --profile prepare-modalities config --quiet
  compose --profile modalities config --quiet
  services="$(compose --profile prepare-modalities --profile modalities config --services)"
  for service in modality-fetch "${MODALITY_SERVICES[@]}"; do
    [[ $'\n'"$services"$'\n' == *$'\n'"$service"$'\n'* ]] || die "Compose profile render is missing service: $service"
  done
  log "Configuration pins, identity, private listeners, secret, paths, and Compose profiles are valid"
}

preflight() {
  local failures=0 command_name available_kib
  validate_config
  log "Preflight is read-only; it will not pull images or alter the host"
  for command_name in curl docker jq nvidia-smi nvidia-ctk nvidia-container-cli python3 sha256sum stat; do
    command -v "$command_name" >/dev/null 2>&1 || { warn "Missing command: $command_name"; failures=$((failures + 1)); }
  done
  [[ "$(uname -s)" == Linux && "$(uname -m)" == aarch64 ]] || { warn "Target must be Linux/ARM64"; failures=$((failures + 1)); }
  docker info >/dev/null 2>&1 || { warn "Docker daemon is unavailable"; failures=$((failures + 1)); }
  docker info --format '{{json .Runtimes}}' 2>/dev/null | jq -e 'has("nvidia")' >/dev/null ||
    { warn "Docker has no registered NVIDIA runtime"; failures=$((failures + 1)); }
  if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader >/dev/null 2>&1 ||
      { warn "Host GPU access failed"; failures=$((failures + 1)); }
  fi
  if command -v nvidia-container-cli >/dev/null; then
    nvidia-container-cli info >/dev/null 2>&1 ||
      { warn "NVIDIA container toolkit cannot access the GPU"; failures=$((failures + 1)); }
  fi
  available_kib="$(df -Pk "$GB10_ROOT" | awk 'NR==2 {print $4}')"
  ((available_kib >= MIN_FREE_DISK_GIB * 1024 * 1024)) ||
    { warn "Free disk is below ${MIN_FREE_DISK_GIB} GiB"; failures=$((failures + 1)); }
  if command -v timedatectl >/dev/null && [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == yes ]]; then :
  else warn "Clock synchronization is not verified"; failures=$((failures + 1)); fi
  ((failures == 0)) || die "Preflight found $failures blocking issue(s)"
  log "Preflight passed"
}

manifest_for() { printf '%s/manifests/accepted-%s-cache.json' "$GB10_ROOT" "$1"; }
repo_for() { local name="${1^^}_MODEL_ID"; printf '%s' "${!name}"; }
revision_for() { local name="${1^^}_MODEL_REVISION"; printf '%s' "${!name}"; }
cache_integrity() {
  local kind="$1" action="$2"; shift 2
  "$CACHE_HELPER" "$action" --cache-root "$GB10_ROOT/cache/huggingface" \
    --repo-id "$(repo_for "$kind")" --revision "$(revision_for "$kind")" "$@"
}
verify_manifest() {
  local kind="$1" manifest
  manifest="$(manifest_for "$kind")"
  config_path_is_trusted "$manifest" 0 "Accepted $kind cache manifest" || die "Accepted $kind manifest is missing or untrusted"
  cache_integrity "$kind" verify --manifest "$manifest" || die "$kind cache differs from its accepted manifest"
}
accept_manifest() {
  local kind="$1" manifest temporary
  manifest="$(manifest_for "$kind")"
  if [[ ! -e "$manifest" && ! -L "$manifest" ]]; then
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-${kind}-cache.XXXXXX")"
    if ! cache_integrity "$kind" create >"$temporary"; then rm -f -- "$temporary"; die "Could not create $kind cache manifest"; fi
    if ! chown root:gb10-ai "$temporary" || ! chmod 0440 "$temporary"; then
      rm -f -- "$temporary"; die "Could not secure new $kind cache manifest"
    fi
    if ! ln -- "$temporary" "$manifest"; then rm -f -- "$temporary"; die "$kind manifest appeared during acceptance"; fi
    rm -f -- "$temporary"
  fi
  verify_manifest "$kind"
  log "Verified immutable accepted $kind cache manifest"
}
verify_manifests() { local kind; for kind in embedding vision stt; do verify_manifest "$kind"; done; }
accept_manifests() { local kind; for kind in embedding vision stt; do accept_manifest "$kind"; done; }
verify_piper_files() {
  local directory="$GB10_ROOT/models/piper" file expected actual
  local -a files
  check_piper_directory
  files=("$directory"/*)
  ((${#files[@]} == 3)) || die "Piper directory must contain exactly the pinned model, config, and MODEL_CARD"
  for file in "${files[@]}"; do
    [[ -f "$file" && ! -L "$file" ]] || die "Piper artifacts must be regular non-symlink files"
    [[ "$(stat -c '%u:%g' "$file")" == "0:$GB10_RUNTIME_GID" ]] || die "Accepted Piper artifacts must be owned by root:gb10-ai"
    [[ "$(file_mode "$file")" == 440 ]] || die "Accepted Piper artifacts must have mode 0440"
    case "$(basename -- "$file")" in
      "$PIPER_VOICE_ID.onnx") expected="$PIPER_MODEL_SHA256" ;;
      "$PIPER_VOICE_ID.onnx.json") expected="$PIPER_CONFIG_SHA256" ;;
      MODEL_CARD) expected="$PIPER_MODEL_CARD_SHA256" ;;
      *) die "Unexpected file in immutable Piper directory: $(basename -- "$file")" ;;
    esac
    actual="$(sha256_file "$file")"; [[ "$actual" == "$expected" ]] || die "Pinned Piper artifact SHA-256 mismatch: $(basename -- "$file")"
  done
}
accept_piper_files() {
  local directory="$GB10_ROOT/models/piper" file
  local -a files=("$directory"/*)
  ((${#files[@]} == 3)) || die "Piper fetch did not produce exactly three artifacts"
  for file in "${files[@]}"; do
    [[ -f "$file" && ! -L "$file" ]] || die "Fetched Piper artifacts must be regular non-symlink files"
    chown root:gb10-ai "$file"; chmod 0440 "$file"
  done
  verify_piper_files
}
prepare_artifacts() {
  require_root; validate_config
  install_runtime_programs
  [[ -x "$CACHE_HELPER" ]] || die "Cache integrity helper is missing or not executable"
  provision_piper_directory
  log "Fetching public pinned modality artifacts without an account token"
  compose --profile prepare-modalities run --rm modality-fetch
  accept_manifests; accept_piper_files
}

firewall() {
  local action="$1"
  if [[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]]; then
    log "Loopback listener selected; no routed modality firewall rule is installed"
    return
  fi
  [[ -x "$FIREWALL_HELPER" ]] || die "Firewall helper is missing or not executable"
  "$FIREWALL_HELPER" "$action" "$GB10_BIND_ADDRESS" "$GATEWAY_CIDR" "$COMPUTE_HOST_PORTS"
}
wait_ready() {
  local service container_id state deadline=$((SECONDS + WAIT_SECONDS))
  for service in "${SELECTED_SERVICES[@]}"; do
    state=not-created
    while ((SECONDS < deadline)); do
      container_id="$(compose ps -q "$service" 2>/dev/null || true)"
      [[ -z "$container_id" ]] || state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
      [[ "$state" != healthy ]] || { log "$service is ready"; break; }
      [[ "$state" != exited && "$state" != dead ]] || die "$service stopped before readiness"
      sleep 5
    done
    [[ "$state" == healthy ]] || die "Timed out waiting for $service"
  done
}
http_code() { curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' "$@"; }
assert_auth_denial() {
  local base="$1" method="$2" path="$3" code
  code="$(http_code --request "$method" "$base$path")"; [[ "$code" == 401 || "$code" == 403 ]] || die "Missing authentication was not denied at $base$path"
  code="$(http_code --request "$method" -H 'Authorization: Bearer invalid-modality-smoke-key' "$base$path")"; [[ "$code" == 401 || "$code" == 403 ]] || die "Invalid authentication was not denied at $base$path"
}
smoke_test() {
  local temporary auth embedding_base vision_base stt_base tts_base image_url code
  validate_config; require_command curl
  temporary="$(mktemp -d /tmp/compute-modalities-smoke.XXXXXX)"
  trap 'rm -rf -- "$temporary"' EXIT
  auth="$temporary/auth"; printf 'Authorization: Bearer %s\n' "$(<"$VLLM_API_KEY_FILE")" >"$auth"; chmod 0600 "$auth"
  embedding_base="http://$GB10_BIND_ADDRESS:$EMBEDDING_HOST_PORT"
  vision_base="http://$GB10_BIND_ADDRESS:$VISION_HOST_PORT"
  stt_base="http://$GB10_BIND_ADDRESS:$STT_HOST_PORT"
  tts_base="http://$GB10_BIND_ADDRESS:$TTS_HOST_PORT"
  if [[ "$MODALITY" == embedding || "$MODALITY" == all ]]; then
    assert_auth_denial "$embedding_base" POST /v1/embeddings
    curl --fail --silent --show-error --max-time 180 --header "@$auth" -H 'Content-Type: application/json' \
      --data '{"model":"embedding","input":"hej"}' "$embedding_base/v1/embeddings" >"$temporary/embedding.json"
    jq -e '.data[0].embedding | type == "array" and length > 0' "$temporary/embedding.json" >/dev/null || die "Embedding response was invalid"
  fi
  if [[ "$MODALITY" == vision || "$MODALITY" == all ]]; then
    assert_auth_denial "$vision_base" POST /v1/chat/completions
  python3 - "$temporary/tiny.png" <<'PY'
import base64, pathlib
pathlib.Path(__import__('sys').argv[1]).write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))
PY
  image_url="data:image/png;base64,$(python3 -c 'import base64,sys; print(base64.b64encode(open(sys.argv[1],"rb").read()).decode())' "$temporary/tiny.png")"
  jq -n --arg image "$image_url" '{model:"vision",messages:[{role:"user",content:[{type:"text",text:"Describe this image briefly."},{type:"image_url",image_url:{url:$image}}]}],max_tokens:16}' >"$temporary/vision-request.json"
    curl --fail --silent --show-error --max-time 300 --header "@$auth" -H 'Content-Type: application/json' --data-binary "@$temporary/vision-request.json" "$vision_base/v1/chat/completions" >"$temporary/vision.json"
    jq -e '.choices | type == "array" and length > 0' "$temporary/vision.json" >/dev/null || die "Vision response was invalid"
  fi
  if [[ "$MODALITY" == stt || "$MODALITY" == all ]]; then
    assert_auth_denial "$stt_base" POST /v1/audio/transcriptions
  python3 - "$temporary/tiny.wav" <<'PY'
import sys, wave
with wave.open(sys.argv[1], 'wb') as wav:
    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0' * 8000)
PY
    curl --fail --silent --show-error --max-time 300 --header "@$auth" -F model=stt -F "file=@$temporary/tiny.wav;type=audio/wav" "$stt_base/v1/audio/transcriptions" >"$temporary/stt.json"
    jq -e '.text | type == "string"' "$temporary/stt.json" >/dev/null || die "STT response was invalid"
  fi
  if [[ "$MODALITY" == tts || "$MODALITY" == all ]]; then
    assert_auth_denial "$tts_base" POST /v1/audio/speech
    curl --fail --silent --show-error --max-time 10 "$tts_base/health/ready" >/dev/null || die "TTS adapter readiness failed"
    code="$(http_code --header "@$auth" "$tts_base/v1/audio/speech")"; [[ "$code" == 404 ]] || die "TTS GET method was not denied"
    code="$(http_code --header "@$auth" -H 'Content-Type: application/json' --data '{}' "$tts_base/v1/audio/speeches")"; [[ "$code" == 404 ]] || die "Unknown TTS path was not denied"
    code="$(http_code --header "@$auth" -H 'Content-Type: application/json' --data '{"model":"wrong","voice":"danish-default","input":"Hej"}' "$tts_base/v1/audio/speech")"; [[ "$code" == 400 ]] || die "Unknown TTS model was not denied"
    code="$(http_code --header "@$auth" -H 'Content-Type: application/json' --data '{"model":"tts","voice":"wrong","input":"Hej"}' "$tts_base/v1/audio/speech")"; [[ "$code" == 400 ]] || die "Unknown TTS voice was not denied"
    jq -n --arg model "$TTS_MODEL_ALIAS" --arg voice "$TTS_VOICE_ALIAS" '{model:$model,voice:$voice,input:"Hej",response_format:"wav"}' >"$temporary/tts-request.json"
    curl --fail --silent --show-error --max-time 180 --dump-header "$temporary/tts.headers" --header "@$auth" \
      -H 'Content-Type: application/json' --data-binary "@$temporary/tts-request.json" "$tts_base/v1/audio/speech" >"$temporary/tts.wav"
  python3 - "$temporary/tts.wav" "$temporary/tts.headers" <<'PY'
import sys, wave
headers = open(sys.argv[2], encoding='iso-8859-1').read().lower()
if 'content-type: audio/wav' not in headers: raise SystemExit('TTS Content-Type is not audio/wav')
with open(sys.argv[1], 'rb') as stream:
    header = stream.read(12)
    if header[0:4] != b'RIFF' or header[8:12] != b'WAVE': raise SystemExit('TTS output is not RIFF/WAVE')
with wave.open(sys.argv[1], 'rb') as wav:
    if wav.getnchannels() < 1 or wav.getnframes() < 1: raise SystemExit('TTS output has no audio frames')
PY
    python3 - "$GB10_BIND_ADDRESS" "$WYOMING_TTS_HOST_PORT" <<'PY'
import json, socket, sys
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=10) as sock:
    sock.sendall(b'{"type":"describe","data":null}\n')
    line = sock.makefile('rb').readline(65537)
    if len(line) > 65536: raise SystemExit('Wyoming response header is too large')
    event = json.loads(line)
    if event.get('type') != 'info': raise SystemExit('Wyoming Describe did not return Info')
PY
  fi
  rm -rf -- "$temporary"; trap - EXIT
  log "Smoke passed for selected modality: $MODALITY"
}

write_release_record() {
  local timestamp temporary record digest kind manifest
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"; temporary="$(mktemp "$GB10_ROOT/manifests/.modality-release.XXXXXX")"
  trap 'rm -f -- "${temporary:-}"' EXIT
  {
    printf 'RELEASE_RECORD_SCHEMA=%q\n' 1; printf 'INSTALLED_AT=%q\n' "$timestamp"
    printf 'QUALIFICATION=%q\n' staged-not-production-qualified
    printf 'VLLM_IMAGE=%q\n' "$VLLM_IMAGE"; printf 'PIPER_IMAGE=%q\n' "$PIPER_IMAGE"
    printf 'VLLM_IMAGE_ID=%q\n' "$(docker image inspect --format '{{.Id}}' "$VLLM_IMAGE")"
    printf 'PIPER_IMAGE_ID=%q\n' "$(docker image inspect --format '{{.Id}}' "$PIPER_IMAGE")"
    printf 'RENDERED_MODALITIES_SHA256=%q\n' "$(compose --profile modalities config | sha256sum | cut -d ' ' -f 1)"
    printf 'LISTENER_ADDRESS=%q\n' "$GB10_BIND_ADDRESS"; printf 'COMPUTE_HOST_PORTS=%q\n' "$COMPUTE_HOST_PORTS"
    printf 'GB10_RUNTIME_UID=%q\n' "$GB10_RUNTIME_UID"; printf 'GB10_RUNTIME_GID=%q\n' "$GB10_RUNTIME_GID"
    local field
    for kind in EMBEDDING VISION STT; do
      field="${kind}_MODEL_ID"; printf '%s_MODEL_ID=%q\n' "$kind" "${!field}"
      field="${kind}_MODEL_REVISION"; printf '%s_MODEL_REVISION=%q\n' "$kind" "${!field}"
      field="${kind}_MODEL_LICENSE_ID"; printf '%s_MODEL_LICENSE_ID=%q\n' "$kind" "${!field}"
    done
    for kind in EMBEDDING VISION STT; do
      field="${kind}_GPU_MEMORY_UTILIZATION"; printf '%s_GPU_MEMORY_UTILIZATION=%q\n' "$kind" "${!field}"
    done
    printf 'PIPER_VOICE_ID=%q\n' "$PIPER_VOICE_ID"; printf 'PIPER_VOICE_REVISION=%q\n' "$PIPER_VOICE_REVISION"; printf 'PIPER_VOICE_LICENSE_ID=%q\n' "$PIPER_VOICE_LICENSE_ID"
    printf 'PIPER_MODEL_SHA256=%q\n' "$PIPER_MODEL_SHA256"; printf 'PIPER_CONFIG_SHA256=%q\n' "$PIPER_CONFIG_SHA256"; printf 'PIPER_MODEL_CARD_SHA256=%q\n' "$PIPER_MODEL_CARD_SHA256"
    printf 'TTS_MODEL_ALIAS=%q\n' "$TTS_MODEL_ALIAS"; printf 'TTS_VOICE_ALIAS=%q\n' "$TTS_VOICE_ALIAS"; printf 'TTS_MAX_INPUT_CHARS=%q\n' "$TTS_MAX_INPUT_CHARS"
    printf 'HF_CACHE_MAX_BYTES=%q\n' "$HF_CACHE_MAX_BYTES"; printf 'HF_CACHE_MAX_FILES=%q\n' "$HF_CACHE_MAX_FILES"
    for kind in EMBEDDING VISION STT; do field="${kind}_ARTIFACT_MAX_BYTES"; printf '%s_ARTIFACT_MAX_BYTES=%q\n' "$kind" "${!field}"; field="${kind}_ARTIFACT_MAX_FILES"; printf '%s_ARTIFACT_MAX_FILES=%q\n' "$kind" "${!field}"; done
    printf 'MODEL_CACHE_HELPER_SHA256=%q\n' "$(sha256_file "$(runtime_program_path model-cache-integrity.py)")"
    printf 'TTS_ADAPTER_SHA256=%q\n' "$(sha256_file "$(runtime_program_path openai-wyoming-tts.py)")"
    for kind in embedding vision stt; do manifest="$(manifest_for "$kind")"; printf '%s_CACHE_MANIFEST_SHA256=%q\n' "${kind^^}" "$(sha256_file "$manifest")"; done
  } >"$temporary"
  digest="$(sha256_file "$temporary")"; record="$GB10_ROOT/manifests/installed-modalities-$timestamp-${digest:0:12}.env"
  [[ ! -e "$record" && ! -L "$record" ]] || { rm -f -- "$temporary"; die "Modality release record already exists"; }
  if ! chown root:gb10-ai "$temporary" || ! chmod 0440 "$temporary" || ! ln -- "$temporary" "$record"; then
    rm -f -- "$temporary"; die "Could not atomically publish modality release record"
  fi
  rm -f -- "$temporary"
  temporary=; trap - EXIT
  log "Wrote immutable secret-free staged modality release record: $record"
}

start_modalities() {
  require_root; validate_config; provision_piper_directory; verify_manifests; verify_piper_files; verify_runtime_programs
  firewall apply
  compose --profile "$SELECTED_PROFILE" up -d "${SELECTED_SERVICES[@]}"
  firewall apply; firewall verify; wait_ready
}
install_modalities() {
  require_root; validate_config; preflight
  docker pull "$VLLM_IMAGE"; docker pull "$PIPER_IMAGE"
  prepare_artifacts
  firewall install
  compose --profile "$SELECTED_PROFILE" up -d "${SELECTED_SERVICES[@]}"
  firewall apply; firewall verify; wait_ready; smoke_test; write_release_record
  log "Modality release is staged and requires live-appliance qualification before production promotion"
}
show_status() {
  local service port path
  validate_config; compose --profile "$SELECTED_PROFILE" ps "${SELECTED_SERVICES[@]}"
  for service in "${SELECTED_HTTP_SERVICES[@]}"; do
    path=/health
    case "$service" in
      embedding-primary) port="$EMBEDDING_HOST_PORT" ;;
      vision-primary) port="$VISION_HOST_PORT" ;;
      stt-primary) port="$STT_HOST_PORT" ;;
      tts-openai-adapter) port="$TTS_HOST_PORT"; path=/health/ready ;;
    esac
    if curl --fail --silent --max-time 3 "http://$GB10_BIND_ADDRESS:$port$path" >/dev/null 2>&1; then
      log "$service health: ready"
    else
      warn "$service health: unavailable"
    fi
  done
}

parse_options "$@"
case "$COMMAND" in
  help) usage ;;
  validate) validate_config ;;
  preflight) preflight ;;
  prepare) prepare_artifacts ;;
  install) select_modality; install_modalities ;;
  up) select_modality; start_modalities ;;
  smoke) select_modality; smoke_test ;;
  status) select_modality; show_status ;;
  logs) select_modality; load_env; compose --profile "$SELECTED_PROFILE" logs --tail 200 "${SELECTED_SERVICES[@]}" ;;
  down) select_modality; require_root; load_env; compose --profile "$SELECTED_PROFILE" stop "${SELECTED_SERVICES[@]}"; log "Stopped selected modality services; text runtime and persistent artifacts retained" ;;
  *) usage >&2; die "Unknown command: $COMMAND" ;;
esac
