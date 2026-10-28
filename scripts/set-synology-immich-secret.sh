#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
secret_file="$repo_root/secrets/synology-immich.sops.yaml"

for executable in python3 sops; do
  command -v "$executable" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$executable" >&2
    exit 1
  }
done

[[ -t 0 && -r /dev/tty ]] || {
  printf 'Run this script in an interactive terminal so the password is not echoed.\n' >&2
  exit 1
}
[[ -f "$secret_file" ]] || {
  printf 'Encrypted SOPS file not found: %s\n' "$secret_file" >&2
  exit 1
}

IFS= read -r -s -p 'Paste the Synology immich-svc password: ' password </dev/tty
printf '\n' >/dev/tty
IFS= read -r -s -p 'Paste it again to confirm: ' confirmation </dev/tty
printf '\n' >/dev/tty

if [[ -z "$password" ]]; then
  unset password confirmation
  printf 'Password cannot be empty; SOPS was not changed.\n' >&2
  exit 1
fi
if [[ "$password" != "$confirmation" ]]; then
  unset password confirmation
  printf 'The entries did not match; SOPS was not changed.\n' >&2
  exit 1
fi

secret_value="$(printf '%s' "$password" | python3 -c 'import json, sys; print(json.dumps("username=immich-svc\npassword=" + sys.stdin.read()))')"
unset password confirmation

if ! printf '%s' "$secret_value" | sops set --value-stdin "$secret_file" '["immich"]["synology-smb-credentials"]' >/dev/null; then
  unset secret_value
  printf 'SOPS could not update the credential; the encrypted file was left unchanged.\n' >&2
  exit 1
fi
unset secret_value

printf 'Updated the encrypted Synology credential in %s\n' "${secret_file#"$repo_root"/}"
