#!/usr/bin/env python3
"""Prepare cold, private recovery archives; verify only into a new scratch directory."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
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
MIN_FREE_BYTES = 100 * 1024**3
MAX_ARCHIVE_BYTES = 512 * 1024**3
MAX_ARCHIVE_ENTRIES = 100000
CONTAINERS = ('homecompute-control-plane-caddy-1', 'homecompute-control-plane-litellm-1',
              'homecompute-automation-n8n-1', 'homecompute-mattermost-mattermost-1',
              'homecompute-immich-immich-server-1', 'homecompute-open-webui-open-webui-1',
              'omi-local-backend-1', 'homecompute-books_importer-shelfmark-1',
              'homecompute-books_importer-shelfmark-automated-1', 'homecompute-books_importer-cwa-1',
              'chessmate-backend-1', 'homecompute-image-studio-open-webui-1')
UNITS = ('homecompute-codex-worker-api.service', 'homecompute-codex-broker.service',
         'homecompute-openclaw-household-task-reminders.timer', 'homecompute-openclaw-family-task-reminders.timer',
         'homecompute-openclaw-update-feed.timer', 'homecompute-openclaw-household-task-reminders.service',
         'homecompute-openclaw-family-task-reminders.service', 'homecompute-openclaw-update-feed.service',
         'homecompute-openclaw-task-feed.service',
         'homecompute-openclaw-adapter.service', 'homecompute-openclaw-location-reminders.service',
         'homecompute-openclaw-sites.service', 'homecompute-openclaw-access.service',
         'homecompute-ha-actions.service',
         'homecompute-codex-vm.service', 'homecompute-agents-vm.service')
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
               'image-studio/webui-secret-key': Path('/srv/state/image-studio/webui-secret-key')}
N8N = Path('/srv/state/automation/n8n')
VM_DISKS = ('agents-vm/agents.qcow2', 'codex-vm/root.qcow2', 'codex-vm/work.qcow2')
SEED_PATTERN = r'/nix/store/[0-9a-z]{32}-homecompute-agents-seed\.iso'


def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop('timeout', 180), **kwargs)


def private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('recovery directory must be a real root-only directory')


def sqlite_backup(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError('expected existing SQLite source and new destination')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        if copy.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('SQLite recovery copy failed integrity validation')
    destination.chmod(0o600)


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
    allowed = {('unit', name) for name in UNITS} | {('container', name) for name in CONTAINERS}
    if (not isinstance(stopped, list) or len(stopped) > len(allowed)
            or any(not isinstance(row, (list, tuple)) or len(row) != 2 or tuple(row) not in allowed for row in stopped)):
        raise ValueError('resume journal contains an unreviewed resource')
    failures = []
    for kind, name in reversed(stopped):
        try:
            run(['docker', 'start', name] if kind == 'container' else ['systemctl', 'start', name], timeout=180)
        except (OSError, subprocess.SubprocessError):
            failures.append(name)
    if failures:
        raise RuntimeError('recovery preparation could not resume: ' + ', '.join(failures))
    RESUME_FILE.unlink(missing_ok=True)


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
        raise ValueError('worker activity requires recovery preparation to defer')


def private_browsers_idle() -> None:
    active = run(['docker', 'ps', '--filter', 'name=homecompute-site-view-', '--format', '{{.Names}}'], text=True, timeout=10).stdout
    if active.strip():
        raise ValueError('close active private browser sessions before cold recovery preparation')


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
        # An active private browser can keep writing profile state independently
        # of its owner service. Defer rather than silently claim consistency.
        private_browsers_idle()
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
    if info.name == 'state/agents-vm/seed.iso':
        return None  # Archive the exact owning-unit seed, not an incidental file.
    if any(info.name == 'state/omi-data/' + name or info.name.startswith('state/omi-data/' + name + '/')
           for name in ('audio', 'diary')):
        return None  # NAS-owned originals need their independent media backup.
    if info.issym() and info.linkname.startswith('/nix/store/'):
        return None
    if info.ischr() or info.isblk() or info.isfifo() or not (info.isfile() or info.isdir() or info.issym()):
        raise ValueError('unreviewed special file in recovery source')
    return info


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


def space_required() -> int:
    total = 1024**3  # Dump/staging allowance in addition to the measured files.
    entries = 0
    for source in [*DIRECTORIES.values(), N8N]:
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
    return total + MIN_FREE_BYTES


def prepare() -> Path:
    private_directory(ARCHIVES, create=True)
    name = 'homecompute-applications-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.tar'
    final = ARCHIVES / name
    temporary = ARCHIVES / (name + '.partial')
    with DEPLOY_LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
                raise ValueError('local VM backup is running; cold recovery preparation deferred')
            with tempfile.TemporaryDirectory(prefix='.application-recovery-', dir=ARCHIVES) as staging, quiesced():
                directory = Path(staging)
                seed = agents_seed(); staged_seed = directory / 'agents-seed.iso'
                shutil.copyfile(seed, staged_seed)
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
                        if path != staged_seed:
                            archive.add(path, arcname='databases/' + path.name, filter=archive_filter)
                    for relative, source in {**DIRECTORIES, **{('n8n/' + name): N8N / name
                            for name in ('binaryData', 'nodes', 'ssh') if (N8N / name).is_dir()}}.items():
                        if source.is_symlink() or not source.is_dir():
                            raise ValueError('declared recovery source is absent or symlinked: ' + relative)
                        archive.add(source, arcname='state/' + relative, filter=archive_filter)
                    archive.add(staged_seed, arcname='state/agents-vm/seed.iso')
                    for relative, source in EXTRA_FILES.items():
                        archive.add(source, arcname='state/' + relative, filter=archive_filter)
                    # Original media is owned by the NAS and must have its own
                    # independent backup; copying it back to that NAS is not DR.
                    manifest = {'schema_version': 1, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                                'postgres': sorted(POSTGRES), 'sqlite': ['n8n'], 'vms': ['agents-vm', 'codex-vm'],
                                'media_included': False, 'host_age_identity_included': False,
                                'nix_store_symlinks_included': False,
                                'agents_seed': {'archive_path': 'state/agents-vm/seed.iso', 'source': str(seed),
                                                'sha256': hashlib.sha256(staged_seed.read_bytes()).hexdigest()}}
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
    seed = target / 'state/agents-vm/seed.iso'
    metadata = manifest.get('agents_seed', {})
    if (seed.is_symlink() or not seed.is_file() or seed.stat().st_size > 32 * 1024**2
            or metadata.get('archive_path') != 'state/agents-vm/seed.iso'
            or not re.fullmatch(SEED_PATTERN, metadata.get('source', ''))
            or hashlib.sha256(seed.read_bytes()).hexdigest() != metadata.get('sha256')):
        raise ValueError('restored agents boot seed is missing or does not match its manifest')
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
    for relative in VM_DISKS:
        disk = target / 'state' / relative
        if disk.is_symlink() or not disk.is_file():
            raise ValueError('required VM disk is missing from the recovery archive')
        run(['qemu-img', 'check', '-q', str(disk)], timeout=600)
    return {'sqlite_integrity': 'ok', 'sqlite_databases_checked': len(sqlite_files),
            'gzip_dumps_checked': len(POSTGRES), 'qcow2_disks_checked': len(VM_DISKS),
            'agents_seed_verified': True,
            'postgres_import_verified': False, 'vm_boot_verified': False, 'media_included': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    commands.add_parser('resume')
    check = commands.add_parser('verify')
    check.add_argument('archive', type=Path)
    check.add_argument('target', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('trusted root operator required')
    os.umask(0o077)
    def interrupted(signum, frame):
        raise InterruptedError('recovery preparation interrupted; resuming sources')
    signal.signal(signal.SIGTERM, interrupted)
    if args.command == 'prepare':
        print(json.dumps({'archive': str(prepare()), 'off_host_backup': False}))
    elif args.command == 'resume':
        if RESUME_FILE.exists() or RECOVERY_MARKER.exists():
            with DEPLOY_LOCK.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                resume()
                RECOVERY_MARKER.unlink(missing_ok=True)
    else:
        print(json.dumps(verify(args.archive, args.target), sort_keys=True))


if __name__ == '__main__':
    main()
