#!/usr/bin/env bash
# Prepare the pinned Qwen-Image-2.1 SGLang Diffusion runtime and checkpoint on
# home-spark. This command does not stop, start, or reconfigure a serving lane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly ROOT=/srv/gb10-ai
readonly LANE_DIR="$ROOT/qwen-image-2.1"
readonly RUNTIME_DIR="$LANE_DIR/venv"
readonly SOURCE_DIR="$LANE_DIR/sglang"
readonly CACHE_DIR="$ROOT/cache/qwen-image"
readonly PIP_CACHE_DIR="$CACHE_DIR/pip"
readonly STATE_DIR="$ROOT/manifests"
readonly STATE_FILE="$STATE_DIR/qwen-image-sglang.env"
readonly HF_TOKEN_FILE=/etc/gb10-ai/secrets/hf_token
readonly MODEL_ID=Qwen/Qwen-Image-2.1
readonly MODEL_REVISION=790c92633540aa0cb11d9abf19eb46d861714758
readonly SGLANG_REVISION=ddebc52f237a1dbb56533469ab2ec2a7b856c4ab
readonly SGLANG_WHEEL_VERSION=0.5.20
readonly LOCK_FILE=/var/lock/homecompute-qwen-image-prepare.lock

die() { printf '[qwen-image-prepare] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[qwen-image-prepare] %s\n' "$*"; }

