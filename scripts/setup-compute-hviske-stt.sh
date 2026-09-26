#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/hviske-stt/compose.yaml"
CACHE_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
ADAPTER="$SCRIPT_DIR/wyoming-openai-stt.py"
FIREWALL_HELPER=/usr/local/libexec/gb10-compute-firewall
FIREWALL_CONFIG=/etc/gb10-ai/firewall.env
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  COMPUTE_NODE_NAME GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID
  HVISKE_IMAGE HVISKE_MODEL_ID HVISKE_MODEL_REVISION HVISKE_MODEL_LICENSE_ID
  GB10_BIND_ADDRESS HVISKE_WYOMING_PORT GATEWAY_CIDR VLLM_API_KEY_FILE
  HVISKE_LICENSE_DECISION HVISKE_PRIVATE_INGRESS_CONFIRMED
  HVISKE_GPU_MEMORY_UTILIZATION HVISKE_MAX_NUM_SEQS HVISKE_MAX_AUDIO_BYTES
  HVISKE_MAX_AUDIO_SECONDS HVISKE_UPSTREAM_TIMEOUT_SECONDS HVISKE_MAX_CONNECTIONS
  HVISKE_ARTIFACT_MAX_BYTES HVISKE_ARTIFACT_MAX_FILES
  HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES MIN_FREE_DISK_GIB
)
COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${HVISKE_ENV_FILE:-/etc/gb10-ai/hviske.env}"
WAIT_SECONDS=1200
MANIFEST=""

usage() {
  cat <<'USAGE'
Usage: setup-compute-hviske-stt.sh COMMAND [OPTIONS]
Commands:
  validate   Validate the immutable model/runtime tuple and Compose profile
  preflight  Run read-only host, GPU, disk, and runtime checks
  prepare    Fetch and atomically accept the pinned model cache
  install    Pull, prepare, start, smoke-test, and record a staged release
  up         Start only the opt-in Hviske and Wyoming services
  smoke      Exercise Wyoming Describe and a bounded silent transcription
  status     Show only the Hviske project state
  logs       Show the last 200 lines from the Hviske project
  down       Stop only the Hviske project; retain cache and manifest
Options:
  --env FILE      Environment file (default: /etc/gb10-ai/hviske.env)
  --wait SECONDS  Readiness timeout (default: 1200)
USAGE
}
log() { printf '[compute-hviske] %s\n' "$*"; }
die() { printf '[compute-hviske] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun with sudo"; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
sha256_file() { sha256sum "$1" | cut -d ' ' -f 1; }

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

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 "Hviske configuration" "${CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
  MANIFEST="$GB10_ROOT/manifests/accepted-hviske-cache.json"
}
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
require_value() {
  local value="${!1:-}"
  [[ -n "$value" && "$value" != *REPLACE_WITH* && "$value" != *CHANGEME* && "$value" != *TODO* ]] ||
    die "Missing or placeholder setting: $1"
}
check_directory() {
  local path="$1" identity="$2" mode="$3" label="$4"
  [[ -d "$path" && ! -L "$path" ]] || die "$label must be a non-symlink directory"
  [[ "$(stat -c '%u:%g' "$path")" == "$identity" ]] || die "$label has the wrong numeric identity"
  [[ "$(file_mode "$path")" == "$mode" ]] || die "$label must have mode 0$mode"
}
check_secret() {
  local mode
  [[ "$VLLM_API_KEY_FILE" == /etc/gb10-ai/secrets/vllm_api_key ]] || die "Unexpected API-key path"
  [[ -f "$VLLM_API_KEY_FILE" && -s "$VLLM_API_KEY_FILE" && ! -L "$VLLM_API_KEY_FILE" ]] ||
    die "VLLM_API_KEY_FILE must be a non-empty regular file"
  mode="$(file_mode "$VLLM_API_KEY_FILE")"
  [[ "$mode" == 440 ]] || die "VLLM_API_KEY_FILE must have mode 0440"
  [[ "$(stat -c '%u:%g' "$VLLM_API_KEY_FILE")" == "0:$GB10_RUNTIME_GID" ]] ||
    die "VLLM_API_KEY_FILE must be owned by root:gb10-ai"
}
license_allows_activation() {
  case "$HVISKE_LICENSE_DECISION" in
    personal-noncommercial|syvai-commercial-license-approved) ;;
    *) die "Hviske activation requires a reviewed personal/noncommercial or SYVAI commercial license decision" ;;
  esac
}

verify_private_firewall() {
  [[ "$GB10_BIND_ADDRESS" != 10.77.10.10 ]] || {
    [[ -x "$FIREWALL_HELPER" ]] || die "Installed compute firewall helper is missing"
    "$FIREWALL_HELPER" verify --config "$FIREWALL_CONFIG"
  }
}

