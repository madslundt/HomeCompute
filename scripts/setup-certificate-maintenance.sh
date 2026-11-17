#!/usr/bin/env bash
# Install the two certificate operations from the published Git source.
set -euo pipefail
umask 077

readonly BASE=/opt/homecompute/certificate-maintenance
readonly STATE=/var/lib/homecompute/certificate-maintenance
readonly ATTACHED=/etc/systemd/system.attached
readonly KEY_COMMENT=homecompute-openbot-tls-monitor
readonly CORE_UNITS=(homecompute-openbot-tls-monitor homecompute-caddy-ca-backup)
readonly FILES=(scripts/certificate-maintenance.py scripts/nas-recovery-backup.py
  deploy/certificate-maintenance/homecompute-openbot-tls-monitor.service
  deploy/certificate-maintenance/homecompute-openbot-tls-monitor.timer
  deploy/certificate-maintenance/homecompute-caddy-ca-backup.service
  deploy/certificate-maintenance/homecompute-caddy-ca-backup.timer)

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

managed_link() {
  local target="$1" link="$2"
  if [[ -L "$link" ]]; then
    [[ "$(readlink "$link")" == "$target" ]] || { echo "Unexpected link: $link" >&2; return 1; }
  else
    [[ ! -e "$link" ]] || { echo "Unmanaged path: $link" >&2; return 1; }
    ln -s -- "$target" "$link"
  fi
}

install_release() {
  local revision="$1" release="$BASE/releases/$1" file
  shift
  [[ "$revision" =~ ^[a-f0-9]{40}$ && "$EUID" == 0 ]] || return 1
  # Only the two owning hosts install these assets. Versioned files stay immutable.
  for path in "$BASE" "$BASE/releases" "$release"; do
    [[ ! -L "$path" ]] || { echo "Unsafe release directory: $path" >&2; return 1; }
    install -d -m 0755 -o root -g root -- "$path"
  done
  if [[ -L "$BASE/current" ]]; then
    [[ "$(readlink "$BASE/current")" =~ ^"$BASE"/releases/[a-f0-9]{40}$ ]] || return 1
  else
    [[ ! -e "$BASE/current" ]] || return 1
  fi
  for file in "$@"; do
    [[ ! -L "$release/$file" ]] || return 1
    if [[ -e "$release/$file" ]]; then
      cmp -s -- "$source_dir/$file" "$release/$file"
    else
      install -D -m 0444 -o root -g root -- "$source_dir/$file" "$release/$file"
    fi
  done
  local next
  next="$(mktemp -d "$BASE/.current.XXXXXX")"
  ln -s -- "$release" "$next/link"
  mv -Tf -- "$next/link" "$BASE/current"
  rmdir -- "$next"
}

