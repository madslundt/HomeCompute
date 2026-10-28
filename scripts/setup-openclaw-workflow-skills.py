#!/usr/bin/env python3
"""Install only reviewed workflow skills into existing roles; retain all other native state."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

from openclaw_workflow_skills import WORKFLOWS, SKILLS

START, END = '<!-- homecompute-workflows:start -->', '<!-- homecompute-workflows:end -->'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink refused')


def read(path, *, private=False, optional=False):
    safe_path(path)
    if optional and not path.exists():
        return None
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022) or info.st_size > 1048576):
        raise ValueError('Owned bounded file required')
    return path.read_bytes()


def identity(raw, skill, trigger):
    text = raw.decode()
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('Workflow instruction markers refused')
    block = START + '\nLoad ' + skill + ' before working with ' + trigger + '.\n' + END
    if START in text:
        first, rest = text.split(START, 1)
        _, last = rest.split(END, 1)
        return (first + block + last).encode()
    return raw + (b'\n\n' if raw else b'') + block.encode() + b'\n'


def candidate(before, root):
    after = copy.deepcopy(before)
    if 'read' in before.get('tools', {}).get('deny', []) or 'read' in before.get('tools', {}).get('subagents', {}).get('tools', {}).get('deny', []):
        raise ValueError('Qualified skill reader required')
    entries = after['agents']['entries']
    if not {'main', *WORKFLOWS} <= set(entries):
        raise ValueError('Existing assigned roles required')
    for role, entry in entries.items():
        skills = entry.get('skills')
        if (not isinstance(skills, list) or len(set(skills)) != len(skills)
                or any(not isinstance(name, str) or '*' in name for name in skills)):
            raise ValueError('Explicit finite role skills required')
        expected = {WORKFLOWS[role][0]} if role in WORKFLOWS else set()
        if set(skills) & SKILLS - expected:
            raise ValueError('Unexpected workflow assignment refused')
    for role, (name, _) in WORKFLOWS.items():
        entry = entries[role]
        if entry['workspace'] != str(root / ('workspace-' + role)):
            raise ValueError('Canonical assigned workspace required')
        if 'read' not in entry['tools'].get('allow', []) or 'read' in entry['tools'].get('deny', []):
            raise ValueError('Qualified skill reader required')
        if name not in entry['skills']:
            entry['skills'].append(name)
    return after


def plan(config, source):
    native = read(config, private=True)
    before = json.loads(native)
    root = config.parent
    after = candidate(before, root)
    sources = {}
    for name in sorted(SKILLS):
        directory = source / 'deploy/openclaw/skills' / name
        safe_path(directory)
        if {p.name for p in directory.iterdir()} != {'SKILL.md'}:
            raise ValueError('Instruction-only skill required')
        content = read(directory / 'SKILL.md')
        if (not 1 <= len(content) <= 16384 or not re.match(
                r'^---\nname: ' + re.escape(name) + r'\ndescription: [^\n]+\n---\n', content.decode())):
            raise ValueError('Reviewed skill frontmatter required')
        sources[name] = content
    ledger_path = root / 'team-skill-ownership.json'
    ledger_raw = read(ledger_path, private=True)
    ledger = json.loads(ledger_raw)
    if (not isinstance(ledger, dict) or any(not isinstance(k, str)
            or not re.fullmatch(r'workspace(?:-[a-z0-9-]+)?/skills/[a-z][a-z0-9-]*/SKILL.md', k)
            or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for k, v in ledger.items())):
        raise ValueError('Managed skill ledger refused')
    files = []
    for role, (name, trigger) in sorted(WORKFLOWS.items()):
        workspace = Path(after['agents']['entries'][role]['workspace'])
        path = workspace / 'AGENTS.md'
        raw = read(path)
        files.append((path, raw, identity(raw, name, trigger)))
        path = workspace / 'skills' / name / 'SKILL.md'
        raw = read(path, optional=True)
        relative = str(path.relative_to(root))
        content = sources[name]
        if raw is not None and raw != content and ledger.get(relative) != digest(raw):
            raise ValueError('Unreviewed skill overwrite refused')
        files.append((path, raw, content))
        ledger[relative] = digest(content)
    files.append((ledger_path, ledger_raw, (json.dumps(ledger, sort_keys=True, indent=2) + '\n').encode()))
    changes = [{'path': 'agents.entries.' + role + '.skills', 'value': after['agents']['entries'][role]['skills']}
               for role in sorted(WORKFLOWS) if before['agents']['entries'][role]['skills'] != after['agents']['entries'][role]['skills']]
    summary = [{'path': str(p.relative_to(root)), 'before': digest(a) if a is not None else None,
                'after': digest(b)} for p, a, b in files]
    fence = digest(json.dumps({'config': digest(native), 'files': summary, 'changes': changes}, sort_keys=True).encode())
    result = {'plan_sha256': fence, 'source_config_sha256': digest(native), 'role_count': len(before['agents']['entries']),
              'assignments': {role: name for role, (name, _) in WORKFLOWS.items()}, 'files': summary,
              'changed_files': sum(a != b for _, a, b in files), 'config_changed': bool(changes)}
    return native, after, changes, files, result


def write(path, content):
    safe_path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.parent.stat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o002
            or info.st_mode & 0o020 and info.st_gid != os.getegid()):
        raise ValueError('Owned destination directory required')
    fd, temporary = tempfile.mkstemp(prefix='.workflow-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def native(changes, config, *, apply, runner):
    if not changes:
        return
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(config), NODE_OPTIONS='--max-old-space-size=384')
    result = runner(['openclaw', 'config', 'set', '--batch-json', json.dumps(changes)]
                    + ([] if apply else ['--dry-run']), env=env, capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('Native workflow configuration refused')


def setup(config, source, *, apply=False, expected_plan_sha256=None, runner=subprocess.run):
    raw, after, changes, files, result = plan(config, source)
    native(changes, config, apply=False, runner=runner)
    if apply:
        if expected_plan_sha256 != result['plan_sha256']:
            raise ValueError('Reviewed workflow plan changed')
        if read(config, private=True) != raw or any(read(p, optional=True) != a for p, a, _ in files):
            raise ValueError('Workflow inputs changed during validation')
        if changes or result['changed_files']:
            backup_root = config.parent / 'workflow-skill-backups'
            safe_path(backup_root)
            backup_root.mkdir(mode=0o700, exist_ok=True)
            info = backup_root.stat()
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Private backup directory required')
            backup = backup_root / str(uuid.uuid4())
            backup.mkdir(mode=0o700)
            write(backup / 'openclaw-before.json', raw)
            for i, (_, old, new) in enumerate(files):
                if old is not None and old != new:
                    write(backup / (str(i) + '.before'), old)
            write(backup / 'plan.json', (json.dumps(result, indent=2) + '\n').encode())
            for path, old, new in files:
                if old != new:
                    write(path, new)
            native(changes, config, apply=True, runner=runner)
            if json.loads(read(config, private=True)) != after or any(read(p) != b for p, _, b in files):
                raise ValueError('Workflow readback differs; retain backup for recovery')
            result['backup_directory'] = str(backup)
    return {**result, 'applied': apply, 'restart_required': bool(changes or result['changed_files'])}


def rollback(config, backup, *, apply=False, expected_plan_sha256=None, runner=subprocess.run):
    safe_path(backup)
    if backup.parent != config.parent / 'workflow-skill-backups':
        raise ValueError('Canonical workflow backup required')
    raw = read(config, private=True)
    current = json.loads(raw)
    before = json.loads(read(backup / 'openclaw-before.json', private=True))
    installed = candidate(before, config.parent)
    if current not in (before, installed):
        raise ValueError('Later native changes require reconciliation')
    receipt = json.loads(read(backup / 'plan.json', private=True))
    allowed = {'team-skill-ownership.json'}
    for role, (skill, _) in WORKFLOWS.items():
        allowed |= {'workspace-' + role + '/AGENTS.md', 'workspace-' + role + '/skills/' + skill + '/SKILL.md'}
    rows = receipt['files']
    if len(rows) != len(allowed) or {row['path'] for row in rows} != allowed:
        raise ValueError('Scoped backup paths required')
    files, hashes = [], []
    for i, row in enumerate(rows):
        if row['before'] == row['after']:
            continue
        path = config.parent / row['path']
        actual = read(path, optional=True)
        old = read(backup / (str(i) + '.before'), private=True) if row['before'] is not None else None
        old_hash = digest(old) if old is not None else None
        actual_hash = digest(actual) if actual is not None else None
        if old_hash != row['before'] or actual_hash not in {row['before'], row['after']}:
            raise ValueError('Later file changes require reconciliation')
        files.append((path, actual, old))
        hashes.append((row['path'], actual_hash, old_hash))
    changes = [{'path': 'agents.entries.' + role + '.skills', 'value': before['agents']['entries'][role]['skills']}
               for role in sorted(WORKFLOWS) if current['agents']['entries'][role]['skills'] != before['agents']['entries'][role]['skills']]
    fence = digest(json.dumps({'config': digest(raw), 'files': hashes}, sort_keys=True).encode())
    native(changes, config, apply=False, runner=runner)
    if apply:
        if fence != expected_plan_sha256 or read(config, private=True) != raw or any(read(p, optional=True) != a for p, a, _ in files):
            raise ValueError('Rollback plan changed')
        for path, actual, old in files:
            if old is None:
                path.unlink(missing_ok=True)
                if not any(path.parent.iterdir()):
                    path.parent.rmdir()
            elif old != actual:
                write(path, old)
        native(changes, config, apply=True, runner=runner)
        if json.loads(read(config, private=True)) != before or any(read(p, optional=True) != b for p, _, b in files):
            raise ValueError('Rollback readback differs; retain backup')
    return {'plan_sha256': fence, 'applied': apply, 'rollback': True, 'files': len(files), 'config_changed': bool(changes)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/sandbox/.openclaw/openclaw.json'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--rollback', type=Path, help='restore an exact retained workflow backup; refuses later changes')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if bool(args.source) == bool(args.rollback):
            raise ValueError('Choose source or rollback')
        if args.rollback:
            result = rollback(args.config, args.rollback, apply=args.apply, expected_plan_sha256=args.expected_plan_sha256)
        else:
            result = setup(args.config, args.source, apply=args.apply, expected_plan_sha256=args.expected_plan_sha256)
        print(json.dumps(result))
    except (OSError, ValueError, TypeError, KeyError):
        raise SystemExit('Workflow skill setup refused; preserve private backup and review source/configuration.') from None
