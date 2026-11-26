#!/usr/bin/env bash
# Install the two certificate operations from the published Git source.
set -euo pipefail
umask 077

readonly BASE=/opt/homecompute/certificate-maintenance
readonly STATE=/var/lib/homecompute/certificate-maintenance
readonly ATTACHED=/etc/systemd/system.attached
readonly KEY_COMMENT=homecompute-openbot-tls-monitor
readonly MONITOR=homecompute-openbot-tls-monitor
readonly BACKUP=homecompute-caddy-ca-backup
readonly MONITOR_FILES=(scripts/certificate-maintenance.py
  deploy/certificate-maintenance/homecompute-openbot-tls-monitor.service
  deploy/certificate-maintenance/homecompute-openbot-tls-monitor.timer)
readonly BACKUP_FILES=(scripts/certificate-maintenance.py scripts/nas-recovery-backup.py
  deploy/certificate-maintenance/homecompute-caddy-ca-backup.service
  deploy/certificate-maintenance/homecompute-caddy-ca-backup.timer)

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

managed_link() {
  local target="$1" link="$2" legacy="${3:-}" previous next
  if [[ -L "$link" ]]; then
    previous="$(readlink "$link")"
    [[ "$previous" != "$target" ]] || return 0
    [[ "$previous" == "$legacy" ]] || { echo "Unexpected link: $link" >&2; return 1; }
    next="$(mktemp -d "$(dirname -- "$link")/.certificate-maintenance.XXXXXX")"
    ln -s -- "$target" "$next/link"
    mv -Tf -- "$next/link" "$link"
    rmdir -- "$next"
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
  for file in "$@"; do
    [[ ! -L "$release/$file" ]] || return 1
    if [[ -e "$release/$file" ]]; then
      cmp -s -- "$source_dir/$file" "$release/$file"
    else
      install -D -m 0444 -o root -g root -- "$source_dir/$file" "$release/$file"
    fi
  done
}

select_release() {
  local revision="$1" release="$BASE/releases/$1" pointer="$BASE/$2" next
  [[ "$revision" =~ ^[a-f0-9]{40}$ && "$EUID" == 0 && -d "$release" && ! -L "$release" ]] || return 1
  if [[ -L "$pointer" ]]; then
    [[ "$(readlink "$pointer")" =~ ^"$BASE"/releases/[a-f0-9]{40}$ ]] || return 1
  else
    [[ ! -e "$pointer" ]] || return 1
  fi
  next="$(mktemp -d "$BASE/.current.XXXXXX")"
  ln -s -- "$release" "$next/link"
  mv -Tf -- "$next/link" "$pointer"
  rmdir -- "$next"
}