validate_config() {
  local key services
  local -a required_keys=(
    COMPUTE_NODE_NAME GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID HVISKE_IMAGE
    HVISKE_MODEL_ID HVISKE_MODEL_REVISION HVISKE_MODEL_LICENSE_ID GB10_BIND_ADDRESS
    HVISKE_WYOMING_PORT VLLM_API_KEY_FILE HVISKE_LICENSE_DECISION
    HVISKE_PRIVATE_INGRESS_CONFIRMED HVISKE_GPU_MEMORY_UTILIZATION HVISKE_MAX_NUM_SEQS
    HVISKE_MAX_AUDIO_BYTES HVISKE_MAX_AUDIO_SECONDS HVISKE_UPSTREAM_TIMEOUT_SECONDS HVISKE_MAX_CONNECTIONS
    HVISKE_ARTIFACT_MAX_BYTES HVISKE_ARTIFACT_MAX_FILES HF_CACHE_MAX_BYTES
    HF_CACHE_MAX_FILES MIN_FREE_DISK_GIB
  )
  load_env
  for key in "${required_keys[@]}"; do require_value "$key"; done
  [[ "$COMPUTE_NODE_NAME" == home-spark && "$(hostname -s)" == home-spark ]] ||
    die "Hviske lifecycle must run on configured host home-spark"
  [[ "$GB10_ROOT" == /srv/gb10-ai ]] || die "GB10_ROOT must be /srv/gb10-ai"
  [[ "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" == "$(id -u gb10-ai):$(id -g gb10-ai)" ]] ||
    die "Runtime identity does not match gb10-ai"
  check_directory "$GB10_ROOT/cache/huggingface" "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" 750 "Hugging Face cache"
  check_directory "$GB10_ROOT/cache/vllm" "$GB10_RUNTIME_UID:$GB10_RUNTIME_GID" 750 "vLLM cache"
  check_directory "$GB10_ROOT/manifests" "0:$GB10_RUNTIME_GID" 750 "Manifest directory"
  check_directory "$GB10_ROOT/runtime" "0:$GB10_RUNTIME_GID" 750 "Runtime directory"
  [[ "$HVISKE_IMAGE" == nvcr.io/nvidia/vllm@sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d ]] ||
    die "HVISKE_IMAGE must be the reviewed NVIDIA 26.04 ARM64/vLLM 0.19 digest"
  [[ "$HVISKE_MODEL_ID" == syvai/hviske-v5.3 ]] || die "Unexpected Hviske model"
  [[ "$HVISKE_MODEL_REVISION" == 5d1a09822018702dc51d763e3a867b62d26b3501 ]] || die "Unexpected model revision"
  [[ "$HVISKE_MODEL_LICENSE_ID" == cc-by-nc-4.0 ]] || die "Unexpected model license"
  [[ "$HVISKE_WYOMING_PORT" == 10301 ]] || die "Wyoming port must be 10301"
  case "$GB10_BIND_ADDRESS" in
    127.0.0.1) ;;
    10.77.10.10)
      [[ "$GATEWAY_CIDR" == 10.77.10.2/32 ]] || die "Private ingress source must be 10.77.10.2/32"
      ;;
    *) die "Listener must bind loopback or 10.77.10.10" ;;
  esac
  case "$HVISKE_LICENSE_DECISION" in
    review-required|personal-noncommercial|syvai-commercial-license-approved) ;;
    *) die "Invalid HVISKE_LICENSE_DECISION" ;;
  esac
  [[ "$HVISKE_PRIVATE_INGRESS_CONFIRMED" == true || "$HVISKE_PRIVATE_INGRESS_CONFIRMED" == false ]] ||
    die "HVISKE_PRIVATE_INGRESS_CONFIRMED must be true or false"
  [[ "$HVISKE_GPU_MEMORY_UTILIZATION" == 0.10 && "$HVISKE_MAX_NUM_SEQS" == 4 ]] ||
    die "Hviske GPU scheduling differs from the staged tuple"
  [[ "$HVISKE_MAX_AUDIO_BYTES:$HVISKE_MAX_AUDIO_SECONDS:$HVISKE_UPSTREAM_TIMEOUT_SECONDS:$HVISKE_MAX_CONNECTIONS" == 10000000:60:120:4 ]] ||
    die "Wyoming request bounds differ from the staged tuple"
  [[ "$HVISKE_ARTIFACT_MAX_BYTES:$HVISKE_ARTIFACT_MAX_FILES" == 6000000000:32 ]] ||
    die "Artifact bounds differ from the reviewed tuple"
  [[ "$HF_CACHE_MAX_BYTES:$HF_CACHE_MAX_FILES" == 536870912000:50000 ]] || die "Cache bounds changed"
  if [[ ! "$MIN_FREE_DISK_GIB" =~ ^[1-9][0-9]*$ ]] || ((10#$MIN_FREE_DISK_GIB < 200)); then
    die "MIN_FREE_DISK_GIB must be at least 200"
  fi
  check_secret
  require_command docker; require_command python3; require_command sha256sum
  docker compose version >/dev/null
  compose --profile prepare --profile hviske config --quiet
  services="$(compose --profile prepare --profile hviske config --services)"
  for key in hviske-fetch hviske-primary hviske-wyoming; do
    [[ $'\n'"$services"$'\n' == *$'\n'"$key"$'\n'* ]] || die "Compose render is missing $key"
  done
  log "Pinned vLLM 0.19, model, license gate, request bounds, and Compose profile are valid"
}

install_runtime_programs() {
  local source destination temporary
  for source in "$CACHE_HELPER" "$ADAPTER"; do
    destination="$GB10_ROOT/runtime/$(basename -- "$source")"
    temporary="$(mktemp "$GB10_ROOT/runtime/.$(basename -- "$source").XXXXXX")"
    install -m 0440 -o root -g gb10-ai "$source" "$temporary"
    mv -fT "$temporary" "$destination"
  done
  install -d -m 0750 -o gb10-ai -g gb10-ai "$GB10_ROOT/cache/vllm/hviske"
}
verify_runtime_programs() {
  local source destination
  for source in "$CACHE_HELPER" "$ADAPTER"; do
    destination="$GB10_ROOT/runtime/$(basename -- "$source")"
    config_path_is_trusted "$destination" 0 "Installed runtime program" || die "Untrusted runtime program"
    cmp -s "$source" "$destination" || die "Installed runtime differs from this release; run prepare"
  done
}
verify_manifest() {
  config_path_is_trusted "$MANIFEST" 0 "Accepted Hviske manifest" || die "Accepted manifest is missing or untrusted"
  "$CACHE_HELPER" verify --cache-root "$GB10_ROOT/cache/huggingface" --repo-id "$HVISKE_MODEL_ID" \
    --revision "$HVISKE_MODEL_REVISION" --manifest "$MANIFEST"
}
accept_manifest() {
  local temporary
  if [[ ! -e "$MANIFEST" && ! -L "$MANIFEST" ]]; then
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-hviske-cache.XXXXXX")"
    "$CACHE_HELPER" create --cache-root "$GB10_ROOT/cache/huggingface" --repo-id "$HVISKE_MODEL_ID" \
      --revision "$HVISKE_MODEL_REVISION" >"$temporary"
    chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
    ln -- "$temporary" "$MANIFEST" || { rm -f -- "$temporary"; die "Manifest appeared during acceptance"; }
    rm -f -- "$temporary"
  fi
  verify_manifest
}

preflight() {
  validate_config
  local command_name failures=0 available_kib
  for command_name in docker nvidia-smi nvidia-container-cli python3 sha256sum; do
    command -v "$command_name" >/dev/null || { printf 'Missing command: %s\n' "$command_name" >&2; failures=$((failures + 1)); }
  done
  [[ "$(uname -s):$(uname -m)" == Linux:aarch64 ]] || { printf 'Target must be Linux/ARM64\n' >&2; failures=$((failures + 1)); }
  docker info >/dev/null 2>&1 || failures=$((failures + 1))
  available_kib="$(df -Pk "$GB10_ROOT" | awk 'NR==2 {print $4}')"
  ((available_kib >= MIN_FREE_DISK_GIB * 1024 * 1024)) || failures=$((failures + 1))
  ((failures == 0)) || die "Preflight found $failures blocking issue(s)"
  log "Preflight passed"
}

prepare() {
  require_root; validate_config; install_runtime_programs
  compose --profile prepare run --rm hviske-fetch
  accept_manifest
  log "Pinned Hviske cache is prepared; no service was activated"
}
wait_ready() {
  local service container state deadline=$((SECONDS + WAIT_SECONDS))
  for service in hviske-primary hviske-wyoming; do
    state=missing
    while ((SECONDS < deadline)); do
      container="$(compose --profile hviske ps -q "$service")"
      [[ -z "$container" ]] || state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
      [[ "$state" != healthy ]] || break
      [[ "$state" != exited && "$state" != dead ]] || die "$service stopped before readiness"
      sleep 5
    done
    [[ "$state" == healthy ]] || die "Timed out waiting for $service"
  done
}
start() {
  require_root; validate_config; license_allows_activation; verify_manifest; verify_runtime_programs
  if [[ "$GB10_BIND_ADDRESS" == 10.77.10.10 && "$HVISKE_PRIVATE_INGRESS_CONFIRMED" != true ]]; then
    die "Private activation requires a verified 10.77.10.2/32 -> 10.77.10.10:10301 ingress rule"
  fi
  verify_private_firewall
  compose --profile hviske up -d hviske-primary hviske-wyoming
  wait_ready
  log "Hviske is staged on $GB10_BIND_ADDRESS:$HVISKE_WYOMING_PORT; production routing remains a separate gate"
}
smoke() {
  validate_config
  python3 - "$GB10_BIND_ADDRESS" "$HVISKE_WYOMING_PORT" <<'PY'
import json, socket, struct, sys

def event(kind, data=None, payload=b''):
    data_bytes = json.dumps(data or {}, separators=(',', ':')).encode() if data else b''
    header = {'type': kind, 'version': '1.10.2'}
    if data_bytes: header['data_length'] = len(data_bytes)
    if payload: header['payload_length'] = len(payload)
    return json.dumps(header, separators=(',', ':')).encode() + b'\n' + data_bytes + payload

def receive(stream):
    header = json.loads(stream.readline(65537))
    if header.get('data_length'): header['data'] = json.loads(stream.read(header['data_length']))
    if header.get('payload_length'): stream.read(header['payload_length'])
    return header

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=10) as sock:
    stream = sock.makefile('rb')
    sock.sendall(event('describe'))
    info = receive(stream)
    if info.get('type') != 'info' or not info.get('data', {}).get('asr'): raise SystemExit('Describe failed')
    sock.sendall(event('transcribe', {'name':'stt-danish','language':'da'}))
    audio_format = {'rate':16000,'width':2,'channels':1}
    sock.sendall(event('audio-start', audio_format))
    sock.sendall(event('audio-chunk', audio_format, struct.pack('<h', 0) * 8000))
    sock.sendall(event('audio-stop'))
    result = receive(stream)
    if result.get('type') != 'transcript' or not isinstance(result.get('data', {}).get('text'), str):
        raise SystemExit('Transcription failed')
PY
  log "Wyoming Describe and bounded transcription smoke passed"
}
write_release_record() {
  local timestamp temporary digest record
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  temporary="$(mktemp "$GB10_ROOT/manifests/.hviske-release.XXXXXX")"
  {
    printf 'RELEASE_RECORD_SCHEMA=%q\n' 1
    printf 'QUALIFICATION=%q\n' staged-not-production-qualified
    printf 'HVISKE_IMAGE=%q\n' "$HVISKE_IMAGE"
    printf 'HVISKE_MODEL_ID=%q\n' "$HVISKE_MODEL_ID"
    printf 'HVISKE_MODEL_REVISION=%q\n' "$HVISKE_MODEL_REVISION"
    printf 'HVISKE_MODEL_LICENSE_ID=%q\n' "$HVISKE_MODEL_LICENSE_ID"
    printf 'HVISKE_LICENSE_DECISION=%q\n' "$HVISKE_LICENSE_DECISION"
    printf 'WYOMING_LISTENER=%q\n' "$GB10_BIND_ADDRESS:$HVISKE_WYOMING_PORT"
    printf 'CACHE_MANIFEST_SHA256=%q\n' "$(sha256_file "$MANIFEST")"
    printf 'ADAPTER_SHA256=%q\n' "$(sha256_file "$GB10_ROOT/runtime/wyoming-openai-stt.py")"
  } >"$temporary"
  digest="$(sha256_file "$temporary")"
  record="$GB10_ROOT/manifests/installed-hviske-$timestamp-${digest:0:12}.env"
  chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"; ln -- "$temporary" "$record"
  rm -f -- "$temporary"
  log "Wrote staged release record: $record"
}

case "$COMMAND" in
  help) usage ;;
  validate) validate_config ;;
  preflight) preflight ;;
  prepare) prepare ;;
  up) start ;;
  smoke) smoke ;;
  install) require_root; preflight; docker pull "$HVISKE_IMAGE"; prepare; start; smoke; write_release_record ;;
  status) validate_config; compose --profile hviske ps hviske-primary hviske-wyoming ;;
  logs) validate_config; compose --profile hviske logs --tail 200 hviske-primary hviske-wyoming ;;
  down) require_root; validate_config; compose --profile hviske down --remove-orphans ;;
  *) die "Unknown command: $COMMAND" ;;
esac
