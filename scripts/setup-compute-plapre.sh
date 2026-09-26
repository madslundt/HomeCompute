#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/deploy/compute-node/plapre/compose.yaml"
FIREWALL_HELPER=/usr/local/libexec/gb10-compute-firewall
FIREWALL_CONFIG=/etc/gb10-ai/firewall.env
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

CONFIG_KEYS=(
  PLAPRE_BASE_IMAGE PLAPRE_LOCAL_IMAGE PLAPRE_CODE_REVISION PLAPRE_MODEL_REVISION
  KANADE_CODE_REVISION KANADE_MODEL_REVISION HIFT_MODEL_REVISION
  TORCHAUDIO_CODE_REVISION
  PLAPRE_RUNTIME_UID PLAPRE_RUNTIME_GID PLAPRE_BIND_ADDRESS PLAPRE_WYOMING_PORT
  PLAPRE_GPU_MEMORY_UTILIZATION PLAPRE_MAX_MODEL_LEN PLAPRE_VOICE_ALIAS
  PLAPRE_SPEAKER_ID PLAPRE_TEMPO PLAPRE_MAX_INPUT_CHARS PLAPRE_MAX_PCM_BYTES
  PLAPRE_REQUEST_TIMEOUT_SECONDS PLAPRE_MODEL_LICENSE_ID PLAPRE_VOICE_APPROVAL
  PLAPRE_PRIVATE_INGRESS_CONFIRMED
)
COMMAND="${1:-help}"
(($# == 0)) || shift
ENV_FILE="${PLAPRE_ENV_FILE:-/etc/gb10-ai/plapre-tts.env}"

usage() {
  cat <<'USAGE'
Usage: setup-compute-plapre.sh COMMAND [--env FILE]
Commands:
  validate  Validate exact pins, bounds, publication topology, and opt-in gates
  build     Build the pinned derived image; do not start it
  up        Start only the isolated Plapre/Wyoming project
  smoke     Synthesize one fixed Danish phrase over Wyoming
  status    Show only this project's containers
  logs      Show the last 200 service log lines
  down      Stop only this project; retain its local image

The existing Piper service is independent and remains the Home Assistant
fallback. No command changes Home Assistant, the compute firewall, or routing.
USAGE
}
log() { printf '[compute-plapre] %s\n' "$*"; }
die() { printf '[compute-plapre] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "This command changes system state; rerun with sudo"; }

while (($#)); do
  case "$1" in
    --env) (($# >= 2)) || die "--env requires a file"; ENV_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 "Plapre configuration" "${CONFIG_KEYS[@]}" ||
    die "Refusing untrusted or malformed configuration: $ENV_FILE"
}
compose() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

validate_config() {
  local services
  load_env
  [[ "$PLAPRE_BASE_IMAGE" == nvcr.io/nvidia/vllm@sha256:604e5b052d1ce1b87952c72bf95cc637192c0051fc15a32886dff20bffd5c514 ]] ||
    die "Base image must be the reviewed NVIDIA vLLM 26.02 ARM64 digest"
  [[ "$PLAPRE_CODE_REVISION" == b111239d3b099cfafcb54b3471a8e9e9ba71ae8b ]] || die "Unexpected Plapre code revision"
  [[ "$PLAPRE_MODEL_REVISION" == 007e0b471e377dd5061786f7df6ad659d15c4d5f ]] || die "Unexpected Plapre model revision"
  [[ "$KANADE_CODE_REVISION" == 961f20bf892c59f391d0b6c5f7b88e70ed919b99 ]] || die "Unexpected Kanade code revision"
  [[ "$KANADE_MODEL_REVISION" == cb2c8f10959ff0e5d3e97c9b82fcc3779c9532a5 ]] || die "Unexpected Kanade model revision"
  [[ "$HIFT_MODEL_REVISION" == eec1ae6c79877dbd9379285cf8789c9e0879293d ]] || die "Unexpected HiFT model revision"
  [[ "$TORCHAUDIO_CODE_REVISION" == 34c52a67e8941bbd8e6adaca0eb0b9eabec11d78 ]] || die "Unexpected torchaudio code revision"
  [[ "$PLAPRE_MODEL_LICENSE_ID" == cc-by-4.0 ]] || die "Unexpected Plapre model license"
  [[ "$PLAPRE_GPU_MEMORY_UTILIZATION:$PLAPRE_MAX_MODEL_LEN" == 0.06:512 ]] ||
    die "Coexistence envelope must begin at 0.06 GPU memory and 512 tokens"
  [[ "$PLAPRE_WYOMING_PORT" == 10201 ]] || die "Wyoming port must be 10201"
  [[ "$PLAPRE_VOICE_ALIAS:$PLAPRE_SPEAKER_ID" == danish-default:tor ]] ||
    die "Only the reviewed external alias mapping may be advertised"
  [[ "$PLAPRE_TEMPO" == 1.25 ]] || die "Plapre household tempo must be 1.25"
  [[ "$PLAPRE_MAX_INPUT_CHARS:$PLAPRE_MAX_PCM_BYTES:$PLAPRE_REQUEST_TIMEOUT_SECONDS" == 2000:48000000:300 ]] ||
    die "Adapter request bounds differ from the reviewed tuple"
  [[ "$PLAPRE_RUNTIME_UID" =~ ^[1-9][0-9]*$ && "$PLAPRE_RUNTIME_GID" =~ ^[1-9][0-9]*$ ]] ||
    die "Runtime UID and GID must be positive numeric identities"
  case "$PLAPRE_BIND_ADDRESS" in
    127.0.0.1|10.77.10.10) ;;
    *) die "Wyoming must bind loopback or the compute private-link address" ;;
  esac
  [[ "$PLAPRE_VOICE_APPROVAL" == review-required || "$PLAPRE_VOICE_APPROVAL" == approved-household-use ]] ||
    die "PLAPRE_VOICE_APPROVAL must be review-required or approved-household-use"
  [[ "$PLAPRE_PRIVATE_INGRESS_CONFIRMED" == true || "$PLAPRE_PRIVATE_INGRESS_CONFIRMED" == false ]] ||
    die "PLAPRE_PRIVATE_INGRESS_CONFIRMED must be true or false"
  command -v docker >/dev/null || die "docker is required"
  docker compose version >/dev/null
  compose config --quiet
  services="$(compose config --services)"
  for service in plapre-primary plapre-wyoming plapre-edge; do
    [[ $'\n'"$services"$'\n' == *$'\n'"$service"$'\n'* ]] || die "Compose render is missing $service"
  done
  log "Pinned image, sources, artifacts, resource bounds, voice alias, and topology are valid"
}

activation_gates() {
  [[ "$PLAPRE_VOICE_APPROVAL" == approved-household-use ]] ||
    die "Activation requires a recorded approved-household-use voice decision"
  if [[ "$PLAPRE_BIND_ADDRESS" == 10.77.10.10 && "$PLAPRE_PRIVATE_INGRESS_CONFIRMED" != true ]]; then
    die "Private-link activation requires the exact source -> 10.77.10.10:10201 firewall rule first"
  fi
  if [[ "$PLAPRE_BIND_ADDRESS" == 10.77.10.10 ]]; then
    [[ -x "$FIREWALL_HELPER" ]] || die "Installed compute firewall helper is missing"
    "$FIREWALL_HELPER" verify --config "$FIREWALL_CONFIG"
  fi
}

build_image() {
  require_root
  validate_config
  compose build --pull plapre-primary
  log "Built the pinned local image without starting a service"
}

start_services() {
  require_root
  validate_config
  activation_gates
  docker image inspect "$PLAPRE_LOCAL_IMAGE" >/dev/null 2>&1 || die "Local image is missing; run build first"
  compose up -d --no-build --wait --wait-timeout 1200
  log "Plapre Wyoming is staged on $PLAPRE_BIND_ADDRESS:$PLAPRE_WYOMING_PORT"
}

smoke() {
  validate_config
  activation_gates
  compose exec -T plapre-wyoming /opt/plapre/venv/bin/python - "$PLAPRE_VOICE_ALIAS" <<'PY'
import asyncio
import sys
from wyoming.audio import AudioChunk, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.tts import Synthesize, SynthesizeVoice

async def main():
    total = 0
    client = AsyncClient.from_uri("tcp://127.0.0.1:10201", connect_timeout=5, read_timeout=300)
    async with client:
        await client.write_event(Synthesize(text="Hej fra Home Assistant.", voice=SynthesizeVoice(name=sys.argv[1])).event())
        while True:
            event = await client.read_event()
            if event is None or Error.is_type(event.type):
                raise SystemExit("Wyoming synthesis failed")
            if AudioChunk.is_type(event.type):
                total += len(AudioChunk.from_event(event).audio)
            if AudioStop.is_type(event.type):
                break
    if total <= 0 or total % 2:
        raise SystemExit("Wyoming returned invalid PCM")

asyncio.run(main())
PY
  log "Wyoming synthesis smoke returned non-empty 24 kHz mono PCM"
}

case "$COMMAND" in
  validate) validate_config ;;
  build) build_image ;;
  up) start_services ;;
  smoke) smoke ;;
  status) load_env; compose ps ;;
  logs) load_env; compose logs --tail 200 plapre-primary plapre-wyoming plapre-edge ;;
  down) require_root; load_env; compose down ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
