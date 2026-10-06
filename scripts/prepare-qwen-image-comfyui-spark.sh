#!/usr/bin/env bash
# Prepare an exclusive ComfyUI/Qwen-Image-Edit-2511 + Scottzilla LoRA lane on home-spark.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly ROOT=/srv/gb10-ai/qwen-image-edit-comfyui
readonly MODEL_ROOT="$ROOT/models"
readonly COMFY_COMMIT=e9027f2b30f37bb3052714eb08fcf479542f4fc0
readonly WORKFLOW_COMMIT=0e5c5efb32ba6f3365d6da07da64aaf668157042
readonly MODEL_REVISION=f68ace85e60b4a02a323e394253731947657b7d2
readonly LORA_REVISION=66e89e998dd4ea1a359c4bf0dd5e17d2f0b06ef0
readonly HF_TOKEN_FILE=/etc/gb10-ai/secrets/hf_token
readonly FLASH_CONTAINER=qwen38-flash-ultrafast
readonly IMAGE_CONTAINER=homecompute-qwen-image-edit-comfyui

die() { printf '[qwen-image-edit-comfyui] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[qwen-image-edit-comfyui] %s\n' "$*"; }

[[ $EUID == 0 ]] || die 'run as root on home-spark'
[[ $(hostname -s) == home-spark ]] || die 'this deployment is restricted to home-spark'
[[ $(uname -m) == aarch64 ]] || die 'DGX Spark ARM64 is required'
for cmd in docker git curl python3 df systemctl install; do command -v "$cmd" >/dev/null || die "missing prerequisite: $cmd"; done
docker info >/dev/null 2>&1 || die 'Docker is unavailable'
[[ -s $HF_TOKEN_FILE && ! -L $HF_TOKEN_FILE ]] || die "Hugging Face token is missing: $HF_TOKEN_FILE"
[[ $(stat -c '%u:%g' "$HF_TOKEN_FILE") == 0:* ]] || die 'Hugging Face token must be root-owned'
[[ $((8#$(stat -c '%a' "$HF_TOKEN_FILE") & 8#007)) == 0 ]] || die 'Hugging Face token must not be world accessible'
[[ $(docker inspect --format '{{.State.Running}}' "$FLASH_CONTAINER" 2>/dev/null || true) == true ]] ||
  die "expected default Flash-Next container is not running: $FLASH_CONTAINER"
[[ $(docker inspect --format '{{.State.Running}}' "$IMAGE_CONTAINER" 2>/dev/null || true) != true ]] ||
  die 'Qwen Image service is already running; stop it through the mode controller before reinstalling'

free_gib=$(df -BG --output=avail /srv | tail -n 1 | tr -dc '0-9')
[[ ${free_gib:-0} -ge 60 ]] || die "need at least 60 GiB free on /srv; found ${free_gib:-0} GiB"

install -d -o root -g root -m 0755 "$ROOT" "$ROOT/build" "$ROOT/build/context" "$ROOT/build/context/ComfyUI" "$ROOT/models" \
  "$ROOT/models/diffusion_models" "$ROOT/models/text_encoders" "$ROOT/models/vae" "$ROOT/models/loras" \
  "$ROOT/input" "$ROOT/output" "$ROOT/workflows" /var/lib/homecompute-qwen-image

if [[ ! -d $ROOT/build/ComfyUI/.git ]]; then
  git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git "$ROOT/build/ComfyUI"
fi
git -C "$ROOT/build/ComfyUI" fetch --quiet --no-tags origin "$COMFY_COMMIT"
git -C "$ROOT/build/ComfyUI" checkout --quiet --detach "$COMFY_COMMIT"
[[ $(git -C "$ROOT/build/ComfyUI" rev-parse HEAD) == "$COMFY_COMMIT" ]] || die 'ComfyUI revision mismatch'
tar -C "$ROOT/build/ComfyUI" --exclude=.git -cf - . | tar -C "$ROOT/build/context/ComfyUI" -xf -

cat >"$ROOT/build/Dockerfile" <<EOF
FROM nvcr.io/nvidia/pytorch:26.02-py3
WORKDIR /opt/ComfyUI
RUN apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY ComfyUI/ /opt/ComfyUI/
RUN sed -i '/^torch\\b/d; /^torchvision\\b/d; /^torchaudio\\b/d; /^nvidia/d' requirements.txt && pip install --no-cache-dir -r requirements.txt
EXPOSE 8188
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
EOF
docker build --pull -t homecompute/qwen-image-edit-comfyui:20261003 -f "$ROOT/build/Dockerfile" "$ROOT/build/context"

log 'downloading Qwen-Image-Edit-2511 and the Scottzilla NSFW editing LoRA'
docker run --rm --mount "type=bind,src=$HF_TOKEN_FILE,dst=/run/secrets/hf_token,readonly" \
  --mount "type=bind,src=$MODEL_ROOT,dst=/models" \
  homecompute/qwen-image-edit-comfyui:20261003 python -c '
from pathlib import Path
import os
from huggingface_hub import hf_hub_download
token = Path("/run/secrets/hf_token").read_text().strip()
revision = "'"$MODEL_REVISION"'"
for repo, rev, source_name, destination_name in [
    ("Comfy-Org/Qwen-Image-Edit_ComfyUI", revision,
     "split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors",
     "diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors"),
    ("Comfy-Org/Qwen-Image-Edit_ComfyUI", revision,
     "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
     "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    ("Comfy-Org/Qwen-Image-Edit_ComfyUI", revision,
     "split_files/vae/qwen_image_vae.safetensors",
     "vae/qwen_image_vae.safetensors"),
    ("ScottzillaSystems/qwen-image-edit-plus-nsfw-lora", "'"$LORA_REVISION"'",
     "qwen-image-edit-plus-nsfw-lora.safetensors",
     "loras/qwen-image-edit-plus-nsfw-lora.safetensors"),
]:
    destination = Path("/models") / destination_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        print(f"already present: {destination_name}", flush=True)
        continue
    downloaded = Path(hf_hub_download(repo_id=repo, filename=source_name, revision=rev,
                                      token=token, local_dir="/models"))
    os.link(downloaded.resolve(), destination)
    print(f"ready: {destination_name}", flush=True)
'

workflow_url="https://raw.githubusercontent.com/Comfy-Org/workflow_templates/$WORKFLOW_COMMIT/templates/image_qwen_image_edit_2511.json"
curl --fail --silent --show-error --location "$workflow_url" -o "$ROOT/workflows/qwen-image-edit-2511-scottzilla-nsfw.json"
python3 - "$ROOT/workflows/qwen-image-edit-2511-scottzilla-nsfw.json" <<'PATCH_WORKFLOW'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
workflow = json.loads(path.read_text())
graphs = workflow.get("definitions", {}).get("subgraphs", [])
graph = next((g for g in graphs if any(n.get("id") == 153 for n in g.get("nodes", []))), None)
if graph is None:
    raise SystemExit("Qwen Edit 2511 LoRA loader was not found in pinned workflow")
nodes = {n["id"]: n for n in graph["nodes"]}
# Replace the template's optional speed LoRA with Scottzilla's adapter. Keep
# the Qwen recommended 40-step path and CFG 4 for this editing LoRA.
nodes[153]["widgets_values"] = ["qwen-image-edit-plus-nsfw-lora.safetensors", 1]
nodes[155]["widgets_values"] = [4]
# The template wires its turbo toggle to both a four-step path and a model
# switch. Select the adapter model independently, leaving the turbo toggle off.
switch = nodes[163]
switch_input = next(i for i in switch["inputs"] if i.get("name") == "switch")
switch_input["link"] = None
switch["widgets_values"] = [True]
for source in graph["nodes"]:
    for output in source.get("outputs", []):
        if output.get("links"):
            output["links"] = [x for x in output["links"] if x != 333]
graph["links"] = [link for link in graph["links"] if link["id"] != 333]
path.write_text(json.dumps(workflow, indent=2) + "\n")
PATCH_WORKFLOW
python3 -m json.tool "$ROOT/workflows/qwen-image-edit-2511-scottzilla-nsfw.json" >/dev/null || die 'patched workflow is invalid JSON'
for sample in leather_sofa.png texture_fur.png; do
  curl --fail --silent --show-error --location \
    "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/$WORKFLOW_COMMIT/input/$sample" \
    -o "$ROOT/input/$sample"
done

if docker inspect "$IMAGE_CONTAINER" >/dev/null 2>&1; then
  docker rm "$IMAGE_CONTAINER" >/dev/null
fi
docker create --name "$IMAGE_CONTAINER" --restart=no --gpus all --ipc=host --ulimit memlock=-1:-1 \
  -p 127.0.0.1:8188:8188 \
  -v "$MODEL_ROOT:/opt/ComfyUI/models:ro" -v "$ROOT/input:/opt/ComfyUI/input" \
  -v "$ROOT/output:/opt/ComfyUI/output" -v "$ROOT/workflows:/opt/ComfyUI/user/default/workflows:ro" \
  homecompute/qwen-image-edit-comfyui:20261003 >/dev/null

cat >/usr/local/sbin/homecompute-qwen-image-mode <<'MODE'
#!/usr/bin/env bash
set -Eeuo pipefail
FLASH=qwen38-flash-ultrafast
IMAGE=homecompute-qwen-image-edit-comfyui
LOCK=/var/lock/homecompute-qwen-image-mode.lock
IDLE=/var/lib/homecompute-qwen-image/idle-since
exec 9>"$LOCK"
flock -x 9

queue_empty() {
  curl -fsS --max-time 5 http://127.0.0.1:8188/queue | python3 -c 'import json,sys; q=json.load(sys.stdin); sys.exit(0 if not q.get("queue_running") and not q.get("queue_pending") else 1)'
}
flash_idle() {
  curl -fsS --max-time 5 http://127.0.0.1:18300/metrics | python3 -c 'import re,sys; t=sys.stdin.read(); n=[float(x) for x in re.findall(r"^vllm:num_requests_(?:running|waiting)(?:\{[^}]*\})?\s+([0-9.eE+-]+)$",t,re.M)]; sys.exit(0 if len(n)>=2 and all(x==0 for x in n) else 1)'
}
wait_ready() {
  local url="$1" deadline=$((SECONDS + 300))
  while ((SECONDS < deadline)); do
    curl -fsS --max-time 5 "$url" >/dev/null && return 0
    sleep 5
  done
  return 1
}
wait_flash_idle() {
  local deadline=$((SECONDS + 1200)) stable=0
  while ((SECONDS < deadline)); do
    if flash_idle; then stable=$((stable + 10)); ((stable >= 60)) && return 0
    else stable=0
    fi
    sleep 10
  done
  echo 'Flash-Next did not remain idle for 60 seconds; image mode was not started.' >&2
  return 1
}
to_text() {
  if docker inspect "$IMAGE" >/dev/null 2>&1 && [[ $(docker inspect --format '{{.State.Running}}' "$IMAGE") == true ]]; then
    queue_empty || { echo 'ComfyUI has queued or running work; refusing to unload.' >&2; return 1; }
    systemctl stop homecompute-qwen-image.service
  fi
  rm -f "$IDLE"
  systemctl start homecompute-flash-next.service
  wait_ready http://127.0.0.1:18300/health || { echo 'Flash-Next did not become ready.' >&2; return 1; }
}
case "${1:-}" in
  image)
    wait_flash_idle
    if ! systemctl start homecompute-qwen-image.service || ! wait_ready http://127.0.0.1:8188/system_stats; then
      systemctl stop homecompute-qwen-image.service || true
      systemctl start homecompute-flash-next.service
      echo 'ComfyUI did not become ready; restored Flash-Next.' >&2
      exit 1
    fi
    date +%s >"$IDLE"
    ;;
  text)
    to_text
    ;;
  status)
    printf 'flash=%s image=%s\n' "$(docker inspect --format '{{.State.Status}}' "$FLASH" 2>/dev/null || echo missing)" \
      "$(docker inspect --format '{{.State.Status}}' "$IMAGE" 2>/dev/null || echo missing)"
    ;;
  expire)
    [[ -e $IDLE ]] || exit 0
    if ! docker inspect "$IMAGE" >/dev/null 2>&1 || [[ $(docker inspect --format '{{.State.Running}}' "$IMAGE") != true ]]; then rm -f "$IDLE"; exit 0; fi
    if ! queue_empty; then date +%s >"$IDLE"; exit 0; fi
    now=$(date +%s); since=$(cat "$IDLE");
    if ((now - since >= 3600)); then to_text; fi
    ;;
  *) echo 'Usage: homecompute-qwen-image-mode {image|text|status}' >&2; exit 2 ;;
