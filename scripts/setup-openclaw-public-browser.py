#!/usr/bin/env python3
"""Reconcile public browsing policy without changing role grants or credentials.

Run as the native config owner after installing the reviewed protected modules
and worker image. Default validates only; restart remains the native lifecycle's
responsibility. Never print native command output or browser credentials.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = Path('/sandbox/.openclaw')
DNS_MODULE = Path('/opt/homecompute-public-dns/public-dns.mjs')
POLICY = {'allowedHostnames': ['127.0.0.1']}


def stage_modules(root, source, *, expected_guard_sha256, expected_dns_sha256, expected_schema_sha256=None):
    """Root-only, digest-fenced module installation; never restart a gateway."""
    if os.geteuid() != 0 or root != NATIVE_ROOT:
        raise ValueError('Root in the managed sandbox required')
    guard = root / 'extensions/homecompute-safety'
    dns = DNS_MODULE
    for path, expected in [(guard / 'index.mjs', expected_guard_sha256),
                           (guard / 'public-dns.mjs', expected_dns_sha256), (dns, expected_dns_sha256)]:
        info = path.lstat()
        if (any(p.is_symlink() for p in (path, *path.parents)) or info.st_uid != 0
                or info.st_mode & 0o222 or not stat.S_ISREG(info.st_mode)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError('Protected preimage digest mismatch')
    originals = source / 'deploy/openclaw/safety-plugin'
    files = [(guard / name, originals / name) for name in ('public-dns.mjs', 'browser-schema.mjs', 'index.mjs')]
    files.insert(0, (dns, originals / 'public-dns.mjs'))
    for target, original in files:
        for path in (original, target.parent):
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('Module symlink refused')
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Root-owned module paths required')
        if target.is_symlink():
            raise ValueError('Module symlink refused')
        if target.exists():
            info = target.lstat()
            if target.is_symlink() or info.st_uid != 0 or info.st_mode & 0o222:
                raise ValueError('Protected module required')
            if (target.name == 'browser-schema.mjs' and target.read_bytes() != original.read_bytes()
                    and hashlib.sha256(target.read_bytes()).hexdigest() != expected_schema_sha256):
                raise ValueError('Existing browser schema preimage digest required')
    changed = [(p, original.read_bytes()) for p, original in files
               if not p.exists() or p.read_bytes() != original.read_bytes()]
    if not changed:
        return {'staged': True, 'changed_files': 0}
    # tempfile creates a root-only rollback directory without following a
    # pre-existing backup directory supplied by the runtime user.
    backup = Path(tempfile.mkdtemp(prefix='homecompute-browser-before-', dir='/var/tmp'))
    for index, (path, _) in enumerate(changed):
        if path.exists():
            (backup / str(index)).write_bytes(path.read_bytes())
    (backup / 'paths.json').write_text(json.dumps([str(p) for p, _ in changed]))
    for path, content in changed:
        fd, name = tempfile.mkstemp(prefix='.public-browser-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
                os.fchmod(output.fileno(), 0o555)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    return {'staged': True, 'changed_files': len(changed), 'backup': str(backup), 'restart_required': True}


def candidate(before):
    after = copy.deepcopy(before)
    browser = before['browser']
    if (browser.get('enabled') is not True or browser.get('evaluateEnabled') is not False
            or browser.get('defaultProfile') != 'homecompute'
            or set(browser.get('profiles', {})) != {'homecompute'}
            or browser['profiles']['homecompute'].get('attachOnly') is not True
            or before['plugins']['entries']['homecompute-safety']['enabled'] is not True):
        raise ValueError('Protected isolated browser required')
    url = urlsplit(browser['profiles']['homecompute']['cdpUrl'])
    query = parse_qs(url.query, strict_parsing=True)
    if (url.scheme != 'http' or url.netloc != '127.0.0.1:18800' or url.path != '/'
            or url.fragment or set(query) != {'token'} or len(query['token']) != 1
            or not 32 <= len(query['token'][0]) <= 256):
        raise ValueError('Fixed authenticated CDP endpoint required')
    old = browser.get('ssrfPolicy', {})
    if (set(old) - {'dangerouslyAllowPrivateNetwork', 'allowedHostnames'}
            or ('dangerouslyAllowPrivateNetwork' in old and old['dangerouslyAllowPrivateNetwork'] is not False)
            or not isinstance(old.get('allowedHostnames'), list)
            or not all(isinstance(host, str) and '*' not in host for host in old['allowedHostnames'])):
        raise ValueError('Existing browser policy needs separate reconciliation')
    after['browser']['ssrfPolicy'] = copy.deepcopy(POLICY)
    return after


def protected_modules(source, root):
    # The DNS controller verifies its separately installed module by digest too.
    targets = [(root / 'extensions/homecompute-safety' / name,
                source / 'deploy/openclaw/safety-plugin' / name)
               for name in ('index.mjs', 'public-dns.mjs', 'browser-relay.mjs', 'llm-queue.mjs',
                            'openclaw.plugin.json', 'package.json')]
    targets.append((DNS_MODULE,
                    source / 'deploy/openclaw/safety-plugin/public-dns.mjs'))
    for target, original in targets:
        info = target.lstat()
        if (any(p.is_symlink() for p in (target, *target.parents)) or info.st_uid != 0
                or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o222
                or target.read_bytes() != original.read_bytes()):
            raise ValueError('Reviewed protected browser modules required')


def setup(config, source, *, apply=False, expected_config_sha256=None, runner=subprocess.run):
    if any(p.is_symlink() for p in (config, *config.parents)):
        raise ValueError('Symlink refused')
    info = config.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 1048576):
        raise ValueError('Private native configuration required')
    raw = config.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_config_sha256 is not None and expected_config_sha256 != digest:
        raise ValueError('Source configuration changed')
    before = json.loads(raw)
    after = candidate(before)
    changed = after != before
    changes = [{'path': 'browser.ssrfPolicy', 'value': POLICY}]
    environment = {**os.environ, 'OPENCLAW_CONFIG_PATH': str(config), 'NODE_OPTIONS': '--max-old-space-size=384'}

    def native(dry_run):
        command = ['openclaw', 'config', 'set', '--batch-json', json.dumps(changes)]
        if dry_run:
            command.append('--dry-run')
        result = runner(command, env=environment, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError('Native browser configuration refused')

    native(True)
    result = {'source_config_sha256': digest, 'config_changed': changed,
              'schema_validated': True, 'applied': False, 'restart_required': changed}
    if apply:
        if expected_config_sha256 != digest:
            raise ValueError('Exact reviewed configuration digest required')
        protected_modules(source, config.parent)
        if config.read_bytes() != raw:
            raise ValueError('Configuration changed during validation')
        if changed:
            backup = config.parent / 'public-browser-backups'
            if backup.is_symlink():
                raise ValueError('Backup symlink refused')
            backup.mkdir(mode=0o700, exist_ok=True)
            owned = backup.stat()
            if owned.st_uid != os.geteuid() or stat.S_IMODE(owned.st_mode) != 0o700:
                raise ValueError('Private backup directory required')
            backup = backup / (str(uuid.uuid4()) + '.json')
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
            native(False)
            if json.loads(config.read_bytes()) != after:
                raise ValueError('Native readback differs; inspect retained backup')
            result['backup'] = str(backup)
        result['applied'] = True
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/sandbox/.openclaw/openclaw.json'))
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-config-sha256')
    parser.add_argument('--stage-modules', action='store_true', help='root-only digest-fenced module staging')
    parser.add_argument('--expected-guard-sha256')
    parser.add_argument('--expected-dns-sha256')
    parser.add_argument('--expected-schema-sha256', help='required if replacing an existing different schema helper')
    args = parser.parse_args()
    try:
        if args.stage_modules:
            if args.apply or not args.expected_guard_sha256 or not args.expected_dns_sha256:
                raise ValueError('Module staging requires exact preimage digests')
            result = stage_modules(args.config.parent, args.source,
                                   expected_guard_sha256=args.expected_guard_sha256,
                                   expected_dns_sha256=args.expected_dns_sha256,
                                   expected_schema_sha256=args.expected_schema_sha256)
        else:
            result = setup(args.config, args.source, apply=args.apply,
                           expected_config_sha256=args.expected_config_sha256)
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise SystemExit('Public browser setup refused; inspect owned sources and private backup.') from None
