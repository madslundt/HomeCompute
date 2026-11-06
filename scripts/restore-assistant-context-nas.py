#!/usr/bin/env python3
"""Prepare an assistant-only restore from one exact encrypted NAS snapshot.

This verifies and stages only assistant-context members. It does not restore
or qualify other application databases, VMs, PostgreSQL, or the full archive.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FILES = ('scripts/restore-assistant-context-nas.py', 'scripts/nas-recovery-backup.py',
         'scripts/setup-nas-recovery-backup.py', 'scripts/application-recovery.py')
SNAPSHOT = re.compile(r'[0-9a-f]{64}\Z')
ARCHIVE = re.compile(r'homecompute-applications-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z\.tar\Z')
MAX_ENTRIES = 100_000
MAX_ASSISTANT_BYTES = 2 * 1024**3
MAX_MEMBER_BYTES = 512 * 1024**2
MAX_MANIFEST_BYTES = 2 * 1024**2
MOUNT_PARENT = Path('/srv/restore-tests')
STATE = Path('/var/lib/homecompute/nas-recovery-backup')
RELEASES_ROOT = Path('/srv/homecompute/releases')
FENCE = 'restore-fence.json'


class Refusal(ValueError):
    """A restore precondition or archive safety check failed."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _private_dir(path: Path, *, create=False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Refusal('private restore directory required')


def _atomic_json(path: Path, value: dict) -> None:
    raw = _canonical(value)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest_scope(manifest: dict, directories: set[str], files: set[str]) -> tuple[set[str], set[str]]:
    allowed_dirs = {
        'assistant-context/state', 'assistant-context/collector',
        'assistant-context/improvements', 'assistant-context/policy',
        'assistant-context/policy-controller', 'assistant-context/delivery-auth',
    }
    allowed_files = {
        'assistant-context/clients.json', 'assistant-context/improvement-grant.json',
        'assistant-context/embedding-credential.json', 'assistant-context/briefing-credential.json',
        'assistant-context/delegation-identity', 'assistant-context/delegation-known-hosts',
        'assistant-context/improvement-github-token', 'assistant-context/policy-health-token',
    }
    data = manifest.get('assistant_context') if isinstance(manifest, dict) else None
    if (manifest.get('schema_version') != 1 or not isinstance(data, dict)
            or set(data) != {'directories', 'files', 'sqlite_databases'}):
        raise Refusal('assistant-context manifest shape refused')
    dirs, names, dbs = data['directories'], data['files'], data['sqlite_databases']
    if (not all(isinstance(items, list) for items in (dirs, names, dbs))
            or any(not isinstance(v, str) for values in (dirs, names, dbs) for v in values)
            or len(dirs) != len(set(dirs)) or len(names) != len(set(names))
            or len(dbs) != len(set(dbs)) or not set(dirs) <= allowed_dirs or not set(names) <= allowed_files):
        raise Refusal('assistant-context manifest paths refused')
    prefixes = tuple('assistant-context/' + item.split('/', 1)[1] for item in dirs)
    for name in dbs:
        parts = PurePosixPath(name).parts
        if (name.startswith('/') or '..' in parts or not name.startswith(prefixes)
                or not name.endswith('.sqlite3')):
            raise Refusal('assistant SQLite manifest path refused')
    if 'assistant-context/state' in dirs:
        if 'assistant-context/clients.json' not in names:
            raise Refusal('assistant state restore lacks clients metadata')
        for basename in ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3'):
            if 'assistant-context/state/' + basename not in dbs:
                raise Refusal('assistant state restore lacks a required SQLite backup')
    if not dirs and not names:
        raise Refusal('empty assistant restore scope refused')
    directories.update(dirs); files.update(names)
    return set(dbs), set(dirs)


def exact_snapshot(snapshot_id: str, snapshots: list[dict]) -> dict:
    if not SNAPSHOT.fullmatch(snapshot_id):
        raise Refusal('exact full snapshot ID required')
    matches = [item for item in snapshots if item.get('id') == snapshot_id]
    if (len(matches) != 1 or matches[0].get('paths') != ['/srv/backups/agent-recovery']
            or 'agent-recovery-archives' not in (matches[0].get('tags') or [])):
        raise Refusal('exact full snapshot is not a unique matching archive snapshot')
    return matches[0]