prepare_core() {
  [[ "$(hostname -s)" == home-core && "$EUID" == 0 ]] || return 1
  install_release "$1" "${FILES[@]}"
  [[ ! -L "$STATE" && ! -L "$STATE/probe-key" ]] || return 1
  install -d -m 0700 -o root -g root -- "$STATE"
  if [[ ! -e "$STATE/probe-key" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C "$KEY_COMMENT" -f "$STATE/probe-key"
  fi
  [[ "$(stat -c '%u:%a:%h' "$STATE/probe-key")" == 0:600:1 ]] || return 1
  ssh-keygen -y -f "$STATE/probe-key"
}

install_guest() {
  local revision="$1" public="$2"
  [[ "$(hostname -s)" == agents && "$EUID" == 0 ]] || return 1
  [[ "$public" =~ ^ssh-ed25519\ [A-Za-z0-9+/]{68}$ ]] || return 1
  install_release "$revision" scripts/certificate-maintenance.py
  /usr/bin/python3 -I - "$public" <<'PY'
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile

user = pwd.getpwnam("hermes-operator")
path = Path(user.pw_dir) / ".ssh/authorized_keys"
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != user.pw_uid or info.st_mode & 0o077:
    raise ValueError("unexpected agents SSH authorized_keys ownership or permissions")
original = path.read_text()
marker = "homecompute-openbot-tls-monitor"
line = ('restrict,from="10.77.20.1",command="/usr/bin/sudo -n /usr/bin/python3 '
        '-I /opt/homecompute/certificate-maintenance/current/scripts/certificate-maintenance.py probe" '
        + sys.argv[1] + " " + marker)
matches = [entry for entry in original.splitlines() if entry.endswith(" " + marker)]
if matches:
    if matches != [line]:
        raise ValueError("unexpected existing OpenBot probe authorization")
    raise SystemExit(0)
with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
    candidate = Path(stream.name)
    stream.write(original.rstrip("\n") + "\n" + line + "\n")
    os.fchown(stream.fileno(), user.pw_uid, user.pw_gid)
try:
    if path.read_text() != original:
        raise ValueError("SSH authorized_keys changed during installation")
    os.replace(candidate, path)
finally:
    candidate.unlink(missing_ok=True)
PY
}

activate_core() {
  [[ "$(hostname -s)" == home-core && "$EUID" == 0 ]] || return 1
  [[ "$(readlink "$BASE/current")" == "$BASE/releases/$1" ]] || return 1
  python3 -I "$BASE/current/scripts/certificate-maintenance.py" check-recipient
  local name unit fragment
  local unit_directory="$BASE/current/deploy/certificate-maintenance"
  mkdir -p -- "$ATTACHED/timers.target.wants"
  for name in "${CORE_UNITS[@]}"; do
    for unit in "$name.service" "$name.timer"; do
      managed_link "$unit_directory/$unit" "$ATTACHED/$unit"
    done
    managed_link "$ATTACHED/$name.timer" "$ATTACHED/timers.target.wants/$name.timer"
  done
  systemd-analyze verify "$ATTACHED/homecompute-openbot-tls-monitor.timer" \
    "$ATTACHED/homecompute-openbot-tls-monitor.service" "$ATTACHED/homecompute-caddy-ca-backup.timer" \
    "$ATTACHED/homecompute-caddy-ca-backup.service"
  systemctl daemon-reload
  for name in "${CORE_UNITS[@]}"; do
    fragment="$(systemctl show -p FragmentPath --value "$name.timer")"
    [[ "$(readlink -f "$fragment")" == "$(readlink -f "$ATTACHED/$name.timer")" ]] || return 1
    systemctl start "$name.timer"
  done
  systemctl start --no-block homecompute-openbot-tls-monitor.service homecompute-caddy-ca-backup.service
}

deploy() {
  local revision published public
  revision="$(git -C "$source_dir" rev-parse HEAD)"
  published="$(git -C "$source_dir" ls-remote --exit-code origin refs/heads/master | cut -f1)"
  [[ "$revision" =~ ^[a-f0-9]{40}$ && "$revision" == "$published" &&
     -z "$(git -C "$source_dir" status --porcelain --untracked-files=all)" ]] ||
    { echo 'Use a clean checkout of the published origin/master revision.' >&2; return 1; }
  local -a core=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 home-core)
  local -a guest=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5
    -J home-core -o IdentitiesOnly=yes -i "${OPENBOT_GUEST_SSH_KEY:-$HOME/.ssh/id_ed25519_ai-services-01}"
    hermes-operator@10.77.20.2)
  local stage='set -euo pipefail; stage=$(mktemp -d); trap "rm -rf -- $stage" EXIT; tar -xf - -C "$stage"; bash "$stage/scripts/setup-certificate-maintenance.sh"'
  public="$(git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh "${FILES[@]}" |
    "${core[@]}" "sudo -n bash -c '$stage prepare-core $revision'")"
  read -r key_type key_data _ <<< "$public"
  [[ "$key_type" == ssh-ed25519 && "$key_data" =~ ^[A-Za-z0-9+/]{68}$ ]] || return 1
  git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh scripts/certificate-maintenance.py |
    "${guest[@]}" "sudo -n bash -c '$stage install-guest $revision \"ssh-ed25519 $key_data\"'"
  git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh |
    "${core[@]}" "sudo -n bash -c '$stage activate-core $revision'"
}

case "${1:-}" in
  deploy) [[ $# == 1 ]]; deploy ;;
  prepare-core) [[ $# == 2 ]]; prepare_core "$2" ;;
  install-guest) [[ $# == 3 ]]; install_guest "$2" "$3" ;;
  activate-core) [[ $# == 2 ]]; activate_core "$2" ;;
  *) echo "Usage: $0 deploy" >&2; exit 2 ;;
esac
