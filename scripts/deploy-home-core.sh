#!/usr/bin/env bash
# Run from a reviewed checkout on home-core: sudo bash scripts/deploy-home-core.sh SHA
set -Eeuo pipefail
revision="${1:-}"
[[ $# == 1 && "$revision" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Usage: sudo bash scripts/deploy-home-core.sh FULL_COMMIT_SHA\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Run as root on home-core.\n' >&2; exit 1;
}
for executable in git nixos-rebuild docker flock; do command -v "$executable" >/dev/null; done
install -d -m 0755 /srv/homecompute/releases
install -d -m 0700 /var/lib/homecompute
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }
# Restoring state and the identity is a separate operation; never initialize an
# empty replacement for the migrated n8n instance as a side effect of deployment.
for required_file in /var/lib/sops-nix/key.txt /srv/state/automation/n8n/database.sqlite /srv/state/automation/n8n/config; do
  [[ -s "$required_file" ]] || { printf 'Restore required file first: %s\n' "$required_file" >&2; exit 1; }
done
release="/srv/homecompute/releases/$revision"
if [[ ! -d "$release" ]]; then
  git clone https://github.com/madslundt/HomeCompute.git "$release"
fi
if ! git -C "$release" cat-file -e "$revision^{commit}" 2>/dev/null; then
  git -C "$release" fetch origin "$revision"
fi
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Release checkout is dirty; preserving it: %s\n' "$release" >&2; exit 1;
}
git -C "$release" checkout --detach "$revision"
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]]
(cd /var/lib/homecompute && nixos-rebuild build --flake "$release#home-core")
nixos-rebuild switch --flake "$release#home-core"
gateway=(docker compose --env-file /etc/homecompute/control-plane.env -f "$release/deploy/control-plane/compose.yaml")
automation=(docker compose --env-file /etc/homecompute/automation.env -f "$release/deploy/automation/compose.yaml" -f "$release/deploy/automation/production.yaml")
homepage=(docker compose --env-file /etc/homecompute/homepage.env -f "$release/deploy/homepage/compose.yaml")
open_webui=(docker compose --env-file /etc/homecompute/open-webui.env -f "$release/deploy/open-webui/compose.yaml")
model_manager=(env "MODEL_MANAGER_RELEASE=$revision" docker compose --env-file /etc/homecompute/model-manager.env -f "$release/deploy/model-manager/compose.yaml")
piper=(docker compose --env-file /etc/homecompute/piper-tts.env -f "$release/deploy/piper-tts/compose.yaml")
stt=(docker compose --env-file /etc/homecompute/wyoming-stt.env -f "$release/deploy/wyoming-stt/compose.yaml")
"${gateway[@]}" config --quiet
"${automation[@]}" config --quiet
"${homepage[@]}" config --quiet
open_webui_ready=false
if [[ -s /run/secrets/open-webui/litellm-api-key && -s /run/secrets/open-webui/webui-secret-key ]]; then
  open_webui_ready=true
  "${open_webui[@]}" config --quiet
fi
model_manager_ready=false
if [[ -s /run/secrets/model-manager/username &&
      -s /run/secrets/model-manager/password &&
      -s /run/secrets/model-manager/spark-ssh-key &&
      -s /etc/homecompute/model-manager-known-hosts &&
      $(grep -cx 'MODEL_MANAGER_SPARK_FORCED_COMMAND_READY=1' /etc/homecompute/model-manager.env || true) == 1 ]]; then
  model_manager_ready=true
  "${model_manager[@]}" config --quiet
fi
"${piper[@]}" config --quiet
"${stt[@]}" config --quiet
stt_model="$("${stt[@]}" config --format json | jq -er '.services.stt.environment.WYO_WHISPER_MODEL')"
"${gateway[@]}" pull
"${automation[@]}" pull n8n
"${homepage[@]}" pull
"${piper[@]}" pull piper
"${piper[@]}" build moss
"${stt[@]}" pull stt
"${automation[@]}" build --no-cache aula-mcp
"${automation[@]}" build --no-cache tilbudstrolden-mcp
if [[ "$open_webui_ready" == true ]]; then
  "${open_webui[@]}" pull
fi
if [[ "$model_manager_ready" == true ]]; then
  "${model_manager[@]}" build --no-cache model-manager
fi
"${gateway[@]}" up -d --wait --wait-timeout 180
"${automation[@]}" up -d --wait --wait-timeout 180
"${homepage[@]}" up -d --wait --wait-timeout 180
if [[ "$open_webui_ready" == true ]]; then
  "${open_webui[@]}" up -d --wait --wait-timeout 180
else
  docker rm --force homecompute-open-webui-open-webui-1 >/dev/null 2>&1 || true
  printf 'Open WebUI credentials are not provisioned; leaving Open WebUI stopped.\n'
fi
if [[ "$model_manager_ready" == true ]]; then
  "${model_manager[@]}" up -d --wait --wait-timeout 180
else
  docker rm --force homecompute-model-manager-model-manager-1 >/dev/null 2>&1 || true
  printf 'Model manager secrets, pinned Spark host key, or forced-command readiness gate are missing; leaving model manager stopped.\n'
fi
if [[ -s /srv/state/piper-tts/models/da_DK-talesyntese-medium.onnx ]]; then
  "${piper[@]}" up -d --wait --wait-timeout 600
else
  printf 'Piper voice is not prepared; leaving Danish TTS stopped.\n'
fi
if [[ -f /srv/state/wyoming-stt/models/.homecompute-ready-model ]] &&
  [[ $(</srv/state/wyoming-stt/models/.homecompute-ready-model) == "$stt_model" ]]; then
  "${stt[@]}" up -d --wait --wait-timeout 300 stt
else
  printf 'Faster Whisper model is not prepared; leaving Danish STT stopped.\n'
fi
if [[ -L /srv/homecompute/current ]]; then
  previous=$(readlink /srv/homecompute/current)
  if [[ "$previous" != "$release" ]]; then ln -sfn "$previous" /srv/homecompute/previous; fi
fi
ln -sfn "$release" /srv/homecompute/current
printf '%s\n' "$revision" > /var/lib/homecompute/deployed-revision
printf 'Healthy deployment: %s\n' "$revision"
printf 'Books importer is managed separately; this deployment does not reconcile it.\n'
