#!/usr/bin/env bash
# Install a pinned mobile companion plugin on the existing owner profile.
set -Eeuo pipefail
umask 077

case "${1:-}" in
  conduit)
    readonly repository=https://github.com/kaishi00/hermes-conduit-notifier.git
    readonly plugin_revision=695019b57e606b456fc0f6f60038cd1310a8d9a5
    readonly plugin=conduit_push
    ;;
  bighelp)
    readonly repository=https://github.com/promptclickrun/bighelp-plugin.git
    readonly plugin_revision=ea4b349b2550761d2929779e4881dfbfa9d9c568
    readonly plugin=loopdy
    ;;
  *) echo 'Select conduit or bighelp.' >&2; exit 2 ;;
esac
shift
readonly sandbox=hermes-personal-v2
readonly unit=homecompute-hermes-personal.service

release_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$release_root"
operation=${1:-plan}
case "$operation" in
  plan|apply|rollback) ;;
  *) echo 'Usage: setup-hermes-mobile-plugin.sh conduit|bighelp [plan|apply [--defer-backup]|rollback]' >&2; exit 2 ;;
esac
backup=required
if [[ ${2:-} == --defer-backup && "$operation" == apply && $# == 2 ]]; then
  backup=deferred
else
  [[ $# -le 1 ]]
fi

revision="$(git rev-parse HEAD)"
git merge-base --is-ancestor "$revision" origin/master
[[ -z "$(git status --porcelain --untracked-files=no)" ]]
[[ "$(id -un)" == hermes-operator ]]

export NEMOCLAW_GATEWAY_PORT=8080 OPENSHELL_GATEWAY=nemoclaw NEMOCLAW_NO_AUTO_AUDIT_LOGS=1
native() {
  nemohermes "$sandbox" exec -- "$@"
}
require_owner() {
  nemohermes "$sandbox" status --json |
    jq -e '.agent == "hermes" and .phase == "Ready" and (.hostMounts | not or length == 0)' >/dev/null || return
  native hermes --version | grep -qE '^Hermes Agent v0\.23\.0 .*upstream 2bd378ed'
}
plugin_state() {
  native python3 -c '
import json, sys
from pathlib import Path
import yaml

home = Path("/sandbox/.hermes")
name, revision = sys.argv[1:]
plugin = home / "plugins" / name
metadata_file = plugin.parent / ".install-metadata.json"
record = json.loads(metadata_file.read_text()).get(name) if metadata_file.is_file() else None
enabled = name in (yaml.safe_load((home / "config.yaml").read_text()).get("plugins") or {}).get("enabled", [])
if not (record or plugin.exists() or plugin.is_symlink() or enabled):
    print("absent")
elif plugin.is_dir() and not plugin.is_symlink() and record and record.get("pinned") is True and record.get("revision") == revision:
    print("enabled" if enabled else "installed")
else:
    sys.exit("Unknown mobile plugin installation. Review its provenance before modifying it.")
' "$plugin" "$plugin_revision"
}
require_readiness() {
  python3 - "$backup" <<'PYTHON'
import datetime
import json
import os
from pathlib import Path
import runpy
import stat
import sys

guard = runpy.run_path("scripts/connect-hermes-mcp.py")
if sys.argv[1] == "required":
    guard["gates"]()
else:
    path = guard["GATES"] / "agents-network.json"
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
        sys.exit("Private agents network readiness record required.")
    value = json.loads(path.read_bytes())
    observed = datetime.datetime.strptime(value["observed_at"], "%Y-%m-%dT%H:%M:%SZ")
    age = datetime.datetime.now(datetime.timezone.utc) - observed.replace(tzinfo=datetime.timezone.utc)
    if (value.get("schema_version") != 1 or value.get("gate") != "agents-network"
            or value.get("status") != "ready" or value.get("scope") != "hermes-household"
            or not value.get("evidence") or not datetime.timedelta(0) <= age <= datetime.timedelta(days=7)):
        sys.exit("Fresh agents network readiness is required.")
    print("Operator deferred off-host backup. Fresh network readiness verified.")
PYTHON
}
wait_for_owner() {
  if [[ "$plugin" == loopdy ]]; then
    # The Webapp runs in the gateway process. Leave the OpenShell container
    # running and let the managed supervisor restart the gateway gracefully.
    native hermes gateway restart || return
  else
    systemctl --user restart "$unit" || return
  fi
  require_owner
}
bighelp_telemetry_disabled() {
  native hermes bighelp telemetry status |
    jq -e '.enabled == false and .sources["hermes bighelp telemetry"] == "off"' >/dev/null
}
configure_plugin() {
  [[ "$plugin" == loopdy ]] || return 0
  native hermes bighelp telemetry off >/dev/null && bighelp_telemetry_disabled
}

require_owner
state="$(plugin_state)"
printf 'Owner sandbox %s, plugin %s, commit %s, state %s\n' "$sandbox" "$plugin" "$plugin_revision" "$state"
[[ "$operation" != plan ]] || exit 0
exec 9>"$HOME/.local/state/homecompute-hermes-mobile-plugin.lock"
flock -n 9
state="$(plugin_state)"

if [[ "$operation" == rollback ]]; then
  [[ "$state" == enabled ]] || exit 0
  native hermes plugins disable "$plugin"
  wait_for_owner
  [[ "$(plugin_state)" == installed ]]
  exit 0
fi
if [[ "$state" == enabled ]]; then
  if [[ "$plugin" == loopdy ]] && ! bighelp_telemetry_disabled; then
    require_readiness
    configure_plugin
  fi
  exit 0
fi
require_readiness
if native grep -qE '^HOMECOMPUTE_CHATGPT_API_KEY=sk-' /sandbox/.hermes/.env; then
  echo 'Managed Hermes rejects the raw owner Sol credential during restart. Reconcile the owner model binding before applying a mobile plugin.' >&2
  exit 1
else
  [[ $? == 1 ]]
fi

# Retain only this operation's private preimage. An off-host backup uses its
# separate lifecycle and this copy is not recovery evidence for that gate.
# shellcheck disable=SC2016
native bash -c '
set -eu
umask 077
root=/sandbox/.hermes/.homecompute-mobile-plugin
test ! -L "$root"
mkdir -m 700 -p "$root"
backup="$(mktemp -d "$root/pre-install.XXXXXX")"
install -m 600 /sandbox/.hermes/config.yaml "$backup/config.yaml"
metadata=/sandbox/.hermes/plugins/.install-metadata.json
if test -f "$metadata"; then
  install -m 600 "$metadata" "$backup/install-metadata.json"
fi
printf "Private mobile plugin rollback preimage saved in %s\n" "$backup"
'

if [[ "$state" == absent ]]; then
  # Fetch on the guest, and pass only the pinned Git objects into OpenShell.
  # The native installer reviews the plugin tree without opening sandbox egress.
  guest_stage="$(mktemp -d)"
  sandbox_stage=''
  cleanup() {
    if [[ "$sandbox_stage" =~ ^/sandbox/\.mobile-plugin-install\.[a-zA-Z0-9]+$ ]]; then
      native rm -rf -- "$sandbox_stage" || echo 'Mobile plugin sandbox staging cleanup needs attention' >&2
    fi
    rm -rf -- "$guest_stage"
  }
  trap cleanup EXIT
  git -C "$guest_stage" init -q --bare artifact.git
  git --git-dir="$guest_stage/artifact.git" fetch -q --depth=1 "$repository" "$plugin_revision"
  git --git-dir="$guest_stage/artifact.git" symbolic-ref HEAD refs/heads/pinned
  git --git-dir="$guest_stage/artifact.git" update-ref refs/heads/pinned "$plugin_revision" ''
  tar -C "$guest_stage" -czf "$guest_stage/plugin.tar.gz" artifact.git
  checksum="$(sha256sum "$guest_stage/plugin.tar.gz" | cut -d ' ' -f 1)"
  sandbox_stage="$(native mktemp -d /sandbox/.mobile-plugin-install.XXXXXX)"
  [[ "$sandbox_stage" =~ ^/sandbox/\.mobile-plugin-install\.[a-zA-Z0-9]+$ ]]
  nemohermes "$sandbox" upload "$guest_stage/plugin.tar.gz" "$sandbox_stage/plugin.tar.gz"
  # shellcheck disable=SC2016
  native bash -c '
    set -euo pipefail
    cd "$1"
    printf "%s  plugin.tar.gz\n" "$2" | sha256sum --check --strict
    tar --extract --gzip --no-same-owner --file plugin.tar.gz
    [[ "$(git --git-dir=artifact.git rev-parse HEAD)" == "$3" ]]
    git --git-dir=artifact.git fsck --strict --no-dangling
    hermes plugins install "file://$PWD/artifact.git" --ref "$3" --no-enable
  ' bash "$sandbox_stage" "$checksum" "$plugin_revision"
  [[ "$(plugin_state)" == installed ]]
fi

native hermes plugins enable "$plugin" --no-allow-tool-override
[[ "$(plugin_state)" == enabled ]]
if ! configure_plugin || ! wait_for_owner || [[ "$(plugin_state)" != enabled ]]; then
  echo 'Mobile plugin activation could not be verified. Attempting to disable it.' >&2
  native hermes plugins disable "$plugin" && wait_for_owner
  exit 1
fi
printf '%s is pinned and enabled on the Ready owner sandbox.\n' "$plugin"
