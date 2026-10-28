#!/usr/bin/env python3
"""Targeted Synology recovery lifecycle; default plans without changing the host.

Apply stages only the declared SMB mount, recovery tools and gated schedules.
Repository initialization, the first cold backup and isolated verification are
separate explicit operations. Existing NAS repositories/data are never replaced.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

DEST = Path('/opt/homecompute/nas-recovery')
STATE = Path('/var/lib/homecompute/nas-recovery-backup')
CONFIG = Path('/etc/homecompute/nas-recovery-backup.json')
MOUNT = Path('/mnt/homecompute-backups')
ATTACHED = Path('/etc/systemd/system.attached')
LOCK = Path('/var/lib/homecompute/deploy.lock')
RESTIC_PASSWORD = Path('/run/secrets/restic/password')
PYTHON = '/run/current-system/sw/bin/python3'
MOUNT_UNIT = r'mnt-homecompute\x2dbackups'
PREFIX = 'homecompute-nas-recovery-'
GUARD = ATTACHED / 'restic-backups-agents-vm-bootstrap.service.d/90-homecompute-nas-recovery.conf'
FILES = ('scripts/setup-nas-recovery-backup.py', 'scripts/nas-recovery-backup.py',
         'scripts/application-recovery.py', 'scripts/provision-nas-backup-credentials.py',
         'config/synology-backup.json', 'modules/nixos/nas-recovery-backup.nix',
         'modules/nixos/agents-vm-backup.nix')


def run(arguments: list[str], *, check=True, timeout=30, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=timeout, env=env)


def safe(path: Path, *, directory=False, symlink=False, bounded=True) -> None:
    info = path.stat() if symlink else path.lstat()
    if (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or bounded and not directory and info.st_size > 2 * 1024 * 1024):
        raise ValueError('protected bounded regular path required')


def directory(path: Path, mode=0o755) -> None:
    if not path.exists() and not path.is_symlink():
        if not path.parent.exists(): directory(path.parent)
        else: safe(path.parent, directory=True)
        path.mkdir(mode=mode)
    safe(path, directory=True); path.chmod(mode)


def write(path: Path, data: bytes, mode=0o644) -> None:
    safe(path.parent, directory=True)
    if path.exists() or path.is_symlink(): safe(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def policy(source: bytes) -> dict:
    value = json.loads(source)
    expected = {'schema_version': 1, 'address': '192.168.30.236', 'share': 'homecompute-backups',
                'username': 'homecompute-backup', 'credentials_file': '/etc/homecompute/nas-recovery-backup.credentials',
                'mountpoint': str(MOUNT), 'protocol': 'SMB3.1.1', 'restic_encryption_required': True}
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise ValueError('only the declared dedicated Synology backup destination is admitted')
    return value


def bundle(source: Path) -> tuple[dict, str]:
    contents, digest = {}, hashlib.sha256()
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('bounded regular published source required')
        data = path.read_bytes()
        if name.endswith('.py'): compile(data, name, 'exec')
        contents[name] = data; digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    policy(contents['config/synology-backup.json'])
    return contents, digest.hexdigest()


def published(source: Path, revision: str, contents: dict) -> None:
    if not re.fullmatch('[0-9a-f]{40}', revision) or source.resolve() != Path('/srv/homecompute/releases') / revision:
        raise ValueError('exact immutable published release required')
    safe(source, directory=True)
    if run(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.strip() != revision:
        raise ValueError('release revision mismatch')
    run(['git', '-C', str(source), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    for name, data in contents.items():
        result = subprocess.run(['git', '-C', str(source), 'show', revision + ':' + name], capture_output=True, check=True, timeout=15)
        if result.stdout != data: raise ValueError('unpublished source changes refused')


def credentials(source: Path, settings: dict) -> None:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('nas_credentials', source / 'scripts/provision-nas-backup-credentials.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.validate(Path(settings['credentials_file']), settings['username'])
    safe(RESTIC_PASSWORD, symlink=True)
    info = RESTIC_PASSWORD.stat()
    if info.st_uid != 0 or info.st_mode & 0o077 or not 16 <= info.st_size <= 4096:
        raise ValueError('existing private SOPS Restic password required')


def qemu_directory() -> str:
    candidate = Path('/run/current-system/sw/bin/qemu-img')
    if not candidate.exists():
        candidate = Path('/nix/var/nix/gcroots/homecompute-codex-vm/qemu').resolve() / 'bin/qemu-img'
    resolved = candidate.resolve()
    if not str(resolved).startswith('/nix/store/'):
        raise ValueError('reviewed Nix-store qemu-img required')
    safe(resolved, bounded=False)
    for program in ('restic', 'mount.cifs', 'docker', 'systemctl'):
        binary = Path('/run/current-system/sw/bin') / program
        safe(binary, symlink=True, bounded=False)
    return str(resolved.parent)


def definitions(settings: dict, qemu: str) -> dict[Path, bytes]:
    values = {
        MOUNT_UNIT + '.mount': f'''[Unit]
Description=Dedicated Synology HomeCompute encrypted backup share
Wants=network-online.target
After=network-online.target
[Mount]
What=//{settings['address']}/{settings['share']}
Where={MOUNT}
Type=cifs
Options=vers=3.1.1,credentials={settings['credentials_file']},uid=0,gid=0,file_mode=0600,dir_mode=0700,nosuid,nodev,noexec,_netdev
TimeoutSec=15
''',
        MOUNT_UNIT + '.automount': f'''[Unit]
Description=On-demand dedicated HomeCompute NAS backup mount
[Automount]
Where={MOUNT}
[Install]
WantedBy=multi-user.target
''',
    }
    for command in ('backup', 'check'):
        before = (f'ExecStartPre={PYTHON} -I {DEST}/current/scripts/nas-recovery-backup.py validate\n'
                  f'ExecStartPre={PYTHON} -I {DEST}/current/scripts/application-recovery.py prepare\n') if command == 'backup' else ''
        after = f'ExecStopPost={PYTHON} -I {DEST}/current/scripts/application-recovery.py resume\n' if command == 'backup' else ''
        capability = 'CAP_DAC_READ_SEARCH' if command == 'backup' else ''
        home_read = ('BindReadOnlyPaths=/home/mads/projects/chessmate/config/certificates '
                     '/home/mads/projects/chessmate/config/.env\n') if command == 'backup' else ''
        home_protect = 'tmpfs' if command == 'backup' else 'yes'
        values[PREFIX + command + '.service'] = f'''[Unit]
Description=HomeCompute encrypted NAS recovery {command}
Requires=network-online.target
After=network-online.target
RequiresMountsFor={MOUNT}
ConditionPathExists={STATE}/backup-authorized
[Service]
Type=oneshot
User=root
UMask=0077
Environment=PATH=/run/current-system/sw/bin:{qemu}
Environment=HOME={STATE}
Environment=DOCKER_HOST=unix:///var/run/docker.sock
Environment=DOCKER_CONFIG={STATE}/docker
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=DOCKER_CONTEXT HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
{before}ExecStart={PYTHON} -I {DEST}/current/scripts/nas-recovery-backup.py {command}
{after}TimeoutStartSec=2h
TimeoutStopSec=10min
KillMode=mixed
MemoryMax=1G
CPUQuota=50%
IOSchedulingClass=idle
Nice=10
NoNewPrivileges=yes
CapabilityBoundingSet={capability}
AmbientCapabilities={capability}
PrivateTmp=yes
ProtectSystem=strict
ProtectHome={home_protect}
{home_read}ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths={MOUNT} {STATE} /srv/backups/agent-recovery /var/lib/homecompute/deploy.lock /run/homecompute
RestrictAddressFamilies=AF_UNIX
'''
        schedule, delay = ('*-*-* 03:15:00', '30m') if command == 'backup' else ('weekly', '2h')
        values[PREFIX + command + '.timer'] = f'''[Unit]
Description=HomeCompute NAS recovery {command} schedule
[Timer]
OnCalendar={schedule}
RandomizedDelaySec={delay}
Persistent=yes
Unit={PREFIX}{command}.service
[Install]
WantedBy=timers.target
'''
    result = {ATTACHED / name: content.encode() for name, content in values.items()}
    result[GUARD] = b'[Service]\nExecCondition=/run/current-system/sw/bin/test ! -e /run/homecompute/application-recovery-in-progress\n'
    return result


def links() -> dict[Path, str]:
    result = {ATTACHED / 'multi-user.target.wants' / (MOUNT_UNIT + '.automount'): str(ATTACHED / (MOUNT_UNIT + '.automount'))}
    result.update({ATTACHED / 'timers.target.wants' / (PREFIX + command + '.timer'): str(ATTACHED / (PREFIX + command + '.timer'))
                   for command in ('backup', 'check')})
    return result


def symlink(path: Path, target: str) -> None:
    directory(path.parent)
    temporary = path.parent / ('.link-' + uuid.uuid4().hex)
    temporary.symlink_to(target); os.replace(temporary, path)


def save(paths: list[Path], backup: Path) -> None:
    rows = []
    for index, path in enumerate(paths):
        row = {'path': str(path), 'kind': 'absent'}
        if path.is_symlink(): row.update(kind='link', target=os.readlink(path))
        elif path.exists():
            safe(path); info = path.stat(); name = str(index)
            write(backup / name, path.read_bytes(), 0o600)
            row.update(kind='file', copy=name, mode=stat.S_IMODE(info.st_mode))
        rows.append(row)
    write(backup / 'manifest.json', json.dumps(rows).encode(), 0o600)


def restore(backup: Path, allowed: set[str]) -> None:
    safe(backup / 'manifest.json')
    rows = json.loads((backup / 'manifest.json').read_bytes())
    if any(row.get('path') not in allowed for row in rows): raise ValueError('unmanaged rollback target refused')
    for row in rows:
        path = Path(row['path'])
        if row['kind'] == 'absent': path.unlink(missing_ok=True)
        elif row['kind'] == 'link': symlink(path, row['target'])
        else: write(path, (backup / row['copy']).read_bytes(), row['mode'])


@contextmanager
def locked(path: Path):
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB); yield


def busy() -> None:
    for command in ('backup', 'check'):
        result = run(['systemctl', 'show', PREFIX + command + '.service', '-p', 'LoadState', '-p', 'ActiveState'], check=False)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if fields == {'LoadState': 'not-found', 'ActiveState': 'inactive'} and result.returncode in {0, 1, 4}: continue
        if result.returncode != 0 or fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in {'inactive', 'failed'}:
            raise ValueError('a NAS recovery job is running or its state is unavailable')


def active_units() -> list[str]:
    names = [MOUNT_UNIT + '.mount', MOUNT_UNIT + '.automount', PREFIX + 'backup.timer', PREFIX + 'check.timer']
    return [name for name in names if run(['systemctl', 'is-active', name], check=False).returncode == 0]


def compatible_mount(settings: dict) -> None:
    result = run(['findmnt', '--json', '--mountpoint', str(MOUNT), '--output', 'TARGET,SOURCE,FSTYPE'], check=False)
    if result.returncode == 1: return
    if result.returncode != 0: raise ValueError('existing mount state is unavailable')
    expected = f'//{settings["address"]}/{settings["share"]}'
    entries = json.loads(result.stdout).get('filesystems', [])
    cifs = {'target': str(MOUNT), 'source': expected, 'fstype': 'cifs'}
    automount = {'target': str(MOUNT), 'source': 'systemd-1', 'fstype': 'autofs'}
    # Staging may encounter the declared automount before its first trigger.
    # Actual backup validation separately requires CIFS to be present on top.
    if entries not in ([automount], [cifs], [automount, cifs]):
        raise ValueError('foreign filesystem at the backup mountpoint refused')


def apply(source: Path, revision: str, expected: str) -> dict:
    contents, digest = bundle(source)
    if digest != expected: raise ValueError('reviewed NAS bundle hash mismatch')
    published(source, revision, contents); settings = policy(contents['config/synology-backup.json'])
    credentials(source, settings); qemu = qemu_directory(); units = definitions(settings, qemu)
    compatible_mount(settings)
    directory(STATE, 0o700)
    with locked(STATE / 'lifecycle.lock'), locked(LOCK):
        busy()
        for path, mode in ((DEST, 0o755), (DEST / 'releases', 0o755), (STATE, 0o700), (STATE / 'deploy-backups', 0o700),
                           (MOUNT, 0o700), (Path('/srv/backups/agent-recovery'), 0o700), (Path('/srv/restore-tests'), 0o700), (Path('/run/homecompute'), 0o755)):
            directory(path, mode)
        receipt = STATE / 'deployment.json'
        if any(path.exists() or path.is_symlink() for path in [*units, *links()]) and not receipt.exists():
            raise ValueError('existing unmanaged NAS units require reconciliation')
        for path in units:
            if path == GUARD: continue
            fragment = run(['systemctl', 'show', path.name, '-p', 'FragmentPath', '--value'], check=False).stdout.strip()
            if fragment and fragment != str(path): raise ValueError('another NAS unit definition takes precedence')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        backup = STATE / 'deploy-backups' / stamp; directory(backup, 0o700)
        paths = [*units, *links(), CONFIG, DEST / 'current', receipt]
        save(paths, backup)
        previous_active = active_units()
        try:
            run(['systemctl', 'stop', PREFIX + 'backup.timer', PREFIX + 'check.timer'], check=False)
            version = DEST / 'releases' / (revision + '-' + digest[:16])
            for name, data in contents.items():
                directory((version / name).parent)
                if (version / name).exists() and (version / name).read_bytes() != data: raise ValueError('immutable NAS bundle changed')
                write(version / name, data, 0o444)
            symlink(DEST / 'current', str(version))
            runtime = {'address': settings['address'], 'share': settings['share'], 'credentialsFile': settings['credentials_file']}
            if CONFIG.exists() or CONFIG.is_symlink():
                if json.loads(CONFIG.read_bytes()) != runtime: raise ValueError('existing NAS runtime settings differ')
            else: write(CONFIG, json.dumps(runtime).encode())
            for path, data in units.items(): directory(path.parent); write(path, data)
            for path, target in links().items(): symlink(path, target)
            run(['systemd-analyze', 'verify', *[str(path) for path in units if path != GUARD]])
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'start', MOUNT_UNIT + '.automount', MOUNT_UNIT + '.mount'])
            run([PYTHON, '-I', str(version / 'scripts/nas-recovery-backup.py'), 'validate'], env=environment(qemu))
            for path in units:
                if path == GUARD: continue
                fragment = run(['systemctl', 'show', path.name, '-p', 'FragmentPath', '--value']).stdout.strip()
                if fragment != str(path): raise ValueError('installed NAS unit precedence mismatch')
            record = {'revision': revision, 'bundle_hash': digest, 'version': str(version), 'qemu_bin': qemu,
                      'backup': stamp, 'state': 'staged-first-backup-pending',
                      'managed_paths': [str(path) for path in paths], 'previous_active': previous_active,
                      'digests': {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
            write(receipt, json.dumps(record).encode(), 0o600)
            if (STATE / 'backup-authorized').exists():
                run(['systemctl', 'start', PREFIX + 'backup.timer', PREFIX + 'check.timer']); record['state'] = 'scheduled'
                write(receipt, json.dumps(record).encode(), 0o600)
            return record
        except BaseException:
            run(['systemctl', 'stop', MOUNT_UNIT + '.automount', MOUNT_UNIT + '.mount'], check=False)
            restore(backup, {str(path) for path in paths}); run(['systemctl', 'daemon-reload'])
            if previous_active: run(['systemctl', 'start', *previous_active])
            raise


def environment(qemu: str) -> dict:
    return {'PATH': '/run/current-system/sw/bin:' + qemu, 'HOME': str(STATE), 'DOCKER_HOST': 'unix:///var/run/docker.sock',
            'DOCKER_CONFIG': str(STATE / 'docker'), 'PYTHONDONTWRITEBYTECODE': '1'}


def installed(expected: str) -> dict:
    safe(STATE / 'deployment.json'); record = json.loads((STATE / 'deployment.json').read_bytes())
    if record['bundle_hash'] != expected: raise ValueError('installed NAS receipt hash mismatch')
    version = Path(record['version'])
    if version.parent != DEST / 'releases' or not re.fullmatch('[0-9a-f]{40}-[0-9a-f]{16}', version.name):
        raise ValueError('unmanaged NAS installed version refused')
    for name in FILES:
        safe(version / name)
        if hashlib.sha256((version / name).read_bytes()).hexdigest() != record['digests'][name]:
            raise ValueError('installed NAS source changed')
    return record


def operation(command: str, record: dict, args) -> dict:
    version = Path(record['version']); env = environment(record['qemu_bin'])
    nas = [PYTHON, '-I', str(version / 'scripts/nas-recovery-backup.py')]
    recovery = [PYTHON, '-I', str(version / 'scripts/application-recovery.py')]
    if command == 'resume':
        run([*recovery, 'resume'], env=env, timeout=1800); return {'state': 'sources-resumed'}
    run([*nas, 'validate'], env=env)
    if command in {'initialize', 'proof', 'check'}:
        run([*nas, 'init' if command == 'initialize' else command], env=env, timeout=7500)
        return {'state': command + '-completed', 'application_restore_accepted': False}
    if command == 'backup-now':
        if not args.acknowledge_pause: raise ValueError('explicit --acknowledge-pause required for cold application backup')
        busy(); permit = STATE / 'backup-authorized'; created = not permit.exists()
        if created: write(permit, b'first explicit cold backup\n', 0o600)
        started = datetime.now(timezone.utc).timestamp()
        try: run(['systemctl', 'start', PREFIX + 'backup.service'], timeout=7500)
        finally:
            if created: permit.unlink()
        if (STATE / 'last-backup.json').stat().st_mtime < started:
            raise ValueError('fresh completed NAS backup evidence required')
        return json.loads((STATE / 'last-backup.json').read_bytes())
    if command == 'restore-verify':
        if not args.snapshot or not args.archive or not args.target: raise ValueError('restore needs exact snapshot, archive and new scratch target')
        result = json.loads(run([*nas, 'restore-application', '--snapshot', args.snapshot, '--archive', args.archive,
                                '--target', str(args.target)], env=env, timeout=7500).stdout)
        application = args.target.with_name(args.target.name + '-application')
        proof = json.loads(run([*recovery, 'verify', result['restored_archive'], str(application)],
                              env=env, timeout=7500).stdout)
        manifest = application / 'manifest.json'; safe(manifest)
        output = {'snapshot': args.snapshot, 'archive': args.archive, 'verification': proof,
                  'restore_directory': str(application.resolve()),
                  'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
        write(STATE / 'last-application-verification.json', json.dumps(output).encode(), 0o600); return output
    backup = json.loads((STATE / 'last-backup.json').read_bytes())
    proof = json.loads((STATE / 'last-application-verification.json').read_bytes())
    if (backup['snapshot'] != proof['snapshot'] or proof['verification'].get('sqlite_integrity') != 'ok'
            or proof['verification'].get('qcow2_disks_checked') != 3 or proof['verification'].get('gzip_dumps_checked') != 4):
        raise ValueError('matching first application backup and isolated verification required before scheduling')
    marker = STATE / 'backup-authorized'
    if not marker.exists():
        for name in ('backup', 'check'):
            stamp = Path('/var/lib/systemd/timers') / ('stamp-' + PREFIX + name + '.timer')
            if stamp.is_symlink(): raise ValueError('timer timestamp symlink refused')
            stamp.touch()
        write(marker, b'scheduled after isolated application verification\n', 0o600)
    run(['systemctl', 'start', PREFIX + 'backup.timer', PREFIX + 'check.timer'])
    record['state'] = 'scheduled'; write(STATE / 'deployment.json', json.dumps(record).encode(), 0o600)
    return {'state': 'scheduled', 'postgres_import_verified': False, 'vm_boot_verified': False}


def rollback(record: dict) -> dict:
    if record['state'] == 'retired': raise ValueError('use the canonical NixOS rollback after retirement')
    with locked(STATE / 'lifecycle.lock'), locked(LOCK):
        busy()
        if not re.fullmatch('[0-9]{8}T[0-9]{6}-[0-9a-f]{8}', record['backup']):
            raise ValueError('exact private backup identifier required')
        settings = policy((Path(record['version']) / 'config/synology-backup.json').read_bytes())
        allowed = {str(path) for path in [*definitions(settings, record['qemu_bin']), *links(), CONFIG, DEST / 'current', STATE / 'deployment.json']}
        if set(record['managed_paths']) != allowed: raise ValueError('managed NAS rollback scope changed')
        run(['systemctl', 'stop', PREFIX + 'backup.timer', PREFIX + 'check.timer', MOUNT_UNIT + '.automount', MOUNT_UNIT + '.mount'], check=False)
        restore(STATE / 'deploy-backups' / record['backup'], allowed)
        run(['systemctl', 'daemon-reload'])
        if record['previous_active']: run(['systemctl', 'start', *record['previous_active']])
        # NAS repository contents, credentials, keys and prepared archives are
        # durable data and are deliberately outside rollback's managed paths.
        return {'state': 'configuration-rolled-back', 'repository_data_deleted': False}


def retire(record: dict) -> dict:
    settings = policy((Path(record['version']) / 'config/synology-backup.json').read_bytes())
    units = definitions(settings, record['qemu_bin'])
    with locked(STATE / 'lifecycle.lock'), locked(LOCK):
        busy()
        for path in units:
            canonical = (Path('/run/systemd/generator') if path.suffix in {'.mount', '.automount'}
                         else Path('/etc/systemd/system')) / path.name
            if path == GUARD:
                canonical = Path('/etc/systemd/system/restic-backups-agents-vm-bootstrap.service')
            text = canonical.read_text()
            if path == GUARD and 'application-recovery-in-progress' not in text:
                raise ValueError('canonical local VM exclusion guard is absent')
            if path.suffix == '.mount' and (f'What=//{settings["address"]}/{settings["share"]}' not in text
                                           or settings['credentials_file'] not in text):
                raise ValueError('canonical NAS mount differs')
            if path.name in {PREFIX + 'backup.service', PREFIX + 'check.service'}:
                for filename in ('nas-recovery-backup.py', 'application-recovery.py'):
                    if filename == 'application-recovery.py' and path.name.endswith('check.service'): continue
                    candidates = re.findall(r'/nix/store/[^\s";]+' + re.escape(filename), text)
                    if not candidates or any(Path(p).read_bytes() != (DEST / 'current/scripts' / filename).read_bytes() for p in candidates):
                        raise ValueError('canonical recovery source is not reconciled')
        if not CONFIG.is_symlink() or not str(CONFIG.resolve()).startswith('/nix/store/'):
            raise ValueError('canonical NixOS NAS settings have not replaced the targeted configuration')
        try:
            for path in [*units, *links()]: path.unlink()
            run(['systemctl', 'daemon-reload'])
        except BaseException:
            for path, data in units.items(): write(path, data)
            for path, target in links().items(): symlink(path, target)
            run(['systemctl', 'daemon-reload']); raise
        record['state'] = 'retired'; write(STATE / 'deployment.json', json.dumps(record).encode(), 0o600)
        return {'state': 'retired', 'repository_data_deleted': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='plan', choices=('plan', 'apply', 'status', 'initialize', 'proof', 'check', 'backup-now', 'restore-verify', 'enable-schedule', 'resume', 'rollback', 'retire'))
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--revision'); parser.add_argument('--expected-hash'); parser.add_argument('--acknowledge-pause', action='store_true')
    parser.add_argument('--snapshot'); parser.add_argument('--archive'); parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    if args.command == 'plan':
        _, digest = bundle(args.source); result = {'bundle_hash': digest, 'files': list(FILES), 'applied': False}
    else:
        if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core': raise PermissionError('trusted home-core root operator required')
        if args.command == 'status': result = json.loads((STATE / 'deployment.json').read_bytes())
        elif args.command == 'apply':
            if not args.revision or not args.expected_hash: parser.error('apply requires revision and reviewed bundle hash')
            result = apply(args.source, args.revision, args.expected_hash)
        else:
            record = installed(args.expected_hash)
            if args.command == 'rollback': result = rollback(record)
            elif args.command == 'retire': result = retire(record)
            else:
                with locked(STATE / 'lifecycle.lock'): result = operation(args.command, record, args)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