[[ $EUID == 0 ]] || die 'run as root from the signed-in home-spark shell'
[[ $(hostname -s) == home-spark ]] || die 'this preparation script is restricted to home-spark'
[[ $(uname -m) == aarch64 || $(uname -m) == arm64 ]] || die 'DGX Spark ARM64 is required'
for cmd in git python3 runuser df flock; do command -v "$cmd" >/dev/null || die "missing prerequisite: $cmd"; done
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || die 'python3-venv/ensurepip is required'
id gb10-ai >/dev/null 2>&1 || die 'the existing gb10-ai service account is required'
[[ -s $HF_TOKEN_FILE && ! -L $HF_TOKEN_FILE ]] || die "Hugging Face token is missing: $HF_TOKEN_FILE"
[[ $(stat -c '%u:%g' "$HF_TOKEN_FILE") == 0:* ]] || die 'Hugging Face token must be root-owned'
[[ $((8#$(stat -c '%a' "$HF_TOKEN_FILE") & 8#007)) == 0 ]] || die 'Hugging Face token must not be world accessible'

install -d -o root -g root -m 0755 /var/lock
exec 9>"$LOCK_FILE"
flock -n 9 || die 'another Qwen-Image preparation is running'

for path in "$RUNTIME_DIR" "$SOURCE_DIR" "$CACHE_DIR"; do
  parent="$path"
  while [[ ! -e $parent ]]; do parent="$(dirname -- "$parent")"; done
  free_gib="$(df -BG --output=avail "$parent" | tail -n 1 | tr -dc '0-9')"
  [[ ${free_gib:-0} -ge 80 ]] || die "need at least 80 GiB free on the filesystem containing $path; found ${free_gib:-0} GiB"
done

# Stage under the established service account. Do not touch the live text lane.
install -d -o gb10-ai -g gb10-ai -m 0750 "$LANE_DIR" "$CACHE_DIR" "$CACHE_DIR/hf-home" "$PIP_CACHE_DIR"
install -d -o root -g gb10-ai -m 0750 "$STATE_DIR"
if [[ -e $RUNTIME_DIR && ! -x $RUNTIME_DIR/bin/python ]]; then
  [[ -d $RUNTIME_DIR && -z $(find "$RUNTIME_DIR" -mindepth 1 -maxdepth 1 -print -quit) ]] ||
    die "incomplete runtime directory exists; inspect before removing: $RUNTIME_DIR"
  rmdir "$RUNTIME_DIR"
fi
if [[ ! -x $RUNTIME_DIR/bin/python ]]; then
  runuser -u gb10-ai -- /usr/bin/python3 -m venv "$RUNTIME_DIR"
fi
runuser -u gb10-ai -- env PIP_CACHE_DIR="$PIP_CACHE_DIR" "$RUNTIME_DIR/bin/python" -m pip install --quiet --upgrade pip

if [[ -e $SOURCE_DIR ]]; then
  [[ -d $SOURCE_DIR/.git ]] || die "source path exists but is not a Git checkout: $SOURCE_DIR"
  [[ -z $(runuser -u gb10-ai -- git -C "$SOURCE_DIR" status --porcelain) ]] || die 'SGLang checkout has local changes; refusing to overwrite it'
else
  runuser -u gb10-ai -- git clone --no-checkout https://github.com/sgl-project/sglang.git "$SOURCE_DIR"
fi
runuser -u gb10-ai -- git -C "$SOURCE_DIR" fetch --quiet --no-tags origin "$SGLANG_REVISION"
runuser -u gb10-ai -- git -C "$SOURCE_DIR" checkout --quiet --detach "$SGLANG_REVISION"
[[ $(runuser -u gb10-ai -- git -C "$SOURCE_DIR" rev-parse HEAD) == "$SGLANG_REVISION" ]] || die 'SGLang source revision did not match the reviewed pin'

# Install the released ARM64 kernel wheel first, then overlay the unreleased
# Qwen-Image integration without resolving dependencies a second time.
if ! runuser -u gb10-ai -- "$RUNTIME_DIR/bin/python" -c 'from importlib.metadata import version; version("sglang-kernel")' >/dev/null 2>&1; then
  log "installing SGLang Diffusion wheel $SGLANG_WHEEL_VERSION (large CUDA dependencies may take several minutes)"
  runuser -u gb10-ai -- env PIP_CACHE_DIR="$PIP_CACHE_DIR" "$RUNTIME_DIR/bin/python" -m pip install --quiet --pre \
    "sglang[diffusion]==$SGLANG_WHEEL_VERSION"
fi
log "overlaying pinned SGLang source $SGLANG_REVISION"
runuser -u gb10-ai -- env PIP_CACHE_DIR="$PIP_CACHE_DIR" SGLANG_BUILD_RUST_EXTS=none \
  "$RUNTIME_DIR/bin/python" -m pip install --quiet --no-deps --editable "$SOURCE_DIR/python"
runuser -u gb10-ai -- "$RUNTIME_DIR/bin/python" - <<'PY'
import inspect
import pathlib
import sglang
from sglang.multimodal_gen import registry

assert "Qwen/Qwen-Image-2.1" in inspect.getsource(registry), "pinned runtime does not register Qwen-Image-2.1"
assert pathlib.Path(sglang.__file__).resolve().is_relative_to(pathlib.Path("/srv/gb10-ai/qwen-image-2.1/sglang").resolve()), "source overlay is not active"
print("Pinned SGLang source exposes Qwen-Image-2.1")
PY

log "downloading $MODEL_ID at $MODEL_REVISION (Hugging Face license access must already be accepted)"
HF_HOME="$CACHE_DIR/hf-home" HF_HUB_DISABLE_XET=1 \
  HF_HUB_DOWNLOAD_TIMEOUT=30 HF_TOKEN_FILE="$HF_TOKEN_FILE" \
  "$RUNTIME_DIR/bin/python" - "$MODEL_ID" "$MODEL_REVISION" <<'PY'
import os
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

os.environ["HF_TOKEN"] = Path(os.environ["HF_TOKEN_FILE"]).read_text().strip()
path = snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2])
print(path)
PY
chown -R gb10-ai:gb10-ai "$CACHE_DIR/hf-home"

snapshot="$CACHE_DIR/hf-home/hub/models--Qwen--Qwen-Image-2.1/snapshots/$MODEL_REVISION"
[[ -f $snapshot/model_index.json ]] || die "pinned checkpoint snapshot is missing model_index.json: $snapshot"
for component in processor text_encoder transformer vae scheduler; do
  [[ -d $snapshot/$component ]] || die "pinned checkpoint is missing $component"
done

temporary="$STATE_FILE.tmp.$$"
{
  printf 'MODEL_ID=%q\n' "$MODEL_ID"
  printf 'MODEL_REVISION=%q\n' "$MODEL_REVISION"
  printf 'SGLANG_REVISION=%q\n' "$SGLANG_REVISION"
  printf 'SGLANG_WHEEL_VERSION=%q\n' "$SGLANG_WHEEL_VERSION"
  printf 'RUNTIME_DIR=%q\n' "$RUNTIME_DIR"
  printf 'SOURCE_DIR=%q\n' "$SOURCE_DIR"
  printf 'PIP_CACHE_DIR=%q\n' "$PIP_CACHE_DIR"
  printf 'HF_HOME=%q\n' "$CACHE_DIR/hf-home"
  printf 'PREPARED_AT_UTC=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$temporary"
chown root:gb10-ai "$temporary"
chmod 0640 "$temporary"
mv -fT "$temporary" "$STATE_FILE"
log "runtime and checkpoint prepared; existing services and routes are unchanged"
log "recorded pins in $STATE_FILE; image service has not been started"
