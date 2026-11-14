#!/usr/bin/env bash
# Application-only PodWave release. Invoke from the workstation CLI over SSH.
set -Eeuo pipefail
revision="${1:-}"
helper_revision="${2:-$revision}"
[[ ( $# == 1 || $# == 2 ) && "$revision" =~ ^[0-9a-f]{40}$ && "$helper_revision" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Usage: sudo bash deploy-podwave.sh FULL_COMMIT_SHA\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Run as root on home-core.\n' >&2; exit 1;
}
for executable in git docker flock python3 jq install stat tar date sha256sum; do command -v "$executable" >/dev/null; done
install -d -m 0755 /srv/homecompute/releases
install -d -m 0700 /var/lib/homecompute
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }

ensure_release() {
  local selected_revision="$1"
  local selected_release="/srv/homecompute/releases/$selected_revision"
  local status
  if [[ ! -d "$selected_release" ]]; then
    git clone https://github.com/madslundt/HomeCompute.git "$selected_release" >&2 || return 1
  fi
  if ! git -C "$selected_release" cat-file -e "$selected_revision^{commit}" 2>/dev/null; then
    git -C "$selected_release" fetch origin master >&2 || return 1
    if ! git -C "$selected_release" cat-file -e "$selected_revision^{commit}" 2>/dev/null; then
      git -C "$selected_release" fetch origin "$selected_revision" >&2 || return 1
    fi
  fi
  status="$(git -C "$selected_release" status --porcelain --untracked-files=all)" || return 1
  [[ -z "$status" ]] || {
    printf 'Release checkout is dirty; preserving it: %s\n' "$selected_release" >&2; return 1;
  }
  git -C "$selected_release" checkout --detach "$selected_revision" >&2 || return 1
  status="$(git -C "$selected_release" status --porcelain --untracked-files=all)" || return 1
  [[ -z "$status" ]] || {
    printf 'Release checkout is dirty after selection; preserving it: %s\n' "$selected_release" >&2; return 1;
  }
  printf '%s' "$selected_release"
}
release="$(ensure_release "$revision")"
helper_release="$release"
if [[ "$helper_revision" != "$revision" ]]; then
  helper_release="$(ensure_release "$helper_revision")"
  git -C "$helper_release" merge-base --is-ancestor "$helper_revision" origin/master || {
    printf 'PodWave deployment helper must come from the published origin/master release.\n' >&2; exit 1;
  }
fi

for required_path in \
  /etc/homecompute/control-plane.env \
  /srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt \
  /srv/state/podwave; do
  [[ -e "$required_path" ]] || { printf 'Missing PodWave prerequisite: %s\n' "$required_path" >&2; exit 1; }
done
[[ -d /srv/state/podwave && $(stat -c '%u' /srv/state/podwave) == 1000 ]] || {
  printf 'PodWave state must remain owned by UID 1000.\n' >&2; exit 1;
}
docker network inspect homecompute-podwave >/dev/null || {
  printf 'The PodWave bridge is missing; run the full home-core deployment first.\n' >&2; exit 1;
}
caddy_container="$(docker ps --filter label=com.docker.compose.project=homecompute-control-plane \
  --filter label=com.docker.compose.service=caddy --format '{{.ID}}')"
[[ -n "$caddy_container" ]] || {
  printf 'The control-plane Caddy service is not running; run the full home-core deployment first.\n' >&2; exit 1;
}

# Refresh the stable public copy of the private Caddy root CA used by PodWave.
install -D -m 0644 \
  /srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt \
  /etc/homecompute/control-plane/home-core-root.crt
python3 "$release/scripts/setup-podwave-auth.py"

podwave=(docker compose -f "$release/deploy/podwave/compose.yaml")
source "$helper_release/scripts/lib/podwave-backup.sh"
"${podwave[@]}" config --quiet
image_ref="$("${podwave[@]}" config --format json | jq -er '.services.podwave.image')"
[[ "$image_ref" =~ ^homecompute/podwave:[0-9a-f]{40}$ ]] || {
  printf 'PodWave image is not pinned to a full upstream commit: %s\n' "$image_ref" >&2; exit 1;
}
podwave_container="$("${podwave[@]}" ps -q podwave)"
[[ -n "$podwave_container" ]] || {
  printf 'An existing PodWave container is required; use the full home-core deployment for first setup.\n' >&2; exit 1;
}
[[ $(docker inspect --format '{{.State.Health.Status}}' "$podwave_container") == healthy ]] || {
  printf 'The existing PodWave container is not healthy; preserving it for recovery.\n' >&2; exit 1;
}
running_image="$(docker inspect --format '{{.Config.Image}}' "$podwave_container")"
previous=""
if [[ -L /srv/homecompute/podwave-current ]]; then
  previous="$(readlink /srv/homecompute/podwave-current)"
elif [[ -L /srv/homecompute/current ]]; then
  previous_release="$(readlink /srv/homecompute/current)"
  previous_compose="$previous_release/deploy/podwave/compose.yaml"
  if [[ -f "$previous_compose" ]]; then
    previous_image="$(docker compose -f "$previous_compose" config --format json | jq -er '.services.podwave.image')"
    if [[ "$previous_image" == "$running_image" ]]; then
      previous="$previous_release"
    fi
  fi
fi
"${podwave[@]}" build --pull podwave
backup_path="$(backup_podwave_state "$revision" "${podwave[@]}")"
[[ -n "$backup_path" ]] || {
  printf 'PodWave state backup was not created; use the full deployment for first setup.\n' >&2; exit 1;
}

"${podwave[@]}" up -d --wait --wait-timeout 180
podwave_container="$("${podwave[@]}" ps -q podwave)"
[[ -n "$podwave_container" ]] || { printf 'PodWave container was not created.\n' >&2; exit 1; }
[[ $(docker inspect --format '{{.Config.Image}}' "$podwave_container") == "$image_ref" ]] || {
  printf 'Running PodWave image does not match its pinned upstream commit.\n' >&2; exit 1;
}
[[ $(docker inspect --format '{{.State.Health.Status}}' "$podwave_container") == healthy ]] || {
  printf 'PodWave did not become healthy.\n' >&2; exit 1;
}
python3 "$release/scripts/setup-podwave-server-settings.py" --container "$podwave_container"

# Verify the same authenticated HTTPS route used by clients without putting
# the generated password in argv or logging it.
python3 - <<'PY'
import base64
import ssl
import urllib.request
from pathlib import Path

password = Path('/srv/state/podwave-auth/password.txt').read_text().strip()
request = urllib.request.Request('https://podwave.home.arpa/api/health')
request.add_header('Authorization', 'Basic ' + base64.b64encode(f'podwave:{password}'.encode()).decode())
context = ssl.create_default_context(cafile='/etc/homecompute/control-plane/home-core-root.crt')
with urllib.request.urlopen(request, context=context, timeout=15) as response:
    if response.status != 200:
        raise SystemExit(f'PodWave authenticated URL check returned HTTP {response.status}')
    response.read()
print('Authenticated PodWave URL check: HTTP 200')
PY

if [[ -n "$previous" && "$previous" != "$release" ]]; then
  previous_link="/srv/homecompute/.podwave-previous-$revision"
  ln -sfn "$previous" "$previous_link"
  mv -Tf "$previous_link" /srv/homecompute/podwave-previous
fi
current_link="/srv/homecompute/.podwave-current-$revision"
ln -sfn "$release" "$current_link"
mv -Tf "$current_link" /srv/homecompute/podwave-current
marker_tmp="/var/lib/homecompute/.podwave-deployed-revision-$revision"
printf '%s\n' "$revision" > "$marker_tmp"
chmod 0644 "$marker_tmp"
mv -f "$marker_tmp" /var/lib/homecompute/podwave-deployed-revision
printf 'Healthy PodWave deployment: %s (upstream image %s)\n' "$revision" "${image_ref#*:}"
printf 'PodWave state backup: %s\n' "$backup_path"
printf 'The full home-core release marker was not changed.\n'
