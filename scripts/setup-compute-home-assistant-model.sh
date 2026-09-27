#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/home-assistant-model/compose.yaml"
CACHE_HELPER="$SCRIPT_DIR/model-cache-integrity.py"
EDGE_HELPER="$SCRIPT_DIR/tcp-edge-proxy.py"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  VLLM_IMAGE HOME_MODEL_ID HOME_MODEL_REVISION HOME_TOKENIZER_REVISION HOME_CODE_REVISION
  HOME_MODEL_LICENSE_ID HOME_CHAT_TEMPLATE_SHA256 HOME_ARTIFACT_MAX_BYTES HOME_ARTIFACT_MAX_FILES
  GB10_ROOT GB10_RUNTIME_UID GB10_RUNTIME_GID GB10_BIND_ADDRESS HOME_MODEL_HOST_PORT
  HF_TOKEN_FILE VLLM_API_KEY_FILE HF_CACHE_MAX_BYTES HF_CACHE_MAX_FILES MIN_FREE_DISK_GIB
  HOME_MAX_MODEL_LEN HOME_MAX_NUM_SEQS HOME_MAX_BATCHED_TOKENS HOME_GPU_MEMORY_UTILIZATION
  HOME_DEFAULT_CHAT_TEMPLATE_KWARGS
)
COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${HOME_MODEL_ENV_FILE:-/etc/gb10-ai/home-assistant-model.env}"
WAIT_SECONDS=1200

