#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

action="${1:-}"
[[ $# == 1 && "$action" =~ ^(prepare|up|down|status)$ ]] || {
  printf 'Usage: sudo bash scripts/setup-home-core-stt.sh prepare|up|down|status\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Run as root on home-core.\n' >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE=/etc/homecompute/wyoming-stt.env
MODEL_DIR=/srv/state/wyoming-stt/models
READY_FILE="$MODEL_DIR/.homecompute-ready-model"
[[ -r "$ENV_FILE" ]] || { printf 'Missing %s\n' "$ENV_FILE" >&2; exit 1; }

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

required=(WYOMING_STT_IMAGE WYOMING_STT_LAN_ADDRESS WYOMING_STT_PORT WYOMING_STT_MODEL WYOMING_STT_LANGUAGE WYOMING_STT_BEAM_SIZE WYOMING_STT_CPU_THREADS)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { printf 'Missing setting: %s\n' "$name" >&2; exit 1; }
done
[[ "$WYOMING_STT_MODEL" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { printf 'Invalid STT model id.\n' >&2; exit 1; }
[[ "$WYOMING_STT_LANGUAGE" == da ]] || { printf 'Unexpected STT language.\n' >&2; exit 1; }
[[ "$WYOMING_STT_BEAM_SIZE" =~ ^[1-9][0-9]*$ ]] || { printf 'Invalid beam size.\n' >&2; exit 1; }
[[ "$WYOMING_STT_CPU_THREADS" =~ ^[1-9][0-9]*$ ]] || { printf 'Invalid CPU thread count.\n' >&2; exit 1; }

compose=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/deploy/wyoming-stt/compose.yaml")

verify_cached_file() {
  local filename="$1" candidate resolved
  while IFS= read -r -d '' candidate; do
    resolved="$(realpath -e -- "$candidate")" || continue
    if [[ "$resolved" == "$MODEL_DIR/"* && -f "$resolved" && -s "$resolved" ]]; then
      return 0
    fi
  done < <(find "$MODEL_DIR" \( -type f -o -type l \) -name "$filename" -print0)
  return 1
}

verify_model() {
  [[ -f "$READY_FILE" && ! -L "$READY_FILE" ]] || return 1
  [[ $(<"$READY_FILE") == "$WYOMING_STT_MODEL" ]] || return 1
  verify_cached_file config.json || return 1
  verify_cached_file model.bin
}

case "$action" in
  prepare)
    install -d -m 0750 -o 1000 -g 1000 "$MODEL_DIR"
    "${compose[@]}" pull stt-model-fetch
    "${compose[@]}" --profile prepare up -d --wait --wait-timeout 900 stt-model-fetch
    "${compose[@]}" --profile prepare rm -sf stt-model-fetch
    verify_cached_file config.json || {
      printf 'Prepared model has no non-empty config.json.\n' >&2; exit 1;
    }
    verify_cached_file model.bin || {
      printf 'Prepared model has no non-empty model.bin.\n' >&2; exit 1;
    }
    printf '%s\n' "$WYOMING_STT_MODEL" >"$READY_FILE"
    chmod 0444 "$READY_FILE"
    verify_model
    printf 'Prepared Wyoming Faster Whisper model: %s\n' "$WYOMING_STT_MODEL"
    ;;
  up)
    verify_model || { printf 'Run prepare first.\n' >&2; exit 1; }
    "${compose[@]}" up -d --wait --wait-timeout 300 stt
    ;;
  down)
    "${compose[@]}" --profile prepare down
    ;;
  status)
    "${compose[@]}" --profile prepare ps
    ;;
esac
