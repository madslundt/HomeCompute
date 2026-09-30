#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

COMMAND="${1:-help}"
(( $# == 0 )) || shift
PROFILE=quality
ENV_FILE="${GB10_ENV_FILE:-/etc/gb10-ai/gb10.env}"
WAIT_SECONDS=1800
log() { printf '[flash-next] %s\n' "$*"; }
die() { printf '[flash-next] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die 'Rerun this system-changing command with sudo'; }
CONFIG_TEMPLATE="$SCRIPT_DIR/../config/compute-node.env.example"
[[ -f "$CONFIG_TEMPLATE" ]] || die 'Compute-node configuration template is missing'
mapfile -t CONFIG_KEYS < <(sed -nE 's/^([A-Z][A-Z0-9_]*)=.*/\1/p' "$CONFIG_TEMPLATE")
(( ${#CONFIG_KEYS[@]} > 0 )) || die 'Compute-node configuration template has no allowlisted keys'

usage() {
  cat <<'USAGE'
Usage: setup-compute-flash-next.sh COMMAND [OPTIONS]
Commands:
  validate          Check immutable source/model pins and host prerequisites
  prepare           Prepare the selected profile's pinned source, image and weights
  install           Alias for prepare; does not start or stop any model
  activate-canary    Exclusively start one selected profile and run health/tool smoke checks
  switch-profile     Restore the previous text service, then activate the selected profile
  smoke             Check the isolated OpenAI protocol and tool-call endpoint
  status            Show candidate state and recorded local image identity
  deactivate-canary Stop candidate and restore the previously running text containers
Options:
  --profile NAME    quality (default) or ultrafast
  --env FILE        Root-owned compute-node environment (default /etc/gb10-ai/gb10.env)
  --wait SECONDS    Readiness timeout (default 1800)

Activation is an explicit operator-only cold swap. It never changes production
routes. Speech services are not stopped. The previous running text containers
are recorded before they are stopped, so deactivate-canary can restore them.
USAGE
}
parse_options() {
  while (( $# )); do
    case "$1" in
      --env) (( $# >= 2 )) || die '--env requires a file'; ENV_FILE="$2"; shift 2 ;;
      --profile) (( $# >= 2 )) || die '--profile requires quality or ultrafast'; PROFILE="$2"; [[ "$PROFILE" == quality || "$PROFILE" == ultrafast ]] || die 'Unknown Flash-Next profile'; shift 2 ;;
      --wait) (( $# >= 2 )) || die '--wait requires seconds'; [[ "$2" =~ ^[1-9][0-9]*$ ]] || die '--wait must be a positive integer'; WAIT_SECONDS="$((10#$2))"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
}
load_env() {
  [[ -f "$ENV_FILE" ]] || die "Environment file not found: $ENV_FILE"
  load_trusted_env_file "$ENV_FILE" 0 'Compute-node configuration' "${CONFIG_KEYS[@]}" || die "Refusing untrusted or malformed configuration: $ENV_FILE"
  : "${GB10_ROOT:?Missing GB10_ROOT}" "${FLASH_NEXT_SOURCE_URL:?Missing FLASH_NEXT_SOURCE_URL}"
  : "${FLASH_NEXT_SOURCE_REVISION:?Missing FLASH_NEXT_SOURCE_REVISION}" "${FLASH_NEXT_MODEL_ID:?Missing FLASH_NEXT_MODEL_ID}"
  : "${FLASH_NEXT_MODEL_REVISION:?Missing FLASH_NEXT_MODEL_REVISION}" "${FLASH_NEXT_BASE_IMAGE:?Missing FLASH_NEXT_BASE_IMAGE}"
  : "${FLASH_NEXT_IMAGE:?Missing FLASH_NEXT_IMAGE}" "${FLASH_NEXT_HF_CACHE:?Missing FLASH_NEXT_HF_CACHE}"
  : "${FLASH_NEXT_STATE_DIR:?Missing FLASH_NEXT_STATE_DIR}" "${FLASH_NEXT_HOST_PORT:?Missing FLASH_NEXT_HOST_PORT}"
  : "${FLASH_NEXT_SERVED_MODEL_NAME:?Missing FLASH_NEXT_SERVED_MODEL_NAME}"
}
source_dir() {
  if [[ "$PROFILE" == ultrafast ]]; then printf '%s/src/dime-qwen38-flash-ultrafast' "$GB10_ROOT";
  else printf '%s/src/blazux-qwen38-flash-dgx' "$GB10_ROOT"; fi
}
image_record() {
  if [[ "$PROFILE" == ultrafast ]]; then printf '%s/runtime.env' "$FLASH_ULTRAFAST_STATE_DIR";
  else printf '%s/runtime.env' "$FLASH_NEXT_STATE_DIR"; fi
}
active_record() { printf '%s/previous-text-containers' "$FLASH_NEXT_STATE_DIR"; }
active_profile_record() { printf '%s/active-profile' "$FLASH_NEXT_STATE_DIR"; }
flash() { "$(source_dir)/flash" "$@"; }
available_gib_on_existing_parent() {
  local path="$1"
  while [[ ! -e "$path" ]]; do
    [[ "$path" != / ]] || die 'Could not find an existing filesystem for the candidate cache'
    path="$(dirname -- "$path")"
  done
  df -BG --output=avail "$path" | tail -1 | tr -dc '0-9'
}

validate_quality() {
  load_env
  [[ "$FLASH_NEXT_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'Blazux source revision must be a full Git commit'
  [[ "$FLASH_NEXT_MODEL_REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'Model revision must be a full Hub commit'
  [[ "$FLASH_NEXT_MODEL_ID" == nvidia/Qwen3.8-Flash-Next-NVFP4 ]] || die 'Unexpected model ID; refusing silent checkpoint substitution'
  [[ "$FLASH_NEXT_CTX" == 262144 && "$FLASH_NEXT_YARN" == 0 ]] || die 'Candidate requires native 262144 context with YaRN disabled'
  [[ "$FLASH_NEXT_MODE" == hybrid && "$FLASH_NEXT_MTP" == 2 && "$FLASH_NEXT_SEQS" == 4 ]] || die 'Candidate runtime tuple differs from the qualified profile'
  [[ "$FLASH_NEXT_REASONING_EFFORT" == xhigh ]] || die 'Primary qualification effort must remain xhigh'
  [[ "$FLASH_NEXT_ENABLE_THINKING" == true ]] || die 'Thinking must be enabled for the primary candidate tuple'
  [[ "$FLASH_NEXT_PREFIX_CACHE" == 1 && "$FLASH_NEXT_DET_TOPK" == 1 && "$FLASH_NEXT_EFFORT_ALIAS" == 1 && "$FLASH_NEXT_KV_DTYPE" == auto ]] || die 'Candidate cache/reasoning tuple differs from the qualified profile'
  [[ "$FLASH_NEXT_GPU_MEM" == 0.68 || "$FLASH_NEXT_GPU_MEM" == 0.70 || "$FLASH_NEXT_GPU_MEM" == 0.72 || "$FLASH_NEXT_GPU_MEM" == 0.74 ]] || die 'GPU memory must be one of the controlled 0.68/0.70/0.72/0.74 steps'
  if ! command -v git >/dev/null || ! command -v docker >/dev/null; then die 'git and docker are required'; fi
  [[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || die 'This candidate lifecycle runs only on the ARM64 DGX Spark host'
  local available_gib; available_gib="$(available_gib_on_existing_parent "$FLASH_NEXT_HF_CACHE")"
  [[ "${available_gib:-0}" -ge 160 ]] || die 'Candidate cache filesystem requires at least 160 GiB free for the pinned weights and hybrid layout'
  [[ -d /dev/dri ]] || log 'No /dev/dri device node observed; NVIDIA container access still needs host qualification'
  docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable'
  log "Pins valid: $FLASH_NEXT_SOURCE_REVISION / $FLASH_NEXT_MODEL_ID@$FLASH_NEXT_MODEL_REVISION"
}

validate_ultrafast() {
  load_env
  [[ "$FLASH_ULTRAFAST_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ && "$FLASH_ULTRAFAST_MODEL_REVISION" =~ ^[0-9a-f]{40}$ && "$FLASH_ULTRAFAST_PLE_REVISION" =~ ^[0-9a-f]{40}$ ]] || die 'UltraFast source/model/PLE revisions must be full commits'
  [[ "$FLASH_ULTRAFAST_SOURCE_REVISION" == 0c391a3e74b6a775cfe248691ca7fd855b1876a5 ]] || die 'Unexpected UltraFast source revision'
  [[ "$FLASH_ULTRAFAST_MODEL_ID" == Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid && "$FLASH_ULTRAFAST_MODEL_REVISION" == 8b82f0b7abe3d1150a7827d298c75e86267636ae ]] || die 'Unexpected UltraFast checkpoint; refusing silent substitution'
  [[ "$FLASH_ULTRAFAST_PLE_MODEL_ID" == Saren/Qwen3.8-Flash-Next-ple-table-fp8 && "$FLASH_ULTRAFAST_PLE_REVISION" == 50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14 ]] || die 'Unexpected UltraFast PLE table; refusing silent substitution'
  [[ "$FLASH_ULTRAFAST_CTX" == 262144 && "$FLASH_ULTRAFAST_MTP" == 3 && "$FLASH_ULTRAFAST_SEQS" == 8 && "$FLASH_ULTRAFAST_KV_BYTES" == 16g ]] || die 'UltraFast v16b context/MTP/sequence/KV tuple differs from the pinned profile'
  [[ "$FLASH_ULTRAFAST_HOST_PORT" == "$FLASH_NEXT_HOST_PORT" && "$FLASH_ULTRAFAST_SERVED_MODEL_NAME" == automation-qualification ]] || die 'UltraFast must use the isolated qualification listener and stable qualification alias'
  [[ "$FLASH_ULTRAFAST_HOST_BIND" == "$GB10_BIND_ADDRESS" ]] || die 'UltraFast must bind to the configured private GB10 address'
  if ! command -v git >/dev/null || ! command -v docker >/dev/null || ! command -v patch >/dev/null; then
    die 'git, docker and patch are required'
  fi
  [[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || die 'UltraFast lifecycle runs only on ARM64 GB10'
  local available_gib; available_gib="$(available_gib_on_existing_parent "$FLASH_ULTRAFAST_MODELS_ROOT")"
  [[ "${available_gib:-0}" -ge 160 ]] || die 'UltraFast model filesystem requires at least 160 GiB free'
  docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable'
  log "UltraFast pins valid: $FLASH_ULTRAFAST_SOURCE_REVISION / $FLASH_ULTRAFAST_MODEL_ID@$FLASH_ULTRAFAST_MODEL_REVISION"
}
validate() { if [[ "$PROFILE" == ultrafast ]]; then validate_ultrafast; else validate_quality; fi; }

prepare_source() {
  local dir; dir="$(source_dir)"
  mkdir -p "$(dirname "$dir")" "$FLASH_NEXT_STATE_DIR" "$FLASH_NEXT_HF_CACHE"
  if [[ -e "$dir" ]]; then
    [[ -d "$dir/.git" ]] || die "Source path exists but is not a Git checkout: $dir"
    [[ -z "$(git -C "$dir" status --porcelain)" ]] || die 'Pinned Blazux checkout has local changes; refusing to overwrite it'
    git -C "$dir" fetch --quiet --no-tags "$FLASH_NEXT_SOURCE_URL" "$FLASH_NEXT_SOURCE_REVISION"
  else
    git clone --quiet --no-checkout "$FLASH_NEXT_SOURCE_URL" "$dir"
    git -C "$dir" fetch --quiet --no-tags origin "$FLASH_NEXT_SOURCE_REVISION"
  fi
  git -C "$dir" checkout --quiet --detach "$FLASH_NEXT_SOURCE_REVISION"
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$FLASH_NEXT_SOURCE_REVISION" ]] || die 'Blazux checkout does not match pinned commit'
  [[ -f "$dir/Dockerfile" && -x "$dir/flash" && -x "$dir/scripts/prepare-hybrid.sh" ]] || die 'Pinned Blazux source is missing expected entry points'
  [[ "$(sed -n 's/^FROM //p' "$dir/Dockerfile" | head -1)" == vllm/vllm-openai:v0.30.0 ]] || die 'Pinned Dockerfile base version differs from configuration'
}

prepare_model() {
  local dir snapshot refs tmp image_id build_context hybrid_manifest
  dir="$(source_dir)"
  build_context="$(mktemp -d "$FLASH_NEXT_STATE_DIR/build-context.XXXXXX")"
  git -C "$dir" archive "$FLASH_NEXT_SOURCE_REVISION" | tar -x -C "$build_context"
  sed -i "s|^FROM vllm/vllm-openai:v0.30.0$|FROM $FLASH_NEXT_BASE_IMAGE|" "$build_context/Dockerfile"
  [[ "$(sed -n 's/^FROM //p' "$build_context/Dockerfile" | head -1)" == "$FLASH_NEXT_BASE_IMAGE" ]] || die 'Could not pin Dockerfile base image digest'
  if ! docker build --pull -t "$FLASH_NEXT_IMAGE" "$build_context"; then rm -rf "$build_context"; die 'Pinned candidate image build failed'; fi
  rm -rf "$build_context"
  image_id="$(docker image inspect --format '{{.Id}}' "$FLASH_NEXT_IMAGE")"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die 'Could not capture exact candidate image ID'
  mkdir -p "$(dirname "$(image_record)")"
  {
    printf 'SOURCE_REVISION=%s\n' "$FLASH_NEXT_SOURCE_REVISION"
    printf 'MODEL_ID=%s\n' "$FLASH_NEXT_MODEL_ID"
    printf 'MODEL_REVISION=%s\n' "$FLASH_NEXT_MODEL_REVISION"
    printf 'BASE_IMAGE=%s\n' "$FLASH_NEXT_BASE_IMAGE"
    printf 'IMAGE_TAG=%s\n' "$FLASH_NEXT_IMAGE"
    printf 'IMAGE_ID=%s\n' "$image_id"
    printf 'BUILD_TIME_UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'MODE=%s\nCTX=%s\nYARN=%s\nMTP=%s\nSEQS=%s\n' "$FLASH_NEXT_MODE" "$FLASH_NEXT_CTX" "$FLASH_NEXT_YARN" "$FLASH_NEXT_MTP" "$FLASH_NEXT_SEQS"
    printf 'GPU_MEM=%s\nPREFIX_CACHE=%s\nDET_TOPK=%s\nEFFORT_ALIAS=%s\nKV_DTYPE=%s\n' "$FLASH_NEXT_GPU_MEM" "$FLASH_NEXT_PREFIX_CACHE" "$FLASH_NEXT_DET_TOPK" "$FLASH_NEXT_EFFORT_ALIAS" "$FLASH_NEXT_KV_DTYPE"
    printf 'EXTRA=%s\nREASONING_EFFORT=%s\nENABLE_THINKING=%s\n' "$FLASH_NEXT_EXTRA" "$FLASH_NEXT_REASONING_EFFORT" "$FLASH_NEXT_ENABLE_THINKING"
  } >"$(image_record)"
  chmod 0440 "$(image_record)"

  docker run --rm --name flash-next-fetch \
    -e HF_HOME=/hf -v "$FLASH_NEXT_HF_CACHE:/hf" \
    --entrypoint hf "$FLASH_NEXT_IMAGE" download "$FLASH_NEXT_MODEL_ID" \
    --revision "$FLASH_NEXT_MODEL_REVISION" --max-workers 8

  refs="$FLASH_NEXT_HF_CACHE/hub/models--${FLASH_NEXT_MODEL_ID//\//--}/refs"
  snapshot="$FLASH_NEXT_HF_CACHE/hub/models--${FLASH_NEXT_MODEL_ID//\//--}/snapshots/$FLASH_NEXT_MODEL_REVISION"
  [[ -d "$snapshot" ]] || die 'Pinned model revision did not produce the expected cache snapshot'
  mkdir -p "$refs"
  tmp="$refs/.main.$$"; printf '%s\n' "$FLASH_NEXT_MODEL_REVISION" >"$tmp"; mv -f "$tmp" "$refs/main"
  (cd "$dir" && MODEL="$FLASH_NEXT_MODEL_ID" HF_CACHE="$FLASH_NEXT_HF_CACHE" IMAGE="$FLASH_NEXT_IMAGE" ./scripts/prepare-hybrid.sh)
  [[ -f "$snapshot-fp8hybrid/.prepared" ]] || die 'Hybrid layout preparation did not complete'
  hybrid_manifest="$FLASH_NEXT_STATE_DIR/hybrid-layout.sha256"
  find "$snapshot-fp8hybrid" -type f ! -name .prepared -print0 | LC_ALL=C sort -z | xargs -0 sha256sum >"$hybrid_manifest"
  chmod 0440 "$hybrid_manifest"
  printf 'HYBRID_LAYOUT_MANIFEST_SHA256=%s\n' "$(sha256sum "$hybrid_manifest" | cut -d ' ' -f 1)" >>"$(image_record)"
  log "Pinned weights and hybrid layout prepared; image ID recorded in $(image_record)"
}

install_candidate() {
  require_root; validate
  if candidate_running; then die 'Stop the active qualification candidate before installing or rebuilding a profile'; fi
  if [[ "$PROFILE" == quality ]]; then
    prepare_source; prepare_model
  else
    install_ultrafast
  fi
  log "$PROFILE profile installed and prepared; no model was started and production routes were not changed"
}

ultrafast_runtime_dir() { printf '%s/runtime' "$FLASH_ULTRAFAST_STATE_DIR"; }
ultrafast_source_dir() { printf '%s/src/dime-qwen38-flash-ultrafast' "$GB10_ROOT"; }
ultrafast_container_running() { docker inspect -f '{{.State.Running}}' "$FLASH_ULTRAFAST_NAME" 2>/dev/null | grep -qx true; }
quality_container_running() { docker inspect -f '{{.State.Running}}' qwen38-flash-homecompute 2>/dev/null | grep -qx true; }
candidate_running() { ultrafast_container_running || quality_container_running; }

install_ultrafast() {
  local dir runtime model_dir output_dir table_dir image_id patch_file expected_patch model_report tokenizer_config_sha
  dir="$(ultrafast_source_dir)"
  runtime="$(ultrafast_runtime_dir)"
  model_dir="$FLASH_ULTRAFAST_MODELS_ROOT/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid"
  output_dir="$FLASH_ULTRAFAST_MODELS_ROOT/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid-mtpdense-g32"
  table_dir="$FLASH_ULTRAFAST_MODELS_ROOT/ple-table-fp8"
  patch_file="$SCRIPT_DIR/../deploy/compute-node/patches/dime-ultrafast-private-launch.patch"
  expected_patch=9cf8895b57560381c555970d2d043c089df9fa3eb2b9dab69440a035b14228cb
  [[ "$(sha256sum "$patch_file" | awk '{print $1}')" == "$expected_patch" ]] || die 'HomeCompute private-launch patch checksum changed'
  mkdir -p "$(dirname "$dir")" "$FLASH_ULTRAFAST_MODELS_ROOT" "$FLASH_ULTRAFAST_STATE_DIR"
  if [[ -e "$dir" ]]; then
    [[ -d "$dir/.git" && -z "$(git -C "$dir" status --porcelain)" ]] || die 'UltraFast source path is not a clean Git checkout'
    git -C "$dir" fetch --quiet --no-tags "$FLASH_ULTRAFAST_SOURCE_URL" "$FLASH_ULTRAFAST_SOURCE_REVISION"
  else
    git clone --quiet --no-checkout "$FLASH_ULTRAFAST_SOURCE_URL" "$dir"
    git -C "$dir" fetch --quiet --no-tags origin "$FLASH_ULTRAFAST_SOURCE_REVISION"
  fi
  git -C "$dir" checkout --quiet --detach "$FLASH_ULTRAFAST_SOURCE_REVISION"
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$FLASH_ULTRAFAST_SOURCE_REVISION" ]] || die 'UltraFast checkout does not match its pinned commit'
  local build_script_sha dockerfile6c_sha dockerfile6d_sha serve_sha launcher_sha env_sha
  build_script_sha="$(sha256sum "$dir/recipe/build/image/build.sh" | awk '{print $1}')"
  dockerfile6c_sha="$(sha256sum "$dir/recipe/build/image/Dockerfile.iter6c" | awk '{print $1}')"
  dockerfile6d_sha="$(sha256sum "$dir/recipe/build/image/Dockerfile.iter6d" | awk '{print $1}')"
  serve_sha="$(sha256sum "$dir/recipe/config/v16b/serve.sh" | awk '{print $1}')"
  launcher_sha="$(sha256sum "$dir/recipe/config/v16b/launch.sh" | awk '{print $1}')"
  env_sha="$(sha256sum "$dir/recipe/config/v16b/env" | awk '{print $1}')"
  [[ "$build_script_sha" == a9418c3d2ad6321f7c348f017c8b7d19de3474f0334188d2a6afb8708ed4d0f4 && \
     "$dockerfile6c_sha" == e9aff690bf014b4913af3071c6a011bb1eadaeed1d7d4fea38fb012cc56cf89b && \
     "$dockerfile6d_sha" == b302b04bf5cd23bdc69cec7b35cf1b57d8bad16b071bb68cf2e3924f30fa0ecf && \
     "$serve_sha" == 3758d9552e09dd8778415305e62385dcbdfad1f886406d081f5d83136a375131 && \
     "$launcher_sha" == 5f81f5abe72cd5cb9e6aab2f226f7cf8f8f43249bc647f1fba45c761555764aa && \
     "$env_sha" == 3ef3605f47da62905c70071791dab74aef39f1e039c3665082742842481c0641 ]] || die 'Pinned UltraFast build or launch inputs changed'
  (cd "$dir/recipe/build/image" && bash ./build.sh --run)
  image_id="$(docker image inspect --format '{{.Id}}' "$FLASH_ULTRAFAST_IMAGE")"
  [[ -d "$model_dir" ]] || mkdir -p "$model_dir"
  docker run --rm -v "$FLASH_ULTRAFAST_MODELS_ROOT:/models" --entrypoint hf "$FLASH_ULTRAFAST_IMAGE" download "$FLASH_ULTRAFAST_MODEL_ID" --revision "$FLASH_ULTRAFAST_MODEL_REVISION" --local-dir "/models/$(basename "$model_dir")"
  tokenizer_config_sha="$(sha256sum "$model_dir/tokenizer_config.json" | awk '{print $1}')"
  [[ "$tokenizer_config_sha" == 792fa3f0cb88b111e54ef3134c873531008c4df471d108da17903426e308aa7b ]] || die 'UltraFast tokenizer config differs from the reviewed pinned file'
  mkdir -p "$table_dir"
  docker run --rm -v "$FLASH_ULTRAFAST_MODELS_ROOT:/models" --entrypoint hf "$FLASH_ULTRAFAST_IMAGE" download "$FLASH_ULTRAFAST_PLE_MODEL_ID" --revision "$FLASH_ULTRAFAST_PLE_REVISION" --local-dir "/models/$(basename "$table_dir")"
  MODELS_ROOT="$FLASH_ULTRAFAST_MODELS_ROOT" MODEL_DIR="$model_dir" OUT_DIR="$output_dir" IMAGE="$FLASH_ULTRAFAST_IMAGE" \
    bash "$dir/recipe/build/model/build.sh" --run

  if [[ -e "$runtime" ]]; then
    [[ -d "$runtime" && ! -L "$runtime" ]] || die 'UltraFast runtime path is not a regular directory'
    local unexpected_runtime_file
    unexpected_runtime_file="$(find "$runtime" -mindepth 1 -maxdepth 1 -type f ! -name env ! -name image ! -name serve.sh ! -name launch.sh ! -name draft-vocab-ids-K65536.txt.gz -print -quit)"
    [[ -z "$unexpected_runtime_file" ]] || die "Unexpected file in UltraFast runtime directory: $unexpected_runtime_file"
  fi
  mkdir -p "$runtime"
  cp "$dir/recipe/config/v16b/launch.sh" "$dir/recipe/config/v16b/serve.sh" "$dir/recipe/config/v16b/draft-vocab-ids-K65536.txt.gz" "$runtime/"
  (cd "$runtime" && patch --batch --forward --fuzz=0 -p4 <"$patch_file") || die 'Could not apply pinned HomeCompute launch patch'
  printf '%s\n' "$FLASH_ULTRAFAST_IMAGE" >"$runtime/image"
  cat >"$runtime/env" <<ENV
NAME='$FLASH_ULTRAFAST_NAME'
IMAGE='$FLASH_ULTRAFAST_IMAGE'
MODEL_DIR='$output_dir'
TABLE_DIR='$table_dir'
PORT='$FLASH_ULTRAFAST_HOST_PORT'
HOST_BIND='$FLASH_ULTRAFAST_HOST_BIND'
SERVED_NAME='$FLASH_ULTRAFAST_SERVED_MODEL_NAME'
HOME_COMPUTE_PROFILE='ultrafast'
TOOL_PARSER='$FLASH_ULTRAFAST_TOOL_CALL_PARSER'
REASONING_PARSER='$FLASH_ULTRAFAST_REASONING_PARSER'
PREFIX_CACHE='$FLASH_ULTRAFAST_PREFIX_CACHE'
GPU_MEM='$FLASH_ULTRAFAST_GPU_MEM'
KV_BYTES='$FLASH_ULTRAFAST_KV_BYTES'
SEQS='$FLASH_ULTRAFAST_SEQS'
MTP='$FLASH_ULTRAFAST_MTP'
CTX='$FLASH_ULTRAFAST_CTX'
EXTRA='$FLASH_ULTRAFAST_EXTRA'
LOAD_FORMAT='fastsafetensors'
PREWARM='1'
WORKERS='32'
PLE_PREFETCH='0'
PLE_MADV_RANDOM='1'
PLE_FAST_ROWS='16'
PLE_CHUNK='8'
SPEC_EXTRA='{"rejection_sample_method":"block","draft_sample_method":"probabilistic"}'
HIT_DEBUG='0'
STEP_PROFILE='0'
FP8_HYBRID='1'
CUDA_LAUNCH_BLOCKING='0'
FLASHINFER_AUTOTUNE='0'
ENV
  chmod 0440 "$runtime/env" "$runtime/image" "$runtime/serve.sh" "$runtime/launch.sh"
  model_report="$output_dir/dense-mtp-build-report.json"
  [[ -f "$model_report" ]] || die 'UltraFast dense-MTP report is missing'
  mkdir -p "$(dirname "$(image_record)")"
  {
    printf 'PROFILE=ultrafast\nSOURCE_REVISION=%s\nMODEL_ID=%s\nMODEL_REVISION=%s\nPLE_MODEL_ID=%s\nPLE_REVISION=%s\n' "$FLASH_ULTRAFAST_SOURCE_REVISION" "$FLASH_ULTRAFAST_MODEL_ID" "$FLASH_ULTRAFAST_MODEL_REVISION" "$FLASH_ULTRAFAST_PLE_MODEL_ID" "$FLASH_ULTRAFAST_PLE_REVISION"
    printf 'PARENT_IMAGE=%s\nIMAGE_TAG=%s\nIMAGE_ID=%s\n' "$FLASH_ULTRAFAST_BASE_IMAGE" "$FLASH_ULTRAFAST_IMAGE" "$image_id"
    printf 'LAUNCH_PATCH_SHA256=%s\nMODEL_BUILD_REPORT_SHA256=%s\nBUILD_TIME_UTC=%s\n' "$expected_patch" "$(sha256sum "$model_report" | awk '{print $1}')" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'TOKENIZER_CONFIG_SHA256=%s\nSERVED_NAME=%s\nTOOL_PARSER=%s\nREASONING_PARSER=%s\n' "$tokenizer_config_sha" "$FLASH_ULTRAFAST_SERVED_MODEL_NAME" "$FLASH_ULTRAFAST_TOOL_CALL_PARSER" "$FLASH_ULTRAFAST_REASONING_PARSER"
    printf 'CTX=%s\nSEQS=%s\nGPU_MEM=%s\nKV_BYTES=%s\nMTP=%s\nDRAFT_VOCAB_SIZE=%s\n' "$FLASH_ULTRAFAST_CTX" "$FLASH_ULTRAFAST_SEQS" "$FLASH_ULTRAFAST_GPU_MEM" "$FLASH_ULTRAFAST_KV_BYTES" "$FLASH_ULTRAFAST_MTP" "$FLASH_ULTRAFAST_DRAFT_VOCAB_SIZE"
    printf 'PREFIX_CACHE=%s\nMTP_TIER=%s\nMTP_GROUP_SIZE=%s\nLOAD_FORMAT=fastsafetensors\nPLE_MMAP=1\n' "$FLASH_ULTRAFAST_PREFIX_CACHE" "$FLASH_ULTRAFAST_MTP_TIER" "$FLASH_ULTRAFAST_MTP_GROUP_SIZE"
  } >"$(image_record)"
  chmod 0440 "$(image_record)"
  log 'Pinned UltraFast image, target checkpoint, PLE table and dense-MTP layout prepared; runtime remains stopped'
}
restore_previous() {
  local name active_profile
  active_profile="$(cat "$(active_profile_record)" 2>/dev/null || true)"
  if [[ "$active_profile" == ultrafast ]] && ultrafast_container_running; then docker stop "$FLASH_ULTRAFAST_NAME" >/dev/null 2>&1 || true; fi
  if [[ "$active_profile" == quality ]] && quality_container_running; then NAME=qwen38-flash-homecompute flash stop >/dev/null 2>&1 || docker stop qwen38-flash-homecompute >/dev/null 2>&1 || true; fi
  if [[ -f "$(active_record)" ]]; then
    while IFS= read -r name; do [[ -n "$name" ]] && docker start "$name" >/dev/null; done <"$(active_record)"
    rm -f "$(active_record)"
  fi
  rm -f "$(active_profile_record)"
}
on_signal() { restore_previous || true; exit 130; }

smoke() {
  load_env
  local base="http://${GB10_BIND_ADDRESS}:${FLASH_NEXT_HOST_PORT}" served_model="$FLASH_NEXT_SERVED_MODEL_NAME" effort="$FLASH_NEXT_REASONING_EFFORT"
  if [[ "$PROFILE" == ultrafast ]]; then
    base="http://${FLASH_ULTRAFAST_HOST_BIND}:${FLASH_ULTRAFAST_HOST_PORT}"
    served_model="$FLASH_ULTRAFAST_SERVED_MODEL_NAME"
    effort=high
  fi
  curl --fail --silent --max-time 10 "$base/health" >/dev/null || die 'Candidate health endpoint failed'
  python3 - "$base" "$served_model" "$effort" <<'PY'
import json, sys, urllib.error, urllib.request
base, model, effort = sys.argv[1:]
def post(path, body):
    data=json.dumps(body).encode()
    request=urllib.request.Request(base+path,data=data,headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=300) as response: return json.load(response)
models=urllib.request.urlopen(base+'/v1/models',timeout=20)
items=json.load(models).get('data',[])
if not any(row.get('id')==model for row in items): raise SystemExit('candidate served alias missing')
data=post('/v1/chat/completions',{
 'model':model,'temperature':0,'max_tokens':64,'reasoning_effort':effort,'chat_template_kwargs':{'enable_thinking':True},'tool_choice':'required',
 'messages':[{'role':'user','content':'Call report_status with status set to ready.'}],
 'tools':[{'type':'function','function':{'name':'report_status','description':'Report a status.',
 'parameters':{'type':'object','properties':{'status':{'type':'string','enum':['ready']}},'required':['status'],'additionalProperties':False}}}]
})
calls=data.get('choices',[{}])[0].get('message',{}).get('tool_calls',[])
if len(calls)!=1 or calls[0].get('function',{}).get('name')!='report_status': raise SystemExit('required tool-call smoke failed')
try: args=json.loads(calls[0]['function']['arguments'])
except Exception as exc: raise SystemExit('tool arguments were not valid JSON') from exc
if args!={'status':'ready'}: raise SystemExit('tool-call smoke returned unexpected arguments')
usage=data.get('usage',{})
print(json.dumps({'health':'ok','served_alias':model,'tool_call':'pass','input_tokens':usage.get('prompt_tokens'),
 'output_tokens':usage.get('completion_tokens')},sort_keys=True))
PY
}

activate_canary() {
  require_root; validate
  [[ -f "$(image_record)" ]] || die 'Run install first; no pinned local image record exists'
  local expected actual name
  if [[ "$PROFILE" == ultrafast ]]; then
    expected="$(sed -n 's/^IMAGE_ID=//p' "$(image_record)")"
    actual="$(docker image inspect --format '{{.Id}}' "$FLASH_ULTRAFAST_IMAGE" 2>/dev/null || true)"
  else
    expected="$(sed -n 's/^IMAGE_ID=//p' "$(image_record)")"
    actual="$(docker image inspect --format '{{.Id}}' "$FLASH_NEXT_IMAGE" 2>/dev/null || true)"
  fi
  [[ -n "$expected" && "$actual" == "$expected" ]] || die 'Local image differs from the recorded candidate image ID'
  if [[ -e "$(active_record)" || -e "$(active_profile_record)" ]]; then die 'A prior activation record exists; run deactivate-canary before activating again'; fi
  mkdir -p "$FLASH_NEXT_STATE_DIR"
  : >"$(active_record)"
  for name in gb10-text-primary gb10-home-fast gb10-automation-primary gb10-automation-nvidia-primary; do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" == true ]]; then
      printf '%s\n' "$name" >>"$(active_record)"
    fi
  done
  [[ -s "$(active_record)" ]] || die 'No known resident text service found; refusing an untracked activation'
  chmod 0600 "$(active_record)"
  printf '%s\n' "$PROFILE" >"$(active_profile_record)"
  chmod 0600 "$(active_profile_record)"
  trap on_signal INT TERM
  while IFS= read -r name; do docker stop "$name" >/dev/null; done <"$(active_record)"
  if [[ "$PROFILE" == ultrafast ]]; then
    local runtime_dir="$FLASH_ULTRAFAST_STATE_DIR/runtime"
    if docker inspect "$FLASH_ULTRAFAST_NAME" >/dev/null 2>&1; then
      [[ "$(docker inspect -f '{{ index .Config.Labels "homecompute.managed" }}' "$FLASH_ULTRAFAST_NAME")" == flash-next && \
         "$(docker inspect -f '{{.Image}}' "$FLASH_ULTRAFAST_NAME")" == "$expected" ]] || {
        restore_previous; trap - INT TERM; die 'Existing UltraFast container is not the recorded HomeCompute candidate';
      }
      docker start "$FLASH_ULTRAFAST_NAME" >/dev/null
    else
      (cd "$runtime_dir" && ./launch.sh --run)
    fi
  else
    (cd "$(source_dir)" && NAME=qwen38-flash-homecompute IMAGE="$FLASH_NEXT_IMAGE" MODEL="$FLASH_NEXT_MODEL_ID" \
      HF_CACHE="$FLASH_NEXT_HF_CACHE" MODE="$FLASH_NEXT_MODE" CTX="$FLASH_NEXT_CTX" YARN="$FLASH_NEXT_YARN" \
      MTP="$FLASH_NEXT_MTP" SEQS="$FLASH_NEXT_SEQS" GPU_MEM="$FLASH_NEXT_GPU_MEM" \
      PREFIX_CACHE="$FLASH_NEXT_PREFIX_CACHE" DET_TOPK="$FLASH_NEXT_DET_TOPK" EFFORT_ALIAS="$FLASH_NEXT_EFFORT_ALIAS" \
      KV_DTYPE="$FLASH_NEXT_KV_DTYPE" EXTRA="$FLASH_NEXT_EXTRA" PORT="$FLASH_NEXT_HOST_PORT" \
      SERVED_MODEL_NAME="$FLASH_NEXT_SERVED_MODEL_NAME" ./scripts/serve.sh)
  fi
  if ! wait_for_health; then
    restore_previous; trap - INT TERM; die 'Candidate failed to start; the previous text services were restored'
  fi
  if ! smoke; then restore_previous; trap - INT TERM; die 'Candidate smoke failed; the previous text services were restored'; fi
  trap - INT TERM
  log 'Candidate is active only on its canary port; the LiteLLM production route is unchanged'
}
wait_for_health() {
  local deadline=$((SECONDS + WAIT_SECONDS))
  local port="$FLASH_NEXT_HOST_PORT"
  [[ "$PROFILE" == ultrafast ]] && port="$FLASH_ULTRAFAST_HOST_PORT"
  local bind="$GB10_BIND_ADDRESS"
  [[ "$PROFILE" == ultrafast ]] && bind="$FLASH_ULTRAFAST_HOST_BIND"
  until curl --fail --silent --max-time 3 "http://${bind}:${port}/health" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || return 1
    sleep 5
  done
}
status() {
  load_env
  if [[ "$PROFILE" == ultrafast ]]; then
    printf 'profile: ultrafast\nsource: %s\nmodel: %s@%s\n' "$FLASH_ULTRAFAST_SOURCE_REVISION" "$FLASH_ULTRAFAST_MODEL_ID" "$FLASH_ULTRAFAST_MODEL_REVISION"
  else
    printf 'profile: quality\nsource: %s\nmodel: %s@%s\n' "$FLASH_NEXT_SOURCE_REVISION" "$FLASH_NEXT_MODEL_ID" "$FLASH_NEXT_MODEL_REVISION"
  fi
  if [[ -f "$(image_record)" ]]; then cat "$(image_record)"; else printf 'image: not built\n'; fi
  if [[ "$(cat "$(active_profile_record)" 2>/dev/null || true)" == ultrafast ]] && ultrafast_container_running; then printf 'candidate: ultrafast-running\n'
  elif quality_container_running; then printf 'candidate: quality-running\n'
  else printf 'candidate: stopped\n'; fi
}

parse_options "$@"
case "$COMMAND" in
  validate) validate ;;
  prepare|install) install_candidate ;;
  activate-canary) activate_canary ;;
  switch-profile)
    require_root
    [[ -f "$(active_record)" ]] || die 'No active qualification profile to switch; use activate-canary'
    restore_previous
    activate_canary
    ;;
  smoke) smoke ;;
  status) status ;;
  deactivate-canary) require_root; load_env; restore_previous; log 'Candidate stopped and prior text containers restored' ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
