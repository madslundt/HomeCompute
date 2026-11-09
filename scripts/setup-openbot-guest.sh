#!/usr/bin/env bash
# Install the source-pinned OpenBot service into the isolated agents VM.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly GUEST="hermes-operator@10.77.20.2"
readonly KEY_FILE="${OPENBOT_GUEST_SSH_KEY:-$HOME/.ssh/id_ed25519_ai-services-01}"
readonly REMOTE_BASE="/home/hermes-operator/.local/share/homecompute-openbot"
readonly ASSETS=(
  "$REPO_ROOT/deploy/openbot/compose.yaml"
  "$REPO_ROOT/deploy/openbot/seccomp.json"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot.service"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot-firewall.service"
  "$REPO_ROOT/deploy/openbot/network-policy.py"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot-health"
  "$REPO_ROOT/config/openbot-network.json"
  "$REPO_ROOT/config/openbot-install.json"
)

usage() {
  cat <<'EOF'
Usage: setup-openbot-guest.sh install|login|status

install  Install or reconcile the pinned service in the agents VM. The current
         checkout must be a clean commit equal to origin/master and Home Core's
         deployed revision. Managed upgrades preserve /data and refuse drift.
         This does not sign in to OpenBot or configure a provider.
status   Read guest systemd and container status without showing account data.
login    Complete the official email-code server sign-in in this terminal.
EOF
}

ssh_guest() {
  ssh -J home-core -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" "$GUEST" "$@"
}

require_published_release() {
  [[ -f "$KEY_FILE" && ! -L "$KEY_FILE" ]] || { echo "Missing dedicated guest SSH identity: $KEY_FILE" >&2; return 1; }
  local revision published deployed porcelain
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  published="$(git -C "$REPO_ROOT" rev-parse origin/master)"
  porcelain="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
  [[ "$revision" =~ ^[0-9a-f]{40}$ && "$revision" == "$published" && -z "$porcelain" ]] || {
    echo 'Use a clean checkout of the exact published origin/master revision.' >&2; return 1;
  }
  deployed="$(ssh home-core 'sudo -n cat /var/lib/homecompute/deployed-revision')"
  [[ "$deployed" == "$revision" ]] || { echo 'Home Core must be deployed from this exact published revision first.' >&2; return 1; }
}

status() {
  ssh_guest 'sudo -n systemctl is-active homecompute-openbot-firewall.service homecompute-openbot.service || true; sudo -n docker inspect --format "{{.State.Status}} {{.Config.Image}} ports={{json .HostConfig.PortBindings}}" homecompute-openbot 2>/dev/null || true; sudo -n docker exec homecompute-openbot openbot version 2>/dev/null || true'
}

login() {
  require_published_release
  ssh -tt -J home-core -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" \
    "$GUEST" 'sudo -n docker exec -it homecompute-openbot openbot login --name home-core'
}

