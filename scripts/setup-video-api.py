#!/usr/bin/env python3
"""Plan or install the private video API worker on home-core."""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote

ROOT = Path('/opt/homecompute/video-api')
UNITS = Path('/etc/systemd/system.attached')
BACKUPS = Path('/var/lib/homecompute-video-api-updates')
WORKER_STATE = Path('/var/lib/homecompute-video-api')
PRIVATE_STATE = Path('/var/lib/private/homecompute-video-api')
UNIT_NAME = 'homecompute-video-api.service'
BIN = '/run/current-system/sw/bin'
SOURCES = {'video_api_service.py': 'scripts/video_api_service.py',
           'homecompute_videos.py': 'deploy/open-webui/functions/homecompute_videos.py'}
UNIT = f'''# Managed by HomeCompute video API setup.
[Unit]
Description=Private authenticated-gateway video API worker
After=network-online.target docker.service homecompute-comfyui-firewall.service
Wants=network-online.target
[Service]
Type=simple
DynamicUser=yes
StateDirectory=homecompute-video-api
StateDirectoryMode=0700
WorkingDirectory={ROOT}
ExecStart={BIN}/python3 {ROOT}/video_api_service.py --state /var/lib/homecompute-video-api
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0077
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
IPAddressDeny=any
IPAddressAllow=172.28.200.2/32
[Install]
WantedBy=multi-user.target
'''


def host_check() -> None:
    if platform.system() != 'Linux' or socket.gethostname().split('.')[0] != 'home-core' or os.geteuid() != 0:
        raise ValueError('Run as root on Linux home-core')


def safe(path: Path, *, directory=False) -> None:
    for item in [*reversed(path.absolute().parents), path]:
        if item.is_symlink():
            raise ValueError(f'Symlink deployment path refused: {item}')
        if item.exists() and (item.stat().st_uid != 0 or item.stat().st_mode & 0o022):
            raise ValueError(f'Expected root-owned, non-writable path: {item}')
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f'Unexpected deployment path type: {path}')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def targets() -> dict:
    return {**{name: ROOT / name for name in SOURCES}, 'unit': UNITS / UNIT_NAME}


def boot_link() -> Path:
    return UNITS / 'multi-user.target.wants' / UNIT_NAME


def enabled() -> bool:
    link = boot_link()
    safe(link.parent, directory=True)
    if link.is_symlink():
        if link.lstat().st_uid != 0 or os.readlink(link) != str(UNITS / UNIT_NAME):
            raise ValueError('Refusing an unmanaged video API boot link')
        return True
    if link.exists():
        raise ValueError('The video API boot link must not be a regular file')
    return False


def baseline() -> dict:
    result = {}
    for name, path in targets().items():
        safe(path)
        result[name] = digest(path.read_bytes()) if path.exists() else None
    if result['unit'] is not None and not (UNITS / UNIT_NAME).read_text().startswith('# Managed by HomeCompute video API setup.\n'):
        raise ValueError('Refusing an unmanaged video API unit')
    result['enabled'] = enabled()
    return result


def service_active() -> bool:
    result = subprocess.run([f'{BIN}/systemctl', 'is-active', '--quiet', UNIT_NAME],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    if result.returncode not in (0, 3, 4):
        raise RuntimeError('Could not inspect the fixed video API service')
    return result.returncode == 0


def control(action: str) -> None:
    if action not in {'daemon-reload', 'restart', 'stop'}:
        raise ValueError('Unknown service action')
    args = [f'{BIN}/systemctl', action] + ([] if action == 'daemon-reload' else [UNIT_NAME])
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)