prepare_monitor() {
  [[ "$(hostname -s)" == home-core && "$EUID" == 0 ]] || return 1
  local revision="$1" host_key="$2" pin="$STATE/probe_known_hosts" expected
  [[ "$host_key" =~ ^[A-Za-z0-9+/]{68}$ && ! -L "$STATE" &&
     ! -L "$STATE/probe-key" && ! -L "$pin" ]] || return 1
  install -d -m 0700 -o root -g root -- "$STATE"
  expected="10.77.20.2 ssh-ed25519 $host_key"
  if [[ -e "$pin" ]]; then
    if [[ ! -f "$pin" || "$(stat -c '%u:%a:%h' "$pin")" != 0:600:1 ]] ||
       ! cmp -s "$pin" <(printf '%s\n' "$expected"); then
      echo 'Existing OpenBot monitor host pin differs. Verify the guest key before rotating the pin.' >&2
      return 1
    fi
  else
    ( set -o noclobber; printf '%s\n' "$expected" >"$pin" )
  fi
  if [[ ! -e "$STATE/probe-key" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C "$KEY_COMMENT" -f "$STATE/probe-key"
  fi
  [[ "$(stat -c '%u:%a:%h' "$STATE/probe-key")" == 0:600:1 ]] || return 1
  install_release "$revision" "${MONITOR_FILES[@]}"
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
  select_release "$revision" current
}

activate_timer() {
  [[ "$(hostname -s)" == home-core && "$EUID" == 0 ]] || return 1
  local name="$1" pointer="$2" unit fragment
  local unit_directory="$BASE/$pointer/deploy/certificate-maintenance" legacy=
  if [[ "$name" == "$MONITOR" ]]; then
    legacy="$BASE/current/deploy/certificate-maintenance"
  fi
  mkdir -p -- "$ATTACHED/timers.target.wants"
  systemd-analyze verify "$unit_directory/$name.timer" "$unit_directory/$name.service"
  for unit in "$name.service" "$name.timer"; do
    managed_link "$unit_directory/$unit" "$ATTACHED/$unit" "${legacy:+$legacy/$unit}"
  done
  managed_link "$ATTACHED/$name.timer" "$ATTACHED/timers.target.wants/$name.timer"
  systemctl daemon-reload
  for unit in "$name.service" "$name.timer"; do
    fragment="$(systemctl show -p FragmentPath --value "$unit")"
    [[ "$(readlink -f "$fragment")" == "$(readlink -f "$ATTACHED/$unit")" ]] || return 1
  done
  systemctl start "$name.timer"
}

deploy() {
  local mode="$1" revision published host_key public
  revision="$(git -C "$source_dir" rev-parse HEAD)"
  published="$(git -C "$source_dir" ls-remote --exit-code origin refs/heads/master | cut -f1)"
  [[ "$revision" =~ ^[a-f0-9]{40}$ && "$revision" == "$published" &&
     -z "$(git -C "$source_dir" status --porcelain --untracked-files=all)" ]] ||
    { echo 'Use a clean checkout of the published origin/master revision.' >&2; return 1; }
  local -a core=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 home-core)
  local -a guest=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5
    -o HostKeyAlgorithms=ssh-ed25519 -o UpdateHostKeys=no
    -J home-core -o IdentitiesOnly=yes -i "${OPENBOT_GUEST_SSH_KEY:-$HOME/.ssh/id_ed25519_ai-services-01}"
    hermes-operator@10.77.20.2)
  local stage='set -euo pipefail; stage=$(mktemp -d); trap "rm -rf -- $stage" EXIT; tar -xf - -C "$stage"; bash "$stage/scripts/setup-certificate-maintenance.sh"'
  host_key="$("${guest[@]}" "awk 'NR == 1 && \$1 == \"ssh-ed25519\" {print \$2}' /etc/ssh/ssh_host_ed25519_key.pub")"
  [[ "$host_key" =~ ^[A-Za-z0-9+/]{68}$ ]] || return 1
  public="$(git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh "${MONITOR_FILES[@]}" |
    "${core[@]}" "sudo -n bash -c '$stage prepare-monitor $revision $host_key'")"
  read -r key_type key_data _ <<< "$public"
  [[ "$key_type" == ssh-ed25519 && "$key_data" =~ ^[A-Za-z0-9+/]{68}$ ]] || return 1
  git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh scripts/certificate-maintenance.py |
    "${guest[@]}" "sudo -n bash -c '$stage install-guest $revision \"ssh-ed25519 $key_data\"'"
  git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh |
    "${core[@]}" "sudo -n bash -c '$stage activate-monitor $revision'"
  if [[ "$mode" == deploy ]]; then
    git -C "$source_dir" archive "$revision" scripts/setup-certificate-maintenance.sh "${BACKUP_FILES[@]}" |
      "${core[@]}" "sudo -n bash -c '$stage activate-all $revision'"
  fi
}

case "${1:-}" in
  deploy|deploy-monitor) [[ $# == 1 ]]; deploy "$1" ;;
  prepare-monitor) [[ $# == 3 ]]; prepare_monitor "$2" "$3" ;;
  install-guest) [[ $# == 3 ]]; install_guest "$2" "$3" ;;
  activate-monitor)
    [[ $# == 2 && "$(hostname -s)" == home-core && "$EUID" == 0 &&
       "$2" =~ ^[a-f0-9]{40}$ ]]
    python3 -I "$BASE/releases/$2/scripts/certificate-maintenance.py" check-probe
    select_release "$2" monitor-current
    activate_timer "$MONITOR" monitor-current
    ;;
  activate-all)
    [[ $# == 2 && "$(hostname -s)" == home-core && "$EUID" == 0 ]]
    python3 -I "$BASE/monitor-current/scripts/certificate-maintenance.py" check-recipient
    install_release "$2" "${BACKUP_FILES[@]}"
    select_release "$2" current
    activate_timer "$BACKUP" current
    systemctl start --no-block "$MONITOR.service" "$BACKUP.service"
    ;;
  *) echo "Usage: $0 deploy|deploy-monitor" >&2; exit 2 ;;
esac
