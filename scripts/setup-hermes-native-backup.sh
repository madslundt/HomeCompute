#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
destination="$HOME/.local/share/homecompute-hermes-backup"
ssh_dir="$HOME/.ssh"
authorized_keys="$ssh_dir/authorized_keys"
public_key_file="$REPO_ROOT/config/hermes-backup-authorized-key.pub"

[[ $(id -un) == hermes-operator ]] || { echo 'Install the Hermes backup helper as hermes-operator.' >&2; exit 1; }
[[ -f $SCRIPT_DIR/export-hermes-state.sh && -f $SCRIPT_DIR/hermes-native-home-snapshot.py && -f $public_key_file ]] || {
  echo 'Both tracked Hermes backup helpers must be present in this release.' >&2
  exit 1
}

install -d -m 0700 "$destination"
for source in export-hermes-state.sh hermes-native-home-snapshot.py; do
  target="$destination/$source"
  if [[ -e $target || -L $target ]]; then
    [[ -f $target && ! -L $target ]] || { echo "Unsafe existing helper: $target" >&2; exit 1; }
  fi
  mode=0600
  [[ $source == export-hermes-state.sh ]] && mode=0700
  install -m "$mode" -- "$SCRIPT_DIR/$source" "$target"
done

read -r key_type key_data key_comment <"$public_key_file"
[[ $key_type == ssh-ed25519 && $key_data =~ ^[A-Za-z0-9+/]+=*$ && $key_comment == homecompute-hermes-state-backup ]] || {
  echo 'The tracked Hermes backup public key is malformed.' >&2
  exit 1
}
if [[ -e $ssh_dir || -L $ssh_dir ]]; then
  [[ -d $ssh_dir && ! -L $ssh_dir && $(stat -c %u "$ssh_dir") == $(id -u) ]] || {
    echo 'Existing .ssh must be a real operator-owned directory.' >&2
    exit 1
  }
  chmod 0700 "$ssh_dir"
else
  install -d -m 0700 "$ssh_dir"
fi
if [[ -e $authorized_keys || -L $authorized_keys ]]; then
  [[ -f $authorized_keys && ! -L $authorized_keys && $(stat -c '%u:%h' "$authorized_keys") == "$(id -u):1" ]] || {
    echo 'Existing authorized_keys must be a regular operator-owned file.' >&2
    exit 1
  }
else
  : >"$authorized_keys"
  chmod 0600 "$authorized_keys"
fi
entry='from="10.77.20.1",command="/home/hermes-operator/.local/share/homecompute-hermes-backup/export-hermes-state.sh",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc '"$key_type $key_data $key_comment"
if ! grep -Fqx -- "$entry" "$authorized_keys"; then
  printf '%s\n' "$entry" >>"$authorized_keys"
  chmod 0600 "$authorized_keys"
fi

echo 'Hermes state export helper and restricted backup SSH key installed.'