def verify_unit() -> None:
    subprocess.run([f'{BIN}/systemd-analyze', 'verify', str(UNITS / UNIT_NAME)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)


def active_jobs(*, required=False) -> int:
    """Read only the fixed worker database, including systemd's managed state link."""
    if WORKER_STATE.is_symlink():
        if WORKER_STATE.lstat().st_uid != 0 or os.readlink(WORKER_STATE) not in (
                'private/homecompute-video-api', str(PRIVATE_STATE)):
            raise ValueError('Untrusted worker state directory link')
    if not WORKER_STATE.exists():
        if required:
            raise ValueError('Cannot inspect the active video worker queue')
        return 0
    state = WORKER_STATE.resolve(strict=True)
    if state not in (WORKER_STATE, PRIVATE_STATE) or not state.is_dir():
        raise ValueError('Unexpected worker state directory')
    database = state / 'jobs.sqlite3'
    if database.is_symlink() or database.exists() and not database.is_file():
        raise ValueError('Untrusted worker job database')
    if not database.exists():
        if required:
            raise ValueError('Cannot inspect the active video worker queue')
        return 0
    try:
        with closing(sqlite3.connect('file:' + quote(str(database), safe='/') + '?mode=ro', uri=True, timeout=3)) as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','in_progress')").fetchone()[0]
    except sqlite3.Error:
        raise ValueError('Cannot verify the private video worker queue') from None


def require_idle(was_active: bool) -> None:
    if active_jobs(required=was_active):
        raise ValueError('Video jobs are queued or in progress; retry the update after they finish')


def mkdir(path: Path, mode=0o755) -> None:
    safe(path, directory=True)
    if not path.exists():
        path.mkdir(parents=True, mode=mode)
    path.chmod(mode)


def publish(path: Path, data: bytes, mode=0o644) -> None:
    safe(path.parent, directory=True)
    safe(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchown(handle.fileno(), 0, 0)
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(source: Path) -> tuple[dict, dict]:
    payload = {}
    for name, relative in SOURCES.items():
        path = source / relative
        if any(item.is_symlink() for item in [*path.absolute().parents, path]):
            raise ValueError('Reviewed source paths must not use symlinks')
        payload[name] = path.read_bytes()
        compile(ast.parse(payload[name], filename=relative), relative, 'exec')
    payload['unit'] = UNIT.encode()
    before = baseline()
    hashes = {name: digest(value) for name, value in payload.items()}
    fingerprint = digest(json.dumps({'source': hashes, 'installed': before}, sort_keys=True).encode())
    changed = any(before[name] != value for name, value in hashes.items()) or not before['enabled']
    return {'read_only': True, 'plan_sha256': fingerprint, 'source_sha256': hashes,
            'installed_sha256': before, 'changes_needed': changed, 'unit': UNIT_NAME,
            'bind': '172.28.200.1:18194', 'allowed_peer': '172.28.200.2',
            'engine': 'http://172.28.200.2:8188', 'state': '/var/lib/homecompute-video-api',
            'actions': ['Back up the two installed Python files and fixed service unit privately',
                        'Atomically publish root-owned files and the restricted DynamicUser unit',
                        'Enable and restart only homecompute-video-api.service'],
            'unchanged': ['LLM service', 'Caddy configuration', 'model containers', 'gateway configuration']}, payload


def apply(plan: dict, payload: dict) -> dict:
    if baseline() != plan['installed_sha256']:
        raise ValueError('Installed files changed after the reviewed plan')
    if not plan['changes_needed']:
        return {'applied': False, 'changed': False, 'unit': UNIT_NAME}
    was_active = service_active()
    require_idle(was_active)
    originals = {name: path.read_bytes() if path.exists() else None for name, path in targets().items()}
    mkdir(BACKUPS, 0o700)
    backup = BACKUPS / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    mkdir(backup, 0o700)
    for name, value in originals.items():
        if value is not None:
            publish(backup / (UNIT_NAME if name == 'unit' else name), value, 0o600)
    publish(backup / 'manifest.json', (json.dumps({**plan, 'was_active': was_active}, indent=2) + '\n').encode(), 0o600)
    restarted = False
    try:
        mkdir(ROOT)
        mkdir(UNITS)
        mkdir(boot_link().parent)
        for name, path in targets().items():
            publish(path, payload[name])
        verify_unit()
        require_idle(was_active)
        if not enabled():
            boot_link().symlink_to(UNITS / UNIT_NAME)
        control('daemon-reload')
        restarted = True
        control('restart')
        deadline = time.monotonic() + 30
        while not service_active():
            if time.monotonic() >= deadline:
                raise TimeoutError('The video API service did not become active')
            time.sleep(1)
        return {'applied': True, 'changed': True, 'unit': UNIT_NAME, 'backup': str(backup)}
    except Exception as error:
        failures = []
        if restarted and not was_active:
            try:
                control('stop')
            except Exception:
                failures.append('could not stop the new service')
        for name, path in targets().items():
            try:
                if originals[name] is None:
                    safe(path)
                    path.unlink(missing_ok=True)
                else:
                    publish(path, originals[name])
            except Exception:
                failures.append(f'could not restore {name}')
        try:
            if not plan['installed_sha256']['enabled'] and enabled():
                boot_link().unlink()
            control('daemon-reload')
            if was_active and restarted:
                control('restart')
        except Exception:
            failures.append('could not restore service activation')
        recovery = '; '.join(failures) if failures else 'previous files and activation restored'
        raise RuntimeError(f'Video API installation failed: {type(error).__name__}; {recovery}; backup: {backup}') from error


def run(source: Path, *, do_apply=False, expected_plan=None) -> dict:
    host_check()
    plan, payload = prepare(source)
    if not do_apply:
        return plan
    if not expected_plan or expected_plan != plan['plan_sha256']:
        raise ValueError('Apply requires the matching --expected-plan from the read-only plan')
    return apply(plan, payload)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.source, do_apply=args.apply, expected_plan=args.expected_plan), indent=2))
    except Exception as error:
        parser.exit(1, f'{error}\n')
