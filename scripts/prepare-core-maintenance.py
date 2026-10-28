#!/usr/bin/env python3
"""Prepare a root NixOS plan for an already-built immutable home-core system.

Preparation performs only store/profile queries. It never builds, downloads,
sets a profile or activates a system. Every changed closure component is
accounted for; ambiguous names, additions, removals or versionless artifacts
require approval of the entire exact plan. Execution shares the core deploy lock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time

from infrastructure_maintenance import ENV, NIX_SYSTEM, trusted_path, validate_plan

NIX = '/run/current-system/sw/bin/nix'
NIX_ENV = '/run/current-system/sw/bin/nix-env'
READLINK = '/run/current-system/sw/bin/readlink'
CHECKSUM = '/run/current-system/sw/bin/sha256sum'
CURRENT = '/run/current-system'
PROFILE = '/nix/var/nix/profiles/system'
SYSTEM = re.compile(NIX_SYSTEM + r'\Z')
STORE = re.compile(r'/nix/store/[a-z0-9]{32}-([A-Za-z0-9+._?=-]{1,255})\Z')
SEMVER_NAME = re.compile(r'([A-Za-z0-9][A-Za-z0-9+._-]*?)-(\d+\.\d+\.\d+)(?:-(bin|dev|lib|man|doc|info|debug|static))?\Z')
VERSION_NAME = re.compile(r'([A-Za-z0-9][A-Za-z0-9+._-]*?)-([0-9][A-Za-z0-9.+~_-]*)\Z')
COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,127}\Z')
MAX_CLOSURE_BYTES = 8 * 1024 * 1024
MAX_CLOSURE_PATHS = 10000


def read_command(argv, runner=subprocess.run):
    result = runner(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                    env=ENV.copy(), cwd='/', shell=False)
    if (result.returncode or not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
            or len(result.stdout) + len(result.stderr) > MAX_CLOSURE_BYTES):
        raise ValueError('trusted read-only Nix query failed or exceeded output budget')
    return result.stdout


def system_path(value):
    if not isinstance(value, str) or not SYSTEM.fullmatch(value):
        raise ValueError('immutable home-core NixOS system path required')
    return value


def resolved_system(output):
    if not isinstance(output, bytes) or not output.endswith(b'\n') or output.count(b'\n') != 1:
        raise ValueError('one resolved immutable system path required')
    return system_path(output[:-1].decode('utf-8', errors='strict'))


def closure(output, root):
    if not isinstance(output, bytes) or len(output) > MAX_CLOSURE_BYTES:
        raise ValueError('bounded closure metadata required')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate closure metadata key')
            result[key] = value
        return result
    value = json.loads(output, object_pairs_hook=unique)
    if isinstance(value, dict):
        paths = list(value)
        if any(not isinstance(info, dict) or info.get('path', path) != path for path, info in value.items()):
            raise ValueError('closure path identity mismatch')
    elif isinstance(value, list):
        if any(not isinstance(info, dict) or not isinstance(info.get('path'), str) for info in value):
            raise ValueError('explicit closure paths required')
        paths = [info['path'] for info in value]
    else:
        raise ValueError('unsupported closure metadata')
    if (not 1 <= len(paths) <= MAX_CLOSURE_PATHS or len(set(paths)) != len(paths)
            or root not in paths or any(not STORE.fullmatch(path) for path in paths)):
        raise ValueError('complete unique immutable closure required')
    return frozenset(paths)


def package(path):
    name = STORE.fullmatch(path)[1]
    match = SEMVER_NAME.fullmatch(name)
    if match:
        component, version, output = match.groups()
        if output:
            component += '-' + output
    else:
        match = VERSION_NAME.fullmatch(name)
        component, version = match.groups() if match else (name, 'unversioned')
    if not COMPONENT.fullmatch(component):
        return 'opaque.' + hashlib.sha256(name.encode()).hexdigest()[:32], version, True
    return component, version, match is None or SEMVER_NAME.fullmatch(name) is None


def release(path):
    name = path.split('-nixos-system-home-core-', 1)[1]
    match = re.fullmatch(r'(\d{2}\.(?:05|11))(?:\.[A-Za-z0-9._+-]+)?', name)
    return match[1] if match else 'unknown'


def changes(old_paths, new_paths, installed_system, candidate_system):
    old, new, uncertain = {}, {}, set()
    for paths, target, root in ((old_paths, old, installed_system), (new_paths, new, candidate_system)):
        for path in paths - {root}:
            component, version, ambiguous = package(path)
            target.setdefault(component, []).append((path, version))
            if ambiguous:
                uncertain.add(component)
    updates = [{'component': 'nixos-system', 'scheme': 'nixos',
                'installed': release(installed_system), 'candidate': release(candidate_system)}]
    reasons = []
    for component in sorted(old.keys() | new.keys()):
        left, right = sorted(old.get(component, [])), sorted(new.get(component, []))
        if left == right:
            continue
        if not left or not right:
            reasons.append(component + ': added_or_removed')
        if len(left) > 1 or len(right) > 1:
            reasons.append(component + ': ambiguous_duplicates')
        if component in uncertain:
            reasons.append(component + ': unknown_version')
        def versions(rows):
            return ','.join(version for _, version in rows) if rows else 'absent'
        previous, proposed = versions(left), versions(right)
        if previous == proposed:
            reasons.append(component + ': artifact_changed_without_version_change')
        if len(previous) > 256 or len(proposed) > 256:
            raise ValueError('ambiguous component versions exceed bounded review metadata')
        updates.append({'component': component, 'scheme': 'semver', 'installed': previous, 'candidate': proposed})
    if len(updates) > 4096:
        raise ValueError('entire closure change exceeds 4096 components; no subset is authorized')
    return updates, reasons


def immutable_store_path(path, *, directory=False):
    """Nix's root-owned sticky store permits builders to create new objects.

    Each selected object and every other ancestor remains root-owned and
    protected; the sticky store directory prevents builders replacing it.
    """
    path = Path(path)
    if not str(path).startswith('/nix/store/'):
        raise ValueError('immutable store object required')
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        store = parent == Path('/nix/store')
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0
                or (info.st_mode & 0o022 and not (store and info.st_mode & stat.S_ISVTX and not info.st_mode & 0o002))):
            raise ValueError('store object must be root-controlled')
        if (parent != path or directory) and not stat.S_ISDIR(info.st_mode):
            raise ValueError('store ancestor must be a directory')
        if parent == path and not directory and not stat.S_ISREG(info.st_mode):
            raise ValueError('regular immutable store file required')
    return path


def activation_script(candidate):
    immutable_store_path(candidate, directory=True)
    script = Path(candidate) / 'bin/switch-to-configuration'
    # The activation script can be an immutable store symlink; verify its target
    # stays in the store and is protected before hashing the canonical link path.
    target = script.resolve(strict=True)
    if not str(target).startswith('/nix/store/'):
        raise ValueError('activation script must resolve inside the immutable store')
    immutable_store_path(target)
    if not target.stat().st_mode & 0o111:
        raise ValueError('already-built executable activation script required')
    return script


def prepare(candidate, *, host='home-core', runner=subprocess.run, clock=time.time):
    if os.geteuid() != 0 or host != 'home-core':
        raise ValueError('root on home-core required')
    candidate = system_path(str(candidate))
    script = activation_script(candidate)
    current_check, profile_check = [READLINK, '-f', CURRENT], [READLINK, '-f', PROFILE]
    current_output, profile_output = read_command(current_check, runner), read_command(profile_check, runner)
    current, profile = resolved_system(current_output), resolved_system(profile_output)
    if current == candidate:
        raise ValueError('candidate is already active; reconcile profile separately')
    old = closure(read_command([NIX, 'path-info', '--offline', '--recursive', '--json', current], runner), current)
    new = closure(read_command([NIX, 'path-info', '--offline', '--recursive', '--json', candidate], runner), candidate)
    updates, reasons = changes(old, new, current, candidate)
    if profile != current:
        reasons.append('active_system_and_boot_profile_differ')
    checksum_argv = [CHECKSUM, str(script)]
    checksum_output = read_command(checksum_argv, runner)
    if not re.fullmatch(rb'[a-f0-9]{64}  ' + re.escape(str(script).encode()) + rb'\n', checksum_output):
        raise ValueError('exact activation script checksum required')
    probes = [(current_check, current_output), (profile_check, profile_output), (checksum_argv, checksum_output)]
    desired_digest = hashlib.sha256((candidate + '\n').encode()).hexdigest()
    plan = {'schema_version': 1, 'policy': {'operation': 'update', 'target': 'home-core', 'updates': updates},
            'requires_approval': bool(reasons),
            'prechecks': [{'argv': argv, 'stdout_sha256': hashlib.sha256(output).hexdigest()} for argv, output in probes],
            'steps': [[NIX_ENV, '--profile', PROFILE, '--set', candidate], [str(script), 'switch']],
            'postchecks': [{'argv': argv, 'stdout_sha256': desired_digest} for argv in (current_check, profile_check)],
            'timeout_seconds': 900, 'expires_at': clock() + 3600}
    validate_plan(plan)
    return plan, reasons


def write_plan(path, plan):
    if os.geteuid() != 0:
        raise ValueError('root plan output ownership required')
    path = Path(os.path.abspath(path))
    trusted_path(path.parent, 0, directory=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(plan, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        plan, reasons = prepare(args.candidate, host=socket.gethostname().split('.')[0])
        write_plan(args.output, plan)
        print(json.dumps({'plan_file': str(args.output), 'approval_required': validate_plan(plan)['approval_required'],
                          'review_reasons': reasons, 'installed_changes': False}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError) as error:
        print('NixOS preparation refused: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
