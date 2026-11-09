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
)

usage() {
  cat <<'EOF'
Usage: setup-openbot-guest.sh install|login|status

install  Install the pinned service into the agents VM. The current checkout
         must be a clean commit equal to origin/master and Home Core's deployed
         revision. This does not sign in to OpenBot or configure a provider.
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
  local revision stage release
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  stage="/tmp/homecompute-openbot-$revision"
  release="$REMOTE_BASE/releases/$revision"
  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" <<'REMOTE_PREP'
set -Eeuo pipefail
base="$1"; stage="$2"
command -v docker >/dev/null
docker compose version >/dev/null
docker info >/dev/null
test ! -L "$base" || { echo 'OpenBot data root must not be a symlink' >&2; exit 1; }
install -d -m 0700 "$base" "$base/releases" "$stage"
for path in "$base/current" "$base/compose.yaml" "$base/seccomp.json"; do
  if test -e "$path" || test -L "$path"; then
    echo 'Unmanaged OpenBot path already exists; preserve and inspect it' >&2
    exit 1
  fi
done
if docker inspect homecompute-openbot >/dev/null 2>&1; then
  echo 'Container name already exists; preserve and inspect it' >&2
  exit 1
fi
REMOTE_PREP

  scp -q -J home-core -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" "${ASSETS[@]}" "$GUEST:$stage/"

  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" <<'REMOTE_INSTALL'
set -Eeuo pipefail
base="$1"; stage="$2"; release="$3"
install -d -o hermes-operator -g hermes-operator -m 0700 "$release"
for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health openbot-network.json; do
  if test -e "$release/$file" || test -L "$release/$file"; then
    cmp -s "$stage/$file" "$release/$file" || { echo 'Published release assets differ; refusing overwrite' >&2; exit 1; }
  else
    sudo -n install -o hermes-operator -g hermes-operator -m 0600 "$stage/$file" "$release/$file"
  fi
done
sudo -n chmod 0644 "$release/compose.yaml" "$release/seccomp.json" "$release/homecompute-openbot.service" "$release/homecompute-openbot-firewall.service" "$release/openbot-network.json"
sudo -n chmod 0755 "$release/network-policy.py" "$release/homecompute-openbot-health"
sudo -n install -d -o root -g root -m 0755 /etc/homecompute-openbot /usr/local/libexec
sudo -n install -o root -g root -m 0644 "$release/openbot-network.json" /etc/homecompute-openbot/network.json
sudo -n install -o root -g root -m 0755 "$release/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy
sudo -n install -o root -g root -m 0755 "$release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health
for file in homecompute-openbot.service homecompute-openbot-firewall.service; do
  target="/etc/systemd/system/$file"
  if test -e "$target" || test -L "$target"; then
    cmp -s "$release/$file" "$target" || { echo 'Existing systemd unit differs from source; preserve and inspect it' >&2; exit 1; }
  else
    sudo -n install -o root -g root -m 0644 "$release/$file" "$target"
  fi
done
if test ! -e "$base/data" && test ! -L "$base/data"; then
  install -d -o hermes-operator -g hermes-operator -m 0700 "$base/data"
fi
test ! -L "$base/data"
test "$(stat -c %a "$base/data")" = 700
test "$(stat -c %u "$base/data")" = 1000
if test -e "$base/current" || test -L "$base/current"; then
  test -L "$base/current" && test "$(readlink -f "$base/current")" = "$(readlink -f "$release")" || {
    echo 'An OpenBot release is already active; use its documented upgrade lifecycle' >&2
    exit 1
  }
else
  ln -s "$release" "$base/current"
fi
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
