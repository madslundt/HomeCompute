#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/compose.yaml"
ENV_TEMPLATE="$REPO_ROOT/config/compute-node.env.example"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

COMPUTE_CONFIG_KEYS=(
  COMPUTE_NODE_NAME VLLM_IMAGE MODEL_ID MODEL_REVISION TOKENIZER_REVISION CODE_REVISION
  MODEL_PROVENANCE_URL MODEL_LICENSE_ID MODEL_WEIGHT_FORMAT MODEL_QUANTIZATION CHAT_TEMPLATE_SHA256
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS VLLM_HOST_PORT GATEWAY_CIDR
  FIREWALL_CONFIRMED HF_TOKEN_FILE VLLM_API_KEY_FILE VLLM_MAX_MODEL_LEN VLLM_MAX_NUM_SEQS
  VLLM_MAX_BATCHED_TOKENS VLLM_GPU_MEMORY_UTILIZATION VLLM_SHM_SIZE VLLM_ATTENTION_BACKEND
  VLLM_MOE_BACKEND VLLM_REASONING_PARSER VLLM_TOOL_CALL_PARSER VLLM_SPECULATIVE_CONFIG
  ALLOW_UNSUPPORTED_HOST
)

COMMAND="${1:-help}"
if (($# > 0)); then
  shift
fi

ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1200

usage() {
  cat <<'USAGE'
Usage: setup-compute-node.sh COMMAND [OPTIONS]

Commands:
  preflight  Read-only GB10/DGX OS, Docker, GPU, disk, and clock checks
  init       Create the service account, directories, config, and secret files
  validate   Reject floating/incomplete artifacts and unsafe deployment inputs
  install    Validate, pull the pinned image, verify GPU access, deploy, and smoke-test
  up         Start an already initialized and validated release
  status     Show Compose and vLLM health state
  smoke      Test /health, /v1/models, and a minimal /v1/responses request
  logs       Show the last 200 text-runtime log lines
  down       Stop this release without deleting models, caches, or secrets
  rollback   Deploy the exact prior release supplied with --env PRIOR_ENV
  help       Show this help

Options:
  --env FILE       Release environment file (default: /etc/gb10-ai/gb10.env)
  --wait SECONDS   Readiness timeout for install/up/rollback (default: 1200)

Safe first run:
  sudo ./scripts/setup-compute-node.sh init --env /etc/gb10-ai/gb10.env
  sudoedit /etc/gb10-ai/gb10.env
  sudoedit /etc/gb10-ai/secrets/hf_token
  sudo ./scripts/setup-compute-node.sh preflight --env /etc/gb10-ai/gb10.env
  sudo ./scripts/setup-compute-node.sh validate --env /etc/gb10-ai/gb10.env
  sudo ./scripts/setup-compute-node.sh install --env /etc/gb10-ai/gb10.env

The installer intentionally does not install or replace DGX OS, GPU drivers,
Docker, or the NVIDIA Container Toolkit. Supported DGX Spark-class appliances
ship that stack; changing it is a separately qualified platform operation.
USAGE
}

log() {
  printf '[compute-node] %s\n' "$*"
}

warn() {
  printf '[compute-node] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[compute-node] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun it with sudo"
}

parse_options() {
  while (($# > 0)); do
    case "$1" in
      --env)
        (($# >= 2)) || die "--env requires a file"
        ENV_FILE="$2"
        shift 2
        ;;
      --wait)
        (($# >= 2)) || die "--wait requires seconds"
        [[ "$2" =~ ^[0-9]+$ ]] || die "--wait must be a positive integer"
        ((10#$2 > 0)) || die "--wait must be greater than zero"
        WAIT_SECONDS="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE (run init first)"
  load_trusted_env_file "$ENV_FILE" 0 "Compute-node configuration" "${COMPUTE_CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

is_placeholder() {
  [[ "$1" == *REPLACE_WITH* || "$1" == *CHANGEME* || "$1" == *TODO* || -z "$1" ]]
}

is_ipv4_address() {
  local address="$1"
  local octet
  local -a octets
  local IFS='.'

  read -r -a octets <<<"$address"
  ((${#octets[@]} == 4)) || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
    ((10#$octet >= 0 && 10#$octet <= 255)) || return 1
  done
}

is_ipv4_cidr() {
  local cidr="$1"
  local address="$cidr"
  local prefix="32"

  if [[ "$cidr" == */* ]]; then
    address="${cidr%/*}"
    prefix="${cidr##*/}"
  fi
  is_ipv4_address "$address" || return 1
  [[ "$prefix" =~ ^[0-9]{1,2}$ ]] || return 1
  ((10#$prefix >= 0 && 10#$prefix <= 32))
}

require_env_value() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || die "Missing required setting: $name"
  if is_placeholder "$value"; then
    die "Replace placeholder setting: $name"
  fi
}

secret_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

check_secret_file() {
  local name="$1"
  local path="${!name:-}"
  local owner_gid
  local owner_uid
  local mode
  local parent
  local parent_mode

  require_env_value "$name"
  [[ -f "$path" ]] || die "$name does not exist: $path"
  [[ -s "$path" ]] || die "$name is empty: $path"
  [[ ! -L "$path" ]] || die "$name must not be a symbolic link: $path"
  mode="$(secret_mode "$path")"
  owner_uid="$(stat -c '%u' "$path")"
  owner_gid="$(stat -c '%g' "$path")"
  [[ "$mode" == "440" ]] || die "$name must have mode 0440 for the non-root container (found $mode)"
  [[ "$owner_uid" == "0" ]] || die "$name must be owned by root"
  [[ "$owner_gid" == "$(id -g gb10-ai)" ]] || die "$name must be group-owned by gb10-ai"

  parent="$(dirname -- "$path")"
  [[ ! -L "$parent" ]] || die "$name parent directory must not be a symbolic link: $parent"
  parent_mode="$(secret_mode "$parent")"
  [[ "$parent_mode" == "750" ]] || die "$name parent directory must have mode 0750 (found $parent_mode)"
  [[ "$(stat -c '%u' "$parent")" == "0" ]] || die "$name parent directory must be owned by root"
  [[ "$(stat -c '%g' "$parent")" == "$(id -g gb10-ai)" ]] || die "$name parent directory must be group-owned by gb10-ai"
}

validate_init_paths() {
  local secret_path

  require_env_value GB10_ROOT
  require_env_value HF_TOKEN_FILE
  require_env_value VLLM_API_KEY_FILE
  [[ ! -L "$ENV_FILE" ]] || die "Environment file must not be a symbolic link: $ENV_FILE"

  [[ "$GB10_ROOT" == "/srv/gb10-ai" ]] || die "Phase C requires GB10_ROOT=/srv/gb10-ai"
  [[ "$HF_TOKEN_FILE" == "/etc/gb10-ai/secrets/hf_token" ]] || die "Phase C requires HF_TOKEN_FILE=/etc/gb10-ai/secrets/hf_token"
  [[ "$VLLM_API_KEY_FILE" == "/etc/gb10-ai/secrets/vllm_api_key" ]] || die "Phase C requires VLLM_API_KEY_FILE=/etc/gb10-ai/secrets/vllm_api_key"

  for secret_path in "$HF_TOKEN_FILE" "$VLLM_API_KEY_FILE"; do
    [[ "$secret_path" == /* ]] || die "Secret paths must be absolute: $secret_path"
    case "$secret_path" in
      /|/etc|/root|/home|/usr|/var|/srv)
        die "Secret path is too broad: $secret_path"
        ;;
    esac
    [[ ! -L "$secret_path" ]] || die "Secret path must not be a symbolic link: $secret_path"
    [[ ! -e "$secret_path" || -f "$secret_path" ]] || die "Secret path must be a regular file: $secret_path"
    if [[ -e "$(dirname -- "$secret_path")" ]]; then
      [[ ! -L "$(dirname -- "$secret_path")" ]] || die "Secret parent must not be a symbolic link: $(dirname -- "$secret_path")"
    fi
  done
}

validate_config() {
  local require_registry_secret="${1:-true}"
  local value
  local env_mode

  load_env
  require_env_value COMPUTE_NODE_NAME
  require_env_value VLLM_IMAGE
  require_env_value MODEL_ID
  require_env_value MODEL_REVISION
  require_env_value TOKENIZER_REVISION
  require_env_value CODE_REVISION
  require_env_value MODEL_PROVENANCE_URL
  require_env_value MODEL_LICENSE_ID
  require_env_value MODEL_WEIGHT_FORMAT
  require_env_value MODEL_QUANTIZATION
  require_env_value CHAT_TEMPLATE_SHA256
  require_env_value GB10_ROOT
  require_env_value GB10_RUNTIME_UID
  require_env_value GB10_RUNTIME_GID
  require_env_value GB10_BIND_ADDRESS
  require_env_value VLLM_HOST_PORT
  require_env_value VLLM_API_KEY_FILE
  require_env_value HF_TOKEN_FILE

  [[ ! -L "$ENV_FILE" ]] || die "Environment file must not be a symbolic link: $ENV_FILE"
  env_mode="$(secret_mode "$ENV_FILE")"
  [[ "$env_mode" == "600" || "$env_mode" == "640" || "$env_mode" == "400" || "$env_mode" == "440" ]] ||
    die "Environment file must not be world-accessible (expected 0600/0640/0400/0440; found $env_mode)"

  [[ "$COMPUTE_NODE_NAME" =~ ^[a-z][a-z0-9-]{0,31}$ ]] ||
    die "COMPUTE_NODE_NAME must be a lowercase DNS-style host name"
  [[ "$(hostname -s)" == "$COMPUTE_NODE_NAME" ]] ||
    die "Expected compute host name $COMPUTE_NODE_NAME, found $(hostname -s)"

  [[ "$VLLM_IMAGE" =~ ^[a-zA-Z0-9._/:@-]+@sha256:[a-f0-9]{64}$ ]] ||
    die "VLLM_IMAGE must be an immutable @sha256 digest, not a tag"
  [[ "$MODEL_ID" =~ ^[a-zA-Z0-9._/-]+$ ]] || die "MODEL_ID contains unsafe characters"

  for value in "$MODEL_REVISION" "$TOKENIZER_REVISION" "$CODE_REVISION"; do
    [[ "$value" =~ ^[a-fA-F0-9]{40}$ ]] ||
      die "Model, tokenizer, and code revisions must each be full 40-hex commits"
  done
  [[ "$MODEL_PROVENANCE_URL" =~ ^https://[^[:space:]]+$ ]] || die "MODEL_PROVENANCE_URL must be an HTTPS URL"
  [[ "$MODEL_LICENSE_ID" =~ ^[a-zA-Z0-9._+-]+$ ]] || die "MODEL_LICENSE_ID is malformed"
  [[ "$MODEL_WEIGHT_FORMAT" =~ ^[a-zA-Z0-9._-]+$ ]] || die "MODEL_WEIGHT_FORMAT is malformed"
  [[ "$MODEL_QUANTIZATION" =~ ^[a-zA-Z0-9._-]+$ ]] || die "MODEL_QUANTIZATION is malformed"
  [[ "$CHAT_TEMPLATE_SHA256" =~ ^[a-fA-F0-9]{64}$ ]] || die "CHAT_TEMPLATE_SHA256 must be a 64-hex digest"

  [[ "$GB10_ROOT" == "/srv/gb10-ai" ]] || die "Phase C requires GB10_ROOT=/srv/gb10-ai"
  [[ "$HF_TOKEN_FILE" == "/etc/gb10-ai/secrets/hf_token" ]] || die "Phase C requires the documented Hugging Face token path"
  [[ "$VLLM_API_KEY_FILE" == "/etc/gb10-ai/secrets/vllm_api_key" ]] || die "Phase C requires the documented vLLM API-key path"

  [[ "$GB10_RUNTIME_UID" =~ ^[0-9]+$ ]] || die "GB10_RUNTIME_UID must be numeric (run init)"
  [[ "$GB10_RUNTIME_GID" =~ ^[0-9]+$ ]] || die "GB10_RUNTIME_GID must be numeric (run init)"
  id gb10-ai >/dev/null 2>&1 || die "gb10-ai service account is missing (run init)"
  [[ "$GB10_RUNTIME_UID" == "$(id -u gb10-ai)" ]] || die "GB10_RUNTIME_UID does not match the gb10-ai service account"
  [[ "$GB10_RUNTIME_GID" == "$(id -g gb10-ai)" ]] || die "GB10_RUNTIME_GID does not match the gb10-ai service account"

  is_ipv4_address "$GB10_BIND_ADDRESS" || die "GB10_BIND_ADDRESS must be an IPv4 address"
  [[ "$GB10_BIND_ADDRESS" != "0.0.0.0" ]] ||
    die "A wildcard GB10 bind is forbidden; use loopback or a restricted private address"

  if [[ "$GB10_BIND_ADDRESS" != "127.0.0.1" ]]; then
    [[ "${FIREWALL_CONFIRMED:-false}" == "true" ]] ||
      die "Non-loopback bind requires FIREWALL_CONFIRMED=true after allow-listing the control-plane source"
    require_env_value GATEWAY_CIDR
    is_ipv4_cidr "$GATEWAY_CIDR" || die "GATEWAY_CIDR must be an IPv4 address or CIDR"
  fi

  [[ "$VLLM_HOST_PORT" =~ ^[0-9]+$ ]] || die "VLLM_HOST_PORT must be an integer"
  ((10#$VLLM_HOST_PORT >= 1024 && 10#$VLLM_HOST_PORT <= 65535)) ||
    die "VLLM_HOST_PORT must be between 1024 and 65535"

  [[ "${VLLM_MAX_MODEL_LEN:-}" =~ ^[0-9]+$ ]] || die "VLLM_MAX_MODEL_LEN must be an integer"
  ((VLLM_MAX_MODEL_LEN >= 8192)) || die "VLLM_MAX_MODEL_LEN is below the Phase C fixture baseline"
  [[ "${VLLM_MAX_NUM_SEQS:-}" =~ ^[0-9]+$ ]] || die "VLLM_MAX_NUM_SEQS must be an integer"
  ((VLLM_MAX_NUM_SEQS >= 1 && VLLM_MAX_NUM_SEQS <= 16)) || die "VLLM_MAX_NUM_SEQS must be between 1 and 16"
  [[ "${VLLM_MAX_BATCHED_TOKENS:-}" =~ ^[0-9]+$ ]] || die "VLLM_MAX_BATCHED_TOKENS must be an integer"
  ((VLLM_MAX_BATCHED_TOKENS > 0)) || die "VLLM_MAX_BATCHED_TOKENS must be greater than zero"
  [[ "${VLLM_GPU_MEMORY_UTILIZATION:-}" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "VLLM_GPU_MEMORY_UTILIZATION must be numeric"
  awk -v n="$VLLM_GPU_MEMORY_UTILIZATION" 'BEGIN { exit !(n >= 0.20 && n <= 0.90) }' ||
    die "VLLM_GPU_MEMORY_UTILIZATION must be between 0.20 and 0.90"
  [[ "${VLLM_SHM_SIZE:-}" =~ ^[0-9]+[mMgG][bB]?$ ]] || die "VLLM_SHM_SIZE must look like 16gb or 16384mb"

  for value in \
    "${VLLM_ATTENTION_BACKEND:-}" \
    "${VLLM_MOE_BACKEND:-}" \
    "${VLLM_REASONING_PARSER:-}" \
    "${VLLM_TOOL_CALL_PARSER:-}"; do
    [[ "$value" =~ ^[a-zA-Z0-9_.-]+$ ]] || die "A vLLM backend/parser setting is missing or contains unsafe characters"
  done

  if [[ "${MODEL_ID}" == "nvidia/Qwen3.6-35B-A3B-NVFP4" ]]; then
    [[ "$MODEL_PROVENANCE_URL" == "https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4" ]] || die "Qwen3.6 baseline provenance URL does not match the publisher artifact"
    [[ "${MODEL_LICENSE_ID,,}" == "apache-2.0" ]] || die "Qwen3.6 baseline license record must be apache-2.0"
    [[ "${MODEL_WEIGHT_FORMAT,,}" == "modelopt-safetensors" ]] || die "Qwen3.6 baseline weight format must be modelopt-safetensors"
    [[ "${MODEL_QUANTIZATION,,}" == "nvfp4" ]] || die "Qwen3.6 baseline quantization must be nvfp4"
    [[ "${VLLM_ATTENTION_BACKEND:-}" == "flashinfer" ]] || die "Qwen3.6 baseline requires FlashInfer attention"
    [[ "${VLLM_MOE_BACKEND:-}" == "marlin" ]] || die "Qwen3.6 baseline requires Marlin MoE"
    [[ "${VLLM_REASONING_PARSER:-}" == "qwen3" ]] || die "Qwen3.6 baseline requires reasoning parser qwen3"
    [[ "${VLLM_TOOL_CALL_PARSER:-}" == "qwen3_xml" ]] || die "Qwen3.6 baseline requires tool parser qwen3_xml"
    case "${VLLM_SPECULATIVE_CONFIG:-}" in
      '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}')
        warn "Qwen3.6 MTP is experimental on single Spark and may freeze the host; do not promote without the pinned soak and recovery gates"
        ;;
      '')
        ;;
      *)
        die "Qwen3.6 accepts only the safe empty MTP-off setting or the exact experimental MTP-on configuration"
        ;;
    esac
  else
    die "This Phase C recipe supports only nvidia/Qwen3.6-35B-A3B-NVFP4; add a separately validated recipe for another model"
  fi

  if [[ "$require_registry_secret" == "true" ]]; then
    check_secret_file HF_TOKEN_FILE
  fi
  check_secret_file VLLM_API_KEY_FILE

  require_command docker
  docker compose version >/dev/null
  compose config --quiet
  log "Configuration is complete, immutable, and Compose-valid"
}

platform_description() {
  local board="unknown"
  local gpu="unknown"
  if [[ -r /proc/device-tree/model ]]; then
    board="$(tr -d '\0' </proc/device-tree/model)"
  elif command -v dmidecode >/dev/null 2>&1; then
    board="$(dmidecode -s system-product-name 2>/dev/null || true)"
  fi
  gpu="$(nvidia-smi -L 2>/dev/null || true)"
  printf '%s | %s' "$board" "$gpu"
}

preflight() {
  local failures=0
  local hardware
  local disk_path="/"

  log "Preflight is read-only; no packages, files, firewall rules, images, or containers will be changed"
  log "Kernel: $(uname -srmo)"
  if [[ -n "${COMPUTE_NODE_NAME:-}" ]]; then
    if [[ "$(hostname -s)" == "$COMPUTE_NODE_NAME" ]]; then
      log "Compute node name: $COMPUTE_NODE_NAME"
    else
      warn "Expected compute node name $COMPUTE_NODE_NAME, found $(hostname -s)"
      failures=$((failures + 1))
    fi
  fi
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "OS: ${PRETTY_NAME:-unknown}"
  fi

  if [[ "$(uname -s)" != "Linux" ]]; then
    warn "Target must run Linux/DGX OS"
    failures=$((failures + 1))
  fi
  if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "arm64" ]]; then
    warn "Target must be ARM64"
    failures=$((failures + 1))
  fi

  for command_name in docker nvidia-smi nvidia-ctk curl openssl awk jq sha256sum; do
    if command -v "$command_name" >/dev/null 2>&1; then
      log "$command_name: $(command -v "$command_name")"
    else
      warn "Missing command: $command_name"
      failures=$((failures + 1))
    fi
  done

  if command -v docker >/dev/null 2>&1; then
    docker --version || failures=$((failures + 1))
    docker compose version || failures=$((failures + 1))
    docker info >/dev/null 2>&1 || {
      warn "Docker daemon is unavailable to the current user"
      failures=$((failures + 1))
    }
  fi
  if command -v nvidia-ctk >/dev/null 2>&1; then
    nvidia-ctk --version || failures=$((failures + 1))
  fi
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi || failures=$((failures + 1))
  fi

  hardware="$(platform_description)"
  log "Hardware: $hardware"
  if [[ ! "$hardware" =~ (GB10|DGX.Spark|Grace.Blackwell|GX10) ]]; then
    if [[ "${ALLOW_UNSUPPORTED_HOST:-false}" == "true" ]]; then
      warn "Unsupported hardware override is active; this run cannot produce production qualification evidence"
    else
      warn "Could not verify GB10/DGX Spark/GX10 hardware"
      failures=$((failures + 1))
    fi
  fi

  if [[ -n "${GB10_ROOT:-}" ]]; then
    disk_path="$(dirname -- "$GB10_ROOT")"
    [[ -e "$disk_path" ]] || disk_path="/"
  fi
  df -h "$disk_path"
  if command -v timedatectl >/dev/null 2>&1; then
    log "Clock synchronized: $(timedatectl show -p NTPSynchronized --value 2>/dev/null || printf unknown)"
  fi

  ((failures == 0)) || die "Preflight found $failures blocking issue(s)"
  log "Preflight passed"
}

create_service_account() {
  if ! id gb10-ai >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "${GB10_ROOT}" --shell /usr/sbin/nologin gb10-ai
  fi
  getent group gb10-ai >/dev/null || die "gb10-ai group is missing"
}

pin_runtime_identity() {
  local runtime_uid
  local runtime_gid
  local temporary_env

  runtime_uid="$(id -u gb10-ai)"
  runtime_gid="$(id -g gb10-ai)"
  temporary_env="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk -v uid="$runtime_uid" -v gid="$runtime_gid" '
    /^GB10_RUNTIME_UID=/ { print "GB10_RUNTIME_UID=" uid; saw_uid = 1; next }
    /^GB10_RUNTIME_GID=/ { print "GB10_RUNTIME_GID=" gid; saw_gid = 1; next }
    { print }
    END {
      if (!saw_uid) print "GB10_RUNTIME_UID=" uid
      if (!saw_gid) print "GB10_RUNTIME_GID=" gid
    }
  ' "$ENV_FILE" >"$temporary_env"
  chmod 0600 "$temporary_env"
  chown root:root "$temporary_env"
  mv -f "$temporary_env" "$ENV_FILE"
  log "Pinned container identity to gb10-ai (${runtime_uid}:${runtime_gid})"
}

provision_directories() {
  local directory
  for directory in \
    "$GB10_ROOT/cache/huggingface" \
    "$GB10_ROOT/cache/vllm" \
    "$GB10_ROOT/models" \
    "$GB10_ROOT/manifests" \
    "$GB10_ROOT/releases" \
    "$GB10_ROOT/logs"; do
    install -d -m 0750 -o gb10-ai -g gb10-ai "$directory"
  done
}

initialize() {
  local env_parent
  require_root
  for command_name in openssl awk getent install useradd; do
    require_command "$command_name"
  done

  [[ "$ENV_FILE" == "/etc/gb10-ai/gb10.env" ]] || die "init only writes /etc/gb10-ai/gb10.env"
  env_parent="$(dirname -- "$ENV_FILE")"
  [[ ! -L "$env_parent" ]] || die "Environment parent must not be a symbolic link: $env_parent"
  install -d -m 0750 "$env_parent"
  if [[ ! -e "$ENV_FILE" ]]; then
    install -m 0600 "$ENV_TEMPLATE" "$ENV_FILE"
    log "Created configuration template: $ENV_FILE"
  else
    log "Preserving existing configuration: $ENV_FILE"
  fi

  load_env
  validate_init_paths
  create_service_account
  pin_runtime_identity
  load_env
  provision_directories

  install -d -m 0750 -o root -g gb10-ai "$(dirname -- "$HF_TOKEN_FILE")" "$(dirname -- "$VLLM_API_KEY_FILE")"
  if [[ ! -e "$VLLM_API_KEY_FILE" ]]; then
    openssl rand -hex 32 >"$VLLM_API_KEY_FILE"
    log "Generated vLLM service credential: $VLLM_API_KEY_FILE"
  fi
  if [[ ! -e "$HF_TOKEN_FILE" ]]; then
    install -m 0440 -o root -g gb10-ai /dev/null "$HF_TOKEN_FILE"
    warn "Created empty Hugging Face token file; fill it before validate/install: $HF_TOKEN_FILE"
  fi
  chown root:gb10-ai "$HF_TOKEN_FILE" "$VLLM_API_KEY_FILE"
  chmod 0440 "$HF_TOKEN_FILE" "$VLLM_API_KEY_FILE"

  log "Initialization complete. Fill pinned revisions, digest, bind/firewall evidence, and the HF token before install."
}

wait_ready() {
  local elapsed=0
  local interval=10
  local container_id
  local health

  log "Waiting up to ${WAIT_SECONDS}s for the model to load"
  while ((elapsed < WAIT_SECONDS)); do
    container_id="$(compose ps -q text-primary 2>/dev/null || true)"
    if [[ -n "$container_id" ]]; then
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
      if [[ "$health" == "healthy" ]]; then
        log "text-primary is healthy"
        return 0
      fi
      if [[ "$health" == "exited" || "$health" == "dead" ]]; then
        compose logs --tail 100 text-primary >&2
        die "text-primary stopped before becoming ready"
      fi
    fi
    if ((elapsed % 30 == 0)); then
      log "Still loading (${elapsed}s elapsed; state=${health:-not-created})"
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  compose logs --tail 100 text-primary >&2
  die "Timed out waiting for text-primary readiness"
}

write_release_record() {
  local record
  local config_hash
  local cuda_version
  local driver_version
  local os_description
  local image_id
  local timestamp
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  record="$GB10_ROOT/manifests/installed-$timestamp.env"
  image_id="$(docker image inspect --format '{{.Id}}' "$VLLM_IMAGE")"
  config_hash="$(compose config | sha256sum | awk '{print $1}')"
  driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | awk 'NR == 1 { print; exit }')"
  cuda_version="$(nvidia-smi | awk '/CUDA Version:/ { for (i = 1; i <= NF; i++) if ($i == "CUDA" && $(i + 1) == "Version:") { print $(i + 2); exit } }')"
  os_description="$(awk '/^PRETTY_NAME=/ { sub(/^[^=]*=/, ""); gsub(/^\"|\"$/, ""); print; exit }' /etc/os-release)"
  os_description="${os_description:-unknown}"
  {
    printf 'INSTALLED_AT=%q\n' "$timestamp"
    printf 'PLATFORM=%q\n' "$(platform_description)"
    printf 'KERNEL=%q\n' "$(uname -srmo)"
    printf 'OS=%q\n' "$os_description"
    printf 'NVIDIA_DRIVER=%q\n' "$driver_version"
    printf 'CUDA_COMPATIBILITY=%q\n' "$cuda_version"
    printf 'RENDERED_CONFIG_SHA256=%q\n' "$config_hash"
    printf 'VLLM_IMAGE=%q\n' "$VLLM_IMAGE"
    printf 'VLLM_IMAGE_ID=%q\n' "$image_id"
    printf 'MODEL_ID=%q\n' "$MODEL_ID"
    printf 'MODEL_REVISION=%q\n' "$MODEL_REVISION"
    printf 'TOKENIZER_REVISION=%q\n' "$TOKENIZER_REVISION"
    printf 'CODE_REVISION=%q\n' "$CODE_REVISION"
    printf 'MODEL_PROVENANCE_URL=%q\n' "$MODEL_PROVENANCE_URL"
    printf 'MODEL_LICENSE_ID=%q\n' "$MODEL_LICENSE_ID"
    printf 'MODEL_WEIGHT_FORMAT=%q\n' "$MODEL_WEIGHT_FORMAT"
    printf 'MODEL_QUANTIZATION=%q\n' "$MODEL_QUANTIZATION"
    printf 'CHAT_TEMPLATE_SHA256=%q\n' "$CHAT_TEMPLATE_SHA256"
    printf 'GB10_RUNTIME_UID=%q\n' "$GB10_RUNTIME_UID"
    printf 'GB10_RUNTIME_GID=%q\n' "$GB10_RUNTIME_GID"
    printf 'ALLOW_UNSUPPORTED_HOST=%q\n' "${ALLOW_UNSUPPORTED_HOST:-false}"
    printf 'FIREWALL_CONFIRMED=%q\n' "${FIREWALL_CONFIRMED:-false}"
    printf 'GATEWAY_CIDR=%q\n' "${GATEWAY_CIDR:-not-applicable}"
    printf 'VLLM_MAX_MODEL_LEN=%q\n' "$VLLM_MAX_MODEL_LEN"
    printf 'VLLM_MAX_NUM_SEQS=%q\n' "$VLLM_MAX_NUM_SEQS"
    printf 'VLLM_MAX_BATCHED_TOKENS=%q\n' "$VLLM_MAX_BATCHED_TOKENS"
    printf 'VLLM_GPU_MEMORY_UTILIZATION=%q\n' "$VLLM_GPU_MEMORY_UTILIZATION"
    printf 'VLLM_ATTENTION_BACKEND=%q\n' "$VLLM_ATTENTION_BACKEND"
    printf 'VLLM_MOE_BACKEND=%q\n' "$VLLM_MOE_BACKEND"
    printf 'VLLM_REASONING_PARSER=%q\n' "$VLLM_REASONING_PARSER"
    printf 'VLLM_TOOL_CALL_PARSER=%q\n' "$VLLM_TOOL_CALL_PARSER"
    printf 'VLLM_SPECULATIVE_CONFIG=%q\n' "${VLLM_SPECULATIVE_CONFIG:-}"
  } >"$record"
  chmod 0640 "$record"
  chown gb10-ai:gb10-ai "$record"
  log "Wrote secret-free release record: $record"
}

smoke_test() {
  local base_url
  local api_key
  local auth_header
  local models_json
  local responses_json
  local alias

  validate_config false
  require_command curl
  require_command jq
  check_secret_file VLLM_API_KEY_FILE
  base_url="http://${GB10_BIND_ADDRESS}:${VLLM_HOST_PORT}"
  api_key="$(<"$VLLM_API_KEY_FILE")"
  auth_header="$(mktemp /tmp/gb10-vllm-auth.XXXXXX)"
  chmod 0600 "$auth_header"
  printf 'Authorization: Bearer %s\n' "$api_key" >"$auth_header"
  trap 'rm -f -- "$auth_header"' EXIT

  curl --fail --silent --show-error "$base_url/health" >/dev/null
  models_json="$(curl --fail --silent --show-error \
    --header "@$auth_header" \
    "$base_url/v1/models")"
  for alias in coding automation research home meeting assistant; do
    jq -e --arg alias "$alias" '.data | any(.id == $alias)' <<<"$models_json" >/dev/null ||
      die "Served model alias is missing: $alias"
  done

  responses_json="$(curl --fail --silent --show-error \
    --header "@$auth_header" \
    -H 'Content-Type: application/json' \
    --data '{"model":"automation","input":"Reply with exactly READY.","max_output_tokens":16}' \
    "$base_url/v1/responses")"
  jq -e '(.id | type == "string") and (.status == "completed")' <<<"$responses_json" >/dev/null ||
    die "Responses smoke test did not complete"
  rm -f -- "$auth_header"
  trap - EXIT
  log "Smoke test passed: health, six logical aliases, and /v1/responses"
}

prepare_model_artifacts() {
  log "Downloading the pinned model tuple in the isolated acquisition profile"
  compose --profile prepare run --rm model-fetch
  log "Model artifacts cached; inference will run offline without the registry token"
}

deploy_release() {
  require_root
  validate_config
  preflight
  create_service_account
  provision_directories

  log "Pulling immutable image: $VLLM_IMAGE"
  docker pull "$VLLM_IMAGE"
  prepare_model_artifacts
  log "Verifying NVIDIA GPU access inside the pinned runtime image"
  docker run --rm --gpus all --user "${GB10_RUNTIME_UID}:${GB10_RUNTIME_GID}" --entrypoint nvidia-smi "$VLLM_IMAGE" >/dev/null

  compose up -d --remove-orphans
  wait_ready
  smoke_test
  write_release_record
  log "Release deployed. This is a Phase C candidate, not Stage 1 acceptance."
}

start_release() {
  require_root
  validate_config false
  compose up -d --remove-orphans
  wait_ready
}

show_status() {
  validate_config false
  compose ps
  local base_url="http://${GB10_BIND_ADDRESS}:${VLLM_HOST_PORT}"
  if curl --fail --silent --max-time 3 "$base_url/health" >/dev/null 2>&1; then
    log "Health endpoint: ready"
  else
    warn "Health endpoint: unavailable"
  fi
}

parse_options "$@"

case "$COMMAND" in
  help)
    usage
    ;;
  preflight)
    if [[ -f "$ENV_FILE" ]]; then
      load_env
    fi
    preflight
    ;;
  init)
    initialize
    ;;
  validate)
    validate_config
    ;;
  install|rollback)
    deploy_release
    ;;
  up)
    start_release
    ;;
  status)
    show_status
    ;;
  smoke)
    smoke_test
    ;;
  logs)
    load_env
    compose logs --tail 200 text-primary
    ;;
  down)
    require_root
    load_env
    compose down
    log "Services stopped; models, caches, manifests, and secrets were retained"
    ;;
  *)
    usage >&2
    die "Unknown command: $COMMAND"
    ;;
esac