def source_bundle_hash(source: Path) -> str:
    digest = hashlib.sha256()
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024**2:
            raise Refusal('bounded restore source files required')
        data = path.read_bytes()
        if name.endswith('.py'): compile(data, name, 'exec')
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def plan(source: Path) -> dict:
    source = source.resolve()
    return {'status': 'plan', 'source': str(source), 'source_bundle_sha256': source_bundle_hash(source),
            'files': list(FILES), 'applied': False}


def _safe_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    if (name.is_absolute() or not name.parts or '..' in name.parts
            or member.issym() or member.islnk() or not (member.isdir() or member.isfile())):
        raise Refusal('unsafe archive member refused')
    if member.isfile() and member.size < 0:
        raise Refusal('negative archive member size refused')


def _member_scope(name: str, dirs: set[str], files: set[str], dbs: set[str]) -> bool:
    if name == 'manifest.json' or name in {'state/' + item for item in files | dbs}:
        return True
    if name == 'state':
        return True
    if name == 'state/assistant-context':
        return True
    relative = name.removeprefix('state/')
    if relative in dirs:
        return True
    return any(relative.startswith(directory + '/') for directory in dirs)


def inspect_archive(archive: tarfile.TarFile) -> tuple[dict, list[tarfile.TarInfo], str]:
    total, seen = 0, set()
    manifest_info = None
    for index, member in enumerate(archive):
        if index >= MAX_ENTRIES:
            raise Refusal('archive entry budget exceeded')
        normalized = str(PurePosixPath(member.name))
        if normalized in seen:
            raise Refusal('duplicate archive member refused')
        seen.add(normalized)
        if member.name == 'manifest.json':
            manifest_info = member
            if not member.isfile() or member.size > MAX_MANIFEST_BYTES:
                raise Refusal('bounded archive manifest required')
    if manifest_info is None:
        raise Refusal('archive manifest missing')
    stream = archive.extractfile(manifest_info)
    raw = stream.read(MAX_MANIFEST_BYTES + 1) if stream else b''
    if len(raw) > MAX_MANIFEST_BYTES:
        raise Refusal('archive manifest exceeds bound')
    try: manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error: raise Refusal('archive manifest invalid') from error
    dbs, dirs = manifest_scope(manifest, set(), set())
    declared_files = set(manifest['assistant_context']['files'])
    allowed_files = {'state/' + name for name in declared_files | dbs}
    declared_dirs = {'state/' + name for name in dirs}
    selected = []
    for member in archive.getmembers():
        if not _member_scope(member.name, dirs, declared_files, dbs):
            continue
        _safe_member(member)
        mode = stat.S_IMODE(member.mode)
        if mode & 0o7000 or (member.isfile() and mode & 0o111):
            raise Refusal('assistant restore accepts data permissions without special or executable bits')
        if any(member.name == 'state/' + database + suffix for database in dbs
               for suffix in ('-wal', '-shm', '-journal')):
            raise Refusal('unexpected SQLite sidecar in assistant archive')
        if member.name == 'manifest.json':
            selected.append(member); continue
        if member.name in {'state', 'state/assistant-context'}:
            selected.append(member); continue
        if member.name in declared_dirs or member.name in allowed_files:
            selected.append(member)
            if member.isfile():
                total += member.size
                if total > MAX_ASSISTANT_BYTES or member.size > MAX_MEMBER_BYTES:
                    raise Refusal('assistant restore byte budget exceeded')
            continue
        if member.isdir() and any(member.name.startswith(path + '/') for path in declared_dirs):
            selected.append(member); continue
        if member.isfile() and any(member.name.startswith('state/' + path + '/') for path in dirs):
            selected.append(member); total += member.size
            if total > MAX_ASSISTANT_BYTES or member.size > MAX_MEMBER_BYTES:
                raise Refusal('assistant restore byte budget exceeded')
            continue
        raise Refusal('undeclared assistant member refused')
    if not selected:
        raise Refusal('no assistant-context archive members found')
    return manifest, selected, hashlib.sha256(raw).hexdigest()


