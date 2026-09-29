#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly COMMAND="${1:-help}"
if (($# > 0)); then shift; fi
if (($# != 0)); then
  printf 'Usage: %s [help|validate|install]\n' "${0##*/}" >&2
  exit 2
fi

readonly ACCOUNT="sparkrun-manager"
readonly ACCOUNT_HOME="/var/lib/sparkrun-manager"
readonly SSH_DIR="$ACCOUNT_HOME/.ssh"
readonly KEY_SOURCE="/etc/gb10-ai/secrets/sparkrun-manager.pub"
readonly WRAPPER="/usr/local/sbin/homecompute-sparkrun-model-manager"
readonly SUDOERS="/etc/sudoers.d/homecompute-sparkrun-manager"
readonly ADAPTER="/srv/homecompute/current/scripts/sparkrun-model-manager.py"
readonly CATALOG="/srv/homecompute/current/config/model-catalog.json"
readonly SPARKRUN_CANDIDATES=("/usr/local/bin/sparkrun" "/root/.local/bin/sparkrun")

usage() {
  cat <<'USAGE'
Usage: setup-compute-sparkrun-manager.sh [help|validate|install]

Provision the forced-command SSH boundary for the HomeCompute catalog-backed
sparkrun model manager. Run on home-spark as root after sparkrun is securely
installed and configured for root with a cluster named home-spark.

The public key must already exist at:
  /etc/gb10-ai/secrets/sparkrun-manager.pub
It must be one root-owned, mode-0600 ssh-ed25519 public-key line. This helper
never handles a private key.

  validate  Check prerequisites and planned configuration without changes
  install   Install the fixed wrapper, locked account, forced key, and sudo rule
USAGE
}

die() { printf '[sparkrun-manager-setup] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[sparkrun-manager-setup] %s\n' "$*"; }
require_root() { [[ ${EUID} -eq 0 ]] || die 'run this command as root'; }
mode_of() { stat -c '%a' -- "$1"; }
owner_of() { stat -c '%u:%g' -- "$1"; }
links_of() { stat -c '%h' -- "$1"; }
no_write_by_others() { (( (8#$(mode_of "$1") & 8#022) == 0 )); }

check_secure_file() {
  local path="$1" expected_mode="$2"
  [[ -f "$path" && ! -L "$path" ]] || die "required regular non-symlink file is missing or unsafe: $path"
  [[ "$(owner_of "$path")" == 0:0 ]] || die "$path must be owned by root:root"
  [[ "$(mode_of "$path")" == "$expected_mode" ]] || die "$path must have mode $expected_mode"
  [[ "$(links_of "$path")" == 1 ]] || die "$path must not have additional hard links"
}

sparkrun_path() {
  local path
  for path in "${SPARKRUN_CANDIDATES[@]}"; do
    if [[ -f "$path" && -x "$path" && ! -L "$path" ]] &&
       [[ "$(owner_of "$path")" == 0:0 ]] && no_write_by_others "$path"; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  die 'sparkrun must already be installed as a protected root-owned executable at /usr/local/bin/sparkrun or /root/.local/bin/sparkrun'
}

check_release() {
  local current resolved
  [[ "$(hostname -s)" == home-spark ]] || die 'this helper is restricted to the home-spark host'
  [[ -L /srv/homecompute/current ]] || die '/srv/homecompute/current must be the deployed release symlink'
  [[ "$(owner_of /srv/homecompute/current)" == 0:0 ]] || die '/srv/homecompute/current symlink must be root-owned'
  current="$(readlink -e -- /srv/homecompute/current)" || die 'cannot resolve /srv/homecompute/current'
  resolved="$(realpath -e -- "$ADAPTER")" || die 'the current release is missing the sparkrun adapter'
  [[ "$current" == /srv/homecompute/releases/* && "$resolved" == "$current/scripts/sparkrun-model-manager.py" ]] ||
    die 'the adapter must come from the active immutable /srv/homecompute/releases/<revision> checkout'
  check_secure_file "$resolved" 644
  check_secure_file "$current/config/model-catalog.json" 644
  no_write_by_others "$current" || die 'the active release directory must not be group/world writable'
  no_write_by_others "$current/scripts" || die 'the active scripts directory must not be group/world writable'
  no_write_by_others "$current/config" || die 'the active config directory must not be group/world writable'
  [[ "$(owner_of "$current")" == 0:0 && "$(owner_of "$current/scripts")" == 0:0 && "$(owner_of "$current/config")" == 0:0 ]] ||
    die 'the active release, scripts, and config directories must be root-owned'
}

check_key_source() {
  check_secure_file "$KEY_SOURCE" 600
  local parent
  parent="$(dirname -- "$KEY_SOURCE")"
  [[ -d "$parent" && ! -L "$parent" && "$(owner_of "$parent")" == 0:* ]] ||
    die 'the public-key source directory must be a root-owned non-symlink directory'
  no_write_by_others "$parent" || die 'the public-key source directory must not be group/world writable'
  python3 - "$KEY_SOURCE" <<'PY'
import base64
import pathlib
import struct
import sys

path = pathlib.Path(sys.argv[1])
raw = path.read_bytes()
if raw.endswith(b"\n"):
    raw = raw[:-1]
if not raw or b"\n" in raw or b"\r" in raw or b"\0" in raw:
    raise SystemExit("public key source must contain exactly one text line")
try:
    fields = raw.decode("ascii").split()
except UnicodeDecodeError as error:
    raise SystemExit("public key line must be ASCII") from error
if len(fields) not in (2, 3) or fields[0] != "ssh-ed25519":
    raise SystemExit("expected one plain ssh-ed25519 public-key line without authorized_keys options")
try:
    blob = base64.b64decode(fields[1], validate=True)
except ValueError as error:
    raise SystemExit("public key has invalid base64") from error
if len(blob) < 4:
    raise SystemExit("public key blob is truncated")
algorithm_length = struct.unpack(">I", blob[:4])[0]
if algorithm_length != len(b"ssh-ed25519") or blob[4:4 + algorithm_length] != b"ssh-ed25519":
    raise SystemExit("public key type does not match its SSH wire-format blob")
offset = 4 + algorithm_length
if len(blob) < offset + 4:
    raise SystemExit("public key blob is truncated")
key_length = struct.unpack(">I", blob[offset:offset + 4])[0]
if key_length != 32 or len(blob) != offset + 4 + key_length:
    raise SystemExit("Ed25519 public key blob has an invalid length")
PY
}

check_sparkrun_config() {
  local binary
  binary="$(sparkrun_path)"
  "$binary" cluster show home-spark --json >/dev/null 2>&1 ||
    die 'root sparkrun configuration must already contain a working cluster named home-spark (check it with: sparkrun cluster show home-spark --json)'
}

check_layout() {
  local path
  [[ -x /usr/bin/python3 && -x /usr/bin/sudo && -x /usr/sbin/visudo ]] ||
    die 'python3, sudo, and visudo must already be installed at their standard paths'
  for path in /usr/local/sbin /etc/sudoers.d /var/lib; do
    [[ -d "$path" && ! -L "$path" && "$(owner_of "$path")" == 0:0 ]] ||
      die "managed parent directory must be a root-owned directory: $path"
    no_write_by_others "$path" || die "managed parent directory must not be group/world writable: $path"
  done
  check_release
  check_key_source
  check_sparkrun_config
  if id "$ACCOUNT" >/dev/null 2>&1; then
    local entry primary
    entry="$(getent passwd "$ACCOUNT")"
    [[ -n "$entry" ]] || die 'sparkrun-manager has an incomplete account database entry'
    [[ "$(printf '%s' "$entry" | cut -d: -f6)" == "$ACCOUNT_HOME" &&
       "$(printf '%s' "$entry" | cut -d: -f7)" == /bin/sh ]] ||
      die 'existing sparkrun-manager account has an unexpected home or shell; inspect it before provisioning'
    primary="$(id -gn "$ACCOUNT")"
    [[ "$primary" == "$ACCOUNT" ]] || die 'sparkrun-manager must have its dedicated primary group'
    [[ "$(id -G "$ACCOUNT")" == "$(id -g "$ACCOUNT")" ]] || die 'sparkrun-manager must not have supplementary groups'
  fi
  for path in "$ACCOUNT_HOME" "$SSH_DIR" "$WRAPPER" "$SUDOERS"; do
    [[ ! -L "$path" ]] || die "managed destination must not be a symlink: $path"
  done
  if [[ -e "$ACCOUNT_HOME" ]]; then
    [[ -d "$ACCOUNT_HOME" && "$(owner_of "$ACCOUNT_HOME")" == 0:0 ]] || die "$ACCOUNT_HOME must be a root-owned directory"
  fi
  if [[ -e "$SSH_DIR" ]]; then
    [[ -d "$SSH_DIR" && "$(owner_of "$SSH_DIR")" == 0:0 ]] || die "$SSH_DIR must be a root-owned directory"
  fi
  if [[ -e "$WRAPPER" ]]; then
    [[ -f "$WRAPPER" && "$(owner_of "$WRAPPER")" == 0:0 ]] || die "$WRAPPER must be a root-owned regular file"
  fi
  if [[ -e "$SUDOERS" ]]; then
    [[ -f "$SUDOERS" && "$(owner_of "$SUDOERS")" == 0:0 ]] || die "$SUDOERS must be a root-owned regular file"
  fi
}

write_atomic() {
  local destination="$1" mode="$2" temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  cat >"$temporary"
  chown root:root "$temporary"
  chmod "$mode" "$temporary"
  mv -fT -- "$temporary" "$destination"
}

install_account() {
  if ! getent group "$ACCOUNT" >/dev/null 2>&1; then
    groupadd --system "$ACCOUNT"
  fi
  if ! id "$ACCOUNT" >/dev/null 2>&1; then
    useradd --system --gid "$ACCOUNT" --home-dir "$ACCOUNT_HOME" --no-create-home --shell /bin/sh "$ACCOUNT"
  fi
  usermod --lock --shell /bin/sh --home "$ACCOUNT_HOME" --gid "$ACCOUNT" --groups '' "$ACCOUNT"
  install -d -o root -g root -m 0755 "$ACCOUNT_HOME"
  # sshd reads authorized_keys before the forced command runs, so the public
  # key path must be traversable/readable by the unprivileged auth process.
  # Keep both objects root-owned and non-writable by the manager account.
  install -d -o root -g root -m 0755 "$SSH_DIR"
}

install_wrapper() {
  write_atomic "$WRAPPER" 0755 <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
if (($# != 0)); then
  printf '{"ok":false,"error":{"code":"invalid_request","message":"this helper accepts JSON only on stdin and no command-line arguments"}}\n'
  exit 2
fi
exec /usr/bin/python3 /srv/homecompute/current/scripts/sparkrun-model-manager.py
WRAPPER
}

install_forced_key() {
  local public_key
  public_key="$(cat -- "$KEY_SOURCE")"
  write_atomic "$SSH_DIR/authorized_keys" 0644 <<EOF
restrict,command="/usr/bin/sudo -n $WRAPPER" $public_key
EOF
  chown root:root "$SSH_DIR"
  chmod 0755 "$SSH_DIR"
  chown root:root "$SSH_DIR/authorized_keys"
  chmod 0644 "$SSH_DIR/authorized_keys"
}

install_sudoers() {
  local temporary
  temporary="$(mktemp "${SUDOERS}.tmp.XXXXXX")"
  printf '%s ALL=(root) NOPASSWD: %s ""\n' "$ACCOUNT" "$WRAPPER" >"$temporary"
  chown root:root "$temporary"
  chmod 0440 "$temporary"
  /usr/sbin/visudo -cf "$temporary" >/dev/null || { rm -f -- "$temporary"; die 'generated sudoers rule did not pass visudo validation'; }
  mv -fT -- "$temporary" "$SUDOERS"
  chown root:root "$SUDOERS"
  chmod 0440 "$SUDOERS"
}

case "$COMMAND" in
  help|-h|--help) usage ;;
  validate)
    require_root
    check_layout
    log 'prerequisites and safe provisioning inputs are valid; no files were changed'
    ;;
  install)
    require_root
    check_layout
    install_account
    install_wrapper
    install_forced_key
    install_sudoers
    # Verify the final authorization file is precisely the one key and mode
    # installed above. The source parser has already excluded embedded lines.
    check_secure_file "$SSH_DIR/authorized_keys" 644
    [[ "$(owner_of "$SSH_DIR")" == 0:0 && "$(mode_of "$SSH_DIR")" == 755 ]] || die 'authorized_keys directory permissions are unsafe'
    [[ "$(owner_of "$WRAPPER")" == 0:0 && "$(mode_of "$WRAPPER")" == 755 ]] || die 'installed wrapper permissions are unsafe'
    [[ "$(owner_of "$SUDOERS")" == 0:0 && "$(mode_of "$SUDOERS")" == 440 ]] || die 'sudoers permissions are unsafe'
    /usr/sbin/visudo -cf "$SUDOERS" >/dev/null || die 'installed sudoers rule failed validation'
    log 'installed forced-command sparkrun manager account and root-owned adapter wrapper'
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
