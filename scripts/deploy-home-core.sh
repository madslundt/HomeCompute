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
[[ ! -e /var/lib/homecompute/homepage-maintenance/active-image.json ]] || {
  printf 'Reconcile the human-approved Homepage image override before full deployment.\n' >&2; exit 1;
}
[[ ! -e /var/lib/homecompute/homepage-maintenance/active-config.json ]] || {
  printf 'Reconcile the Homepage configuration override before full deployment.\n' >&2; exit 1;
}
# Prepared image overrides preserve the selected digest until source pins are
# reconciled. A full deployment must not silently replace those running images.
if [[ -f /var/lib/homecompute-infrastructure-maintenance/plans.sqlite ]]; then
  python3 - /var/lib/homecompute-infrastructure-maintenance/plans.sqlite <<'PY'
import json, sqlite3, sys
with sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True) as db:
    for (raw,) in db.execute("SELECT plan FROM plans WHERE status IN ('succeeded','executing','uncertain')"):
        plan = json.loads(raw)
        if any(arg.startswith('/var/lib/homecompute-infrastructure-maintenance/prepared/') and arg.endswith('.image.json')
               for command in plan['steps'] for arg in command):
            raise SystemExit('Reconcile infrastructure image overrides before full deployment.')
        if any(command[:2] == ['/usr/local/libexec/homecompute-mcp-maintenance', 'apply']
               for command in plan['steps']):
            raise SystemExit('Reconcile MCP image updates and source pins before full deployment.')
PY
fi
# Automatic daemon replacements keep exact deployment state outside Compose.
if [[ -d /var/lib/homecompute-docker-maintenance ]] && find /var/lib/homecompute-docker-maintenance -maxdepth 1 -name '*.active' -print -quit | read -r _active_docker_update; then
  printf 'Reconcile automatic Docker updates and source pins before full deployment.\n' >&2; exit 1;
fi
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
  # GitHub may refuse a direct fetch of an advertised commit SHA. Fetch the
  # published release branch first, then retain SHA fetch for historical pins.
  git -C "$release" fetch origin master
  if ! git -C "$release" cat-file -e "$revision^{commit}" 2>/dev/null; then
    git -C "$release" fetch origin "$revision"
  fi
fi
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]] || {
  printf 'Release checkout is dirty; preserving it: %s\n' "$release" >&2; exit 1;
}
git -C "$release" checkout --detach "$revision"
[[ -z $(git -C "$release" status --porcelain --untracked-files=all) ]]
(cd /var/lib/homecompute && nixos-rebuild build --flake "$release#home-core")
if ! nixos-rebuild switch --flake "$release#home-core"; then
  # A scheduled application-state backup may defer with TEMPFAIL (75) while
  # protected HTTP writers are active. NixOS has still switched successfully
  # in that case, but switch-to-configuration reports the deferred unit as a
  # failed start and returns nonzero. Continue only if the active generation is
  # exactly the build from this release, that one backup is in its safe
  # auto-retry state with no process running, and systemd reports no failed
  # units. Keep its retry enabled; never reset or bypass the backup guard.
  expected_system=$(readlink -f /var/lib/homecompute/result)
  active_system=$(readlink -f /run/current-system)
  backup_active=$(systemctl show homecompute-nas-recovery-backup.service -p ActiveState --value)
  backup_substate=$(systemctl show homecompute-nas-recovery-backup.service -p SubState --value)
  backup_status=$(systemctl show homecompute-nas-recovery-backup.service -p ExecMainStatus --value)
  backup_pid=$(systemctl show homecompute-nas-recovery-backup.service -p MainPID --value)
  backup_timer_active=$(systemctl is-active homecompute-nas-recovery-backup.timer || true)
  failed_units=$(systemctl --failed --plain --no-legend --no-pager | awk 'NF {print $1}')
  health_status=$(systemctl show homecompute-platform-health.service -p ExecMainStatus --value)
  health_only_backup_failure=false
  if [[ "$failed_units" == homecompute-platform-health.service && "$health_status" == 1 ]] &&
     python3 "$release/scripts/platform-health-backup-deferral.py" \
       /var/lib/homecompute-health/homecompute-health.prom; then
    health_only_backup_failure=true
  fi
  if [[ -n "$expected_system" && "$active_system" == "$expected_system" &&
        "$backup_active" == activating && "$backup_substate" == auto-restart &&
        "$backup_status" == 75 && "$backup_pid" == 0 &&
        "$backup_timer_active" == active &&
        ( -z "$failed_units" || "$health_only_backup_failure" == true ) ]]; then
    if [[ "$health_only_backup_failure" == true ]]; then
      printf 'NixOS generation is active. NAS backup readiness is the sole failed health signal; its scheduled retry remains enabled. Continuing without a fresh backup.\n' >&2
    else
      printf 'NixOS generation is active. NAS recovery backup deferred on active writers; its scheduled retry remains enabled.\n' >&2
    fi
  else
    printf 'NixOS switch failed outside the exact safe NAS backup deferral state; refusing to continue.\n' >&2
    exit 1
  fi
