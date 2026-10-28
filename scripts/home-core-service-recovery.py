#!/usr/bin/env python3
"""Recover existing containers after boot-time network and NAS failures."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

ROOT = Path('/opt/homecompute/service-recovery')
UNITS = Path('/etc/systemd/system.attached')
RUNTIME = Path('/run/homecompute-service-recovery')
TARGETS = {
    'immich': ('homecompute-immich-immich-server-1', 'homecompute-immich', 'immich-server'),
    'moss': ('home-core-moss-tts', 'homecompute-piper-tts', 'moss'),
}


def command(arguments: list[str]) -> str:
    return subprocess.check_output(arguments, text=True, stderr=subprocess.DEVNULL, timeout=35)


def mounted(run=command) -> bool:
    try:
        rows = json.loads(run(['findmnt', '--json', '--mountpoint', '/mnt/immich-nas',
                               '--types', 'cifs', '--output', 'SOURCE,FSTYPE']))['filesystems']
        return len(rows) == 1 and rows[0] == {
            'source': '//192.168.30.236/immich-library', 'fstype': 'cifs'}
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False


def lan_ready(run=command) -> bool:
    rows = json.loads(run(['ip', '-j', '-4', 'address', 'show']))
    return any(address.get('local') == '192.168.30.122'
               for row in rows for address in row.get('addr_info', []))


def recover(target: str, *, repair: bool, run=command) -> dict:
    name, project, service = TARGETS[target]
    try:
        value = json.loads(run(['docker', 'inspect', name]))[0]
    except subprocess.CalledProcessError:
        return {'target': target, 'status': 'not-installed'}
    labels = value['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != service):
        raise ValueError('container ownership mismatch')
    state = value['State']
    if state['Status'] == 'running':
        health = state.get('Health', {}).get('Status', 'unknown')
        return {'target': target, 'status': health}
    # Do not activate deliberately unstarted profiles or replace containers.
    if state['Status'] not in {'exited', 'dead'}:
        return {'target': target, 'status': 'held'}
    error = state.get('Error', '')
    boot_failure = (
        target == 'immich' and 'error mounting' in error and 'no such device' in error
    ) or (
        target == 'moss' and 'failed to bind host port' in error
        and 'cannot assign requested address' in error
    )
    if not boot_failure:
        return {'target': target, 'status': 'held'}
    if not lan_ready(run):
        return {'target': target, 'status': 'waiting-for-lan'}
    if target == 'immich':
        volumes = value.get('Mounts', [])
        if not any(v.get('Source') == '/mnt/immich-nas' and v.get('Destination') == '/data'
                   for v in volumes):
            raise ValueError('Immich library mount changed')
        if not mounted(run):
            if not repair:
                return {'target': target, 'status': 'waiting-for-nas'}
            run(['systemctl', 'start', 'mnt-immich\\x2dnas.mount'])
            if not mounted(run):
                raise ValueError('expected NAS filesystem is not mounted')
    if not repair:
        return {'target': target, 'status': 'recoverable'}
    run(['docker', 'start', name])
    return {'target': target, 'status': 'started'}


def recover_funnel(*, repair: bool, run=command) -> dict:
    def state():
        output = run(['systemctl', 'show', 'ttlock-funnel.service',
                      '-p', 'LoadState', '-p', 'ActiveState'])
        return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    value = state()
    if value.get('LoadState') != 'loaded':
        return {'target': 'ttlock', 'status': 'not-installed'}
    if value.get('ActiveState') == 'active':
        return {'target': 'ttlock', 'status': 'healthy'}
    if value.get('ActiveState') != 'failed':
        return {'target': 'ttlock', 'status': 'held'}
    if not repair:
        return {'target': 'ttlock', 'status': 'recoverable'}
    run(['systemctl', 'restart', 'ttlock-funnel.service'])
    return {'target': 'ttlock', 'status': 'started' if state().get('ActiveState') == 'active' else 'failed'}


def atomic(path: Path, body: bytes, mode: int) -> None:
    if path.is_symlink():
        raise ValueError('refusing symlink destination')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(expected: str) -> dict:
    release = Path(__file__).resolve().parents[1]
    if (release.parent != Path('/srv/homecompute/releases')
            or not re.fullmatch('[0-9a-f]{40}', release.name)
            or command(['git', '-C', str(release), 'rev-parse', 'HEAD']).strip() != release.name
            or command(['git', '-C', str(release), 'status', '--porcelain', '--untracked-files=no']).strip()):
        raise ValueError('install from an unchanged published HomeCompute release')
    source = Path(__file__).read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if expected != digest:
        raise ValueError('reviewed source hash does not match')
    # Dedicated lifecycle follows the existing NixOS attached-unit convention.
    # The Nix module owns the equivalent declaration for full host releases.
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    UNITS.mkdir(parents=True, exist_ok=True)
    script = ROOT / 'home-core-service-recovery.py'
    if script.exists():
        old = script.read_bytes()
        previous = json.loads((ROOT / 'receipt.json').read_text())
        if hashlib.sha256(old).hexdigest() != previous['sha256']:
            raise ValueError('installed recovery helper differs from its receipt')
        archive = ROOT / 'rollback' / previous['sha256']
        atomic(archive / script.name, old, 0o600)
        for name in ['homecompute-service-recovery.service', 'homecompute-service-recovery.timer']:
            atomic(archive / name, (UNITS / name).read_bytes(), 0o600)
    atomic(script, source, 0o644)
    unit = '''[Unit]
Description=Recover existing HomeCompute services after network readiness
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=oneshot
RuntimeDirectory=homecompute-service-recovery
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
Environment=PATH=/run/current-system/sw/bin
Environment=DOCKER_HOST=unix:///var/run/docker.sock
UnsetEnvironment=DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
ExecStart=/run/current-system/sw/bin/python3 /opt/homecompute/service-recovery/home-core-service-recovery.py repair
TimeoutStartSec=120
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
'''
    timer = '''[Unit]
Description=Retry HomeCompute service recovery after boot

[Timer]
OnBootSec=60
OnUnitInactiveSec=120
Unit=homecompute-service-recovery.service

[Install]
WantedBy=timers.target
'''
    atomic(UNITS / 'homecompute-service-recovery.service', unit.encode(), 0o644)
    atomic(UNITS / 'homecompute-service-recovery.timer', timer.encode(), 0o644)
    wants = UNITS / 'timers.target.wants'
    wants.mkdir(exist_ok=True)
    link = wants / 'homecompute-service-recovery.timer'
    expected_link = '../homecompute-service-recovery.timer'
    if link.is_symlink():
        if os.readlink(link) != expected_link:
            raise ValueError('unexpected timer enablement link')
    elif link.exists():
        raise ValueError('timer enablement is not a symlink')
    else:
        link.symlink_to(expected_link)
    atomic(ROOT / 'receipt.json', json.dumps({'sha256': digest}).encode(), 0o600)
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'start', 'homecompute-service-recovery.timer'])
    return {'installed': True, 'sha256': digest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'repair', 'install'])
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        parser.error('run as root on home-core')
    if args.action == 'install':
        if not args.expected_sha256:
            parser.error('install requires --expected-sha256 from reviewed source')
        print(json.dumps(install(args.expected_sha256)))
        return
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    if RUNTIME.is_symlink():
        raise ValueError('runtime directory must not be a symlink')
    with (RUNTIME / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        results = []
        for target in TARGETS:
            try:
                results.append(recover(target, repair=args.action == 'repair'))
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                # Do not print upstream commands or private error bodies.
                results.append({'target': target, 'status': 'failed'})
        try:
            results.append(recover_funnel(repair=args.action == 'repair'))
        except (OSError, subprocess.SubprocessError, ValueError):
            results.append({'target': 'ttlock', 'status': 'failed'})
        print(json.dumps({'services': results}))
        if any(row['status'] in {'failed', 'unhealthy', 'waiting-for-lan', 'waiting-for-nas'}
               for row in results):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
