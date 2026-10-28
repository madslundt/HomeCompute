#!/usr/bin/env python3
"""Reconcile only integration instructions/skills, preserving the installed roster."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from openclaw_integration_policy import ROLE_TOOLS

START, END = '<!-- homecompute-integrations:start -->', '<!-- homecompute-integrations:end -->'
SKILLS = {'homecompute-operations', 'household-summary', 'travel-adventures'}


def digest(raw): return hashlib.sha256(raw).hexdigest()


def read(path, *, private=False):
    if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError('Symlink refused')
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022) or info.st_size > 1048576):
        raise ValueError('Owned bounded file required')
    return path.read_bytes()


def identity(raw, instruction):
    text = raw.decode()
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('Managed integration markers refused')
    block = START + '\n' + instruction.strip() + '\n' + END
    if START in text:
        first, rest = text.split(START, 1); _, last = rest.split(END, 1)
        return (first + block + last).encode()
    return raw + (b'\n\n' if raw else b'') + block.encode() + b'\n'


def plan(config, source):
    native = read(config, private=True); before = json.loads(native); root = config.parent
    instructions = json.loads(read(source / 'config/openclaw-integration-instructions.json'))
    if set(instructions) != set(ROLE_TOOLS) or any(not isinstance(v, str) or not 1 <= len(v) <= 4000 for v in instructions.values()):
        raise ValueError('Integration instructions refused')
    entries = before['agents']['entries']
    if not {'main', *ROLE_TOOLS} <= set(entries): raise ValueError('Managed roles required')
    ledger_path = root / 'team-skill-ownership.json'; ledger_raw = read(ledger_path, private=True)
    ledger = json.loads(ledger_raw)
    if (not isinstance(ledger, dict) or any(not isinstance(k, str)
            or not re.fullmatch(r'workspace(?:-[a-z0-9-]+)?/skills/[a-z][a-z0-9-]*/SKILL.md', k)
            or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for k, v in ledger.items())):
        raise ValueError('Managed skill ledger refused')
    files = []
    for role, entry in sorted(entries.items()):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', role): raise ValueError('Role refused')
        workspace = root / ('workspace' if role == 'main' else 'workspace-' + role)
        if entry['workspace'] != str(workspace) or not isinstance(entry['skills'], list):
            raise ValueError('Canonical role workspace required')
        if role in instructions:
            path = workspace / 'AGENTS.md'; raw = read(path)
            files.append((path, raw, identity(raw, instructions[role])))
        for name in SKILLS & set(entry['skills']):
            path = workspace / 'skills' / name / 'SKILL.md'; raw = read(path)
            content = read(source / 'deploy/openclaw/skills' / name / 'SKILL.md')
            relative = str(path.relative_to(root))
            if raw != content and ledger.get(relative) != digest(raw):
                raise ValueError('Unreviewed managed skill modification refused')
            files.append((path, raw, content)); ledger[relative] = digest(content)
    files.append((ledger_path, ledger_raw, (json.dumps(ledger, sort_keys=True, indent=2) + '\n').encode()))
    summary = [{'path': str(p.relative_to(root)), 'before': digest(a), 'after': digest(b)} for p, a, b in files]
    fence = digest(json.dumps({'config': digest(native), 'files': summary}, sort_keys=True).encode())
    return native, files, {'plan_sha256': fence, 'config_sha256': digest(native), 'files': summary,
                          'changed_files': sum(a != b for _, a, b in files)}


def write(path, content):
    fd, name = tempfile.mkstemp(prefix='.integration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def setup(config, source, *, apply=False, expected_plan_sha256=None):
    native, files, result = plan(config, source)
    if apply:
        if expected_plan_sha256 != result['plan_sha256']: raise ValueError('Instruction plan changed')
        if read(config, private=True) != native or any(read(p) != a for p, a, _ in files):
            raise ValueError('Instruction inputs changed')
        if result['changed_files']:
            backup = config.parent / 'integration-instruction-backups'
            if backup.is_symlink(): raise ValueError('Backup refused')
            backup.mkdir(mode=0o700, exist_ok=True)
            info = backup.stat()
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Private backup required')
            backup = backup / str(uuid.uuid4()); backup.mkdir(mode=0o700)
            for i, (path, old, new) in enumerate(files):
                if old != new: write(backup / (str(i) + '.before'), old)
            write(backup / 'plan.json', (json.dumps(result) + '\n').encode())
            for path, old, new in files:
                if old != new: write(path, new)
            if read(config, private=True) != native or any(read(p) != b for p, _, b in files):
                raise ValueError('Instruction readback differs; retained backup requires review')
    return {**result, 'applied': apply, 'native_config_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/sandbox/.openclaw/openclaw.json'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args(); os.umask(0o077)
    try:
        print(json.dumps(setup(args.config, args.source, apply=args.apply, expected_plan_sha256=args.expected_plan_sha256)))
    except (OSError, ValueError, TypeError, KeyError):
        raise SystemExit('Integration instruction setup refused; retain the backup and review owned source/files.') from None