fi
# The temporary live probe override is superseded by this release's Nix unit.
rm -f -- /etc/systemd/system.control/homecompute-platform-health.service.d/90-public-gateway.conf \
  /etc/systemd/system.control/homecompute-compute-ssh-tunnel.service.d/90-spark-stt.conf \
  /etc/systemd/system.control/homecompute-compute-ssh-tunnel-firewall.service.d/90-spark-stt.conf
rm -f -- /usr/local/lib/systemd/system/homecompute-gateway-certificate.service \
  /usr/local/lib/systemd/system/homecompute-gateway-certificate.timer \
  /usr/local/lib/systemd/system/timers.target.wants/homecompute-gateway-certificate.timer \
  /usr/local/lib/systemd/system/homecompute-token-usage.service \
  /usr/local/lib/systemd/system/homecompute-token-usage.timer \
  /usr/local/lib/systemd/system/timers.target.wants/homecompute-token-usage.timer
rm -f -- /etc/systemd/system.control/homecompute-token-usage.service.d/90-cache-pricing.conf
systemctl stop homecompute-token-usage.timer homecompute-token-usage.service >/dev/null 2>&1 || true
rm -f -- /usr/local/bin/homecompute-token-usage
systemctl daemon-reload
python3 "$release/scripts/setup-podwave-auth.py"
# Caddy's private storage keeps its public root certificate root-only. LiteLLM
# runs as UID 101, so publish a read-only copy at a stable, world-readable path.
install -D -m 0644 \
  /srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt \
  /etc/homecompute/control-plane/home-core-root.crt
install -d -o 101 -g 101 -m 0700 /srv/state/control-plane/chatgpt-auth
docker rm --force homecompute-model-manager-model-manager-1 >/dev/null 2>&1 || true
docker network rm homecompute-model-manager_ssh >/dev/null 2>&1 || true
gateway=(docker compose --env-file /etc/homecompute/control-plane.env -f "$release/deploy/control-plane/compose.yaml")
speech_gateway=(docker compose --profile speech-proxies --env-file /etc/homecompute/control-plane.env -f "$release/deploy/control-plane/compose.yaml")
[[ -s /run/secrets/automation/inbox-webhook-env ]] || {
  printf 'Missing SOPS-managed Inbox webhook env file: /run/secrets/automation/inbox-webhook-env\n' >&2
  exit 1
}
automation=(docker compose --env-file /etc/homecompute/automation.env --env-file /run/secrets/automation/inbox-webhook-env -f "$release/deploy/automation/compose.yaml" -f "$release/deploy/automation/production.yaml")
ai_automation_manager=(docker compose --env-file /etc/homecompute/automation.env --env-file /etc/homecompute/control-plane.env -f "$release/deploy/ai-automation-manager/compose.yaml" -f "$release/deploy/ai-automation-manager/production.yaml")
homepage=(docker compose --env-file /etc/homecompute/homepage.env -f "$release/deploy/homepage/compose.yaml")
open_webui=(docker compose --env-file /etc/homecompute/open-webui.env -f "$release/deploy/open-webui/compose.yaml")
piper=(docker compose --env-file /etc/homecompute/piper-tts.env -f "$release/deploy/piper-tts/compose.yaml")
stt=(docker compose --env-file /etc/homecompute/wyoming-stt.env -f "$release/deploy/wyoming-stt/compose.yaml")
immich=(docker compose --env-file /etc/homecompute/immich.env -f "$release/deploy/immich/compose.yaml")
podwave=(docker compose -f "$release/deploy/podwave/compose.yaml")
"${gateway[@]}" config --quiet
"${podwave[@]}" config --quiet
"${automation[@]}" config --quiet
ai_automation_manager_enabled="$(sed -n 's/^AI_AUTOMATION_MANAGER_ENABLED=//p' /etc/homecompute/automation.env | tail -n 1)"
if [[ "$ai_automation_manager_enabled" == 1 ]]; then
  export AI_AUTOMATION_MANAGER_RELEASE="$revision"
  for manager_secret in \
    /run/secrets/automation/inbox-webhook-env \
    /run/secrets/automation/ai-automation-workflow-token \
    /run/secrets/automation/ai-automation-username \
    /run/secrets/automation/ai-automation-password; do
    [[ -s "$manager_secret" ]] || {
      printf 'AI automation manager is enabled but its external secret is missing: %s\n' "$manager_secret" >&2
      exit 1
    }
  done
  "${ai_automation_manager[@]}" config --quiet
