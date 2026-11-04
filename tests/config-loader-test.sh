#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/../scripts/lib/config.sh"

test_root="$(mktemp -d "${TMPDIR:-/tmp}/homecompute-config-test.XXXXXX")"
chmod 0700 "$test_root"
trap 'rm -rf -- "$test_root"' EXIT
owner_uid="$(id -u)"

fail() {
  printf '[config-loader-test] FAIL: %s\n' "$*" >&2
  exit 1
}

write_config() {
  local contents="$1"
  printf '%s\n' "$contents" >"$test_root/test.env"
  chmod 0600 "$test_root/test.env"
}

marker="$test_root/command-was-executed"
write_config "SAFE_VALUE=\$(touch $marker)"
load_trusted_env_file "$test_root/test.env" "$owner_uid" test-config SAFE_VALUE || fail 'literal value was rejected'
[[ "$SAFE_VALUE" == "\$(touch $marker)" ]] || fail 'literal shell syntax was changed'
[[ ! -e "$marker" ]] || fail 'configuration executed shell syntax'

write_config 'SECOND_VALUE=present'
load_trusted_env_file "$test_root/test.env" "$owner_uid" test-config SAFE_VALUE SECOND_VALUE || fail 'second load was rejected'
[[ -z "${SAFE_VALUE+x}" ]] || fail 'removed key retained a stale value'
[[ "$SECOND_VALUE" == 'present' ]] || fail 'new key was not loaded'

write_config $'SAFE_VALUE=first\nSAFE_VALUE=second'
SAFE_VALUE=unchanged
if load_trusted_env_file "$test_root/test.env" "$owner_uid" test-config SAFE_VALUE 2>/dev/null; then
  fail 'duplicate key was accepted'
fi
[[ "$SAFE_VALUE" == 'unchanged' ]] || fail 'failed parse partially changed the environment'

write_config 'UNKNOWN_KEY=value'
if load_trusted_env_file "$test_root/test.env" "$owner_uid" test-config SAFE_VALUE 2>/dev/null; then
  fail 'unknown key was accepted'
fi

write_config 'SAFE_VALUE=value'
chmod 0666 "$test_root/test.env"
if load_trusted_env_file "$test_root/test.env" "$owner_uid" test-config SAFE_VALUE 2>/dev/null; then
  fail 'writable configuration was accepted'
fi

text_lock="$test_root/text-model.lock"
if command -v flock >/dev/null 2>&1; then
  acquire_text_model_runtime_lock "$text_lock" "$owner_uid" || fail 'safe text-model lock was not acquired'
  [[ "${TEXT_MODEL_RUNTIME_LOCK_FD:-}" =~ ^[0-9]+$ ]] || fail 'lock descriptor was not retained by the caller'
  python3 - "$text_lock" <<'PY' || fail 'a second process entered the same text-model lifecycle lock'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    raise SystemExit(1)
finally:
    os.close(fd)
PY
  ln -s "$text_lock" "$test_root/unsafe-lock"
  if acquire_text_model_runtime_lock "$test_root/unsafe-lock" "$owner_uid" 2>/dev/null; then
    fail 'symlink lock path was accepted'
  fi
  unsafe_parent="$test_root/world-writable-lock-dir"
  mkdir "$unsafe_parent"
  chmod 1777 "$unsafe_parent"
  if acquire_text_model_runtime_lock "$unsafe_parent/lock" "$owner_uid" 2>/dev/null; then
    fail 'world-writable lock directory was accepted'
  fi
  pending_marker="$test_root/pending.json"
  : >"$pending_marker"
  if acquire_text_model_runtime_lock "$test_root/pending-lock" "$owner_uid" false "$pending_marker" 2>/dev/null; then
    fail 'another model owner ignored a pending recovery marker'
  fi
  acquire_text_model_runtime_lock "$test_root/recovery-lock" "$owner_uid" true "$pending_marker" ||
    fail 'the owner recovery action could not acquire the shared lifecycle lock'
  race_bin="$test_root/race-bin"
  mkdir "$race_bin"
  real_flock="$(command -v flock)"
  cat >"$race_bin/flock" <<'SH'
#!/bin/sh
: >"$PENDING_MARKER"
exec "$REAL_FLOCK" "$@"
SH
  chmod 0700 "$race_bin/flock"
  race_pending="$test_root/race-pending.json"
  race_lock="$test_root/race-lock"
  if PATH="$race_bin:$PATH" REAL_FLOCK="$real_flock" PENDING_MARKER="$race_pending" \
      acquire_text_model_runtime_lock "$race_lock" "$owner_uid" false "$race_pending" 2>/dev/null; then
    fail 'shell model owner ignored a pending marker created at lock acquisition'
  fi
  python3 - "$race_lock" <<'PY' || fail 'shell lock was not released after pending-marker recheck'
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(1)
finally:
    os.close(fd)
PY
else
  printf '[config-loader-test] SKIP flock lock test; util-linux flock is unavailable on this host\n'
fi

printf '[config-loader-test] PASS\n'
