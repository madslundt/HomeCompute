#!/usr/bin/env python3
"""Deploy only source-pinned readiness, machine inventory and its approved registry.

Default is a read-only bundle plan. Apply requires an immutable published-release
checkout and its exact reviewed hash. Communication code, credentials and channel
policy are never reinstalled. Managed attached drop-ins outrank the existing Nix
and attached base units; retire only after their canonical replacements match.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import stat
import subprocess
import tempfile
import time
import uuid

DEST = Path('/opt/homecompute/observability')
STATE = Path('/var/lib/homecompute-observability')
ATTACHED = Path('/etc/systemd/system.attached')
PRIVATE_REGISTRY = Path('/var/lib/homecompute-openclaw/config/system-monitoring.json')
BUNDLED_REGISTRY = Path('/opt/homecompute/openclaw-communication/config/system-monitoring.json')
PUBLIC_SNAPSHOT = Path('/run/homecompute-openclaw-reports/machine-status.json')
METRICS = Path('/var/lib/homecompute-health/homecompute-health.prom')
LOCK = Path('/var/lib/homecompute/deploy.lock')
PYTHON = '/run/current-system/sw/bin/python3'
HEALTH = 'homecompute-platform-health'
MACHINE = 'homecompute-openclaw-machine-status'
DROP_NAME = '90-homecompute-observability.conf'
FILES = ('scripts/setup-platform-observability.py', 'scripts/platform-health.py',
         'scripts/openclaw-machine-snapshot.py', 'scripts/n8n_observations.py',
         'config/system-monitoring.json', 'modules/nixos/platform-monitoring.nix')


def call(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=30)


def protected(path: Path, *, directory: bool = False, service_owner: bool = False) -> None:
    info = path.lstat()
    owners = {0, os.geteuid()}
    if service_owner:
        owners.add(pwd.getpwnam('homecompute-openclaw').pw_uid)
    if (stat.S_ISLNK(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_size > 2 * 1024 * 1024):
        raise ValueError('protected regular path required')


def directory(path: Path, mode: int = 0o755) -> None:
    if not path.exists() and not path.is_symlink():
        directory(path.parent)
        path.mkdir(mode=mode)
    protected(path, directory=True)
    path.chmod(mode)


def write(path: Path, content: bytes, mode: int = 0o644, owner: tuple[int, int] | None = None) -> None:
    protected(path.parent, directory=True, service_owner=True)
    if path.exists() or path.is_symlink():
        protected(path, service_owner=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
            if owner:
                os.fchown(stream.fileno(), *owner)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def registry(value: dict) -> dict:
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('mode') != 'observe-only'
            or value.get('automatic_actions') is not False or value.get('physical_device_actions') is not False
            or {row.get('id') for row in value.get('systems', [])} != {'home-core', 'home-spark', 'home-assistant'}):
        raise ValueError('fixed observe-only registry required')
    return value


def merged_registry(desired: bytes, existing: bytes) -> bytes:
    value = registry(json.loads(desired)); old = registry(json.loads(existing))
    # HA admission is established by its credential-owning installer. Updating
    # finite host inventory must neither disable it nor admit a new HA source.
    admitted = next(row for row in old['systems'] if row['id'] == 'home-assistant')['enabled']
    if type(admitted) is not bool:
        raise ValueError('existing HA admission must be explicit')
    next(row for row in value['systems'] if row['id'] == 'home-assistant')['enabled'] = admitted
    return (json.dumps(value, indent=2) + '\n').encode()


def bundle(source: Path) -> tuple[dict[str, bytes], str]:
    files, digest = {}, hashlib.sha256()
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('bounded regular source file required')
        content = path.read_bytes()
        if name.endswith('.py'):
            compile(content, name, 'exec')
        files[name] = content
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    policy = registry(json.loads(files['config/system-monitoring.json']))
    constants = {node.targets[0].id: ast.literal_eval(node.value)
                 for node in ast.parse(files['scripts/openclaw-machine-snapshot.py']).body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'CONTAINERS', 'UNITS'}}
    for host in ('home-core', 'home-spark'):
        system = next(row for row in policy['systems'] if row['id'] == host)
        if (set(constants['CONTAINERS'][host]) != {row['name'] for row in system['containers']}
                or set(constants['UNITS'][host]) != {row['name'] for row in system['units']}):
            raise ValueError('snapshot collector and approved registry differ')
    return files, digest.hexdigest()


def release(source: Path, revision: str, contents: dict[str, bytes]) -> None:
    if (not re.fullmatch('[0-9a-f]{40}', revision) or source.is_symlink()
            or source.resolve() != Path('/srv/homecompute/releases') / revision):
        raise ValueError('exact published release directory required')
    protected(source, directory=True)
    if call(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.strip() != revision:
        raise ValueError('release revision mismatch')
    # The wrapper's GitHub clone supplies remote-tracking ancestry. Historical
    # releases are allowed, but unpushed local commits are not deployment input.
    call(['git', '-C', str(source), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    for name, content in contents.items():
        committed = subprocess.run(['git', '-C', str(source), 'show', revision + ':' + name],
                                   check=True, capture_output=True, timeout=15).stdout
        if committed != content:
            raise ValueError('release contains unpublished file changes')


def dropins() -> dict[Path, bytes]:
    health = f'''[Service]
ExecStart=
ExecStart={PYTHON} {DEST}/current/scripts/platform-health.py
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=DOCKER_HOST=unix:///var/run/docker.sock
Environment=DOCKER_CONFIG=/run/homecompute-platform-health/docker
Environment=HOME=/run/homecompute-platform-health
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy DOCKER_CONTEXT SSH_AUTH_SOCK
TimeoutStartSec=20s
RuntimeDirectory=homecompute-platform-health
RuntimeDirectoryMode=0700
MemoryMax=128M
RestrictAddressFamilies=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressAllow=
IPAddressAllow=127.0.0.1/8 100.110.248.102/32 172.28.200.0/24 172.28.201.0/24
ReadOnlyPaths=
ReadOnlyPaths=-/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt
TasksMax=64
LimitCORE=0
'''
    machine = f'''[Service]
ExecStart=
ExecStart={PYTHON} {DEST}/current/scripts/openclaw-machine-snapshot.py --publish-core
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
TimeoutStartSec=12s
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
RestrictAddressFamilies=
RestrictAddressFamilies=AF_UNIX
'''
    return {ATTACHED / (name + '.service.d') / DROP_NAME: text.encode()
            for name, text in ((HEALTH, health), (MACHINE, machine))}


@contextmanager
def locked():
    with LOCK.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def save(paths: list[Path], target: Path) -> None:
    rows = []
    for number, path in enumerate(paths):
        row = {'path': str(path), 'kind': 'absent'}
        if path.is_symlink():
            if path != DEST / 'current':
                raise ValueError('unmanaged symlink refused')
            row.update(kind='symlink', target=os.readlink(path))
        elif path.exists():
            protected(path, service_owner=True)
            info = path.stat(); name = str(number)
            write(target / name, path.read_bytes(), 0o600)
            row.update(kind='file', copy=name, mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        rows.append(row)
    write(target / 'manifest.json', json.dumps(rows).encode(), 0o600)


def link_current(target: str) -> None:
    temporary = DEST / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(target)
    os.replace(temporary, DEST / 'current')


def restore(backup: Path) -> None:
    protected(backup / 'manifest.json')
    allowed = {str(path) for path in [*dropins(), PRIVATE_REGISTRY, BUNDLED_REGISTRY, DEST / 'current', STATE / 'receipt.json']}
    rows = json.loads((backup / 'manifest.json').read_bytes())
    if any(row.get('path') not in allowed for row in rows):
        raise ValueError('backup manifest contains an unmanaged target')
    for row in rows:
        path = Path(row['path'])
        if row['kind'] == 'absent':
            path.unlink(missing_ok=True)
        elif row['kind'] == 'symlink':
            link_current(row['target'])
        else:
            write(path, (backup / row['copy']).read_bytes(), row['mode'], (row['uid'], row['gid']))


def active_timers() -> list[str]:
    return [name + '.timer' for name in (HEALTH, MACHINE)
            if call(['systemctl', 'is-active', name + '.timer'], check=False).returncode == 0]


def verify_units() -> None:
    for name, script in ((HEALTH, 'platform-health.py'), (MACHINE, 'openclaw-machine-snapshot.py')):
        shown = call(['systemctl', 'show', name + '.service', '-p', 'ExecStart', '-p', 'DropInPaths',
                      '-p', 'RestrictAddressFamilies', '-p', 'TimeoutStartUSec', '-p', 'Environment']).stdout
        if str(DEST / 'current/scripts' / script) not in shown or str(ATTACHED / (name + '.service.d') / DROP_NAME) not in shown:
            raise ValueError('another unit definition outranks the reviewed observability drop-in')
        expected_timeout = 20 if name == HEALTH else 12
        if (f'TimeoutStartUSec={expected_timeout}s' not in shown or 'PATH=/run/current-system/sw/bin' not in shown
                or 'AF_UNIX' not in shown or name == HEALTH and 'DOCKER_HOST=unix:///var/run/docker.sock' not in shown):
            raise ValueError('observability socket, executable path or timeout settings were overridden')


def apply(source: Path, revision: str, expected: str) -> dict:
    contents, digest = bundle(source)
    if expected != digest:
        raise ValueError('reviewed bundle hash mismatch')
    release(source, revision, contents)
    target = PRIVATE_REGISTRY if PRIVATE_REGISTRY.exists() else BUNDLED_REGISTRY
    protected(target, service_owner=True)
    original = target.read_bytes(); rendered = merged_registry(contents['config/system-monitoring.json'], original)
    with locked():
        for path, mode in ((DEST, 0o755), (DEST / 'releases', 0o755), (STATE, 0o700), (STATE / 'backups', 0o700)):
            directory(path, mode)
        receipt = STATE / 'receipt.json'
        overlays = dropins()
        if any(path.exists() for path in overlays) and not receipt.exists():
            raise ValueError('existing unmanaged observability drop-in requires review')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        backup = STATE / 'backups' / stamp; directory(backup, 0o700)
        save([*overlays, target, DEST / 'current', receipt], backup)
        timers = active_timers()
        try:
            if timers:
                call(['systemctl', 'stop', *timers])
            call(['systemctl', 'stop', HEALTH + '.service', MACHINE + '.service'])
            version = DEST / 'releases' / (revision + '-' + digest[:16])
            for name, content in contents.items():
                directory((version / name).parent)
                if (version / name).exists() and (version / name).read_bytes() != content:
                    raise ValueError('immutable installed bundle differs')
                write(version / name, content, 0o444)
            link_current(str(version))
            if target.read_bytes() != original:
                raise ValueError('registry changed during installation')
            info = target.stat(); write(target, rendered, stat.S_IMODE(info.st_mode), (info.st_uid, info.st_gid))
            for path, content in overlays.items():
                directory(path.parent); write(path, content)
            call(['systemctl', 'daemon-reload']); verify_units()
            started = time.time()
            readiness = call(['systemctl', 'start', HEALTH + '.service'], check=False).returncode == 0
            call(['systemctl', 'start', MACHINE + '.service'])
            if METRICS.stat().st_mtime < started or PUBLIC_SNAPSHOT.stat().st_mtime < started:
                raise ValueError('fresh probe and snapshot evidence required')
            snapshot = json.loads(PUBLIC_SNAPSHOT.read_bytes())
            system = next(row for row in registry(json.loads(rendered))['systems'] if row['id'] == 'home-core')
            expected_names = {row['name'] for row in system['containers']}
            if {row['name'] for row in snapshot['containers']} != expected_names:
                raise ValueError('expanded inventory was not collected')
            call(['systemctl', 'try-restart', 'homecompute-openclaw-adapter.service'])
            record = {'revision': revision, 'bundle_hash': digest, 'backup': stamp,
                      'registry': str(target), 'readiness': readiness, 'state': 'applied',
                      'applied_at': datetime.now(timezone.utc).isoformat()}
            write(receipt, json.dumps(record, sort_keys=True).encode(), 0o600)
            return record
        except BaseException:
            restore(backup); call(['systemctl', 'daemon-reload'])
            call(['systemctl', 'try-restart', 'homecompute-openclaw-adapter.service'], check=False)
            raise
        finally:
            if timers:
                call(['systemctl', 'start', *timers])


def retire(expected: str) -> dict:
    protected(STATE / 'receipt.json')
    record = json.loads((STATE / 'receipt.json').read_bytes())
    if record['bundle_hash'] != expected:
        raise ValueError('installed receipt hash mismatch')
    with locked():
        current_registry = Path(record['registry']).read_bytes()
        rendered = merged_registry((DEST / 'current/config/system-monitoring.json').read_bytes(), current_registry)
        if json.loads(rendered) != json.loads(current_registry):
            raise ValueError('registry no longer matches the installed inventory')
        for name, relative in ((HEALTH, 'scripts/platform-health.py'), (MACHINE, 'scripts/openclaw-machine-snapshot.py')):
            fragment = Path(call(['systemctl', 'show', name + '.service', '-p', 'FragmentPath', '--value']).stdout.strip())
            commands = [line.split('=', 1)[1] for line in fragment.read_text().splitlines() if line.startswith('ExecStart=')]
            paths = [Path(arg) for command in commands for arg in shlex.split(command) if arg.startswith('/') and arg.endswith('.py')]
            matching = [path for path in paths if path.name.endswith(Path(relative).name)]
            if len(matching) != 1 or matching[0].read_bytes() != (DEST / 'current' / relative).read_bytes():
                raise ValueError('canonical base unit has not reconciled the installed source')
            if name == MACHINE and (matching[0].parent / 'n8n_observations.py').read_bytes() != (DEST / 'current/scripts/n8n_observations.py').read_bytes():
                raise ValueError('canonical snapshot dependency has not been reconciled')
            if name == HEALTH and ('AF_UNIX' not in fragment.read_text() or 'DOCKER_HOST=unix:///var/run/docker.sock' not in fragment.read_text()):
                raise ValueError('canonical health unit lacks required socket environment')
        for path, content in dropins().items():
            if path.read_bytes() != content:
                raise ValueError('managed drop-in changed; retirement refused')
        try:
            for path in dropins():
                path.unlink()
            call(['systemctl', 'daemon-reload'])
        except BaseException:
            for path, content in dropins().items():
                write(path, content)
            call(['systemctl', 'daemon-reload'])
            raise
        record['state'] = 'retired-after-canonical-reconciliation'
        write(STATE / 'receipt.json', json.dumps(record, sort_keys=True).encode(), 0o600)
        return record


def rollback(expected: str, identifier: str | None = None) -> dict:
    protected(STATE / 'receipt.json')
    record = json.loads((STATE / 'receipt.json').read_bytes())
    if record['bundle_hash'] != expected:
        raise ValueError('installed receipt hash mismatch')
    identifier = identifier or record['backup']
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}-[0-9a-f]{8}', identifier):
        raise ValueError('exact private backup identifier required')
    backup = STATE / 'backups' / identifier
    protected(backup, directory=True)
    with locked():
        timers = active_timers()
        try:
            if timers:
                call(['systemctl', 'stop', *timers])
            call(['systemctl', 'stop', HEALTH + '.service', MACHINE + '.service'])
            restore(backup); call(['systemctl', 'daemon-reload'])
            call(['systemctl', 'try-restart', 'homecompute-openclaw-adapter.service'])
            call(['systemctl', 'start', MACHINE + '.service'])
            call(['systemctl', 'start', HEALTH + '.service'], check=False)
        finally:
            if timers:
                call(['systemctl', 'start', *timers])
    result = {'state': 'rolled-back', 'backup': identifier}
    write(STATE / 'last-rollback.json', json.dumps(result).encode(), 0o600)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--revision'); parser.add_argument('--expected-hash')
    parser.add_argument('--backup-id')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true'); actions.add_argument('--status', action='store_true')
    actions.add_argument('--retire', action='store_true')
    actions.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if args.apply or args.retire or args.rollback:
        if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
            raise PermissionError('trusted home-core root operator required')
        if not args.expected_hash or args.apply and not args.revision:
            parser.error('apply needs --revision and --expected-hash; retire/rollback need --expected-hash')
    if args.apply:
        result = apply(args.source, args.revision, args.expected_hash)
    elif args.retire:
        result = retire(args.expected_hash)
    elif args.rollback:
        result = rollback(args.expected_hash, args.backup_id)
    elif args.status:
        result = json.loads((STATE / 'receipt.json').read_bytes())
    else:
        _, digest = bundle(args.source)
        result = {'bundle_hash': digest, 'files': list(FILES), 'applied': False}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
