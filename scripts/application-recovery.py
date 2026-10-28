#!/usr/bin/env python3
"""Prepare cold, private recovery archives; verify only into a new scratch directory."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile

ARCHIVES = Path('/srv/backups/agent-recovery')
SCRATCH = Path('/srv/restore-tests')
DEPLOY_LOCK = Path('/var/lib/homecompute/deploy.lock')
RECOVERY_MARKER = Path('/run/homecompute/application-recovery-in-progress')
LOCAL_VM_BACKUP = 'restic-backups-agents-vm-bootstrap.service'
MIN_FREE_BYTES = 100 * 1024**3
CONTAINERS = ('homecompute-control-plane-caddy-1', 'homecompute-control-plane-litellm-1',
              'homecompute-automation-n8n-1', 'homecompute-mattermost-mattermost-1',
              'homecompute-immich-immich-server-1')
UNITS = ('homecompute-openclaw-household-task-reminders.timer', 'homecompute-openclaw-family-task-reminders.timer',
         'homecompute-openclaw-update-feed.timer', 'homecompute-openclaw-household-task-reminders.service',
         'homecompute-openclaw-family-task-reminders.service', 'homecompute-openclaw-update-feed.service',
         'homecompute-openclaw-task-feed.service',
         'homecompute-openclaw-adapter.service', 'homecompute-openclaw-location-reminders.service',
         'homecompute-openclaw-sites.service', 'homecompute-openclaw-access.service',
         'homecompute-ha-actions.service', 'homecompute-codex-worker-api.service', 'homecompute-codex-broker.service',
         'homecompute-codex-vm.service', 'homecompute-agents-vm.service')
POSTGRES = {'control-plane': 'homecompute-control-plane-postgres-1',
            'mattermost': 'homecompute-mattermost-postgres-1', 'immich': 'homecompute-immich-database-1'}
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
}
N8N = Path('/srv/state/automation/n8n')
VM_DISKS = ('agents-vm/agents.qcow2', 'codex-vm/root.qcow2', 'codex-vm/work.qcow2')


def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop('timeout', 180), **kwargs)


def private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('recovery directory must be a real root-only directory')


def sqlite_backup(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file() or destination.exists():
        raise ValueError('expected existing SQLite source and new destination')
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(destination) as copy:
        original.backup(copy)
        if copy.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('SQLite recovery copy failed integrity validation')
    destination.chmod(0o600)


@contextmanager
def quiesced():
    """Only restart services that were active before this attempt, even on error."""
    stopped = []
    try:
        # An active private browser can keep writing profile state independently
        # of its owner service. Defer rather than silently claim consistency.
        active = run(['docker', 'ps', '--filter', 'name=homecompute-site-view-', '--format', '{{.Names}}'], text=True).stdout
        if active.strip():
            raise ValueError('close active private browser sessions before cold recovery preparation')
        for unit in UNITS:
            result = subprocess.run(['systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID'],
                                    check=False, capture_output=True, text=True, timeout=10).stdout
            if 'ActiveState=active\n' in result:
                stopped.append(('unit', unit))
                run(['systemctl', 'stop', unit], timeout=180)
                state = run(['systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID'], text=True).stdout
                if 'ActiveState=inactive\n' not in state or (unit.endswith('.service') and 'MainPID=0\n' not in state):
                    raise ValueError('a recovery source service did not stop cleanly')
        for container in CONTAINERS:
            result = run(['docker', 'inspect', container, '--format', '{{.State.Running}}'], text=True).stdout
            if result.strip() == 'true':
                stopped.append(('container', container))
                run(['docker', 'stop', '--time', '60', container], timeout=90)
        yield
    finally:
        failures = []
        for kind, name in reversed(stopped):
            try:
                run(['docker', 'start', name] if kind == 'container' else ['systemctl', 'start', name], timeout=180)
            except (OSError, subprocess.SubprocessError):
                failures.append(name)
        if failures:
            raise RuntimeError('recovery preparation could not resume: ' + ', '.join(failures))


def archive_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    # QMP sockets, transient logs and partial backups are not recoverable state.
    if info.name.endswith(('.sock', '.log', '.partial')):
        return None
    if info.issym() and info.linkname.startswith('/nix/store/'):
        return None
    if info.ischr() or info.isblk() or info.isfifo() or not (info.isfile() or info.isdir() or info.issym()):
        raise ValueError('unreviewed special file in recovery source')
    return info


def space_required() -> int:
    total = 1024**3  # Dump/staging allowance in addition to the measured files.
    for source in [*DIRECTORIES.values(), N8N]:
        if source.is_symlink() or not source.is_dir():
            raise ValueError('declared recovery source is absent or symlinked')
        for parent, directories, files in os.walk(source, followlinks=False):
            directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
            for name in files:
                info = (Path(parent) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    return total + MIN_FREE_BYTES


def prepare() -> Path:
    private_directory(ARCHIVES, create=True)
    if shutil.disk_usage(ARCHIVES).free < space_required():
        raise ValueError('recovery needs space for all source files plus 100 GiB remaining headroom')
    name = 'homecompute-applications-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.tar'
    final = ARCHIVES / name
    temporary = ARCHIVES / (name + '.partial')
    with DEPLOY_LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = os.open(RECOVERY_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(marker)
        try:
            # The local VM backup checks our marker before stopping the guest.
            # After creating it, reject an already-started local backup, so its
            # cleanup cannot restart the VM halfway through this cold archive.
            local = run(['systemctl', 'show', LOCAL_VM_BACKUP, '-p', 'ActiveState'], text=True).stdout
            if any('ActiveState=' + state in local for state in ('active', 'activating', 'deactivating')):
                raise ValueError('local VM backup is running; cold recovery preparation deferred')
            with tempfile.TemporaryDirectory(prefix='.application-recovery-', dir=ARCHIVES) as staging, quiesced():
                directory = Path(staging)
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
                        archive.add(path, arcname='databases/' + path.name, filter=archive_filter)
                    for relative, source in {**DIRECTORIES, **{('n8n/' + name): N8N / name
                            for name in ('binaryData', 'nodes', 'ssh') if (N8N / name).is_dir()}}.items():
                        if source.is_symlink() or not source.is_dir():
                            raise ValueError('declared recovery source is absent or symlinked: ' + relative)
                        archive.add(source, arcname='state/' + relative, filter=archive_filter)
                    # Original media is owned by the NAS and must have its own
                    # independent backup; copying it back to that NAS is not DR.
                    manifest = {'schema_version': 1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                'postgres': sorted(POSTGRES), 'sqlite': ['n8n'], 'vms': ['agents-vm', 'codex-vm'],
                                'media_included': False, 'host_age_identity_included': False,
                                'nix_store_symlinks_included': False}
                    entry = directory / 'manifest.json'
                    entry.write_text(json.dumps(manifest, sort_keys=True))
                    archive.add(entry, arcname='manifest.json')
            temporary.chmod(0o600)
            with temporary.open('rb') as source:
                os.fsync(source.fileno())
            os.replace(temporary, final)
            # Retain one previous completed generation for rollback. Other
            # historical agent archives are never deleted by this helper.
            for old in sorted(ARCHIVES.glob('homecompute-applications-*.tar'), reverse=True)[2:]:
                if old.is_file() and not old.is_symlink():
                    old.unlink()
            return final
        finally:
            temporary.unlink(missing_ok=True)
            RECOVERY_MARKER.unlink()


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    result = archive.getmembers()
    for item in result:
        parts = Path(item.name).parts
        if item.name.startswith('/') or '..' in parts or not (item.isfile() or item.isdir() or item.issym()):
            raise ValueError('unsafe recovery archive member')
        if item.issym() and (item.linkname.startswith('/') or '..' in Path(item.linkname).parts):
            # Nix store symlinks are useful configuration references, but never
            # followed during isolated verification or extraction.
            raise ValueError('external symlinks require manual recovery review')
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
    manifest = json.loads((target / 'manifest.json').read_text())
    if (manifest.get('schema_version') != 1 or manifest.get('postgres') != sorted(POSTGRES)
            or manifest.get('vms') != ['agents-vm', 'codex-vm'] or manifest.get('sqlite') != ['n8n']):
        raise ValueError('recovery manifest scope mismatch')
    config = json.loads((target / 'databases/n8n-config.json').read_text())
    if not isinstance(config.get('encryptionKey'), str) or not config['encryptionKey']:
        raise ValueError('n8n credential encryption configuration is absent')
    database = target / 'databases/n8n.sqlite'
    sqlite_files = [database, *sorted((target / 'state').rglob('*.sqlite3')),
                    *sorted((target / 'state').rglob('*.sqlite'))]
    for database in sqlite_files:
        if database.is_symlink() or not database.is_file():
            raise ValueError('restored SQLite file is absent or symlinked')
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('restored SQLite integrity failed')
    for name in POSTGRES:
        with gzip.open(target / ('databases/' + name + '-postgres.sql.gz'), 'rb') as data:
            # gzip verifies its checksum while streaming. PostgreSQL engine
            # import is a separate isolated restore gate, not inferred here.
            while data.read(1024 * 1024):
                pass
    for relative in VM_DISKS:
        disk = target / 'state' / relative
        if disk.is_symlink() or not disk.is_file():
            raise ValueError('required VM disk is missing from the recovery archive')
        run(['qemu-img', 'check', '-q', str(disk)], timeout=600)
    return {'sqlite_integrity': 'ok', 'sqlite_databases_checked': len(sqlite_files),
            'gzip_dumps_checked': len(POSTGRES), 'qcow2_disks_checked': len(VM_DISKS),
            'postgres_import_verified': False, 'vm_boot_verified': False, 'media_included': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    check = commands.add_parser('verify')
    check.add_argument('archive', type=Path)
    check.add_argument('target', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('trusted root operator required')
    os.umask(0o077)
    if args.command == 'prepare':
        print(json.dumps({'archive': str(prepare()), 'off_host_backup': False}))
    else:
        print(json.dumps(verify(args.archive, args.target), sort_keys=True))


if __name__ == '__main__':
    main()
