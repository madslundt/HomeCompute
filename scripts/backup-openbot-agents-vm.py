#!/usr/bin/env python3
"""Capture the agents VM into the existing encrypted NAS repository."""
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
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UNIT = 'homecompute-agents-vm.service'
SYNC_TIMER = 'homecompute-assistant-context-sync.timer'
SYNC_SERVICE = 'homecompute-assistant-context-sync.service'
DISK = Path('/srv/state/agents-vm/agents.qcow2')
STATE = Path('/var/lib/homecompute/openbot-scoped')
RECEIPT = STATE / 'agents-vm-backup.json'
REPOSITORY = Path('/mnt/homecompute-backups/restic-homecompute-selected')
TAG = 'homecompute-openbot-agents-vm'


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load(ROOT / 'scripts/retire-openclaw-core.py')


def live_nas():
    base = core.protected(Path('/var/lib/homecompute/deployed-revision')).decode().strip()
    release = Path('/srv/homecompute/releases') / base
    if (not re.fullmatch('[0-9a-f]{40}', base)
            or Path('/srv/homecompute/current').resolve(strict=True) != release
            or core.command('git', '-C', str(release), 'rev-parse', 'HEAD') != base):
        raise ValueError('Active Home Core release identity must agree')
    name = 'scripts/nas-recovery-backup.py'
    path = release / name
    if core.protected(path) != subprocess.check_output(
            ['git', '-C', str(release), 'show', f'{base}:{name}'], timeout=10):
        raise ValueError('Active NAS lifecycle differs from Git')
    nas = load(path)
    nas.require_mount(nas.load_configuration(nas.CONFIG, mount_only=True))
    nas.private_file(nas.PASSWORD)
    nas.root_directory(REPOSITORY)
    return nas


def stopped():
    state = core.command('systemctl', 'show', UNIT, '-p', 'ActiveState', '-p', 'MainPID')
    return set(state.splitlines()) == {'ActiveState=inactive', 'MainPID=0'}


def command(*args, timeout=120):
    subprocess.run(args, check=True, timeout=timeout, stdout=subprocess.DEVNULL)


def capture(directory, seed):
    info = DISK.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0 or info.st_mode & 0o022:
        raise ValueError('Expected the managed agents qcow2 disk')
    if shutil.disk_usage(directory).free < info.st_size + 2 * 1024**3:
        raise ValueError('Insufficient space for the private staged VM copy')
    if core.command('systemctl', 'is-active', UNIT) != 'active':
        raise ValueError('Expected the agents VM to be active before the attended pause')
    pid = core.command('systemctl', 'show', UNIT, '-p', 'MainPID', '--value')
    if not re.fullmatch('[1-9][0-9]*', pid):
        raise ValueError('Expected a live QEMU process')
    emulator = (Path('/proc') / pid / 'exe').resolve(strict=True)
    qemu_img = emulator.with_name('qemu-img').resolve(strict=True)
    if (not str(emulator).startswith('/nix/store/') or not emulator.name.lstrip('.').startswith('qemu-system-')
            or not str(qemu_img).startswith('/nix/store/')):
        raise ValueError('Expected the immutable agents VM emulator')
    tool_info = qemu_img.stat()
    if not stat.S_ISREG(tool_info.st_mode) or tool_info.st_uid != 0 or tool_info.st_mode & 0o222:
        raise ValueError('Expected immutable qemu-img from the active VM package')
    try:
        command('systemctl', 'stop', UNIT)
        if not stopped():
            raise ValueError('Agents VM did not stop completely')
        captured_at = datetime.now(timezone.utc).isoformat()
        disk_size = DISK.stat().st_size
        command('cp', '--sparse=always', '--', str(DISK), str(directory / DISK.name), timeout=900)
        shutil.copyfile(seed, directory / 'seed.iso')
    finally:
        # Finish resuming the guest even if a second stop signal arrives.
        previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            command('systemctl', 'start', UNIT)
            if core.command('systemctl', 'is-active', UNIT) != 'active':
                raise ValueError('Agents VM did not resume')
        finally:
            signal.signal(signal.SIGTERM, previous)
    if (directory / DISK.name).stat().st_size != disk_size:
        raise ValueError('Staged VM disk size differs from the captured disk')
    copied = directory / DISK.name
    image = json.loads(subprocess.check_output(
        [str(qemu_img), 'info', '--output=json', str(copied)], timeout=30))
    if image.get('format') != 'qcow2' or 'backing-filename' in image or 'data-file' in image.get('format-specific', {}).get('data', {}):
        raise ValueError('Expected a self-contained qcow2 copy')
    command(str(qemu_img), 'check', '-q', str(copied), timeout=900)
    return {'captured_at': captured_at,
            'disk_bytes': disk_size, 'seed_sha256': hashlib.sha256(seed.read_bytes()).hexdigest()}