def _write_fence(target: Path, revision: str, snapshot: str, archive_name: str) -> Path:
    state = target / 'state/assistant-context/state'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    fence = state / FENCE
    _atomic_json(fence, {'schema_version': 1, 'phase': 'pending', 'reason': 'NAS assistant-only verification; trusted deletion replay and admission pending',
                         'source_revision': revision, 'snapshot_id': snapshot,
                         'archive': archive_name, 'created_at': dt.datetime.now(dt.UTC).isoformat()})
    return fence


def extract_assistant(archive_path: Path, target: Path, revision: str,
                      snapshot: str, archive_name: str) -> dict:
    if not SNAPSHOT.fullmatch(snapshot) or not ARCHIVE.fullmatch(archive_name):
        raise Refusal('exact full snapshot ID and archive name required')
    if target.exists() or target.is_symlink(): raise Refusal('new isolated restore target required')
    target.mkdir(mode=0o700, parents=False)
    fence = _write_fence(target, revision, snapshot, archive_name)
    with tarfile.open(archive_path, mode='r:') as archive:
        manifest, selected, manifest_sha256 = inspect_archive(archive)
        for member in selected:
            destination = target / ('manifest.json' if member.name == 'manifest.json' else member.name)
            if member.name == 'state/assistant-context/state/restore-fence.json':
                continue  # Archive fences are never trusted or installed.
            relative = destination.relative_to(target)
            if any(part in {'', '.', '..'} for part in relative.parts): raise Refusal('member traversal refused')
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                if member.name not in {'state', 'state/assistant-context'}:
                    source_mode = stat.S_IMODE(member.mode)
                    policy_root = member.name.rstrip('/') == 'state/assistant-context/policy'
                    if policy_root:
                        if source_mode != 0o750: raise Refusal('managed policy directory mode is unsafe')
                    elif source_mode & 0o077:
                        raise Refusal('assistant directory permissions are too broad')
                    if source_mode & 0o700 != 0o700:
                        raise Refusal('assistant directory owner permissions are incomplete')
                    os.chmod(destination, source_mode or 0o700)
                continue
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source_stream = archive.extractfile(member)
            if source_stream is None: raise Refusal('archive member unreadable')
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            remaining = member.size
            with os.fdopen(fd, 'wb') as output:
                while remaining:
                    block = source_stream.read(min(1024 * 1024, remaining))
                    if not block: raise Refusal('truncated archive member')
                    output.write(block); remaining -= len(block)
                output.flush(); os.fsync(output.fileno())
            os.chmod(destination, 0o600)
            source_mode = stat.S_IMODE(member.mode)
            if member.name == 'manifest.json': continue
            policy_projection = member.name in {
                'state/assistant-context/policy/assistant-briefings.json',
                'state/assistant-context/policy/improvements-status.json'}
            known_hosts = member.name == 'state/assistant-context/delegation-known-hosts'
            if source_mode & (0o022 if known_hosts else 0o037 if policy_projection else 0o077):
                raise Refusal('assistant archive file permissions are too broad')
            os.chmod(destination, source_mode or 0o600)
            if destination.suffix == '.sqlite3': _sqlite_integrity(destination)
    _atomic_json(fence, {'schema_version': 1, 'phase': 'pending', 'reason': 'NAS assistant-only verification; trusted deletion replay and admission pending',
                         'source_revision': revision, 'snapshot_id': snapshot,
                         'archive': archive_name, 'created_at': dt.datetime.now(dt.UTC).isoformat()})
    assistant = manifest['assistant_context']
    expected_files = {'state/' + name for name in assistant['files'] + assistant['sqlite_databases']}
    actual_files = {item.name for item in selected if item.isfile() and item.name.startswith('state/')}
    if not expected_files <= actual_files: raise Refusal('declared assistant files are incomplete')
    hashes = {}
    for member in selected:
        if not member.isfile() or member.name == 'manifest.json': continue
        destination = target / member.name
        if destination.is_symlink() or not destination.is_file():
            raise Refusal('declared assistant file missing after extraction')
        mode = stat.S_IMODE(destination.stat().st_mode)
        known_hosts = member.name == 'state/assistant-context/delegation-known-hosts'
        policy_projection = member.name in {
            'state/assistant-context/policy/assistant-briefings.json',
            'state/assistant-context/policy/improvements-status.json'}
        if (not stat.S_ISREG(destination.stat().st_mode) or destination.stat().st_nlink != 1
                or mode & (0o022 if known_hosts else 0o037 if policy_projection else 0o077)):
            raise Refusal('assistant credential file permissions are unsafe')
        hashes[member.name] = _file_sha256(destination)
    for name in assistant['files']:
        if name in {'assistant-context/clients.json', 'assistant-context/improvement-grant.json'}:
            try: json.loads((target / 'state' / name).read_bytes())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise Refusal('assistant credential metadata JSON is invalid') from error
    for directory in assistant['directories']:
        path = target / 'state' / directory
        if path.is_symlink() or not path.is_dir(): raise Refusal('assistant state directory is missing')
        for parent, child_dirs, child_files in os.walk(path, followlinks=False):
            current = Path(parent)
            mode = stat.S_IMODE(current.stat().st_mode)
            policy_root = directory == 'assistant-context/policy' and current == path
            if current.is_symlink() or (mode != 0o750 if policy_root else mode & 0o077):
                raise Refusal('assistant directory permissions are unsafe')
            for child in child_dirs:
                entry = current / child
                if entry.is_symlink() or not entry.is_dir() or entry.stat().st_mode & 0o077:
                    raise Refusal('assistant nested directory is unsafe')
            for child in child_files:
                entry = current / child
                info = entry.lstat()
                projection = (directory == 'assistant-context/policy' and current == path
                              and child in {'assistant-briefings.json', 'improvements-status.json'})
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_mode & (0o037 if projection else 0o077)):
                    raise Refusal('assistant state file permissions are unsafe')
    return {'schema_version': 1, 'verification_scope': 'assistant-context-only',
            'full_application_verified': False, 'postgres_verified': False, 'vms_verified': False,
            'source_revision': revision, 'snapshot_id': snapshot, 'archive': archive_name,
            'manifest_sha256': manifest_sha256, 'member_sha256': hashes,
            'assistant_directory_count': len(manifest['assistant_context']['directories']),
            'assistant_file_count': len(manifest['assistant_context']['files']),
            'sqlite_database_count': len(manifest['assistant_context']['sqlite_databases']),
            'pending_fence': str(fence)}


