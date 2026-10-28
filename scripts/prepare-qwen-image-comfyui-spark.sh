#!/usr/bin/env bash
# Prepare an exclusive ComfyUI/Qwen-Image-Edit-2511 + Scottzilla LoRA lane on home-spark.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly ROOT=/srv/gb10-ai/qwen-image-edit-comfyui
readonly MODEL_ROOT="$ROOT/models"
readonly USER_ROOT="$ROOT/user"
readonly WORKFLOW_ROOT="$USER_ROOT/default/workflows"
readonly COMFY_COMMIT=e9027f2b30f37bb3052714eb08fcf479542f4fc0
readonly WORKFLOW_COMMIT=0e5c5efb32ba6f3365d6da07da64aaf668157042
readonly MODEL_REVISION=f68ace85e60b4a02a323e394253731947657b7d2
readonly COMPONENT_REVISION=1f12b17be14c89b026c51a91d67c32f84bb047bc
readonly LORA_REVISION=66e89e998dd4ea1a359c4bf0dd5e17d2f0b06ef0
readonly HF_TOKEN_FILE=/etc/gb10-ai/secrets/hf_token
readonly IMAGE_CONTAINER=homecompute-qwen-image-edit-comfyui

die() { printf '[qwen-image-edit-comfyui] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[qwen-image-edit-comfyui] %s\n' "$*"; }

[[ $EUID == 0 ]] || die 'run as root on home-spark'
[[ $(hostname -s) == home-spark ]] || die 'this deployment is restricted to home-spark'
[[ $(uname -m) == aarch64 ]] || die 'DGX Spark ARM64 is required'
# Only root may choose the configured default; browsers receive fixed actions.
TEXT_CONTAINER="${TEXT_CONTAINER:-}"
if [[ -z $TEXT_CONTAINER && -e /etc/homecompute/image-mode.json ]]; then
  TEXT_CONTAINER="$(PYTHONPATH="$SCRIPT_DIR" python3 -c 'from llm_admission import TextConfig; print(TextConfig.load().container)')"
