#!/usr/bin/env bash
# Deploy only the private automation manager from a published HomeCompute release.
set -Eeuo pipefail
revision="${1:-}"
if [[ $# != 1 || ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Usage: sudo bash scripts/deploy-ai-automation-manager.sh FULL_COMMIT_SHA\n' >&2
  exit 2
fi
if [[ $EUID != 0 || $(hostname) != home-core ]]; then
  printf 'Run as root on home-core.\n' >&2
  exit 1
fi

exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || { printf 'Another deployment is running.\n' >&2; exit 1; }
config=/etc/homecompute/automation.env
state=/srv/state/automation/ai-automation-manager/automations.sqlite3
if [[ $(sed -n 's/^AI_AUTOMATION_MANAGER_ENABLED=//p' "$config" | tail -n 1) != 1 || ! -s "$state" ]]; then
  printf 'The existing automation manager must be enabled and have a database.\n' >&2
  exit 1
fi

release="/srv/homecompute/releases/$revision"
if [[ ! -d "$release" ]]; then
  git clone --single-branch --branch master https://github.com/madslundt/HomeCompute.git "$release"
fi
if [[ -n $(git -C "$release" status --porcelain --untracked-files=all) ]]; then
  printf 'Release checkout is dirty; preserving it: %s\n' "$release" >&2
  exit 1
fi
git -C "$release" fetch origin master
git -C "$release" merge-base --is-ancestor "$revision" origin/master
git -C "$release" checkout --detach "$revision"

export AI_AUTOMATION_MANAGER_RELEASE="$revision"
compose=(docker compose --env-file "$config" --env-file /etc/homecompute/control-plane.env
  -f "$release/deploy/ai-automation-manager/compose.yaml"
  -f "$release/deploy/ai-automation-manager/production.yaml")
"${compose[@]}" config --quiet
"${compose[@]}" build ai-automation-manager

backup_dir=/srv/homecompute/operations/ai-automation-manager
install -d -m 0700 "$backup_dir"
python3 - "$state" "$backup_dir" <<'PY'
import os
import sqlite3
import sys
import tempfile

source, destination = sys.argv[1:]
with tempfile.NamedTemporaryFile(prefix="pre-deploy-", suffix=".sqlite3", dir=destination, delete=False) as backup:
    path = backup.name
try:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as existing, sqlite3.connect(path) as snapshot:
        existing.backup(snapshot)
        if snapshot.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("SQLite backup failed its integrity check")
except BaseException:
    os.unlink(path)
    raise
print(f"Saved private automation database backup at {path}")
PY

"${compose[@]}" up -d --no-build --wait --wait-timeout 180 ai-automation-manager
"${compose[@]}" ps ai-automation-manager