fi
"${homepage[@]}" config --quiet
open_webui_ready=false
if [[ -s /run/secrets/open-webui/litellm-api-key &&
      -s /run/secrets/open-webui/webui-secret-key &&
      -s /srv/state/open-webui/embedding-api-key ]]; then
  open_webui_ready=true
  "${open_webui[@]}" config --quiet
fi
"${piper[@]}" config --quiet
"${stt[@]}" config --quiet
install -d -m 0755 /srv/state/control-plane/caddy-config/model-controls
install -m 0644 "$release/deploy/comfyui-control/index.html" /srv/state/control-plane/caddy-config/model-controls/index.html
install -d -m 0755 /srv/state/control-plane/caddy-config/video-api
install -m 0644 "$release/deploy/control-plane/video-api.Caddyfile" /srv/state/control-plane/caddy-config/video-api/Caddyfile
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
"${podwave[@]}" build --pull podwave
edge_network=homecompute-control-plane_edge
speech_proxies_to_restore=()
if docker network inspect "$edge_network" >/dev/null 2>&1; then
  edge_bridge_name="$(docker network inspect --format '{{ index .Options "com.docker.network.bridge.name" }}' "$edge_network")"
  if [[ "$edge_bridge_name" != br-hc-ctrl ]]; then
    printf 'Recreating %s to apply its dedicated host bridge name; the control plane will be briefly unavailable.\n' "$edge_network"
    for proxy in plapre-wyoming-proxy hviske-wyoming-proxy; do
      container="homecompute-control-plane-${proxy}-1"
      if [[ $(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true) == true ]]; then
        speech_proxies_to_restore+=("$proxy")
      fi
    done
    "${speech_gateway[@]}" down
  fi
fi
# Bootstrap authenticated internal services before provisioning the keyless edge.
"${gateway[@]}" up -d --wait --wait-timeout 180 postgres
# Existing databases must be guarded before the updated proxy accepts traffic.
docker exec -i homecompute-control-plane-postgres-1 \
  psql -X -v ON_ERROR_STOP=1 -U postgres -d litellm < "$release/deploy/control-plane/token-usage.sql"
"${gateway[@]}" up -d --wait --wait-timeout 180 postgres litellm
# On first installation, LiteLLM creates its tables during the step above.
docker exec -i homecompute-control-plane-postgres-1 \
  psql -X -v ON_ERROR_STOP=1 -U postgres -d litellm < "$release/deploy/control-plane/token-usage.sql"