def _sqlite_integrity(path: Path) -> None:
    uri = path.resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise Refusal('assistant SQLite integrity check failed')
    finally: db.close()


def _mounted(path: Path) -> dict | None:
    result = subprocess.run(['findmnt', '--json', '--mountpoint', str(path), '--output', 'TARGET,SOURCE,FSTYPE,ID'],
                            check=False, capture_output=True, text=True, timeout=10)
    if result.returncode == 1: return None
    if result.returncode != 0: raise Refusal('mount state cannot be verified')
    entries = json.loads(result.stdout).get('filesystems', [])
    if not entries: return None
    if len(entries) != 1: raise Refusal('mountpoint resolves to an ambiguous mount stack')
    entry = entries[0]
    return {key.lower(): entry.get(key.lower()) for key in ('TARGET', 'SOURCE', 'FSTYPE', 'ID')}


def _restic_mount_identity(value: dict | None, path: Path) -> dict:
    if (not isinstance(value, dict) or value.get('target') != str(path)
            or value.get('source') != 'restic' or value.get('fstype') not in {'fuse', 'fuse.restic'}
            or value.get('id') is None):
        raise Refusal('expected owned Restic FUSE mount identity required')
    return value


def _mount_archive(environment: dict[str, str], snapshot: str, archive_name: str, target: Path,
                   *, popen=subprocess.Popen, sleep=time.sleep, find_mount=_mounted) -> tuple[Path, subprocess.Popen, dict]:
    if not SNAPSHOT.fullmatch(snapshot): raise Refusal('exact full snapshot ID required')
    mountpoint = Path(tempfile.mkdtemp(prefix='assistant-nas-', dir=MOUNT_PARENT))
    mountpoint.chmod(0o700)
    command = ['restic', 'mount', '--owner-root', '--tag', 'agent-recovery-archives',
               '--path-template', 'ids/%I', str(mountpoint)]
    process = popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    expected = mountpoint / 'ids' / snapshot / 'srv/backups/agent-recovery' / archive_name
    mount_identity = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None: raise Refusal('Restic mount exited before becoming ready')
            observed = find_mount(mountpoint)
            if observed:
                mount_identity = _restic_mount_identity(observed, mountpoint)
                if expected.is_file() and not expected.is_symlink():
                    return expected, process, mount_identity
            sleep(0.2)
        raise TimeoutError('Restic exact-snapshot mount did not become ready')
    except Exception as original:
        cleanup_failed = False
        try:
            observed = find_mount(mountpoint)
            if observed:
                observed = _restic_mount_identity(observed, mountpoint)
                if mount_identity is not None and observed != mount_identity:
                    raise Refusal('mount identity changed; preserving mountpoint')
                subprocess.run(['fusermount3', '-u', str(mountpoint)], check=True,
                               capture_output=True, text=True, timeout=60)
            if process.poll() is None: process.terminate()
            process.wait(timeout=30)
            if find_mount(mountpoint): cleanup_failed = True
            else: mountpoint.rmdir()
        except (OSError, subprocess.SubprocessError, Refusal):
            cleanup_failed = True
        if cleanup_failed:
            # Keep an uncertain FUSE mount and its directory intact for operator recovery.
            raise Refusal('mount startup/read failed; mount cleanup unverified, preserving mountpoint') from original
        raise


