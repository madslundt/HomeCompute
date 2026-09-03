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

printf '[config-loader-test] PASS\n'