fi
readonly TEXT_CONTAINER="${TEXT_CONTAINER:-qwen38-flash-ultrafast}"
TEXT_CONTAINER="$TEXT_CONTAINER" PYTHONPATH="$SCRIPT_DIR" python3 -c 'import os; from llm_admission import TextConfig; TextConfig(os.environ["TEXT_CONTAINER"])'
for cmd in docker git curl python3 df systemctl install; do command -v "$cmd" >/dev/null || die "missing prerequisite: $cmd"; done
docker info >/dev/null 2>&1 || die 'Docker is unavailable'
token_mount=()
if [[ -e $HF_TOKEN_FILE ]]; then
  [[ -s $HF_TOKEN_FILE && ! -L $HF_TOKEN_FILE ]] || die 'invalid Hugging Face token file'
  [[ $(stat -c '%u:%g' "$HF_TOKEN_FILE") == 0:* ]] || die 'Hugging Face token must be root-owned'
  [[ $((8#$(stat -c '%a' "$HF_TOKEN_FILE") & 8#007)) == 0 ]] || die 'Hugging Face token must not be world accessible'
  token_mount=(--mount "type=bind,src=$HF_TOKEN_FILE,dst=/run/secrets/hf_token,readonly")
fi
[[ -f $SCRIPT_DIR/comfyui_lifecycle.py && -f $SCRIPT_DIR/comfyui_control_http.py && -f $SCRIPT_DIR/comfyui_progress.py && -f $SCRIPT_DIR/llm_admission.py && -f $SCRIPT_DIR/../deploy/comfyui-control/index.html ]] ||
  die 'deploy this installer with the lifecycle controller and control page from the same release'
# Preparation downloads files without requiring a running GPU service. The
# controller reconciles stopped services and restores Flash after installation.
docker inspect "$TEXT_CONTAINER" >/dev/null 2>&1 ||
  die "configured default text container does not exist: $TEXT_CONTAINER"
[[ $(docker inspect --format '{{.State.Running}}' "$IMAGE_CONTAINER" 2>/dev/null || true) != true ]] ||
  die 'Qwen Image service is already running; stop it through the mode controller before reinstalling'

free_gib=$(df -BG --output=avail /srv | tail -n 1 | tr -dc '0-9')
[[ ${free_gib:-0} -ge 60 ]] || die "need at least 60 GiB free on /srv; found ${free_gib:-0} GiB"

install -d -o root -g root -m 0755 "$ROOT" "$ROOT/build" "$ROOT/build/context" "$ROOT/build/context/ComfyUI" "$ROOT/models" \
  "$ROOT/models/diffusion_models" "$ROOT/models/text_encoders" "$ROOT/models/vae" "$ROOT/models/loras" \
  "$ROOT/input" "$ROOT/output" "$USER_ROOT" "$USER_ROOT/default" "$WORKFLOW_ROOT" /var/lib/homecompute-qwen-image
# Preserve workflows from the earlier workflows-only bind mount.
if [[ -d $ROOT/workflows ]]; then cp -an "$ROOT/workflows/." "$WORKFLOW_ROOT/"; fi

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
docker run --rm "${token_mount[@]}" \
  --mount "type=bind,src=$MODEL_ROOT,dst=/models" \
  homecompute/qwen-image-edit-comfyui:20261003 python -c '
from pathlib import Path
import os
from huggingface_hub import hf_hub_download
token_path = Path("/run/secrets/hf_token")
token = token_path.read_text().strip() if token_path.exists() else False
revision = "'"$MODEL_REVISION"'"
for repo, rev, source_name, destination_name in [
    ("Comfy-Org/Qwen-Image-Edit_ComfyUI", revision,
     "split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors",
     "diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors"),
    ("Comfy-Org/Qwen-Image_ComfyUI", "'"$COMPONENT_REVISION"'",
     "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
     "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"),
    ("Comfy-Org/Qwen-Image_ComfyUI", "'"$COMPONENT_REVISION"'",
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
curl --fail --silent --show-error --location "$workflow_url" -o "$WORKFLOW_ROOT/qwen-image-edit-2511-scottzilla-nsfw.json"
python3 - "$WORKFLOW_ROOT/qwen-image-edit-2511-scottzilla-nsfw.json" <<'PATCH_WORKFLOW'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
workflow = json.loads(path.read_text())
graphs = workflow.get("definitions", {}).get("subgraphs", [])
graph = next((g for g in graphs if any(n.get("id") == 153 for n in g.get("nodes", []))), None)
if graph is None:
    raise SystemExit("Qwen Edit 2511 LoRA loader was not found in pinned workflow")
nodes = {n["id"]: n for n in graph["nodes"]}
# Repurpose the template's boolean as an adapter-only toggle. Both paths keep
# the same sampler settings, so turning the adapter on cannot enable 4-step mode.
nodes[153]["widgets_values"] = ["qwen-image-edit-plus-nsfw-lora.safetensors", 1]
nodes[153]["properties"]["models"] = [{
    "name": "qwen-image-edit-plus-nsfw-lora.safetensors",
    "url": "https://huggingface.co/ScottzillaSystems/qwen-image-edit-plus-nsfw-lora/resolve/66e89e998dd4ea1a359c4bf0dd5e17d2f0b06ef0/qwen-image-edit-plus-nsfw-lora.safetensors",
    "directory": "loras",
}]
for node_id in (154, 155):
    nodes[node_id]["widgets_values"] = [4]
for node_id in (165, 166):
    nodes[node_id]["widgets_values"][0] = 40
nodes[168]["title"] = "Enable NSFW LoRA"
nodes[168]["widgets_values"] = [False]
for item in graph["inputs"]:
    if item.get("name") == "value":
        item["label"] = "enable_nsfw_lora"
for group in graph.get("groups", []):
    if group.get("id") == 4:
        group["title"] = "Optional NSFW LoRA"

# Start with one photo and an instruction. The optional additional reference
# sockets remain available inside the subgraph for more advanced edits later.
workflow["nodes"] = [node for node in workflow["nodes"] if node["id"] != 83]
workflow["links"] = [link for link in workflow["links"] if link[0] != 377]
root_nodes = {node["id"]: node for node in workflow["nodes"]}
root_nodes[41]["title"] = "1. Upload an image"
root_nodes[170]["title"] = "2. Describe your edit"
root_nodes[9]["title"] = "3. Save result"
for item in root_nodes[170]["inputs"]:
    if item.get("name") == "image2":
        item["link"] = None
    elif item.get("name") == "value":
        item["label"] = "enable_nsfw_lora"
nodes[151]["widgets_values"] = [
    "Change the sofa to dark green velvet. Preserve its shape, the room, and the lighting."
]
root_nodes[82]["title"] = "How to edit an image"
root_nodes[82]["size"] = [570, 320]
root_nodes[82]["widgets_values"] = [
    "## Edit one image\n\n"
    "1. Upload a photo in **1. Upload an image**. The sofa image is a ready-to-run example.\n"
    "2. Enter your instruction in **2. Describe your edit**. Say what should change and what should stay.\n"
    "3. Click **Run**, then view and download the image from **3. Save result**.\n\n"
    "**Enable NSFW LoRA** is off by default. Turn it on only when you want the optional Scottzilla adapter. "
    "Both modes use the same 40-step, CFG 4 settings."
]
root_nodes[157]["widgets_values"] = [
    "40 steps and CFG 4 are used with or without the adapter.\n\n"
    "**Enable NSFW LoRA** is off for baseline editing. Turning it on changes only the adapter; "
    "it does not change the number of steps or enable a speed mode."
]
path.write_text(json.dumps(workflow, indent=2) + "\n")
PATCH_WORKFLOW
python3 -m json.tool "$WORKFLOW_ROOT/qwen-image-edit-2511-scottzilla-nsfw.json" >/dev/null || die 'patched workflow is invalid JSON'
for sample in leather_sofa.png texture_fur.png; do
  curl --fail --silent --show-error --location \
    "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/$WORKFLOW_COMMIT/input/$sample" \
    -o "$ROOT/input/$sample"
done

if docker inspect "$IMAGE_CONTAINER" >/dev/null 2>&1; then
  docker rm "$IMAGE_CONTAINER" >/dev/null
fi
docker create --name "$IMAGE_CONTAINER" --restart=no --stop-signal SIGINT --gpus all --ipc=host --ulimit memlock=-1:-1 \
  -p 127.0.0.1:8188:8188 \
  -v "$MODEL_ROOT:/opt/ComfyUI/models:ro" -v "$ROOT/input:/opt/ComfyUI/input" \
  -v "$ROOT/output:/opt/ComfyUI/output" -v "$USER_ROOT:/opt/ComfyUI/user" \
  homecompute/qwen-image-edit-comfyui:20261003 >/dev/null

# Extend the existing forwarding-only identity with exactly the local image endpoints and gated text ingress. Its source-host restriction and other options stay intact.
python3 - <<'TUNNEL_ACCESS'
from pathlib import Path
import pwd, shutil
try:
    account = pwd.getpwnam('homecompute-tunnel')
except KeyError:
    raise SystemExit('Provision the existing homecompute-tunnel identity before image access')
path = Path(account.pw_dir) / '.ssh/authorized_keys'
if path.is_symlink():
    raise SystemExit('Refusing symlinked tunnel authorization')
original = path.read_text()
lines = original.splitlines()
matches = [i for i, line in enumerate(lines)
           if line.startswith('from="192.168.30.122",restrict,port-forwarding,')
           and ' ssh-ed25519 ' in line]
if len(matches) != 1:
    raise SystemExit('Expected one existing restricted home-core forwarding key')
index = matches[0]
for port in (8188, 8189, 18400):
    option = f'permitopen="127.0.0.1:{port}"'
    if option not in lines[index]:
        lines[index] = lines[index].replace(' ssh-ed25519 ', f',{option} ssh-ed25519 ', 1)
updated = '\n'.join(lines) + '\n'
if updated != original:
    backup = path.with_name('authorized_keys.before-comfyui')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated)
    path.chmod(0o600)
TUNNEL_ACCESS

# The controller adopts the already-running Flash container. It does not start
# an independent Flash systemd owner or change other GPU/speech services.
install -d -o root -g root -m 0755 /opt/homecompute/comfyui-control
install -o root -g root -m 0755 "$SCRIPT_DIR/comfyui_lifecycle.py" /opt/homecompute/comfyui-control/comfyui_lifecycle.py
install -o root -g root -m 0644 "$SCRIPT_DIR/comfyui_control_http.py" /opt/homecompute/comfyui-control/comfyui_control_http.py
install -o root -g root -m 0644 "$SCRIPT_DIR/llm_admission.py" /opt/homecompute/comfyui-control/llm_admission.py
install -o root -g root -m 0644 "$SCRIPT_DIR/comfyui_progress.py" /opt/homecompute/comfyui-control/comfyui_progress.py
TEXT_CONTAINER="$TEXT_CONTAINER" TEXT_DISPLAY_NAME="${TEXT_DISPLAY_NAME:-}" \
  TEXT_BASE_URL="${TEXT_BASE_URL:-}" TEXT_HEALTH_PATH="${TEXT_HEALTH_PATH:-}" \
  WORKFLOW_ACTIVITY_URL="${WORKFLOW_ACTIVITY_URL:-}" WORKFLOW_ACTIVITY_CA_FILE="${WORKFLOW_ACTIVITY_CA_FILE:-}" \
  PYTHONPATH="$SCRIPT_DIR" python3 - <<'TEXT_CONFIG'
from dataclasses import asdict, replace
import json, os
from pathlib import Path
from llm_admission import CONFIG, TextConfig, WorkflowActivity
current = TextConfig.load() if CONFIG.exists() else TextConfig(os.environ['TEXT_CONTAINER'])
changes = {'container': os.environ['TEXT_CONTAINER']}
for field, variable in [('display_name', 'TEXT_DISPLAY_NAME'), ('base_url', 'TEXT_BASE_URL'), ('health_path', 'TEXT_HEALTH_PATH')]:
    if os.environ.get(variable):
        changes[field] = os.environ[variable]
configured = replace(current, **changes)
document = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
document['default_text'] = asdict(configured)
for key, variable, default in [('workflow_activity_url', 'WORKFLOW_ACTIVITY_URL', 'https://home-core.tail479ad.ts.net:8443/llm/activity'),
                               ('workflow_activity_ca_file', 'WORKFLOW_ACTIVITY_CA_FILE', None)]:
    document[key] = os.environ.get(variable) or document.get(key) or default
WorkflowActivity(document['workflow_activity_url'], document['workflow_activity_ca_file'])
CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
if CONFIG.is_symlink():
    raise SystemExit('Refusing symlinked image-mode configuration')
temporary = CONFIG.with_suffix('.install-tmp')
with temporary.open('x') as handle:
    os.chmod(temporary, 0o600)
    json.dump(document, handle, indent=2)
    handle.write('\n')
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, CONFIG)
TEXT_CONFIG
install -o root -g root -m 0644 "$SCRIPT_DIR/../deploy/comfyui-control/index.html" /opt/homecompute/comfyui-control/index.html
cat >/etc/systemd/system/homecompute-comfyui-control.service <<'UNIT'
[Unit]
Description=Text, image, and video model lifecycle control
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/homecompute/comfyui-control/comfyui_lifecycle.py
Restart=always
RestartSec=5
UMask=0077
StateDirectory=homecompute-qwen-image
StateDirectoryMode=0700
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=/var/lib/homecompute-qwen-image
RestrictAddressFamilies=AF_INET AF_UNIX
MemoryMax=192M
TasksMax=48

[Install]
WantedBy=multi-user.target
UNIT
cat >/usr/local/sbin/homecompute-qwen-image-mode <<'MODE'
#!/usr/bin/env bash
set -Eeuo pipefail
case "${1:-}" in
  image|text)
    action=start; [[ $1 != text ]] || action=stop
    curl --fail-with-body --silent --show-error -H 'Origin: https://comfyui.home.arpa' \
      -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:8189/control/api/$action"
    ;;
  status) curl --fail --silent --show-error http://127.0.0.1:8189/control/api/status ;;
  *) echo 'Usage: homecompute-qwen-image-mode {image|text|status}' >&2; exit 2 ;;
esac
MODE
chmod 0755 /usr/local/sbin/homecompute-qwen-image-mode
# Remove the old timer's ownership if upgrading a previously prepared release.
systemctl disable --now homecompute-qwen-image-idle.timer 2>/dev/null || true
systemctl disable homecompute-flash-next.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable homecompute-comfyui-control.service
systemctl restart homecompute-comfyui-control.service
log 'prepared image-only ComfyUI with Qwen-Image-Edit-2511 and Scottzilla NSFW LoRA'
log 'Configured text model remains the default; controls listen only on loopback port 8189'
log 'Text admission proxy listens on loopback 18400; core inference tunnel must target it'
log 'image jobs use loopback 8188; /prompt must go through controller admission'
log 'switch with: sudo /usr/local/sbin/homecompute-qwen-image-mode image'
log 'return with: sudo /usr/local/sbin/homecompute-qwen-image-mode text'
