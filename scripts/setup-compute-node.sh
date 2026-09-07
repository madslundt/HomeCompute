#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/compose.yaml"
ENV_TEMPLATE="$REPO_ROOT/config/compute-node.env.example"
FIREWALL_HELPER="$SCRIPT_DIR/configure-compute-firewall.sh"
CACHE_INTEGRITY_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
SECRET_INITIALIZER="$SCRIPT_DIR/initialize-compute-secrets.py"
CONFIG_MIGRATOR="$SCRIPT_DIR/migrate-compute-config.py"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"
COMPUTE_CONFIG_KEYS=(
  COMPUTE_NODE_NAME VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION
  MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS COMPUTE_HOST_PORTS
  VLLM_HOST_PORT EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT TTS_HOST_PORT WYOMING_TTS_HOST_PORT GATEWAY_CIDR
  HF_TOKEN_FILE VLLM_API_KEY_FILE MIN_FREE_DISK_GIB VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS
  VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION VLLM_SHM_SIZE
  VLLM_ATTENTION_BACKEND VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER
  VLLM_SPECULATIVE_CONFIG ALLOW_UNSUPPORTED_HOST
  EMBEDDING_MODEL_ID EMBEDDING_MODEL_REVISION EMBEDDING_MODEL_LICENSE_ID EMBEDDING_GPU_MEMORY_UTILIZATION
  VISION_MODEL_ID VISION_MODEL_REVISION VISION_MODEL_LICENSE_ID VISION_GPU_MEMORY_UTILIZATION
  STT_MODEL_ID STT_MODEL_REVISION STT_MODEL_LICENSE_ID STT_GPU_MEMORY_UTILIZATION
  PIPER_IMAGE PIPER_VOICE_ID PIPER_VOICE_REVISION PIPER_VOICE_LICENSE_ID
  PIPER_MODEL_SHA256 PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256
  TTS_MODEL_ALIAS TTS_VOICE_ALIAS TTS_MAX_INPUT_CHARS HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES TEXT_ARTIFACT_MAX_BYTES TEXT_ARTIFACT_MAX_FILES EMBEDDING_ARTIFACT_MAX_BYTES EMBEDDING_ARTIFACT_MAX_FILES VISION_ARTIFACT_MAX_BYTES VISION_ARTIFACT_MAX_FILES STT_ARTIFACT_MAX_BYTES STT_ARTIFACT_MAX_FILES
)
LEGACY_CONFIG_KEYS=("${COMPUTE_CONFIG_KEYS[@]}" FIREWALL_CONFIRMED); COMMAND="${1:-help}"; (($# == 0)) || shift
ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1200
usage() {
  cat <<'USAGE'
Usage: setup-compute-node.sh COMMAND [OPTIONS]
Commands:
  preflight  Read-only platform, Docker/NVIDIA, disk, and clock gates
  init       Create the service account, directories, config, and secret files
  firewall   Install/reapply and verify the persistent compute ingress policy
  validate   Reject mutable artifacts and unsafe deployment inputs
  install    Validate, pull, preflight, acquire artifacts, deploy, and smoke
  up         Start an initialized and validated release
  status     Show Compose and edge-health state
  smoke      Verify liveness, auth denial, aliases, and Responses protocols
  logs       Show the last 200 text-runtime log lines
  down       Stop this release without deleting persistent data
  rollback   Deploy the exact prior release supplied with --env PRIOR_ENV
  help       Show this help
Options:
  --env FILE       Release environment file (default: /etc/gb10-ai/gb10.env)
  --wait SECONDS   Bounded readiness timeout (default: 1200)

The installer does not install or replace DGX OS, drivers, Docker, or the
NVIDIA Container Toolkit. For a non-loopback listener, run the firewall
command after setting the exact private listener and orchestrator CIDR.
USAGE
}
log() { printf '[compute-node] %s\n' "$*"; }
warn() { printf '[compute-node] WARNING: %s\n' "$*" >&2; }
die() { printf '[compute-node] ERROR: %s\n' "$*" >&2; exit 1; }
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
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
}
config_needs_migration() {
  local line saw_ports=false saw_budget=false
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" != COMPUTE_HOST_PORTS=* ]] || saw_ports=true
    [[ "$line" != HF_CACHE_MAX_BYTES=* ]] || saw_budget=true
    [[ "$line" != FIREWALL_CONFIRMED=* ]] || return 0
  done <"$ENV_FILE"
  [[ "$saw_ports" == false || "$saw_budget" == false ]]
}
migrate_config_if_needed() {
  local temporary
  [[ -e "$ENV_FILE" ]] || return
  config_needs_migration || return
  load_trusted_env_file "$ENV_FILE" 0 "Legacy compute-node configuration" "${LEGACY_CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed legacy configuration: $ENV_FILE"
  [[ -x "$CONFIG_MIGRATOR" ]] || die "Configuration migrator is missing or not executable"
  temporary="$(mktemp "${ENV_FILE}.migration.XXXXXX")"
  if ! "$CONFIG_MIGRATOR" "$ENV_FILE" "$ENV_TEMPLATE" "$temporary"; then rm -f -- "$temporary"; die "Could not migrate compute configuration"; fi
  chown root:root "$temporary"; chmod 0600 "$temporary"; mv -f "$temporary" "$ENV_FILE"
  log "Migrated legacy compute configuration to the exact multi-service schema"
}
load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE (run init first)"
  load_trusted_env_file "$ENV_FILE" 0 "Compute-node configuration" "${COMPUTE_CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
is_placeholder() { [[ -z "$1" || "$1" == *REPLACE_WITH* || "$1" == *CHANGEME* || "$1" == *TODO* ]]; }
require_env_value() { local value="${!1:-}"; is_placeholder "$value" && die "Missing or placeholder setting: $1"; return 0; }
is_ipv4_address() {
  local octet; local -a octets; local IFS='.'
  read -r -a octets <<<"$1"; ((${#octets[@]} == 4)) || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] && ((10#$octet <= 255)) || return 1
  done
}
is_ipv4_cidr() {
  local address="${1%/*}" prefix=32; [[ "$1" == */* ]] && prefix="${1##*/}"
  is_ipv4_address "$address" && [[ "$prefix" =~ ^[0-9]{1,2}$ ]] && ((10#$prefix <= 32))
}
validate_compute_ports() {
  local name port number previous=-1 expected=''
  local -a port_names=(
    VLLM_HOST_PORT EMBEDDING_HOST_PORT VISION_HOST_PORT STT_HOST_PORT
    TTS_HOST_PORT WYOMING_TTS_HOST_PORT
  )
  require_env_value COMPUTE_HOST_PORTS
  for name in "${port_names[@]}"; do
    require_env_value "$name"; port="${!name}"
    [[ "$port" =~ ^[0-9]+$ ]] || die "$name must be numeric"
    number=$((10#$port))
    ((number >= 1024 && number <= 65535)) || die "$name must be 1024 through 65535"
    [[ "$port" == "$number" ]] || die "$name must use canonical decimal notation"
    ((number > previous)) || die "Compute listener ports must be sorted and unique"
    previous=$number; expected+="${expected:+,}$port"
  done
  [[ "$COMPUTE_HOST_PORTS" == "$expected" ]] ||
    die "COMPUTE_HOST_PORTS must exactly match the six ordered listener ports"
}
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
file_links() { stat -c '%h' "$1" 2>/dev/null || stat -f '%l' "$1"; }
check_secret_file() {
  local name="$1" path="${!1:-}" parent mode
  require_env_value "$name"; [[ -f "$path" && -s "$path" && ! -L "$path" ]] || die "$name must be a non-empty regular file: $path"
  [[ "$(file_links "$path")" == 1 ]] || die "$name must not have additional hard links"
  mode="$(file_mode "$path")"; [[ "$mode" == 440 ]] || die "$name must have mode 0440 (found $mode)"
  [[ "$(stat -c '%u' "$path")" == 0 && "$(stat -c '%g' "$path")" == "$(id -g gb10-ai)" ]] ||
    die "$name must be owned by root:gb10-ai"
  parent="$(dirname -- "$path")"; [[ -d "$parent" && ! -L "$parent" && "$(file_mode "$parent")" == 750 ]] ||
    die "$name parent must be a non-symlink directory with mode 0750"
  [[ "$(stat -c '%u:%g' "$parent")" == "0:$(id -g gb10-ai)" ]] || die "$name parent must be owned by root:gb10-ai"
}
validate_firewall_inputs() {
  [[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]] && return
  require_env_value GATEWAY_CIDR
  [[ "$GB10_BIND_ADDRESS" == 10.77.10.10 ]] || die "Production private listener must be 10.77.10.10"
  [[ "$GATEWAY_CIDR" == 10.77.10.2/32 ]] || die "Production gateway source must be exactly 10.77.10.2/32"
}
firewall_policy() {
  local action="$1"
  [[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]] && return
  [[ -x "$FIREWALL_HELPER" ]] || die "Firewall helper is missing or not executable: $FIREWALL_HELPER"
  "$FIREWALL_HELPER" "$action" "$GB10_BIND_ADDRESS" "$GATEWAY_CIDR" "$COMPUTE_HOST_PORTS"
}
validate_firewall_policy() {
  validate_firewall_inputs
  firewall_policy verify
}
validate_init_paths() {
  require_env_value GB10_ROOT; require_env_value HF_TOKEN_FILE; require_env_value VLLM_API_KEY_FILE
  [[ ! -L "$ENV_FILE" ]] || die "Environment file must not be a symbolic link"
  [[ "$GB10_ROOT" == /srv/gb10-ai ]] || die "Phase C requires GB10_ROOT=/srv/gb10-ai"
  [[ "$HF_TOKEN_FILE" == /etc/gb10-ai/secrets/hf_token ]] || die "Unexpected Hugging Face token path"
  [[ "$VLLM_API_KEY_FILE" == /etc/gb10-ai/secrets/vllm_api_key ]] || die "Unexpected vLLM API-key path"
}
validate_config() {
  local registry_secret="${1:-true}" verify_firewall="${2:-true}" value mode
  load_env; require_command jq
  for value in COMPUTE_NODE_NAME VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION \
    MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256 \
    GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS VLLM_HOST_PORT VLLM_API_KEY_FILE \
    HF_TOKEN_FILE MIN_FREE_DISK_GIB VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS VLLM_MAX_BATCHED_TOKENS \
    VLLM_GPU_MEMORY_UTILIZATION VLLM_SHM_SIZE VLLM_ATTENTION_BACKEND VLLM_MOE_BACKEND \
    VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER ALLOW_UNSUPPORTED_HOST HF_CACHE_MAX_BYTES \
    HF_CACHE_MAX_FILES TEXT_ARTIFACT_MAX_BYTES TEXT_ARTIFACT_MAX_FILES; do require_env_value "$value"; done
  [[ ! -L "$ENV_FILE" ]] || die "Environment file must not be a symbolic link"
  mode="$(file_mode "$ENV_FILE")"; [[ "$mode" =~ ^(600|640|400|440)$ ]] || die "Environment file must not be world-accessible"
  [[ "$COMPUTE_NODE_NAME" =~ ^[a-z][a-z0-9-]{0,31}$ && "$(hostname -s)" == "$COMPUTE_NODE_NAME" ]] || die "Compute host name does not match COMPUTE_NODE_NAME"
  [[ "$VLLM_IMAGE" =~ ^[a-zA-Z0-9._/:@-]+@sha256:[a-f0-9]{64}$ ]] || die "VLLM_IMAGE must be an immutable digest"
  [[ "$MODEL_ID" =~ ^[a-zA-Z0-9._/-]+$ ]] || die "MODEL_ID contains unsafe characters"
  for value in "$MODEL_REVISION" "$TOKENIZER_REVISION" "$CODE_REVISION"; do [[ "$value" =~ ^[a-fA-F0-9]{40}$ ]] || die "Every revision must be a full commit"; done
  [[ "$CHAT_TEMPLATE_SHA256" =~ ^[a-fA-F0-9]{64}$ ]] || die "CHAT_TEMPLATE_SHA256 must be a 64-hex digest"
  [[ "$MODEL_PROVENANCE_URL" =~ ^https://[^[:space:]]+$ ]] || die "MODEL_PROVENANCE_URL must use HTTPS"
  [[ "$MODEL_LICENSE_ID" =~ ^[a-zA-Z0-9._+-]+$ && "$MODEL_WEIGHT_FORMAT" =~ ^[a-zA-Z0-9._-]+$ && "$MODEL_QUANTIZATION" =~ ^[a-zA-Z0-9._-]+$ ]] || die "Malformed provenance metadata"
  validate_init_paths
  [[ "$GB10_RUNTIME_UID" =~ ^[0-9]+$ && "$GB10_RUNTIME_GID" =~ ^[0-9]+$ ]] || die "Runtime identity must be numeric"
  id gb10-ai >/dev/null 2>&1 || die "gb10-ai service account is missing (run init)"
  [[ "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" == "$(id -u gb10-ai):$(id -g gb10-ai)" ]] || die "Runtime identity does not match gb10-ai"
  [[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]] || is_ipv4_cidr "${GATEWAY_CIDR:-}" || die "GATEWAY_CIDR must be IPv4/CIDR for a private listener"
  validate_compute_ports
  if [[ "$verify_firewall" == true ]]; then validate_firewall_policy; else validate_firewall_inputs; fi
  [[ "$ALLOW_UNSUPPORTED_HOST" == true || "$ALLOW_UNSUPPORTED_HOST" == false ]] || die "ALLOW_UNSUPPORTED_HOST must be true or false"
  [[ "$MIN_FREE_DISK_GIB" =~ ^[1-9][0-9]*$ ]] || die "MIN_FREE_DISK_GIB must use canonical positive decimal notation"
  [[ "$ALLOW_UNSUPPORTED_HOST" == true ]] || ((MIN_FREE_DISK_GIB >= 200)) || die "Production MIN_FREE_DISK_GIB cannot be lower than 200"
  [[ "$TEXT_ARTIFACT_MAX_BYTES:$TEXT_ARTIFACT_MAX_FILES:$HF_CACHE_MAX_BYTES:$HF_CACHE_MAX_FILES" == 25000000000:32:536870912000:50000 ]] || die "Text/cache acquisition limits differ from the qualified tuple"
  if [[ ! "$VLLM_MAX_MODEL_LEN" =~ ^[0-9]+$ ]] || ((VLLM_MAX_MODEL_LEN < 8192)); then die "Model length is below baseline"; fi
  if [[ ! "$VLLM_MAX_NUM_SEQS" =~ ^[0-9]+$ ]] || ((VLLM_MAX_NUM_SEQS < 1 || VLLM_MAX_NUM_SEQS > 16)); then die "Invalid sequence limit"; fi
  if [[ ! "$VLLM_MAX_BATCHED_TOKENS" =~ ^[0-9]+$ ]] || ((VLLM_MAX_BATCHED_TOKENS <= 0)); then die "Invalid batched-token limit"; fi
  if [[ ! "$VLLM_GPU_MEMORY_UTILIZATION" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! awk -v n="$VLLM_GPU_MEMORY_UTILIZATION" 'BEGIN {exit !(n>=.20 && n<=.90)}'; then die "GPU memory utilization must be 0.20 through 0.90"; fi
  [[ "$VLLM_SHM_SIZE" =~ ^[0-9]+[mMgG][bB]?$ ]] || die "Invalid VLLM_SHM_SIZE"
  for value in "$VLLM_ATTENTION_BACKEND" "$VLLM_MOE_BACKEND" "$VLLM_REASONING_PARSER" "$VLLM_TOOL_CALL_PARSER"; do [[ "$value" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "Malformed backend/parser"; done
  [[ "$MODEL_ID" == nvidia/Qwen3.6-35B-A3B-NVFP4 && "$MODEL_PROVENANCE_URL" == https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4 && "${MODEL_LICENSE_ID,,}" == apache-2.0 && "${MODEL_WEIGHT_FORMAT,,}" == modelopt-safetensors && "${MODEL_QUANTIZATION,,}" == nvfp4 ]] || die "Only the pinned Qwen3.6 NVFP4 baseline recipe is supported"
  [[ "$VLLM_ATTENTION_BACKEND:$VLLM_MOE_BACKEND:$VLLM_REASONING_PARSER:$VLLM_TOOL_CALL_PARSER" == flashinfer:marlin:qwen3:qwen3_xml ]] || die "Qwen3.6 backend/parser recipe changed"
  case "${VLLM_SPECULATIVE_CONFIG:-}" in '') ;; '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}') warn "Experimental MTP override cannot produce acceptance evidence" ;; *) die "Unsupported speculative configuration" ;; esac
  [[ "$registry_secret" != true ]] || check_secret_file HF_TOKEN_FILE; check_secret_file VLLM_API_KEY_FILE
  require_command docker; docker compose version >/dev/null; compose config --quiet
  log "Configuration is complete, immutable, firewall-bound, and Compose-valid"
}
platform_description() {
  local board=unknown gpu=unknown
  [[ ! -r /proc/device-tree/model ]] || board="$(tr -d '\0' </proc/device-tree/model)"
  [[ "$board" != unknown ]] || board="$(dmidecode -s system-product-name 2>/dev/null || printf unknown)"
  gpu="$(nvidia-smi -L 2>/dev/null || printf unknown)"; printf '%s | %s' "$board" "$gpu"
}
preflight() {
  local failures=0 command_name hardware memory_kib available_kib disk_path
  if [[ ! "${MIN_FREE_DISK_GIB:-}" =~ ^[0-9]+$ ]] || ((10#$MIN_FREE_DISK_GIB <= 0)); then die "Preflight requires a positive MIN_FREE_DISK_GIB"; fi
  [[ "${ALLOW_UNSUPPORTED_HOST:-}" == true || "${ALLOW_UNSUPPORTED_HOST:-}" == false ]] || die "Preflight requires ALLOW_UNSUPPORTED_HOST=true or false"
  [[ "$ALLOW_UNSUPPORTED_HOST" == true ]] || ((10#$MIN_FREE_DISK_GIB >= 200)) || die "Production MIN_FREE_DISK_GIB cannot be lower than 200"
  require_env_value GB10_ROOT
  log "Preflight is read-only; it will not pull images or alter the host"
  for command_name in docker nvidia-smi nvidia-ctk nvidia-container-cli curl awk jq sha256sum realpath python3 cmp; do
    if command -v "$command_name" >/dev/null 2>&1; then log "$command_name: $(command -v "$command_name")"; else warn "Missing command: $command_name"; failures=$((failures+1)); fi
  done
  [[ "$(uname -s)" == Linux ]] || { warn "Target must run Linux/DGX OS"; failures=$((failures+1)); }
  [[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || { warn "Target must be ARM64"; failures=$((failures+1)); }
  if command -v docker >/dev/null; then
    docker info >/dev/null 2>&1 || { warn "Docker daemon is unavailable"; failures=$((failures+1)); }
    docker info --format '{{json .Runtimes}}' 2>/dev/null | jq -e 'has("nvidia")' >/dev/null || { warn "Docker has no registered NVIDIA runtime"; failures=$((failures+1)); }
  fi
  if ! command -v nvidia-smi >/dev/null || ! nvidia-smi --query-gpu=name,driver_version --format=csv,noheader >/dev/null; then warn "Host GPU access failed"; failures=$((failures+1)); fi
  if ! command -v nvidia-container-cli >/dev/null || ! nvidia-container-cli info >/dev/null 2>&1; then warn "NVIDIA container toolkit cannot access the GPU"; failures=$((failures+1)); fi
  hardware="$(platform_description)"; memory_kib="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
  log "Hardware: $hardware"; log "System memory KiB: ${memory_kib:-unknown}"
  if [[ "$hardware" =~ (GB10|DGX.Spark|Grace.Blackwell|GX10) && "${memory_kib:-0}" =~ ^[0-9]+$ ]] && ((memory_kib >= 115*1024*1024)); then :
  elif [[ "$ALLOW_UNSUPPORTED_HOST" == true ]]; then warn "Non-production platform override active; no production qualification evidence may be produced"
  else warn "Expected a verified 128 GB-class GB10/DGX Spark platform"; failures=$((failures+1)); fi
  disk_path="$GB10_ROOT"; [[ -e "$disk_path" ]] || disk_path="$(dirname -- "$GB10_ROOT")"; [[ -e "$disk_path" ]] || disk_path=/
  available_kib="$(df -Pk "$disk_path" | awk 'NR==2 {print $4}')"; log "Free disk KiB at $disk_path: $available_kib"
  ((available_kib >= MIN_FREE_DISK_GIB*1024*1024)) || { warn "Free disk is below ${MIN_FREE_DISK_GIB} GiB"; failures=$((failures+1)); }
  if command -v timedatectl >/dev/null && [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" == yes ]]; then log "Clock synchronization: verified"
  else warn "Clock synchronization is not verified"; failures=$((failures+1)); fi
  ((failures == 0)) || die "Preflight found $failures blocking issue(s)"; log "Preflight passed"
}
create_service_account() { id gb10-ai >/dev/null 2>&1 || useradd --system --user-group --home-dir "$GB10_ROOT" --shell /usr/sbin/nologin gb10-ai; getent group gb10-ai >/dev/null || die "gb10-ai group is missing"; }
pin_runtime_identity() {
  local temporary_env uid gid; uid="$(id -u gb10-ai)"; gid="$(id -g gb10-ai)"; temporary_env="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk -v uid="$uid" -v gid="$gid" '/^GB10_RUNTIME_UID=/{print "GB10_RUNTIME_UID="uid;saw_u=1;next}/^GB10_RUNTIME_GID=/{print "GB10_RUNTIME_GID="gid;saw_g=1;next}{print}END{if(!saw_u)print "GB10_RUNTIME_UID="uid;if(!saw_g)print "GB10_RUNTIME_GID="gid}' "$ENV_FILE" >"$temporary_env"
  chmod 0600 "$temporary_env"; chown root:root "$temporary_env"; mv -f "$temporary_env" "$ENV_FILE"
}
provision_directories() {
  local directory
  for directory in "" cache cache/huggingface cache/vllm models logs manifests releases runtime; do
    path_must_be_missing_or_directory "$GB10_ROOT${directory:+/$directory}" "Compute storage directory"
  done
  for directory in cache/huggingface cache/vllm models logs; do
    install -d -m 0750 -o gb10-ai -g gb10-ai "$GB10_ROOT/$directory"
  done
  install -d -m 0750 -o root -g gb10-ai "$GB10_ROOT/manifests" "$GB10_ROOT/releases" "$GB10_ROOT/runtime"
}
path_must_be_missing_or_regular() {
  local path="$1" label="$2"
  [[ ! -e "$path" && ! -L "$path" ]] || [[ -f "$path" && ! -L "$path" ]] ||
    die "$label must be absent or a regular non-symlink file: $path"
}
path_must_be_missing_or_directory() {
  local path="$1" label="$2"
  [[ ! -e "$path" && ! -L "$path" ]] || [[ -d "$path" && ! -L "$path" ]] ||
    die "$label must be absent or a non-symlink directory: $path"
}
precheck_initialization_targets() {
  local env_parent=/etc/gb10-ai secret_parent=/etc/gb10-ai/secrets
  path_must_be_missing_or_directory "$env_parent" "Environment parent"
  [[ -d /etc && ! -L /etc ]] || die "Configuration root must be a non-symlink directory: /etc"
  path_must_be_missing_or_directory /srv "Compute storage parent"
  path_must_be_missing_or_regular "$ENV_FILE" "Environment destination"
  path_must_be_missing_or_directory "$secret_parent" "Secret parent"
  path_must_be_missing_or_regular /etc/gb10-ai/secrets/hf_token "Hugging Face token destination"
  path_must_be_missing_or_regular /etc/gb10-ai/secrets/vllm_api_key "vLLM API-key destination"
  path_must_be_missing_or_directory /srv/gb10-ai "Compute storage root"
}
initialize() {
  local env_parent; require_root
  for command_name in awk getent install useradd mktemp ln python3; do require_command "$command_name"; done
  [[ -x "$SECRET_INITIALIZER" ]] || die "Secret initializer is missing or not executable: $SECRET_INITIALIZER"
  [[ "$ENV_FILE" == /etc/gb10-ai/gb10.env ]] || die "init only writes /etc/gb10-ai/gb10.env"
  precheck_initialization_targets
  env_parent="$(dirname -- "$ENV_FILE")"; install -d -m 0750 -o root -g root "$env_parent"
  if [[ ! -e "$ENV_FILE" ]]; then
    local temporary_env
    temporary_env="$(mktemp "$env_parent/.gb10.env.XXXXXX")"
    install -m 0600 -o root -g root "$ENV_TEMPLATE" "$temporary_env"
    ln -- "$temporary_env" "$ENV_FILE" || { rm -f -- "$temporary_env"; die "Environment destination appeared during initialization"; }
    rm -f -- "$temporary_env"
  fi
  migrate_config_if_needed; load_env; validate_init_paths; create_service_account; pin_runtime_identity; load_env; provision_directories
  install -d -m 0750 -o root -g gb10-ai "$(dirname -- "$HF_TOKEN_FILE")"
  "$SECRET_INITIALIZER" --directory "$(dirname -- "$HF_TOKEN_FILE")" --uid 0 --gid "$(id -g gb10-ai)"
  log "Initialized. Supply pinned values and the HF token; run firewall for a private listener."
}
wait_ready() {
  local elapsed=0 interval=10 container_id health
  log "Waiting at most ${WAIT_SECONDS}s for model readiness"
  while ((elapsed < WAIT_SECONDS)); do
    container_id="$(compose ps -q text-primary 2>/dev/null || true)"; health=not-created
    [[ -z "$container_id" ]] || health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
    [[ "$health" != healthy ]] || { log "text-primary is healthy"; return; }
    [[ "$health" != exited && "$health" != dead ]] || { compose logs --tail 100 text-primary >&2; die "text-primary stopped before readiness"; }
    ((elapsed % 30)) || log "Still loading (${elapsed}s; state=$health)"; sleep "$interval"; elapsed=$((elapsed+interval))
  done
  compose logs --tail 100 text-primary >&2; die "Timed out waiting for text-primary readiness"
}
one_line() { tr '\n\r' '  ' <<<"$1" | awk '{$1=$1;print}'; }
gpu_field() { local value; value="$(nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits 2>/dev/null | awk 'NR==1{print;exit}' || true)"; printf '%s' "${value:-unavailable}"; }
cache_snapshot() { local repo path; repo="models--${MODEL_ID//\//--}"; path="$GB10_ROOT/cache/huggingface/hub/$repo/snapshots/$1"; realpath -e "$path" 2>/dev/null || printf unavailable; }
active_cache_manifest() { printf '%s/manifests/accepted-model-cache.json' "$GB10_ROOT"; }
model_tuple_digest() {
  printf 'schema=1\nmodel=%s\ntokenizer=%s\ncode=%s\nrepo=%s\n' \
    "$MODEL_REVISION" "$TOKENIZER_REVISION" "$CODE_REVISION" "$MODEL_ID" |
    sha256sum | awk '{print $1}'
}
tuple_cache_manifest() { printf '%s/manifests/accepted-model-cache-%s.json' "$GB10_ROOT" "$(model_tuple_digest)"; }
cache_integrity() {
  local action="$1"
  shift
  [[ -x "$CACHE_INTEGRITY_HELPER" ]] || die "Cache-integrity helper is missing or not executable: $CACHE_INTEGRITY_HELPER"
  "$CACHE_INTEGRITY_HELPER" "$action" \
    --cache-root "$GB10_ROOT/cache/huggingface" \
    --repo-id "$MODEL_ID" \
    --revision "$MODEL_REVISION" \
    --revision "$TOKENIZER_REVISION" \
    --revision "$CODE_REVISION" "$@"
}
activate_cache_manifest() {
  local source="$1" active temporary
  active="$(active_cache_manifest)"
  path_must_be_missing_or_regular "$active" "Active model-cache manifest"
  temporary="$(mktemp "$GB10_ROOT/manifests/.active-model-cache.XXXXXX")"
  install -m 0440 -o root -g gb10-ai "$source" "$temporary"
  mv -fT "$temporary" "$active"
}
runtime_cache_helper() { printf '%s/runtime/model-cache-integrity.py' "$GB10_ROOT"; }
install_runtime_cache_helper() {
  local destination temporary
  destination="$(runtime_cache_helper)"; temporary="$(mktemp "$GB10_ROOT/runtime/.model-cache-integrity.XXXXXX")"
  install -m 0440 -o root -g gb10-ai "$CACHE_INTEGRITY_HELPER" "$temporary"
  mv -fT "$temporary" "$destination"
}
verify_runtime_cache_helper() {
  local destination
  destination="$(runtime_cache_helper)"
  config_path_is_trusted "$destination" 0 "Installed cache-integrity helper" ||
    die "Installed cache-integrity helper is missing or untrusted"
  cmp -s "$CACHE_INTEGRITY_HELPER" "$destination" ||
    die "Installed cache-integrity helper differs from this release; run install"
}
accept_model_cache() {
  local manifest temporary
  manifest="$(tuple_cache_manifest)"
  path_must_be_missing_or_regular "$manifest" "Tuple model-cache manifest"
  if [[ -e "$manifest" ]]; then
    config_path_is_trusted "$manifest" 0 "Tuple model-cache manifest" ||
      die "Existing tuple model-cache manifest is untrusted"
    cache_integrity verify --manifest "$manifest" ||
      die "Refusing to re-accept a mutated previously accepted model tuple"
  else
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-model-cache.XXXXXX")"
    if ! cache_integrity create >"$temporary"; then rm -f -- "$temporary"; die "Could not create accepted model-cache manifest"; fi
    chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
    ln -- "$temporary" "$manifest" || { rm -f -- "$temporary"; die "Tuple model-cache manifest appeared during acceptance"; }
    rm -f -- "$temporary"
  fi
  activate_cache_manifest "$manifest"
  log "Activated deterministic immutable model-cache manifest: $manifest"
}
verify_model_cache() {
  local active manifest
  active="$(active_cache_manifest)"; manifest="$(tuple_cache_manifest)"
  config_path_is_trusted "$manifest" 0 "Tuple model-cache manifest" ||
    die "Tuple model-cache manifest is missing or untrusted"
  config_path_is_trusted "$active" 0 "Active model-cache manifest" ||
    die "Active model-cache manifest is missing or untrusted"
  cmp -s "$manifest" "$active" || die "Active model-cache manifest does not match the configured tuple"
  cache_integrity verify --manifest "$active" ||
    die "Pinned model cache failed immutable-manifest verification"
}
write_release_record() {
  local temporary record timestamp digest accepted_manifest_sha power_profile=unavailable os_description cuda_version model_snapshot tokenizer_snapshot code_snapshot
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  accepted_manifest_sha="$(sha256sum "$(active_cache_manifest)" | awk '{print $1}')"
  if command -v nvpmodel >/dev/null 2>&1; then power_profile="$(one_line "$(nvpmodel -q --verbose 2>/dev/null || true)")"; fi
  [[ -n "$power_profile" ]] || power_profile=unavailable
  os_description="$(awk '/^PRETTY_NAME=/{sub(/^[^=]*=/,"");gsub(/^"|"$/,"");print;exit}' /etc/os-release)"
  cuda_version="$(nvidia-smi | awk '/CUDA Version:/{for(i=1;i<=NF;i++)if($i=="CUDA"&&$(i+1)=="Version:"){print $(i+2);exit}}')"
  model_snapshot="$(cache_snapshot "$MODEL_REVISION")"; tokenizer_snapshot="$(cache_snapshot "$TOKENIZER_REVISION")"; code_snapshot="$(cache_snapshot "$CODE_REVISION")"
  [[ "$model_snapshot" != unavailable && "$tokenizer_snapshot" != unavailable && "$code_snapshot" != unavailable ]] ||
    die "Pinned model/tokenizer/code snapshots are not all present in the Hugging Face cache"
  temporary="$(mktemp "$GB10_ROOT/manifests/.release.XXXXXX")"
  {
    printf 'RELEASE_RECORD_SCHEMA=%q\n' 1; printf 'INSTALLED_AT=%q\n' "$timestamp"
    printf 'PLATFORM=%q\n' "$(one_line "$(platform_description)")"; printf 'KERNEL=%q\n' "$(uname -srmo)"; printf 'OS=%q\n' "${os_description:-unknown}"
    printf 'SYSTEM_MEMORY_KIB=%q\n' "$(awk '/^MemTotal:/{print $2;exit}' /proc/meminfo)"; printf 'POWER_PROFILE=%q\n' "$power_profile"
    printf 'GPU_NAME=%q\n' "$(gpu_field name)"; printf 'GPU_UUID=%q\n' "$(gpu_field uuid)"; printf 'GPU_MEMORY_MIB=%q\n' "$(gpu_field memory.total)"
    printf 'POWER_DEFAULT_LIMIT_W=%q\n' "$(gpu_field power.default_limit)"; printf 'POWER_MAX_LIMIT_W=%q\n' "$(gpu_field power.max_limit)"
    printf 'NVIDIA_DRIVER=%q\n' "$(gpu_field driver_version)"; printf 'CUDA_COMPATIBILITY=%q\n' "${cuda_version:-unavailable}"; printf 'DOCKER_VERSION=%q\n' "$(docker version --format '{{.Server.Version}}')"
    printf 'COMPOSE_VERSION=%q\n' "$(docker compose version --short)"; printf 'NVIDIA_CTK_VERSION=%q\n' "$(one_line "$(nvidia-ctk --version)")"
    printf 'NVIDIA_CONTAINER_CLI_VERSION=%q\n' "$(one_line "$(nvidia-container-cli --version)")"
    printf 'RENDERED_CONFIG_SHA256=%q\n' "$(compose config | sha256sum | awk '{print $1}')"
    printf 'LISTENER_SCHEME=%q\n' http; printf 'LISTENER_ADDRESS=%q\n' "$GB10_BIND_ADDRESS"; printf 'LISTENER_PORT=%q\n' "$VLLM_HOST_PORT"; printf 'COMPUTE_HOST_PORTS=%q\n' "$COMPUTE_HOST_PORTS"
    printf 'VLLM_SHM_SIZE=%q\n' "$VLLM_SHM_SIZE"; printf 'VLLM_IMAGE=%q\n' "$VLLM_IMAGE"
    printf 'VLLM_IMAGE_ID=%q\n' "$(docker image inspect --format '{{.Id}}' "$VLLM_IMAGE")"; printf 'VLLM_REPO_DIGESTS=%q\n' "$(one_line "$(docker image inspect --format '{{json .RepoDigests}}' "$VLLM_IMAGE")")"
    printf 'MODEL_ID=%q\n' "$MODEL_ID"; printf 'MODEL_REVISION=%q\n' "$MODEL_REVISION"; printf 'TOKENIZER_REVISION=%q\n' "$TOKENIZER_REVISION"; printf 'CODE_REVISION=%q\n' "$CODE_REVISION"
    printf 'MODEL_SNAPSHOT=%q\n' "$model_snapshot"; printf 'TOKENIZER_SNAPSHOT=%q\n' "$tokenizer_snapshot"; printf 'CODE_SNAPSHOT=%q\n' "$code_snapshot"
    printf 'HF_CACHE_ID=%q\n' "$(stat -c '%d:%i' "$GB10_ROOT/cache/huggingface")"; printf 'VLLM_CACHE_ID=%q\n' "$(stat -c '%d:%i' "$GB10_ROOT/cache/vllm")"
    printf 'MODEL_PROVENANCE_URL=%q\n' "$MODEL_PROVENANCE_URL"; printf 'MODEL_LICENSE_ID=%q\n' "$MODEL_LICENSE_ID"; printf 'MODEL_WEIGHT_FORMAT=%q\n' "$MODEL_WEIGHT_FORMAT"; printf 'MODEL_QUANTIZATION=%q\n' "$MODEL_QUANTIZATION"; printf 'CHAT_TEMPLATE_SHA256=%q\n' "$CHAT_TEMPLATE_SHA256"
    printf 'MODEL_CACHE_MANIFEST_SHA256=%q\n' "$accepted_manifest_sha"; printf 'FIREWALL_POLICY=%q\n' "$([[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]] && printf loopback-only || printf docker-user-original-destination)"
    printf 'MODEL_CACHE_HELPER_SHA256=%q\n' "$(sha256sum "$(runtime_cache_helper)" | awk '{print $1}')"
    printf 'FIREWALL_CHAIN=%q\n' "$([[ "$GB10_BIND_ADDRESS" == 127.0.0.1 ]] && printf not-applicable || printf GB10-COMPUTE)"; printf 'GATEWAY_CIDR=%q\n' "${GATEWAY_CIDR:-not-applicable}"; printf 'ALLOW_UNSUPPORTED_HOST=%q\n' "$ALLOW_UNSUPPORTED_HOST"
    printf 'GB10_RUNTIME_UID=%q\n' "$GB10_RUNTIME_UID"; printf 'GB10_RUNTIME_GID=%q\n' "$GB10_RUNTIME_GID"; printf 'MIN_FREE_DISK_GIB=%q\n' "$MIN_FREE_DISK_GIB"; printf 'TEXT_ARTIFACT_MAX_BYTES=%q\n' "$TEXT_ARTIFACT_MAX_BYTES"; printf 'TEXT_ARTIFACT_MAX_FILES=%q\n' "$TEXT_ARTIFACT_MAX_FILES"; printf 'HF_CACHE_MAX_BYTES=%q\n' "$HF_CACHE_MAX_BYTES"; printf 'HF_CACHE_MAX_FILES=%q\n' "$HF_CACHE_MAX_FILES"
    for digest in VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION VLLM_ATTENTION_BACKEND VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER VLLM_SPECULATIVE_CONFIG; do printf '%s=%q\n' "$digest" "${!digest:-}"; done
  } >"$temporary"
  digest="$(sha256sum "$temporary" | awk '{print $1}')"; record="$GB10_ROOT/manifests/installed-$timestamp-${digest:0:12}.env"
  [[ ! -e "$record" ]] || die "Release record already exists: $record"; chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"; mv "$temporary" "$record"
  log "Wrote immutable secret-free release record: $record"
}
smoke_test() {
  local base_url api_key auth_header stream_file denial models_json responses_json terminal_json alias
  validate_config false; require_command curl; require_command jq
  base_url="http://${GB10_BIND_ADDRESS}:${VLLM_HOST_PORT}"; api_key="$(<"$VLLM_API_KEY_FILE")"
  auth_header="$(mktemp /tmp/gb10-vllm-auth.XXXXXX)"; stream_file="$(mktemp /tmp/gb10-vllm-stream.XXXXXX)"; chmod 0600 "$auth_header" "$stream_file"
  printf 'Authorization: Bearer %s\n' "$api_key" >"$auth_header"; trap 'rm -f -- "$auth_header" "$stream_file"' EXIT
  curl --fail --silent --show-error --max-time 10 "$base_url/health" >/dev/null
  denial="$(curl --silent --show-error --max-time 10 --output /dev/null --write-out '%{http_code}' "$base_url/v1/models")"; [[ "$denial" == 401 ]] || die "Missing credentials were not rejected with HTTP 401"
  denial="$(curl --silent --show-error --max-time 10 --output /dev/null --write-out '%{http_code}' -H 'Authorization: Bearer invalid-smoke-credential' "$base_url/v1/models")"; [[ "$denial" == 401 ]] || die "Invalid credentials were not rejected with HTTP 401"
  models_json="$(curl --fail --silent --show-error --max-time 30 --header "@$auth_header" "$base_url/v1/models")"
  for alias in coding automation research home meeting assistant; do jq -e --arg alias "$alias" '.data|any(.id==$alias)' <<<"$models_json" >/dev/null || die "Missing served alias: $alias"; done
  responses_json="$(curl --fail --silent --show-error --max-time 300 --header "@$auth_header" -H 'Content-Type: application/json' --data '{"model":"automation","input":"Reply with exactly READY.","max_output_tokens":16}' "$base_url/v1/responses")"
  jq -e '(.id|type=="string") and .status=="completed"' <<<"$responses_json" >/dev/null || die "Ordinary Responses request did not complete"
  curl --fail --silent --show-error --no-buffer --max-time 300 --header "@$auth_header" -H 'Content-Type: application/json' --data '{"model":"automation","input":"Reply with exactly STREAM_READY.","max_output_tokens":16,"stream":true}' "$base_url/v1/responses" >"$stream_file"
  awk '{sub(/\r$/,"")} /^event: /{e=substr($0,8); if(done)bad=1; last=e; if(e=="response.completed")done++} END{exit !(done==1 && !bad && last=="response.completed")}' "$stream_file" || die "Responses stream lacked one terminal response.completed event"
  terminal_json="$(awk '{sub(/\r$/,"")} /^event: response.completed$/{getline;sub(/\r$/,"");sub(/^data: /,"");print;exit}' "$stream_file")"
  jq -e '.type=="response.completed" and .response.status=="completed" and (.response.id|type=="string")' <<<"$terminal_json" >/dev/null || die "Responses stream terminal payload was not completed"
  rm -f -- "$auth_header" "$stream_file"; trap - EXIT
  log "Smoke passed: liveness, auth denial, aliases, ordinary Responses, and terminal streaming"
}
prepare_model_artifacts() {
  install_runtime_cache_helper
  log "Acquiring pinned model tuple in isolated fetch network"
  compose --profile prepare run --rm model-fetch
  accept_model_cache
  verify_model_cache
}
configure_firewall() {
  require_root; load_env
  is_ipv4_address "$GB10_BIND_ADDRESS" && [[ "$GB10_BIND_ADDRESS" != 0.0.0.0 ]] || die "Bind address must be a non-wildcard IPv4 address"
  [[ "$GB10_BIND_ADDRESS" != 127.0.0.1 ]] || die "Loopback listeners do not require a host ingress policy"
  is_ipv4_cidr "$GATEWAY_CIDR" || die "GATEWAY_CIDR must be IPv4/CIDR"
  validate_compute_ports
  validate_firewall_inputs; firewall_policy install
}
deploy_release() {
  require_root
  [[ "$COMMAND" != install ]] || migrate_config_if_needed
  if [[ "$COMMAND" == rollback ]]; then validate_config true false; else validate_config; fi
  create_service_account; provision_directories; preflight
  log "Pulling immutable image: $VLLM_IMAGE"; docker pull "$VLLM_IMAGE"
  docker run --pull never --rm --gpus all --user "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" --entrypoint nvidia-smi "$VLLM_IMAGE" >/dev/null
  prepare_model_artifacts
  if [[ "$COMMAND" == rollback ]]; then firewall_policy install; else firewall_policy apply; fi
  compose up -d text-primary
  firewall_policy apply; firewall_policy verify; wait_ready; smoke_test; write_release_record
  log "Release deployed as a candidate, not automatically promoted evidence."
}
start_release() {
  require_root; migrate_config_if_needed; validate_config false false; preflight; verify_model_cache; verify_runtime_cache_helper
  firewall_policy apply; compose up -d text-primary; firewall_policy apply; firewall_policy verify; wait_ready
}
show_status() { validate_config false; compose ps; local url="http://${GB10_BIND_ADDRESS}:${VLLM_HOST_PORT}"; if curl --fail --silent --max-time 3 "$url/health" >/dev/null 2>&1; then log "Health endpoint: ready"; else warn "Health endpoint: unavailable"; fi; }
parse_options "$@"
case "$COMMAND" in
  help) usage ;;
  preflight) load_env; preflight ;;
  init) initialize ;;
  firewall) configure_firewall ;;
  validate) validate_config ;;
  install|rollback) deploy_release ;;
  up) start_release ;;
  status) show_status ;;
  smoke) smoke_test ;;
  logs) load_env; compose logs --tail 200 text-primary ;;
  down) require_root; load_env; compose stop text-primary; log "Stopped text-primary; modality services and persistent artifacts retained" ;;
  *) usage >&2; die "Unknown command: $COMMAND" ;;
esac