# LiteLLM defaults MCP access to open when no key or team declares a policy.
# Apply an explicit deny to existing unscoped keys before starting Caddy with
# the MCP hostname; preserve any already explicit MCP grants.
python3 "$release/scripts/setup-litellm-mcp-default-deny.py"
python3 "$release/scripts/setup-network-inference.py"
python3 "$release/scripts/renew-gateway-certificate.py" --no-restart
# Release Homepage's former LAN port 80 before the gateway claims it.
"${homepage[@]}" up -d --wait --wait-timeout 180
"${gateway[@]}" up -d --wait --wait-timeout 180
"${podwave[@]}" up -d --wait --wait-timeout 180
python3 "$release/scripts/setup-comfyui-access.py"
python3 "$release/scripts/provision-opencode-litellm-key.py" --apply
systemctl restart homecompute-opencode.service
if [[ -d /var/lib/homecompute-image-chat/config-backups ]]; then
  python3 "$release/scripts/setup-open-webui-images.py" --install-shim-only
fi
if [[ ${#speech_proxies_to_restore[@]} -gt 0 ]]; then
  "${speech_gateway[@]}" up -d --wait --wait-timeout 180 "${speech_proxies_to_restore[@]}"
fi
"${automation[@]}" up -d --wait --wait-timeout 180
if [[ "$ai_automation_manager_enabled" == 1 ]]; then
  "${ai_automation_manager[@]}" build --no-cache
  "${ai_automation_manager[@]}" up -d --wait --wait-timeout 180
fi
if [[ "$open_webui_ready" == true ]]; then
  "${open_webui[@]}" up -d --wait --wait-timeout 180
else
  docker rm --force homecompute-open-webui-open-webui-1 >/dev/null 2>&1 || true
  printf 'Open WebUI credentials are not provisioned; leaving Open WebUI stopped.\n'
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
if [[ -f /etc/homecompute/immich.env ]]; then
  immich_library_path="$(sed -n 's/^IMMICH_LIBRARY_PATH=//p' /etc/homecompute/immich.env)"
  immich_library_mount_source="$(sed -n 's/^IMMICH_LIBRARY_MOUNT_SOURCE=//p' /etc/homecompute/immich.env)"
  if [[ -n $immich_library_mount_source ]]; then
    [[ -d $immich_library_path ]] || {
      printf 'Immich library path is missing: %s\n' "$immich_library_path" >&2
      exit 1
    }
    # Access triggers NixOS's systemd automount. Never start Immich against an
    # empty local mountpoint if the NAS is offline or the wrong share is mounted.
    timeout 20s stat "$immich_library_path" >/dev/null || {
      printf 'Immich NAS library could not be mounted: %s\n' "$immich_library_path" >&2
      exit 1
    }
    mounted_source="$(findmnt --noheadings --output SOURCE --types cifs --submounts --target "$immich_library_path")"
    mounted_type="$(findmnt --noheadings --output FSTYPE --types cifs --submounts --target "$immich_library_path")"
    [[ $mounted_source == "$immich_library_mount_source" && $mounted_type == cifs ]] || {
      printf 'Immich library mount mismatch at %s (expected %s, found %s %s).\n' \
        "$immich_library_path" "$immich_library_mount_source" "$mounted_source" "$mounted_type" >&2
      exit 1
    }
  fi
  immich_password_file=/run/secrets/immich/database-password
  [[ -s $immich_password_file ]] || {
    printf 'Immich is configured but its SOPS database password is missing; deployment stopped before Compose.\n' >&2
    exit 1
  }
  DB_PASSWORD="$(<"$immich_password_file")"
  [[ $DB_PASSWORD =~ ^[A-Za-z0-9]+$ ]] || {
    printf 'Immich database password must be non-empty and alphanumeric.\n' >&2
    exit 1
  }
  export DB_PASSWORD
  "${immich[@]}" config --quiet
  "${immich[@]}" pull
  "${immich[@]}" up -d --wait --wait-timeout 1200
else
  printf 'Immich is not enabled in NixOS; leaving the photo service unchanged.\n'
fi
if [[ -L /srv/homecompute/current ]]; then
  previous=$(readlink /srv/homecompute/current)
  if [[ "$previous" != "$release" ]]; then ln -sfn "$previous" /srv/homecompute/previous; fi
fi
ln -sfn "$release" /srv/homecompute/current
printf '%s\n' "$revision" > /var/lib/homecompute/deployed-revision
printf 'Healthy deployment: %s\n' "$revision"
printf 'Books importer is managed separately; this deployment does not reconcile it.\n'