def _cleanup_mount(path: Path, process: subprocess.Popen, *, expected_identity: dict,
                   unmount=None, find_mount=_mounted) -> None:
    observed = find_mount(path)
    if observed:
        if _restic_mount_identity(observed, path) != expected_identity:
            raise Refusal('mount identity changed; preserving fenced output')
        command = unmount or ['fusermount3', '-u', str(path)]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
        if result.returncode: raise Refusal('owned Restic mount did not unmount cleanly; preserving fenced output')
    try: process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise Refusal('owned Restic mount process did not settle; preserving fenced output')
    observed = find_mount(path)
    if observed: raise Refusal('mount remains active; preserving fenced restore output')
    path.rmdir()


def published(source: Path, revision: str, expected_hash: str) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', revision) or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise Refusal('full published source revision and bundle hash required')
    releases = RELEASES_ROOT
    if source.resolve() != releases.resolve() / revision or source.is_symlink():
        raise Refusal('exact release path required')
    info = source.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise Refusal('root-owned immutable release directory required')
    if source_bundle_hash(source) != expected_hash: raise Refusal('published restore bundle hash mismatch')
    def git(*args):
        return subprocess.run(['git', '-C', str(source), '-c', 'safe.directory=' + str(source), *args],
                              check=True, capture_output=True, timeout=20,
                              env={'PATH': '/run/current-system/sw/bin',
                                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}).stdout
    if git('rev-parse', 'HEAD').decode().strip() != revision: raise Refusal('release checkout revision mismatch')
    git('merge-base', '--is-ancestor', revision, 'origin/master')
    for name in FILES:
        if git('show', revision + ':' + name) != (source / name).read_bytes():
            raise Refusal('unpublished restore source refused')


