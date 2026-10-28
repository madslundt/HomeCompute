#!/usr/bin/env python3
"""Review/apply finite request/status and registered-site tool grants for travel and shopper."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
# Operator-only native CLI; batch values come from the validated finite policy.
import subprocess  # nosec B404
import uuid

from openclaw_team_policy import access_candidate, PROTECTED, ACCESS_ROLES, ACCESS_TOOLS

CONFIG = Path('/sandbox/.openclaw/openclaw.json')


def native(batch: list, apply: bool):
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(CONFIG), NODE_OPTIONS='--max-old-space-size=384')
    args = ['openclaw', 'config', 'set', '--batch-json', json.dumps(batch)] + ([] if apply else ['--dry-run'])
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)  # nosec B603
    if result.returncode:
        raise ValueError('Native access policy operation refused')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-config-sha256')
    args = parser.parse_args()
    os.umask(0o077)
    if CONFIG.is_symlink() or not CONFIG.is_file():
        raise ValueError('Native configuration refused')
    raw = CONFIG.read_bytes()
    source_digest = hashlib.sha256(raw).hexdigest()
    if args.apply and args.expected_config_sha256 != source_digest:
        raise ValueError('Native source changed since review')
    before = json.loads(raw)
    after = access_candidate(before, enable_access_upgrade=True)
    if any(after.get(section) != before.get(section) for section in PROTECTED):
        raise ValueError('Protected configuration changed')
    batch = [{'path': key, 'value': after[key]} for key in ('agents', 'tools') if after[key] != before[key]]
    if batch:
        native(batch, False)
    if args.apply and batch:
        if CONFIG.read_bytes() != raw:
            raise ValueError('Native configuration changed during validation')
        backup_root = CONFIG.parent / 'access-setup-backups'
        if any(p.is_symlink() for p in (backup_root, *backup_root.parents)):
            raise ValueError('Native backup symlink refused')
        if backup_root.exists() and (not backup_root.is_dir() or backup_root.stat().st_uid != os.geteuid()
                                     or backup_root.stat().st_mode & 0o777 != 0o700):
            raise ValueError('Native backup directory refused')
        backup = backup_root / str(uuid.uuid4())
        backup.mkdir(mode=0o700, parents=True)
        descriptor = os.open(backup / 'before.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
        native(batch, True)
        actual = json.loads(CONFIG.read_bytes())
        if any(actual.get(key) != after[key] for key in ('agents', 'tools')):
            raise ValueError('Native policy readback differs')
        if any(actual.get(section) != before.get(section) for section in PROTECTED):
            raise ValueError('Protected configuration readback differs')
    print(json.dumps({'source_config_sha256': source_digest, 'schema_validated': True,
                      'applied': args.apply, 'changed': bool(batch), 'roles': sorted(ACCESS_ROLES),
                      'restart_required': args.apply and bool(batch),
                      'grant': sorted(ACCESS_TOOLS),
                      'account_execution_available': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Native access setup refused; private operator review required. No config values are displayed.')