usage() {
  cat <<'USAGE'
Usage: setup-compute-home-assistant-model.sh COMMAND [--env FILE] [--wait SECONDS]
Commands:
  validate  Validate the exact Gemma 4 E4B fast-home tuple
  prepare   Download and accept the pinned model without starting it
  install   Pull the runtime, prepare the artifact, and write a release record
  up        Start the isolated home-fast endpoint and run a Danish smoke test
  smoke     Test health, authentication, alias, and Danish direct response
  status    Show the isolated endpoint
  logs      Show the last 200 service log lines
  down      Stop only the isolated endpoint; retain its model cache
USAGE
}
log() { printf '[home-fast] %s\n' "$*"; }
die() { printf '[home-fast] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun with sudo"; }

while (($#)); do
  case "$1" in
    --env) (($# >= 2)) || die "--env requires a file"; ENV_FILE="$2"; shift 2 ;;
    --wait) (($# >= 2)) || die "--wait requires seconds"; [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "invalid wait"; WAIT_SECONDS="$((10#$2))"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 "Home Assistant model configuration" "${CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
manifest_path() { printf '%s/manifests/accepted-home-model-cache.json' "$GB10_ROOT"; }

validate_config() {
  load_env
  [[ "$VLLM_IMAGE" == nvcr.io/nvidia/vllm@sha256:d049bead397430803ac5b705d50094492f23782716677d746ab721e43f054cf6 ]] || die "Unexpected vLLM image"
  [[ "$HOME_MODEL_ID" == google/gemma-4-E4B-it-qat-w4a16-ct ]] || die "Unexpected home model"
  [[ "$HOME_MODEL_REVISION" == 6cd26aaa2357fb2bad8c51699a7558a4d1a965bb ]] || die "Unexpected home model revision"
  [[ "$HOME_TOKENIZER_REVISION:$HOME_CODE_REVISION" == "$HOME_MODEL_REVISION:$HOME_MODEL_REVISION" ]] || die "Model revisions must match"
  [[ "${HOME_MODEL_LICENSE_ID,,}" == apache-2.0 ]] || die "Unexpected model license"
  [[ "$HOME_CHAT_TEMPLATE_SHA256" == 0a2c8073c878ab1da004bee933a998606537bbb62016310352c7285c3f01c5b5 ]] || die "Chat template digest changed"
  [[ "$HOME_ARTIFACT_MAX_BYTES:$HOME_ARTIFACT_MAX_FILES" == 13000000000:16 ]] || die "Artifact bounds changed"
  [[ "$GB10_BIND_ADDRESS:$HOME_MODEL_HOST_PORT" == 127.0.0.1:8006 ]] || die "home-fast must publish only on loopback port 8006"
  [[ "$HOME_MAX_MODEL_LEN:$HOME_MAX_NUM_SEQS:$HOME_MAX_BATCHED_TOKENS" == 32768:4:4096 ]] || die "Context/concurrency tuple changed"
  [[ "$HOME_GPU_MEMORY_UTILIZATION" == 0.22 ]] || die "Memory envelope changed"
  [[ "$HOME_DEFAULT_CHAT_TEMPLATE_KWARGS" == '{"enable_thinking":false}' ]] || die "Fast path must disable thinking"
  compose --profile prepare config --quiet
  command -v docker >/dev/null || die "docker is required"
  log "Pinned Gemma 4 E4B text-only home-fast tuple is valid"
}

install_helpers() {
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
  config_path_is_trusted "$manifest" 0 "Accepted home model cache manifest" || die "Accepted home model cache manifest is missing or untrusted"
  "$CACHE_HELPER" verify --cache-root "$GB10_ROOT/cache/huggingface" --repo-id "$HOME_MODEL_ID" \
    --revision "$HOME_MODEL_REVISION" --revision "$HOME_TOKENIZER_REVISION" --revision "$HOME_CODE_REVISION" --manifest "$manifest"
}

prepare_model() {
  require_root; validate_config; install_helpers
  compose --profile prepare run --rm home-fetch
  local manifest temporary
  manifest="$(manifest_path)"
  if [[ ! -e "$manifest" && ! -L "$manifest" ]]; then
    temporary="$(mktemp "$GB10_ROOT/manifests/.accepted-home-model-cache.XXXXXX")"
    "$CACHE_HELPER" create --cache-root "$GB10_ROOT/cache/huggingface" --repo-id "$HOME_MODEL_ID" \
      --revision "$HOME_MODEL_REVISION" --revision "$HOME_TOKENIZER_REVISION" --revision "$HOME_CODE_REVISION" >"$temporary"
    chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"
    ln -- "$temporary" "$manifest" || { rm -f -- "$temporary"; die "Manifest appeared during acceptance"; }
    rm -f -- "$temporary"
  fi
  verify_cache
  log "Pinned home-fast artifact is staged and verified"
}

write_record() {
  local timestamp record temporary
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  record="$GB10_ROOT/manifests/home-fast-${timestamp}-${HOME_MODEL_REVISION:0:12}.env"
  temporary="$(mktemp "$GB10_ROOT/manifests/.home-fast.XXXXXX")"
  {
    printf 'INSTALLED_AT=%s\nMODEL_ID=%s\nMODEL_REVISION=%s\n' "$timestamp" "$HOME_MODEL_ID" "$HOME_MODEL_REVISION"
    printf 'VLLM_IMAGE=%s\nMAX_MODEL_LEN=%s\nMEMORY_UTILIZATION=%s\n' "$VLLM_IMAGE" "$HOME_MAX_MODEL_LEN" "$HOME_GPU_MEMORY_UTILIZATION"
    printf 'SERVED_MODEL_NAME=home-fast\nDEFAULT_THINKING=disabled\nLANGUAGE_MODEL_ONLY=true\n'
    printf 'CACHE_MANIFEST_SHA256=%s\n' "$(sha256sum "$(manifest_path)" | cut -d ' ' -f 1)"
  } >"$temporary"
  chown root:gb10-ai "$temporary"; chmod 0440 "$temporary"; mv -fT "$temporary" "$record"
  log "Wrote release record: $record"
}

wait_health() {
  local deadline=$((SECONDS + WAIT_SECONDS))
  until curl --fail --silent --max-time 3 "http://${GB10_BIND_ADDRESS}:${HOME_MODEL_HOST_PORT}/health" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || return 1
    sleep 5
  done
}

smoke() (
  load_env
  local base="http://${GB10_BIND_ADDRESS}:${HOME_MODEL_HOST_PORT}" header code models response
  header="$(mktemp)"; trap 'rm -f -- "$header"' EXIT
  printf 'Authorization: Bearer %s\n' "$(<"$VLLM_API_KEY_FILE")" >"$header"; chmod 0600 "$header"
  curl --fail --silent --max-time 10 "$base/health" >/dev/null
  code="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "$base/v1/models")"
  [[ "$code" == 401 || "$code" == 403 ]] || die "Unauthenticated model listing was not denied"
  models="$(curl --fail --silent --max-time 30 --header "@$header" "$base/v1/models")"
  jq -e '.data | any(.id == "home-fast")' <<<"$models" >/dev/null || die "home-fast alias is missing"
  response="$(curl --fail --silent --max-time 300 --header "@$header" -H 'Content-Type: application/json' \
    --data '{"model":"home-fast","messages":[{"role":"system","content":"Du er Home Assistant. Svar kort på dansk."},{"role":"user","content":"Svar kun med ordet KLAR."}],"temperature":0,"max_tokens":32,"chat_template_kwargs":{"enable_thinking":false}}' \
    "$base/v1/chat/completions")"
  jq -e '.choices[0].message.content | ascii_upcase | contains("KLAR")' <<<"$response" >/dev/null || die "Danish direct-response smoke failed"
  python3 "$SCRIPT_DIR/tool_call_smoke.py" --base-url "$base" --model home-fast \
    --api-key-file "$VLLM_API_KEY_FILE" --choices required auto || die "home-fast tool-call smoke failed"
  log "home-fast health, auth, alias, Danish text, and required/automatic tool-call smokes passed"
)

case "$COMMAND" in
  validate) validate_config ;;
  prepare) prepare_model ;;
  install) require_root; validate_config; compose --profile prepare pull home-fetch home-edge home-primary; prepare_model; write_record ;;
  up) require_root; validate_config; verify_cache; compose up -d home-edge home-primary; wait_health || { compose stop home-primary home-edge; die "home-fast failed readiness"; }; smoke ;;
  smoke) smoke ;;
  status) load_env; compose ps ;;
  logs) load_env; compose logs --tail 200 home-primary home-edge ;;
  down) require_root; load_env; compose down ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