install_openbot() {
  require_published_release
  local revision stage release compose_package compose_version compose_cli_version compose_sha256 compose_architecture
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  compose_package="$(jq -er '.docker_compose.apt_package' "$REPO_ROOT/config/openbot-install.json")"
  compose_version="$(jq -er '.docker_compose.apt_version' "$REPO_ROOT/config/openbot-install.json")"
  compose_cli_version="$(jq -er '.docker_compose.cli_version' "$REPO_ROOT/config/openbot-install.json")"
  compose_sha256="$(jq -er '.docker_compose.deb_sha256' "$REPO_ROOT/config/openbot-install.json")"
  compose_architecture="$(jq -er '.architecture' "$REPO_ROOT/config/openbot-install.json")"
  stage="/tmp/homecompute-openbot-$revision"
  release="$REMOTE_BASE/releases/$revision"
  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" "$compose_package" \
    "$compose_version" "$compose_cli_version" "$compose_sha256" "$compose_architecture" <<'REMOTE_PREP'
set -Eeuo pipefail
base="$1"; stage="$2"; compose_package="$4"; compose_version="$5"
compose_cli_version="$6"; compose_sha256="$7"; compose_architecture="$8"
command -v docker >/dev/null
docker info >/dev/null
test ! -L "$base" || { echo 'OpenBot data root must not be a symlink' >&2; exit 1; }
current="$base/current"
if test -e "$current" || test -L "$current"; then
  test -L "$current" || { echo 'Existing OpenBot current path is not a symlink' >&2; exit 1; }
  previous_release="$(readlink -f "$current")"
  previous_revision="${previous_release##*/}"
  [[ "$previous_release" == "$base/releases/$previous_revision" && "$previous_revision" =~ ^[0-9a-f]{40}$ ]] || {
    echo 'Existing OpenBot release is outside its managed version directory' >&2
    exit 1
  }
  for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health openbot-network.json openbot-install.json; do
    test -f "$previous_release/$file" && test ! -L "$previous_release/$file" || {
      echo 'Existing OpenBot release is incomplete; preserve and inspect it' >&2; exit 1;
    }
  done
  cmp -s "$previous_release/homecompute-openbot.service" /etc/systemd/system/homecompute-openbot.service || {
    echo 'Existing OpenBot systemd unit differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/homecompute-openbot-firewall.service" /etc/systemd/system/homecompute-openbot-firewall.service || {
    echo 'Existing OpenBot firewall unit differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/openbot-network.json" /etc/homecompute-openbot/network.json || {
    echo 'Existing OpenBot network configuration differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy || {
    echo 'Existing OpenBot network helper differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health || {
    echo 'Existing OpenBot health helper differs from its managed release' >&2; exit 1;
  }
  if docker inspect homecompute-openbot >/dev/null 2>&1; then
    project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' homecompute-openbot)"
    service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' homecompute-openbot)"
    [[ "$project" == homecompute-openbot && "$service" == openbot ]] || {
      echo 'Existing container name is not owned by the OpenBot Compose project' >&2; exit 1;
    }
  fi
  test ! -L "$base/data" && test "$(stat -c %a "$base/data")" = 700 && test "$(stat -c %u "$base/data")" = 1000 || {
    echo 'Existing OpenBot data directory has unexpected ownership or permissions' >&2; exit 1;
  }
else
  for path in "$base/compose.yaml" "$base/seccomp.json" \
      /etc/homecompute-openbot/network.json \
      /etc/systemd/system/homecompute-openbot.service \
      /etc/systemd/system/homecompute-openbot-firewall.service \
      /usr/local/libexec/homecompute-openbot-network-policy \
      /usr/local/libexec/homecompute-openbot-health; do
    if test -e "$path" || test -L "$path"; then
      echo 'Unmanaged OpenBot path already exists; preserve and inspect it' >&2
      exit 1
    fi
  done
  if docker inspect homecompute-openbot >/dev/null 2>&1; then
    echo 'Container name already exists; preserve and inspect it' >&2
    exit 1
  fi
fi
for path in "$base/compose.yaml" "$base/seccomp.json"; do
  if test -e "$path" || test -L "$path"; then
    echo 'Unmanaged OpenBot path already exists; preserve and inspect it' >&2
    exit 1
  fi
done
command -v apt-get >/dev/null
test "$(dpkg --print-architecture)" = "$compose_architecture"
installed_compose_version="$(dpkg-query -W -f='${Version}' "$compose_package" 2>/dev/null || true)"
if test "$installed_compose_version" != "$compose_version"; then
  sudo -n apt-get update -qq
  package_dir="$(mktemp -d /tmp/homecompute-openbot-compose.XXXXXX)"
  trap 'rm -rf "$package_dir"' EXIT
  (cd "$package_dir" && apt-get download "$compose_package=$compose_version")
  package="$package_dir/${compose_package}_${compose_version}_${compose_architecture}.deb"
  test -f "$package"
  printf '%s  %s\n' "$compose_sha256" "$package" | sha256sum -c -
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "$package"
fi
test "$(dpkg-query -W -f='${Version}' "$compose_package")" = "$compose_version"
reported_compose_version="$(docker compose version --short | sed 's/^v//')"
case "$reported_compose_version" in
  "$compose_cli_version"|"$compose_cli_version"+*) ;;
  *) echo 'Installed Compose CLI version differs from the pinned source version' >&2; exit 1 ;;
esac
install -d -m 0700 "$base" "$base/releases" "$stage"
REMOTE_PREP

  scp -q -J home-core -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" "${ASSETS[@]}" "$GUEST:$stage/"

  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" <<'REMOTE_INSTALL'
set -Eeuo pipefail
base="$1"; stage="$2"; release="$3"
sudo -n systemctl stop homecompute-openbot.service >/dev/null 2>&1 || true
install -d -o hermes-operator -g hermes-operator -m 0700 "$release"
for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health openbot-network.json openbot-install.json; do
  if test -e "$release/$file" || test -L "$release/$file"; then
    cmp -s "$stage/$file" "$release/$file" || { echo 'Published release assets differ; refusing overwrite' >&2; exit 1; }
  else
    sudo -n install -o hermes-operator -g hermes-operator -m 0600 "$stage/$file" "$release/$file"
  fi
done
sudo -n chmod 0644 "$release/compose.yaml" "$release/seccomp.json" "$release/homecompute-openbot.service" "$release/homecompute-openbot-firewall.service" "$release/openbot-network.json" "$release/openbot-install.json"
sudo -n chmod 0755 "$release/network-policy.py" "$release/homecompute-openbot-health"
sudo -n install -d -o root -g root -m 0755 /etc/homecompute-openbot /usr/local/libexec
sudo -n install -o root -g root -m 0644 "$release/openbot-network.json" /etc/homecompute-openbot/network.json
sudo -n install -o root -g root -m 0755 "$release/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy
sudo -n install -o root -g root -m 0755 "$release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health
for file in homecompute-openbot.service homecompute-openbot-firewall.service; do
  sudo -n install -o root -g root -m 0644 "$release/$file" "/etc/systemd/system/$file"
done
if test ! -e "$base/data" && test ! -L "$base/data"; then
  install -d -o hermes-operator -g hermes-operator -m 0700 "$base/data"
fi
test ! -L "$base/data"
test "$(stat -c %a "$base/data")" = 700
test "$(stat -c %u "$base/data")" = 1000
ln -sfnT "$release" "$base/current"
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now homecompute-openbot-firewall.service
sudo -n systemctl enable --now homecompute-openbot.service
sudo -n systemctl --no-pager --quiet is-active homecompute-openbot.service
sudo -n /usr/local/libexec/homecompute-openbot-health
rm -rf "$stage"
echo 'OpenBot installed and digest-pinned. Account sign-in and model-provider setup remain pending.'
REMOTE_INSTALL
}

main() {
  [[ $# == 1 ]] || { usage >&2; return 2; }
  case "$1" in
    install) install_openbot ;;
    login) login ;;
    status) status ;;
    -h|--help|help) usage ;;
    *) usage >&2; return 2 ;;
  esac
}

main "$@"