def execute(args, source: Path, setup) -> None:
    with setup.locked(STATE / 'lifecycle.lock'):
        setup.busy()
        if ((STATE / 'resume.json').exists()
                or Path('/run/homecompute/application-recovery-in-progress').exists()):
            raise Refusal('NAS or application recovery is still pending')
        installed = setup.installed(args.installed_hash)
        nas_spec = importlib.util.spec_from_file_location(
            'nas_recovery_backup', source / 'scripts/nas-recovery-backup.py')
        nas = importlib.util.module_from_spec(nas_spec); nas_spec.loader.exec_module(nas)
        config = nas.load_configuration(nas.CONFIG); nas.require_mount(config)
        nas.private_file(nas.PASSWORD)
        nas.root_directory(nas.STATE); nas.root_directory(nas.REPOSITORY)
        environment = {'PATH': '/run/current-system/sw/bin:' + installed['qemu_bin'],
                       'RESTIC_PASSWORD_FILE': str(nas.PASSWORD),
                       'RESTIC_REPOSITORY': str(nas.REPOSITORY),
                       'RESTIC_CACHE_DIR': str(nas.STATE / 'cache')}
        backup_receipt = STATE / 'last-backup.json'
        nas.private_file(backup_receipt)
        backup = json.loads(backup_receipt.read_bytes())
        if backup.get('snapshot') != args.snapshot or backup.get('scope') != '/srv/backups/agent-recovery':
            raise Refusal('snapshot does not match current completed NAS backup receipt')
        snapshots = json.loads(subprocess.check_output(['restic', 'snapshots', '--json', '--tag',
            'agent-recovery-archives'], env=environment, text=True, timeout=120))
        exact_snapshot(args.snapshot, snapshots)
        target = Path('/srv/restore-tests') / args.target_name
        if target.exists() or target.is_symlink(): raise Refusal('new target required')
        process = None; mountpoint = None; staged = None
        try:
            archive_path, process, mount_identity = _mount_archive(environment, args.snapshot, args.archive, target)
            mountpoint = Path(process.args[-1])
            staged = extract_assistant(archive_path, target, args.revision, args.snapshot, args.archive)
        finally:
            if process is not None and mountpoint is not None:
                _cleanup_mount(mountpoint, process, expected_identity=mount_identity)
        if staged is None: raise Refusal('assistant staging did not produce verification metadata')
        staged['installed_bundle_hash'] = installed['bundle_hash']
        staged['verified_at'] = dt.datetime.now(dt.UTC).isoformat()
        receipt = STATE / 'last-assistant-context-nas-verification.json'
        _atomic_json(receipt, staged)
        print(json.dumps({'status': 'assistant-context-only-verified', 'receipt': str(receipt),
                          'snapshot': args.snapshot, 'archive': args.archive,
                          'manifest_sha256': staged['manifest_sha256'],
                          'assistant_directory_count': staged['assistant_directory_count'],
                          'assistant_file_count': staged['assistant_file_count'],
                          'sqlite_database_count': staged['sqlite_database_count'],
                          'restore_fence': 'pending'}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan_parser = commands.add_parser('plan', help='print local source bundle hash; do not contact NAS')
    plan_parser.add_argument('--source', type=Path, default=ROOT)
    restore_parser = commands.add_parser('restore', help='verify and stage assistant context from exact NAS snapshot')
    restore_parser.add_argument('--revision', required=True)
    restore_parser.add_argument('--expected-source-hash', required=True)
    restore_parser.add_argument('--installed-hash', required=True)
    restore_parser.add_argument('--snapshot', required=True)
    restore_parser.add_argument('--archive', required=True)
    restore_parser.add_argument('--target-name', required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        print(json.dumps(plan(args.source)))
        return
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise Refusal('root on home-core required')
    os.umask(0o077)
    source = RELEASES_ROOT / args.revision
    published(source, args.revision, args.expected_source_hash)
    if not SNAPSHOT.fullmatch(args.snapshot) or not ARCHIVE.fullmatch(args.archive):
        raise Refusal('exact snapshot and archive required')
    if not re.fullmatch(r'assistant-context-[a-zA-Z0-9-]{1,40}', args.target_name):
        raise Refusal('bounded isolated target name required')
    _private_dir(MOUNT_PARENT); _private_dir(STATE)
    sys.path.insert(0, str(source / 'scripts'))
    spec = importlib.util.spec_from_file_location('nas_recovery_setup', source / 'scripts/setup-nas-recovery-backup.py')
    setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
    execute(args, source, setup)


if __name__ == '__main__':
    try: main()
    except (Refusal, OSError, subprocess.SubprocessError, TimeoutError) as error:
        print(type(error).__name__ + ': restore refused or incomplete', file=sys.stderr)
        raise SystemExit(1)
