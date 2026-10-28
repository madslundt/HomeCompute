#!/usr/bin/env python3
"""Dry-run or apply only the finite specialist MCP grants; no automatic restart."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

from openclaw_integration_policy import candidate, ROLE_TOOLS
from openclaw_team_policy import finite, PROTECTED, TeamRefused

CONFIG = Path('/sandbox/.openclaw/openclaw.json')


def native(batch: list, config: Path, *, apply: bool, runner=subprocess.run):
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(config), NODE_OPTIONS='--max-old-space-size=384')
    result = runner(['openclaw', 'config', 'set', '--batch-json', json.dumps(batch)] + ([] if apply else ['--dry-run']),
                    capture_output=True, text=True, env=env, timeout=60)
    if result.returncode: raise ValueError('Native integration policy refused')


def setup(config: Path = CONFIG, *, apply=False, expected_config_sha256=None, runner=subprocess.run):
    if any(p.is_symlink() for p in (config, *config.parents)):
        raise ValueError('Native integration configuration refused')
    info = config.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
            or info.st_nlink != 1 or info.st_size > 1048576):
        raise ValueError('Owner-private native configuration required')
    raw = config.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    if apply and expected_config_sha256 != source_hash:
        raise ValueError('Native source changed since integration review')
    before = json.loads(raw)
    after = candidate(before, finite=finite, refused=TeamRefused, enable=True)
    if any(after.get(k) != before.get(k) for k in PROTECTED):
        raise ValueError('Protected native sections changed')
    batch = [{'path': k, 'value': after[k]} for k in ('agents', 'tools') if after[k] != before[k]]
    if batch: native(batch, config, apply=False, runner=runner)
    if apply and batch:
        if config.read_bytes() != raw: raise ValueError('Native source changed during validation')
        root = config.parent / 'integration-setup-backups'
        if root.is_symlink(): raise ValueError('Native integration backup refused')
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Private integration backup required')
        backup = root / str(uuid.uuid4()); backup.mkdir(mode=0o700)
        fd = os.open(backup / 'before.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream: stream.write(raw)
        native(batch, config, apply=True, runner=runner)
        if json.loads(config.read_bytes()) != after:
            raise ValueError('Native integration readback differs; retained backup requires review')
    return {'source_config_sha256': source_hash, 'schema_validated': True, 'changed': bool(batch),
            'applied': apply, 'restart_required': apply and bool(batch),
            'role_tools': {role: sorted(names) for role, names in ROLE_TOOLS.items()},
            'provider_readiness': 'Check integration_status separately; native grants do not establish provider access.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--expected-config-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(setup(args.config, apply=args.apply, expected_config_sha256=args.expected_config_sha256)))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        raise SystemExit('Integration tool setup refused; review native policy and the retained private backup.') from None