def verify_snapshot(nas, value):
    snapshot, directory = value['snapshot'], value['directory']
    if not re.fullmatch('[0-9a-f]{64}', snapshot) or not re.fullmatch(
            r'/var/lib/homecompute/openbot-scoped/agents-vm-[a-z0-9_]{8}', directory):
        raise ValueError('Backup receipt must identify a scoped encrypted snapshot')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['captured_at'])).total_seconds()
    if not 0 <= age <= 36 * 3600:
        raise ValueError('Agents VM backup is outside the 36-hour readiness window')
    # Explicitly verify the immutable snapshot rather than trusting a date or NAS tag.
    snapshots = json.loads(nas.restic(['--no-lock', 'snapshots', '--json', snapshot], repository=REPOSITORY))
    if (len(snapshots) != 1 or snapshots[0]['id'] != snapshot
            or snapshots[0].get('paths') != [directory] or TAG not in snapshots[0].get('tags', [])):
        raise ValueError('Encrypted snapshot identity differs from the receipt')
    entries = [json.loads(line) for line in
               nas.restic(['--no-lock', 'ls', '--json', snapshot], repository=REPOSITORY).splitlines()]
    files = {row['path']: row.get('size') for row in entries if row.get('type') == 'file'}
    manifest = directory + '/manifest.json'
    if (set(files) != {manifest, directory + '/' + DISK.name, directory + '/seed.iso'}
            or files[directory + '/' + DISK.name] != value['disk_bytes']):
        raise ValueError('Encrypted snapshot lacks the complete agents VM bundle')
    if json.loads(nas.restic(['--no-lock', 'dump', snapshot, manifest], repository=REPOSITORY)) != {
            key: value[key] for key in ('captured_at', 'disk_bytes', 'seed_sha256')}:
        raise ValueError('Encrypted snapshot manifest differs from the captured bundle')


def wait_for_context_sync():
    # Match the collector's five-minute systemd start timeout. A timer may
    # already have queued a job before stop even if the service is inactive.
    deadline = time.monotonic() + 300
    while True:
        state = core.command('systemctl', 'show', SYNC_SERVICE, '-p', 'ActiveState', '-p', 'Job')
        fields = set(state.splitlines())
        if 'Job=' in fields and fields & {'ActiveState=inactive', 'ActiveState=failed'}:
            return
        if time.monotonic() >= deadline:
            raise ValueError('Assistant-context sync did not finish before the VM backup')
        time.sleep(1)


@contextmanager
def pause_context_sync():
    state = core.command('systemctl', 'show', SYNC_TIMER, '-p', 'ActiveState', '--value')
    if state not in {'active', 'inactive', 'failed'}:
        raise ValueError('Assistant-context sync timer is changing state')
    restore = state == 'active'
    try:
        if restore:
            command('systemctl', 'stop', SYNC_TIMER)
        wait_for_context_sync()
        yield
    finally:
        if restore:
            # A second termination request must not interrupt timer cleanup.
            previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                command('systemctl', 'start', SYNC_TIMER)
            finally:
                signal.signal(signal.SIGTERM, previous)


def backup(nas):
    STATE.mkdir(mode=0o700, exist_ok=True)
    nas.root_directory(STATE)
    seed = load(ROOT / 'scripts/application-recovery.py').agents_seed()
    with pause_context_sync(), tempfile.TemporaryDirectory(prefix='agents-vm-', dir=STATE) as work:
        directory = Path(work)
        metadata = capture(directory, seed)
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps(metadata, sort_keys=True) + '\n')
        nas.require_mount(nas.load_configuration(nas.CONFIG, mount_only=True))
        records = [json.loads(line) for line in nas.restic(
            ['backup', '--json', '--tag', TAG, '--', str(directory)], repository=REPOSITORY).splitlines()]
        summaries = [row['snapshot_id'] for row in records if row.get('message_type') == 'summary']
        if len(summaries) != 1:
            raise ValueError('Restic did not confirm one completed scoped backup')
        value = {**metadata, 'snapshot': summaries[0], 'directory': str(directory)}
        verify_snapshot(nas, value)
        temporary = directory / '.receipt'
        temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
        temporary.replace(RECEIPT)


def interrupted(_signal, _frame):
    raise InterruptedError('Scoped backup interrupted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'verify'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--acknowledge-guest-pause', action='store_true')
    args = parser.parse_args()
    if args.operation == 'backup' and not args.acknowledge_guest_pause:
        parser.error('Backup requires explicit acknowledgement of the agents VM pause')
    try:
        core.release(args.revision)
        os.umask(0o077)
        nas = live_nas()
        if args.operation == 'backup':
            signal.signal(signal.SIGTERM, interrupted)
            with core.LOCK.open('r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                backup(nas)
        else:
            verify_snapshot(nas, json.loads(core.protected(RECEIPT, private=True)))
        print('Verified fresh encrypted snapshot of the agents VM and seed.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'OpenBot VM backup operation stopped ({type(error).__name__}); check the guest and backup state.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
