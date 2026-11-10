#!/usr/bin/env python3
"""Prepare cold, private recovery archives; verify only into a new scratch directory."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import datetime as dt
import fcntl
import grp
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

ARCHIVES = Path('/srv/backups/agent-recovery')
SCRATCH = Path('/srv/restore-tests')
DEPLOY_LOCK = Path('/var/lib/homecompute/deploy.lock')
RECOVERY_MARKER = Path('/run/homecompute/application-recovery-in-progress')
RESUME_FILE = Path('/var/lib/homecompute/nas-recovery-backup/resume.json')
LOCAL_VM_BACKUP = 'restic-backups-agents-vm-bootstrap.service'
BROKER_DATABASE = Path('/srv/state/codex-broker/tasks.sqlite3')
MAIN_HTTP_TABLE = Path('/proc/net/tcp')
MAIN_HTTP_PORTS = {18003, 18005, 18006, 18007, 18816, 18826}
MIN_FREE_BYTES = 100 * 1024**3
MAX_ARCHIVE_BYTES = 512 * 1024**3
MAX_ARCHIVE_ENTRIES = 100000


class RecoveryDeferred(ValueError):
    """Known temporary activity; retry only after all stopped sources resume."""


CONTAINERS = ('homecompute-control-plane-caddy-1', 'homecompute-control-plane-litellm-1',
              'homecompute-automation-n8n-1', 'homecompute-mattermost-mattermost-1',
              'homecompute-immich-immich-server-1', 'homecompute-open-webui-open-webui-1',
              'omi-local-backend-1', 'homecompute-books_importer-shelfmark-1',
              'homecompute-books_importer-shelfmark-automated-1', 'homecompute-books_importer-cwa-1',
              'chessmate-backend-1', 'homecompute-image-studio-open-webui-1')
# Reclaim gateway static addresses before dynamic clients reconnect. WebUI's
# source-declared client address (.5) must precede the dynamic Omi client (.6).
RESUME_FIRST = ('homecompute-control-plane-litellm-1', 'homecompute-control-plane-caddy-1',
                'homecompute-open-webui-open-webui-1')
ACCESS_PROXY = 'homecompute-openclaw-access-proxy.service'
ACCESS_CERTIFICATE_PATH = 'homecompute-openclaw-access-certificate.path'
ACCESS_CERTIFICATE_SERVICE = 'homecompute-openclaw-access-certificate.service'
ACCESS_PROXY_CONTAINER = 'homecompute-openclaw-access-proxy'
UNITS = ('homecompute-codex-worker-api.service', 'homecompute-codex-broker.service',
         'homecompute-openclaw-household-task-reminders.timer', 'homecompute-openclaw-family-task-reminders.timer',
         'homecompute-openclaw-update-feed.timer', 'homecompute-openclaw-household-task-reminders.service',
         'homecompute-openclaw-family-task-reminders.service', 'homecompute-openclaw-update-feed.service',
         'homecompute-openclaw-task-feed.service',
         'homecompute-openclaw-adapter.service', 'homecompute-openclaw-location-reminders.service',
         'homecompute-openclaw-sites.service', ACCESS_CERTIFICATE_PATH, ACCESS_CERTIFICATE_SERVICE,
         ACCESS_PROXY, 'homecompute-openclaw-access.service',
         'homecompute-ha-actions.service',
         'homecompute-codex-vm.service', 'homecompute-agents-vm.service',
         'homecompute-toolbox-vm.service')
ASSISTANT_UNITS = (
    'homecompute-assistant-context-sync.timer', 'homecompute-assistant-context-maintenance.timer',
    'homecompute-assistant-context-improvements.timer',
    'homecompute-assistant-context-sync.service', 'homecompute-assistant-context-maintenance.service',
    'homecompute-assistant-context-improvements.service', 'homecompute-assistant-context.service',
)
ASSISTANT_TIMER_UNITS = tuple(unit for unit in ASSISTANT_UNITS if unit.endswith('.timer'))
ASSISTANT_JOB_UNITS = tuple(unit for unit in ASSISTANT_UNITS
                            if unit.endswith('.service') and unit != 'homecompute-assistant-context.service')
ASSISTANT_CONTEXT_UNIT = 'homecompute-assistant-context.service'
ASSISTANT_DIRECTORIES = {
    'assistant-context/state': Path('/srv/state/assistant-context'),
    'assistant-context/collector': Path('/var/lib/homecompute-assistant'),
    'assistant-context/improvements': Path('/var/lib/homecompute-assistant-improvements'),
    'assistant-context/policy': Path('/var/lib/homecompute-assistant-policy'),
    'assistant-context/policy-controller': Path('/var/lib/homecompute-assistant-policy-controller'),
    'assistant-context/delivery-auth': Path('/etc/homecompute-assistant-context'),
}
ASSISTANT_FILES = {
    'assistant-context/clients.json': Path('/etc/homecompute/assistant-context-clients.json'),
    'assistant-context/improvement-grant.json': Path('/etc/homecompute/assistant-improvement-grant.json'),
    'assistant-context/embedding-credential.json': Path('/etc/homecompute/assistant-context-embedding.json'),
    'assistant-context/briefing-credential.json': Path('/etc/homecompute/assistant-context-briefing.json'),
    'assistant-context/delegation-identity': Path('/etc/homecompute/assistant-context-worker/id_ed25519'),
    'assistant-context/delegation-known-hosts': Path('/etc/homecompute/assistant-context-worker/known_hosts'),
    'assistant-context/improvement-github-token': Path('/etc/homecompute/assistant-improvement-github-token'),
    'assistant-context/policy-health-token': Path('/etc/homecompute/assistant-context-policy-health-token'),
}
POSTGRES = {'control-plane': 'homecompute-control-plane-postgres-1',
            'mattermost': 'homecompute-mattermost-postgres-1', 'immich': 'homecompute-immich-database-1',
            'chessmate': 'chessmate-db-1'}
# Database data directories are deliberately absent. PostgreSQL is dumped and
# n8n SQLite is copied through its transactional backup API, never live cp.
DIRECTORIES = {
    'control-plane/caddy-data': Path('/srv/state/control-plane/caddy-data'),
    'control-plane/caddy-config': Path('/srv/state/control-plane/caddy-config'),
    'mattermost/config': Path('/srv/state/mattermost/config'),
    'mattermost/data': Path('/srv/state/mattermost/data'),
    'mattermost/plugins': Path('/srv/state/mattermost/plugins'),
    'mattermost/client-plugins': Path('/srv/state/mattermost/client-plugins'),
    'mattermost/secrets': Path('/srv/state/mattermost/secrets'),
    'agents-vm': Path('/srv/state/agents-vm'),
    'toolbox-vm': Path('/srv/state/toolbox-vm'),
    'codex-vm': Path('/srv/state/codex-vm'),
    'codex-config': Path('/etc/homecompute/codex-vm'),
    'openclaw/config': Path('/var/lib/homecompute-openclaw/config'),
    'openclaw/communication': Path('/var/lib/homecompute-openclaw/communication'),
    'access': Path('/var/lib/homecompute-access'),
    'sites/config': Path('/var/lib/homecompute-sites/config'),
    'sites/operations': Path('/var/lib/homecompute-sites/operations'),
    'ha-actions': Path('/var/lib/homecompute-ha-actions'),
    'codex-broker/state': Path('/srv/state/codex-broker'),
    'codex-broker/config': Path('/opt/homecompute/codex-broker/config'),
    'codex-broker/secrets': Path('/opt/homecompute/codex-broker/secrets'),
    'open-webui': Path('/srv/state/open-webui/data'),
    'image-studio': Path('/srv/state/image-studio/data'),
    'omi-data': Path('/var/lib/docker/volumes/omi-local_omi-data/_data'),
    'books-importer': Path('/srv/state/books_importer'),
    'chessmate/certificates': Path('/home/mads/projects/chessmate/config/certificates'),
}
EXTRA_FILES = {'open-webui/embedding-api-key': Path('/srv/state/open-webui/embedding-api-key'),
               'image-studio/webui-secret-key': Path('/srv/state/image-studio/webui-secret-key'),
               'chessmate/runtime.env': Path('/home/mads/projects/chessmate/config/.env')}
N8N = Path('/srv/state/automation/n8n')
VM_DISKS = ('agents-vm/agents.qcow2', 'codex-vm/root.qcow2', 'codex-vm/work.qcow2',
            'toolbox-vm/root.qcow2')
SEED_PATTERN = r'/nix/store/[0-9a-z]{32}-homecompute-agents-seed\.iso'
TOOLBOX_SEED_PATTERN = r'/nix/store/[0-9a-z]{32}-homecompute-toolbox-seed\.iso'


def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop('timeout', 180), **kwargs)


def private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('recovery directory must be a real root-only directory')


def sqlite_backup(source: Path, destination: Path, *, readonly_source: bool = True) -> None:
    if source.is_symlink() or not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError('expected existing SQLite source and new destination')
    source_uri = source.resolve().as_uri() + '?mode=ro' if readonly_source else str(source)
    with closing(sqlite3.connect(source_uri, uri=readonly_source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        if copy.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('SQLite recovery copy failed integrity validation')
    destination.chmod(0o600)


def assistant_sqlite_backups(directories: dict[str, Path], staging: Path) -> list[tuple[str, Path, Path]]:
    """Snapshot quiesced SQLite DB/WAL pairs into private writable staging, then online-back them up."""
    backups = []
    backup_root = staging / 'assistant-sqlite'
    source_root = staging / 'assistant-sqlite-inputs'
    for index, (archive_relative, source) in enumerate(assistant_sqlite_sources(directories)):
        destination = backup_root / f'{index:04d}.sqlite3'
        staged_source = source_root / f'{index:04d}.sqlite3'
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged_source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        def signature(path: Path) -> tuple[int, int, int, int, int] | None:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError('assistant SQLite source or sidecar is not a stable regular file')
            return (metadata.st_dev, metadata.st_ino, metadata.st_size,
                    metadata.st_mtime_ns, metadata.st_ctime_ns)

        source_signature = signature(source)
        if source_signature is None:
            raise ValueError('assistant SQLite source disappeared during snapshot')
        sidecar_paths = {suffix: Path(str(source) + suffix) for suffix in ('-wal', '-journal')}
        sidecar_signatures = {suffix: signature(path) for suffix, path in sidecar_paths.items()}
        present_sidecars = [suffix for suffix, value in sidecar_signatures.items() if value is not None]
        if len(present_sidecars) > 1:
            raise ValueError('assistant SQLite has conflicting transaction sidecars')
        staged_paths = [staged_source, *(Path(str(staged_source) + suffix) for suffix in present_sidecars)]
        if any(path.exists() or path.is_symlink() for path in staged_paths):
            raise ValueError('assistant SQLite staging path already exists')
        try:
            shutil.copyfile(source, staged_source)
            staged_source.chmod(0o600)
            for suffix in present_sidecars:
                staged_sidecar = Path(str(staged_source) + suffix)
                shutil.copyfile(sidecar_paths[suffix], staged_sidecar)
                staged_sidecar.chmod(0o600)
            if (signature(source) != source_signature
                    or {suffix: signature(path) for suffix, path in sidecar_paths.items()} != sidecar_signatures):
                raise ValueError('assistant SQLite source changed during snapshot')
            sqlite_backup(staged_source, destination, readonly_source=False)
            if (signature(source) != source_signature
                    or {suffix: signature(path) for suffix, path in sidecar_paths.items()} != sidecar_signatures):
                raise ValueError('assistant SQLite source changed during online backup')
        finally:
            for path in staged_paths:
                path.unlink(missing_ok=True)
        try:
            staged_source.parent.rmdir()
        except OSError:
            pass
        backups.append((archive_relative, source, destination))
    return backups


def assistant_sqlite_archive_filter(info: tarfile.TarInfo, source: Path) -> tarfile.TarInfo | None:
    """Use original DB ownership metadata without chowning root-private staging files."""
    original = source.lstat()
    if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
        raise ValueError('assistant SQLite archive source changed during snapshot')
    info.uid = original.st_uid
    info.gid = original.st_gid
    info.mode = stat.S_IMODE(original.st_mode)
    info.uname = ''
    info.gname = ''
    return archive_filter(info)


def save_resume(stopped: list[tuple[str, str]]) -> None:
    private_directory(RESUME_FILE.parent, create=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=RESUME_FILE.parent, delete=False) as stream:
        json.dump(stopped, stream); stream.flush(); os.fsync(stream.fileno())
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    os.replace(temporary, RESUME_FILE)


def resume(stopped: list[tuple[str, str]] | None = None) -> None:
    if stopped is None:
        if not RESUME_FILE.exists():
            return
        info = RESUME_FILE.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 16384):
            raise ValueError('private bounded resume journal required')
        stopped = json.loads(RESUME_FILE.read_bytes())
    allowed = {('unit', name) for name in (*UNITS, *ASSISTANT_UNITS)} | {('container', name) for name in CONTAINERS}
    if (not isinstance(stopped, list) or len(stopped) > len(allowed)
            or any(not isinstance(row, (list, tuple)) or len(row) != 2 or tuple(row) not in allowed for row in stopped)):
        raise ValueError('resume journal contains an unreviewed resource')
    reversed_resources = [tuple(row) for row in reversed(stopped)]
    priority = [('container', name) for name in RESUME_FIRST if ('container', name) in reversed_resources]
    ordered = [*priority, *[row for row in reversed_resources if row not in priority]]
    failures = []
    proxy_restored = ('unit', ACCESS_PROXY) not in ordered
    for kind, name in ordered:
        if kind == 'unit' and name == ACCESS_CERTIFICATE_PATH and not proxy_restored:
            failures.append(name)
            continue  # Never re-arm renewal while a restored proxy is unready.
        try:
            run(['docker', 'start', name] if kind == 'container' else ['systemctl', 'start', name], timeout=180)
            if kind == 'unit' and name == ACCESS_PROXY:
                approval_proxy_ready()
                proxy_restored = True
        except (OSError, subprocess.SubprocessError, RuntimeError):
            failures.append(name)
    if failures:
        raise RuntimeError('recovery preparation could not resume: ' + ', '.join(failures))
    RESUME_FILE.unlink(missing_ok=True)


def approval_proxy_ready() -> None:
    # Use the existing private Unix admin socket, preserving the service's
    # AF_UNIX-only backup sandbox and all TLS/auth policy. Reloading the same
    # config also loads certificates changed while the renewal path was paused.
    deadline = time.monotonic() + 30
    while True:
        try:
            state = run(['docker', 'inspect', ACCESS_PROXY_CONTAINER, '--format', '{{.State.Running}}'], text=True, timeout=10).stdout.strip()
            if state == 'true':
                run(['docker', 'exec', ACCESS_PROXY_CONTAINER, 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile',
                     '--adapter', 'caddyfile', '--address', 'unix//tmp/caddy-admin.sock'], timeout=10)
                return
        except (OSError, subprocess.SubprocessError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('private approval proxy readiness failed')
        time.sleep(1)


def unit_state(unit: str) -> str:
    output = run(['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'MainPID'], text=True).stdout
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in {'active', 'inactive', 'failed'}:
        raise ValueError('recovery source unit inspection is unavailable or transitional')
    if unit.endswith('.service'):
        pid = fields.get('MainPID', '')
        if not pid.isdecimal() or fields['ActiveState'] != 'active' and pid != '0':
            raise ValueError('recovery source service has an uncertain process state')
    return fields['ActiveState']


def assistant_path_ancestors(path: Path) -> None:
    """Refuse redirected optional source roots and credential paths."""
    for ancestor in reversed(path.parents):
        if ancestor.exists() and ancestor.is_symlink():
            raise ValueError('assistant recovery source has a symlinked ancestor')


def assistant_recovery_sources() -> tuple[dict[str, Path], dict[str, Path]]:
    """Include retained opt-in state without requiring disabled services to exist."""
    directories, files = {}, {}
    for name, path in ASSISTANT_DIRECTORIES.items():
        if not path.exists() and not path.is_symlink():
            continue
        assistant_path_ancestors(path)
        info = path.lstat()
        managed_policy = name == 'assistant-context/policy'
        policy_group = grp.getgrnam('homecompute-context').gr_gid if managed_policy else None
        if (not stat.S_ISDIR(info.st_mode)
                or (managed_policy and (info.st_uid != 0 or info.st_gid != policy_group
                                        or stat.S_IMODE(info.st_mode) != 0o750))
                or (not managed_policy and info.st_mode & 0o077)):
            raise ValueError('assistant recovery directory is unsafe')
        for parent, child_dirs, child_files in os.walk(path, followlinks=False):
            directory = Path(parent)
            if directory.is_symlink():
                raise ValueError('assistant recovery tree contains a symlink')
            for child_name in child_dirs:
                child = directory / child_name
                metadata = child.lstat()
                if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != info.st_uid
                        or (managed_policy and (metadata.st_mode & 0o077 or metadata.st_gid != info.st_gid))
                        or (not managed_policy and metadata.st_mode & 0o077)):
                    raise ValueError('assistant recovery tree contains an unsafe directory')
            for child_name in child_files:
                child = directory / child_name
                metadata = child.lstat()
                policy_projection = managed_policy and child_name in {'assistant-briefings.json', 'improvements-status.json'}
                allowed_policy_mode = (stat.S_IMODE(metadata.st_mode) == 0o640 and metadata.st_gid == policy_group
                                       if policy_projection else not metadata.st_mode & 0o077)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or not allowed_policy_mode or metadata.st_uid != info.st_uid):
                    raise ValueError('assistant recovery tree contains an unsafe file')
        directories[name] = path
    for name, path in ASSISTANT_FILES.items():
        if not path.exists() and not path.is_symlink():
            continue
        assistant_path_ancestors(path)
        info = path.lstat()
        mode_ok = (not info.st_mode & 0o022 and
                   (name == 'assistant-context/delegation-known-hosts' or not info.st_mode & 0o077))
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or not mode_ok:
            raise ValueError('assistant recovery credential file is unsafe')
        files[name] = path
    if 'assistant-context/state' in directories and 'assistant-context/clients.json' not in files:
        raise ValueError('assistant state requires its declared external credentials in recovery coverage')
    return directories, files


def assistant_sqlite_sources(directories: dict[str, Path]) -> list[tuple[str, Path]]:
    """Find declared private SQLite databases for online-backup API snapshots."""
    databases = []
    for archive_name, root in directories.items():
        for parent, _subdirs, names in os.walk(root, followlinks=False):
            for name in names:
                source = Path(parent) / name
                if source.is_symlink():
                    raise ValueError('assistant SQLite source symlink refused')
                info = source.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('assistant SQLite source file refused')
                with source.open('rb') as stream:
                    header = stream.read(16)
                if header == b'SQLite format 3\0':
                    databases.append((str(Path(archive_name) / source.relative_to(root)), source))
                elif name.endswith(('.sqlite3', '.sqlite', '.db')):
                    raise ValueError('declared assistant database is not SQLite')
    return sorted(databases)


def assistant_recovery_units() -> tuple[str, ...]:
    # Disabled optional units may legitimately not be installed. Transitional or
    # unverifiable loaded units remain hard failures through unit_state().
    loaded = []
    for unit in ASSISTANT_UNITS:
        result = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState'],
            capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError('assistant recovery unit inspection failed')
        if result.stdout.strip() == 'LoadState=not-found':
            continue
        if result.stdout.strip() != 'LoadState=loaded':
            raise ValueError('assistant recovery unit inspection unavailable')
        loaded.append(unit)
    return tuple(loaded)


def worker_idle() -> None:
    if BROKER_DATABASE.is_symlink() or not BROKER_DATABASE.is_file():
        raise ValueError('read-only broker state is unavailable')
    with closing(sqlite3.connect(BROKER_DATABASE.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)) as db:
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 0.5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        states = {row[0] for row in db.execute('SELECT state FROM tasks GROUP BY state')}
    if states - {'pending', 'queued', 'running', 'claimed', 'publishing', 'review', 'completed', 'failed', 'cancelled'}:
        raise ValueError('broker contains an unreviewed worker state')
    if states & {'running', 'claimed', 'publishing'}:
        raise RecoveryDeferred('worker activity requires recovery preparation to defer')


def private_browsers_idle() -> None:
    active = run(['docker', 'ps', '--filter', 'name=homecompute-site-view-', '--format', '{{.Names}}'], text=True, timeout=10).stdout
    if active.strip():
        raise RecoveryDeferred('close active private browser sessions before cold recovery preparation')


def main_http_connections(table: str) -> int:
    # Match the reviewed speech-boundary drain's exact IPv4 tunnel endpoints.
    # Conservative states also defer idle keep-alives and half-closed sockets;
    # no request is terminated to make a scheduled backup proceed.
    rows = table.splitlines()
    if not rows or 'local_address' not in rows[0] or 'st' not in rows[0]:
        raise ValueError('main HTTP connection inspection unavailable')
    count = 0
    for row in rows[1:]:
        fields = row.split()
        if (len(fields) < 4 or not re.fullmatch(r'[0-9A-Fa-f]{8}:[0-9A-Fa-f]{4}', fields[1])
                or not re.fullmatch(r'[0-9A-Fa-f]{2}', fields[3])):
            raise ValueError('unknown main HTTP connection state')
        address, port = fields[1].split(':')
        if (socket.inet_ntoa(bytes.fromhex(address)[::-1]) == '172.28.200.1'
                and int(port, 16) in MAIN_HTTP_PORTS and fields[3].upper() not in {'06', '07', '0A'}):
            count += 1
    return count


def main_http_idle() -> None:
    for attempt in range(3):
        with MAIN_HTTP_TABLE.open() as stream:
            table = stream.read(1024 * 1024 + 1)
        if len(table) > 1024 * 1024:
            raise ValueError('main HTTP connection inspection exceeds its bounded budget')
        if main_http_connections(table):
            raise RecoveryDeferred('main HTTP activity requires recovery preparation to defer')
        if attempt < 2: time.sleep(1)


def validate_volumes() -> None:
    name = 'omi-local_omi-data'; mountpoint = str(DIRECTORIES['omi-data'])
    output = run(['docker', 'volume', 'inspect', name], text=True, timeout=10).stdout
    values = json.loads(output) if len(output) <= 32768 else []
    if (not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict) or values[0].get('Name') != name
            or values[0].get('Driver') != 'local' or values[0].get('Scope') != 'local'
            or values[0].get('Mountpoint') != mountpoint or values[0].get('Options') not in (None, {})):
        raise ValueError('exact local Omi application volume is required')
    output = run(['docker', 'inspect', 'omi-local-backend-1', '--format', '{{json .Mounts}}'], text=True, timeout=10).stdout
    rows = json.loads(output) if len(output) <= 32768 else []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('bounded Omi container mount metadata required')
    found = [row for row in rows if row.get('Destination') == '/data']
    if len(found) != 1 or any(found[0].get(key) != value for key, value in
            {'Name': name, 'Source': mountpoint, 'Type': 'volume'}.items()):
        raise ValueError('Omi container no longer owns the declared data volume')


@contextmanager
def quiesced():
    """Only restart services that were active before this attempt, even on error."""
    stopped = []
    if RESUME_FILE.exists() or RESUME_FILE.is_symlink():
        raise ValueError('a previous recovery resume journal requires reconciliation')
    try:
        worker_idle()
        main_http_idle()
        # An active private browser can keep writing profile state independently
        # of its owner service. Defer rather than silently claim consistency.
        private_browsers_idle()
        optional_directories, _ = assistant_recovery_sources()
        optional_units = assistant_recovery_units()
        if optional_units and not optional_directories:
            raise ValueError('loaded assistant units have no declared retained state')
        for unit in (unit for unit in optional_units if unit in ASSISTANT_TIMER_UNITS):
            if unit_state(unit) == 'active':
                stopped.append(('unit', unit))
                save_resume(stopped)
                run(['systemctl', 'stop', unit], timeout=180)
                if unit_state(unit) != 'inactive':
                    raise ValueError('a recovery source service did not stop cleanly')
        for unit in (unit for unit in optional_units if unit in ASSISTANT_JOB_UNITS):
            if unit_state(unit) == 'active':
                raise RecoveryDeferred('assistant source maintenance is active; recovery preparation deferred')
        if optional_units:
            main_http_idle()
        if ASSISTANT_CONTEXT_UNIT in optional_units and unit_state(ASSISTANT_CONTEXT_UNIT) == 'active':
            stopped.append(('unit', ASSISTANT_CONTEXT_UNIT))
            save_resume(stopped)
            run(['systemctl', 'stop', ASSISTANT_CONTEXT_UNIT], timeout=180)
            if unit_state(ASSISTANT_CONTEXT_UNIT) != 'inactive':
                raise ValueError('assistant context service did not stop cleanly')
            main_http_idle()
        for unit in UNITS:
            if unit_state(unit) == 'active':
                stopped.append(('unit', unit))
                save_resume(stopped)
                run(['systemctl', 'stop', unit], timeout=180)
                if unit_state(unit) != 'inactive':
                    raise ValueError('a recovery source service did not stop cleanly')
            if unit == 'homecompute-codex-broker.service':
                # Claims are disabled now. Catch a claim committed between the
                # first idle read and stopping the claim API/broker, before VMs.
                worker_idle()
            if unit == 'homecompute-openclaw-access.service':
                private_browsers_idle()  # Owner routes are stopped; close the launch race before VMs.
        for container in CONTAINERS:
            result = run(['docker', 'inspect', container, '--format', '{{.State.Running}}'], text=True).stdout
            running = result.strip()
            if running not in {'true', 'false'}:
                raise ValueError('recovery source container running state is unavailable')
            if running == 'true':
                stopped.append(('container', container))
                save_resume(stopped)
                run(['docker', 'stop', '--time', '60', container], timeout=90)
        yield
    finally:
        resume(stopped)


def archive_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    # QMP sockets, transient logs and partial backups are not recoverable state.
    if info.name.endswith(('.sock', '.log', '.partial')):
        return None
    if info.name in {'state/agents-vm/seed.iso', 'state/toolbox-vm/seed.iso'}:
        return None  # Archive the exact owning-unit seed, not an incidental file.
    if any(info.name == 'state/omi-data/' + name or info.name.startswith('state/omi-data/' + name + '/')
           for name in ('audio', 'diary')):
        return None  # NAS-owned originals need their independent media backup.
    if info.issym() and info.linkname.startswith('/nix/store/'):
        return None
    if info.ischr() or info.isblk() or info.isfifo() or not (info.isfile() or info.isdir() or info.issym()):
        raise ValueError('unreviewed special file in recovery source')
    return info


def assistant_archive_filter(info: tarfile.TarInfo, database_members: set[str]) -> tarfile.TarInfo | None:
    """Skip live SQLite files/sidecars; their Online Backup copies are archived separately."""
    if info.name in database_members or any(info.name in {name + suffix for suffix in ('-wal', '-shm', '-journal')}
                                             for name in database_members):
        return None
    return archive_filter(info)


def seed_path(script: str) -> Path:
    matches = re.findall(r'file=(' + SEED_PATTERN + r'),if=virtio,format=raw,readonly=on', script)
    if len(matches) != 1:
        raise ValueError('exactly one immutable owning-unit agents seed is required')
    return Path(matches[0])


def agents_seed() -> Path:
    text = run(['systemctl', 'show', 'homecompute-agents-vm.service', '-p', 'ExecStart', '--value'], text=True, timeout=10).stdout
    paths = re.findall(r'\bpath=([^ ;]+)', text) if len(text) <= 32768 else []
    if len(paths) != 1:
        raise ValueError('agents owning unit command is unavailable')
    command = Path(paths[0]); info = command.stat()
    if (not str(command).startswith('/nix/store/') and command != Path('/etc/homecompute/agents-vm-start-16g')
            or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
        raise ValueError('protected owning-unit startup script required')
    seed = seed_path(command.read_text()); info = seed.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222 or not 0 < info.st_size <= 32 * 1024**2:
        raise ValueError('immutable root-owned bounded seed ISO required')
    return seed


def toolbox_seed() -> Path:
    text = run(['systemctl', 'show', 'homecompute-toolbox-vm.service', '-p', 'ExecStart', '--value'], text=True, timeout=10).stdout
    paths = re.findall(r'\bpath=([^ ;]+)', text) if len(text) <= 32768 else []
    if len(paths) != 1:
        raise ValueError('toolbox owning unit command is unavailable')
    command = Path(paths[0]); info = command.stat()
    if (not str(command).startswith('/nix/store/') or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
        raise ValueError('protected toolbox owning-unit startup script required')
    matches = re.findall(r'file=(' + TOOLBOX_SEED_PATTERN + r'),if=virtio,format=raw,readonly=on', command.read_text())
    if len(matches) != 1:
        raise ValueError('exactly one immutable owning-unit toolbox seed is required')
    seed = Path(matches[0]); info = seed.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222 or not 0 < info.st_size <= 32 * 1024**2:
        raise ValueError('immutable root-owned bounded toolbox seed ISO required')
    return seed


def space_required() -> int:
    total = 1024**3  # Dump/staging allowance in addition to the measured files.
    entries = 0
    assistant_dirs, assistant_files = assistant_recovery_sources()
    for source in [*DIRECTORIES.values(), *assistant_dirs.values(), N8N]:
        if source.is_symlink() or not source.is_dir():
            raise ValueError('declared recovery source is absent or symlinked')
        for parent, directories, files in os.walk(source, followlinks=False):
            if source == DIRECTORIES.get('omi-data') and Path(parent) == source:
                directories[:] = [name for name in directories if name not in {'audio', 'diary'}]
            entries += len(directories) + len(files)
            if entries > MAX_ARCHIVE_ENTRIES:
                raise ValueError('recovery source entry budget requires review')
            directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
            for name in files:
                info = (Path(parent) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError('recovery source size budget requires review')
    for source in EXTRA_FILES.values():
        if source.is_symlink() or not source.is_file(): raise ValueError('declared recovery file is absent or symlinked')
        total += source.stat().st_size
        if total > MAX_ARCHIVE_BYTES: raise ValueError('recovery source size budget requires review')
    total += sum(path.stat().st_size for path in assistant_files.values())
    # A consistent SQLite copy exists once in private staging and once in the
    # final uncompressed tar while the archive is being written.
    total += sum(source.stat().st_size for _, source in assistant_sqlite_sources(assistant_dirs))
    return total + MIN_FREE_BYTES


def prepare() -> Path:
    private_directory(ARCHIVES, create=True)
    name = 'homecompute-applications-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.tar'
    final = ARCHIVES / name
    temporary = ARCHIVES / (name + '.partial')
    with DEPLOY_LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryDeferred('another deployment or recovery operation holds the preparation lock') from None
        validate_volumes()
        if shutil.disk_usage(ARCHIVES).free < space_required():
            raise ValueError('recovery needs space for all source files plus 100 GiB remaining headroom')
        marker = os.open(RECOVERY_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(marker)
        try:
            # The local VM backup checks our marker before stopping the guest.
            # After creating it, reject an already-started local backup, so its
            # cleanup cannot restart the VM halfway through this cold archive.
            local = run(['systemctl', 'show', LOCAL_VM_BACKUP, '-p', 'ActiveState'], text=True).stdout
            if any('ActiveState=' + state in local for state in ('active', 'activating', 'deactivating')):
                raise RecoveryDeferred('local VM backup is running; cold recovery preparation deferred')
            with tempfile.TemporaryDirectory(prefix='.application-recovery-', dir=ARCHIVES) as staging, quiesced():
                directory = Path(staging)
                seed = agents_seed(); staged_seed = directory / 'agents-seed.iso'
                shutil.copyfile(seed, staged_seed)
                toolbox_boot_seed = toolbox_seed(); staged_toolbox_seed = directory / 'toolbox-seed.iso'
                shutil.copyfile(toolbox_boot_seed, staged_toolbox_seed)
                sqlite_backup(N8N / 'database.sqlite', directory / 'n8n.sqlite')
                config = N8N / 'config'
                if config.is_symlink() or not config.is_file():
                    raise ValueError('n8n encryption configuration is unavailable')
                shutil.copyfile(config, directory / 'n8n-config.json')
                for name, container in POSTGRES.items():
                    output = directory / (name + '-postgres.sql.gz')
                    raw = directory / (name + '-postgres.sql.partial')
                    command = ['docker', 'exec', container, 'sh', '-ceu',
                               'exec pg_dumpall --clean --if-exists --username="$POSTGRES_USER"']
                    # A private file bounds dump memory and lets subprocess
                    # enforce a real wall-clock timeout, including stalled I/O.
                    with raw.open('xb') as stream:
                        subprocess.run(command, stdout=stream, stderr=subprocess.DEVNULL, timeout=180, check=True)
                    with raw.open('rb') as data, output.open('xb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
                        shutil.copyfileobj(data, compressed, length=1024 * 1024)
                    raw.unlink()
                with tarfile.open(temporary, 'x', dereference=False) as archive:
                    for path in directory.iterdir():
                        if path not in {staged_seed, staged_toolbox_seed}:
                            archive.add(path, arcname='databases/' + path.name, filter=archive_filter)
                    assistant_dirs, assistant_files = assistant_recovery_sources()
                    assistant_db_backups = assistant_sqlite_backups(assistant_dirs, directory)
                    assistant_db_members = {'state/' + relative for relative, _source, _backup in assistant_db_backups}
                    for relative, source in {**DIRECTORIES, **assistant_dirs, **{('n8n/' + name): N8N / name
                            for name in ('binaryData', 'nodes', 'ssh') if (N8N / name).is_dir()}}.items():
                        if source.is_symlink() or not source.is_dir():
                            raise ValueError('declared recovery source is absent or symlinked: ' + relative)
                        member_filter = (lambda info, db_members=assistant_db_members:
                            assistant_archive_filter(info, db_members)) if relative in assistant_dirs else archive_filter
                        archive.add(source, arcname='state/' + relative, filter=member_filter)
                    for relative, source, backup in assistant_db_backups:
                        archive.add(backup, arcname='state/' + relative,
                                    filter=lambda info, original=source: assistant_sqlite_archive_filter(info, original))
                    archive.add(staged_seed, arcname='state/agents-vm/seed.iso')
                    archive.add(staged_toolbox_seed, arcname='state/toolbox-vm/seed.iso')
                    for relative, source in {**EXTRA_FILES, **assistant_files}.items():
                        archive.add(source, arcname='state/' + relative, filter=archive_filter)
                    # Original media is owned by the NAS and must have its own
                    # independent backup; copying it back to that NAS is not DR.
                    manifest = {'schema_version': 1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                'postgres': sorted(POSTGRES), 'sqlite': ['n8n'], 'vms': ['agents-vm', 'codex-vm', 'toolbox-vm'],
                                'media_included': False, 'host_age_identity_included': False,
                                'nix_store_symlinks_included': False,
                                'assistant_context': {'directories': sorted(assistant_dirs),
                                                      'files': sorted(assistant_files),
                                                      'sqlite_databases': sorted(relative for relative, _, _ in assistant_db_backups)},
                                'agents_seed': {'archive_path': 'state/agents-vm/seed.iso', 'source': str(seed),
                                                'sha256': hashlib.sha256(staged_seed.read_bytes()).hexdigest()},
                                'toolbox_seed': {'archive_path': 'state/toolbox-vm/seed.iso', 'source': str(toolbox_boot_seed),
                                                 'sha256': hashlib.sha256(staged_toolbox_seed.read_bytes()).hexdigest()}}
                    entry = directory / 'manifest.json'
                    entry.write_text(json.dumps(manifest, sort_keys=True))
                    archive.add(entry, arcname='manifest.json')
            temporary.chmod(0o600)
            with temporary.open('rb') as source:
                os.fsync(source.fileno())
            # Publish without replacing an existing recovery generation, even
            # if clocks or a concurrent operator reuse the timestamp.
            os.link(temporary, final, follow_symlinks=False)
            temporary.unlink()
            # Retain one previous completed generation for rollback. Other
            # historical agent archives are never deleted by this helper.
            for old in sorted(ARCHIVES.glob('homecompute-applications-*.tar'), reverse=True)[2:]:
                if old.is_file() and not old.is_symlink():
                    old.unlink()
            return final
        finally:
            temporary.unlink(missing_ok=True)
            if not RESUME_FILE.exists():
                RECOVERY_MARKER.unlink()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    result, total = [], 0
    for item in archive:
        result.append(item)
        total += item.size
        if len(result) > MAX_ARCHIVE_ENTRIES or total > MAX_ARCHIVE_BYTES:
            raise ValueError('recovery archive exceeds its reviewed extraction budget')
        parts = Path(item.name).parts
        if item.name.startswith('/') or '..' in parts or not (item.isfile() or item.isdir() or item.issym()):
            raise ValueError('unsafe recovery archive member')
        if item.issym() and (item.linkname.startswith('/') or '..' in Path(item.linkname).parts):
            # Nix store symlinks are useful configuration references, but never
            # followed during isolated verification or extraction.
            raise ValueError('external symlinks require manual recovery review')
        if item.name.startswith('state/assistant-context/') and item.issym():
            raise ValueError('assistant recovery state cannot contain symlinks')
    return result


def verify(archive_path: Path, target: Path) -> dict:
    if archive_path.is_symlink() or not archive_path.is_file() or target.exists() or target.is_symlink():
        raise ValueError('expected private existing archive and new isolated restore directory')
    if not target.is_absolute() or target.parent.resolve() != SCRATCH.resolve() or target.name in {'.', '..'}:
        raise ValueError('restore verification is confined to a new direct child of /srv/restore-tests')
    target.mkdir(mode=0o700)
    with tarfile.open(archive_path, 'r:') as archive:
        records = safe_members(archive)
        archive.extractall(target, members=records, filter='data')
    return verify_restored(target)


def verify_extracted(archive_path: Path, target: Path) -> dict:
    if archive_path.is_symlink() or not archive_path.is_file() or target.is_symlink() or not target.is_dir():
        raise ValueError('existing archive and completed isolated extraction required')
    if not target.is_absolute() or target.parent.resolve() != SCRATCH.resolve():
        raise ValueError('existing verification is confined to private restore scratch')
    with tarfile.open(archive_path, 'r:') as archive:
        records = safe_members(archive)
        manifest = next(item for item in records if item.name == 'manifest.json')
        if manifest.size > 2 * 1024**2 or archive.extractfile(manifest).read() != (target / 'manifest.json').read_bytes():
            raise ValueError('extracted manifest differs from selected archive')
        for item in records:
            path = target / item.name
            if any(parent.is_symlink() for parent in path.parents if parent != target and target in parent.parents):
                raise ValueError('symlinked extracted ancestor')
            info = path.lstat()
            if ((item.isfile() and (not stat.S_ISREG(info.st_mode) or info.st_size != item.size))
                    or (item.isdir() and not stat.S_ISDIR(info.st_mode))
                    or (item.issym() and (not stat.S_ISLNK(info.st_mode) or os.readlink(path) != item.linkname))):
                raise ValueError('extracted archive member is incomplete')
    return verify_restored(target)


def verify_restored(target: Path) -> dict:
    if not target.is_absolute() or target.parent.resolve() != SCRATCH.resolve() or target.is_symlink() or not target.is_dir():
        raise ValueError('completed private restore directory required')
    manifest_path = target / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 2 * 1024**2:
        raise ValueError('bounded regular recovery manifest required')
    manifest = json.loads(manifest_path.read_text())
    legacy_vms = ['agents-vm', 'codex-vm']
    current_vms = ['agents-vm', 'codex-vm', 'toolbox-vm']
    manifest_vms = manifest.get('vms')
    if (manifest.get('schema_version') != 1 or manifest.get('postgres') != sorted(POSTGRES)
            or manifest_vms not in (legacy_vms, current_vms) or manifest.get('sqlite') != ['n8n']):
        raise ValueError('recovery manifest scope mismatch')
    assistant = manifest.get('assistant_context')
    if assistant is not None:
        if (not isinstance(assistant, dict) or set(assistant) not in
                ({'directories', 'files'}, {'directories', 'files', 'sqlite_databases'})
                or not isinstance(assistant['directories'], list) or not isinstance(assistant['files'], list)
                or any(name not in ASSISTANT_DIRECTORIES for name in assistant['directories'])
                or any(name not in ASSISTANT_FILES for name in assistant['files'])
                or len(assistant['directories']) != len(set(assistant['directories']))
                or len(assistant['files']) != len(set(assistant['files']))):
            raise ValueError('assistant recovery manifest scope mismatch')
        for name in assistant['directories']:
            path = target / 'state' / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError('declared assistant recovery state is missing')
            managed_policy = name == 'assistant-context/policy'
            for parent, child_dirs, child_files in os.walk(path, followlinks=False):
                directory = Path(parent)
                mode = stat.S_IMODE(directory.stat().st_mode)
                if (directory.is_symlink()
                        or (managed_policy and directory == path and mode != 0o750)
                        or (not (managed_policy and directory == path) and mode & 0o077)):
                    raise ValueError('restored assistant directory is not private')
                for child_name in child_dirs:
                    child = directory / child_name
                    if child.is_symlink() or not child.is_dir() or child.stat().st_mode & 0o077:
                        raise ValueError('restored assistant directory tree is unsafe')
                for child_name in child_files:
                    child = directory / child_name
                    info = child.lstat()
                    policy_projection = managed_policy and child_name in {'assistant-briefings.json', 'improvements-status.json'}
                    mode_ok = (stat.S_IMODE(info.st_mode) == 0o640 if policy_projection
                               else not info.st_mode & 0o077)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not mode_ok:
                        raise ValueError('restored assistant state file is unsafe')
        for name in assistant['files']:
            path = target / 'state' / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('declared assistant recovery credential is missing or unsafe')
            info = path.lstat()
            known_hosts = name == 'assistant-context/delegation-known-hosts'
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o022 or (not known_hosts and info.st_mode & 0o077)):
                raise ValueError('declared assistant recovery credential mode is unsafe')
        databases = assistant.get('sqlite_databases', [])
        if (not isinstance(databases, list) or len(databases) != len(set(databases))
                or any(not isinstance(name, str) or not name.startswith(tuple(
                    'assistant-context/' + directory.split('/', 1)[1] for directory in assistant['directories']))
                    or '..' in Path(name).parts or name.startswith('/') for name in databases)):
            raise ValueError('assistant SQLite manifest scope mismatch')
        for name in databases:
            path = target / 'state' / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('declared assistant SQLite backup is missing')
        if 'assistant-context/state' in assistant['directories']:
            if 'assistant-context/clients.json' not in assistant['files']:
                raise ValueError('assistant recovery credential coverage is incomplete')
            for name in ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3'):
                path = target / 'state/assistant-context/state' / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('assistant recovery database is incomplete')
            if 'sqlite_databases' in assistant:
                expected = {f'assistant-context/state/{name}' for name in
                            ('context.sqlite3', 'attention.sqlite3', 'coverage.sqlite3', 'delivery.sqlite3')}
                if not expected <= set(databases):
                    raise ValueError('assistant consistent SQLite backup coverage is incomplete')
    seed = target / 'state/agents-vm/seed.iso'
    metadata = manifest.get('agents_seed', {})
    if (seed.is_symlink() or not seed.is_file() or seed.stat().st_size > 32 * 1024**2
            or metadata.get('archive_path') != 'state/agents-vm/seed.iso'
            or not re.fullmatch(SEED_PATTERN, metadata.get('source', ''))
            or hashlib.sha256(seed.read_bytes()).hexdigest() != metadata.get('sha256')):
        raise ValueError('restored agents boot seed is missing or does not match its manifest')
    if manifest_vms == current_vms:
        toolbox_seed_file = target / 'state/toolbox-vm/seed.iso'
        toolbox_metadata = manifest.get('toolbox_seed', {})
        if (toolbox_seed_file.is_symlink() or not toolbox_seed_file.is_file() or toolbox_seed_file.stat().st_size > 32 * 1024**2
                or toolbox_metadata.get('archive_path') != 'state/toolbox-vm/seed.iso'
                or not re.fullmatch(TOOLBOX_SEED_PATTERN, toolbox_metadata.get('source', ''))
                or hashlib.sha256(toolbox_seed_file.read_bytes()).hexdigest() != toolbox_metadata.get('sha256')):
            raise ValueError('restored toolbox boot seed is missing or does not match its manifest')
    config = json.loads((target / 'databases/n8n-config.json').read_text())
    if not isinstance(config.get('encryptionKey'), str) or not config['encryptionKey']:
        raise ValueError('n8n credential encryption configuration is absent')
    database = target / 'databases/n8n.sqlite'
    sqlite_files = [database, *sorted((target / 'state').rglob('*.sqlite3')), *sorted((target / 'state').rglob('*.sqlite'))]
    for candidate in sorted((target / 'state').rglob('*.db')):
        if candidate.is_symlink() or not candidate.is_file(): raise ValueError('restored database file is unsafe')
        with candidate.open('rb') as stream:
            if stream.read(16) == b'SQLite format 3\0': sqlite_files.append(candidate)
    for database in sqlite_files:
        if database.is_symlink() or not database.is_file():
            raise ValueError('restored SQLite file is absent or symlinked')
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('restored SQLite integrity failed')
    for name in POSTGRES:
        with gzip.open(target / ('databases/' + name + '-postgres.sql.gz'), 'rb') as data:
            # gzip verifies its checksum while streaming. PostgreSQL engine
            # import is a separate isolated restore gate, not inferred here.
            while data.read(1024 * 1024):
                pass
    disks_to_check = VM_DISKS if manifest_vms == current_vms else VM_DISKS[:-1]
    for relative in disks_to_check:
        disk = target / 'state' / relative
        if disk.is_symlink() or not disk.is_file():
            raise ValueError('required VM disk is missing from the recovery archive')
        run(['qemu-img', 'check', '-q', str(disk)], timeout=600)
    return {'sqlite_integrity': 'ok', 'sqlite_databases_checked': len(sqlite_files),
            'gzip_dumps_checked': len(POSTGRES), 'qcow2_disks_checked': len(disks_to_check),
            'agents_seed_verified': True, 'toolbox_seed_verified': manifest_vms == current_vms,
            'postgres_import_verified': False, 'vm_boot_verified': False, 'media_included': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    commands.add_parser('resume')
    check = commands.add_parser('verify')
    check.add_argument('archive', type=Path)
    check.add_argument('target', type=Path)
    existing = commands.add_parser('verify-extracted')
    existing.add_argument('archive', type=Path)
    existing.add_argument('target', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('trusted root operator required')
    os.umask(0o077)
    def interrupted(signum, frame):
        raise InterruptedError('recovery preparation interrupted; resuming sources')
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == 'prepare':
        try:
            archive = prepare()
        except RecoveryDeferred as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(75)
        print(json.dumps({'archive': str(archive), 'off_host_backup': False}))
    elif args.command == 'resume':
        if RESUME_FILE.exists() or RECOVERY_MARKER.exists():
            with DEPLOY_LOCK.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                resume()
                RECOVERY_MARKER.unlink(missing_ok=True)
    elif args.command == 'verify-extracted':
        print(json.dumps(verify_extracted(args.archive, args.target), sort_keys=True))
    else:
        print(json.dumps(verify(args.archive, args.target), sort_keys=True))


if __name__ == '__main__':
    main()