esac
MODE
chmod 0755 /usr/local/sbin/homecompute-qwen-image-mode

cat >/etc/systemd/system/homecompute-flash-next.service <<'UNIT'
[Unit]
Description=Default Qwen3.8 Flash-Next model on home-spark
After=docker.service
Requires=docker.service
Conflicts=homecompute-qwen-image.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker start qwen38-flash-ultrafast
ExecStop=/usr/bin/docker stop --time 120 qwen38-flash-ultrafast

[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/systemd/system/homecompute-qwen-image.service <<'UNIT'
[Unit]
Description=Qwen-Image-Edit-2511 ComfyUI lane with Scottzilla LoRA
After=docker.service
Requires=docker.service
Conflicts=homecompute-flash-next.service

[Service]
Type=simple
ExecStart=/usr/bin/docker start --attach homecompute-qwen-image-edit-comfyui
ExecStop=/usr/bin/docker stop --time 60 homecompute-qwen-image-edit-comfyui
Restart=no
TimeoutStartSec=180
TimeoutStopSec=90
UNIT

cat >/etc/systemd/system/homecompute-qwen-image-idle.service <<'UNIT'
[Unit]
Description=Return home-spark to Flash-Next after an idle Qwen Image hour

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/homecompute-qwen-image-mode expire
UNIT

cat >/etc/systemd/system/homecompute-qwen-image-idle.timer <<'UNIT'
[Unit]
Description=Check Qwen Image queue for one-hour idle timeout

[Timer]
OnBootSec=10s
OnUnitActiveSec=10s
AccuracySec=1s
Unit=homecompute-qwen-image-idle.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable homecompute-flash-next.service homecompute-qwen-image-idle.timer
systemctl start homecompute-flash-next.service homecompute-qwen-image-idle.timer
log 'prepared ComfyUI with Qwen-Image-Edit-2511 and Scottzilla NSFW LoRA; Flash-Next remains the active default'
log 'switch with: sudo /usr/local/sbin/homecompute-qwen-image-mode image'
log 'return with: sudo /usr/local/sbin/homecompute-qwen-image-mode text'
log 'UI listens on loopback port 8188; expose it only through the home-core tunnel/reverse proxy'
