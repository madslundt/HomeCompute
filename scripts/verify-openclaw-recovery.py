#!/usr/bin/env python3
"""Inspect actual restored native state in an unprivileged networkless COW VM."""
from contextlib import ExitStack
from datetime import datetime, timezone
import argparse
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery_engine', ROOT / 'scripts/verify-application-recovery.py')
ENGINE = importlib.util.module_from_spec(spec); spec.loader.exec_module(ENGINE)
PROBE = 'deploy/openclaw/recovery/inspect-restored-state.py'
XORRISO = Path('/nix/var/nix/gcroots/homecompute-codex-vm/xorriso/bin/xorriso')


def seed(nonce, expected):
    content = base64.b64encode((ROOT / PROBE).read_bytes()).decode()
    data = {'hostname': 'agents', 'preserve_hostname': True, 'ssh_deletekeys': False,
            'network': {'config': 'disabled'}, 'package_update': False, 'package_upgrade': False,
            'write_files': [{'path': '/run/homecompute-inspect-restored-state.py', 'owner': 'root:root',
                             'permissions': '0500', 'encoding': 'b64', 'content': content}],
            'runcmd': [['python3', '/run/homecompute-inspect-restored-state.py', '--nonce', nonce,
                        '--expected-policy-sha256', expected]]}
    return '#cloud-config\n' + json.dumps(data)


def result_from_log(raw, nonce):
    prefix = ('HC_OPENCLAW_RESTORE_' + nonce + ' ').encode()
    matches = [line[len(prefix):] for line in raw.splitlines() if line.startswith(prefix)]
    if len(matches) != 1 or len(matches[0]) > 16384:
        return None
    result = json.loads(matches[0])
    if not isinstance(result, dict):
        raise ENGINE.VerificationError('invalid-native-proof')
    return result


def accepted(result):
    return (all(result.get(key) is True for key in ('native_policy_verified', 'native_sqlite_integrity',
                 'native_conversations_verified', 'authenticated_gateway_verified', 'required_plugins_loaded',
                 'runtime_version_verified'))
            and result.get('roles') == 12 and type(result.get('native_sqlite_databases')) is int
            and result['native_sqlite_databases'] >= 1
            and result.get('gateway_plugin_errors') == 0 and result.get('openclaw_version') == '2026.9.1'
            and result.get('image') == 'sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a')


def command(executable, descriptors):
    args = ENGINE.vm_command(str(executable), 'agents-vm', descriptors)
    args[args.index('-m') + 1] = '8192'
    return args


def child(uid, gid, kvm):
    ENGINE.vm_child(uid, gid, kvm, memory_limit=12 * 1024**3, cpu_seconds=900,
                    file_limit=2 * 1024**3)


def verify(target, snapshot, archive, expected, revision):
    manifest = ENGINE.restored_input(target, snapshot, archive)
    ENGINE.protected(ENGINE.STATE / 'last-engine-verification.json', limit=65536)
    previous = json.loads((ENGINE.STATE / 'last-engine-verification.json').read_bytes())
    if (any(previous.get(k) != v for k, v in {'snapshot': snapshot, 'archive': archive,
            'manifest_sha256': manifest, 'postgres_import_verified': True, 'vm_boot_verified': True}.items())):
        raise ENGINE.VerificationError('matching-engine-proof-required')
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) * 1024
    if available < 16 * 1024**3 or shutil.disk_usage(ENGINE.SCRATCH).free < 32 * 1024**3:
        raise ENGINE.VerificationError('insufficient-isolated-vm-headroom')
    executable = ENGINE.QEMU_ROOT.resolve(strict=True) / 'bin/qemu-system-x86_64'
    for tool in (executable, XORRISO.resolve(strict=True)):
        info = tool.stat()
        if not str(tool).startswith('/nix/store/') or info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
            raise ENGINE.VerificationError('protected-nix-tool-required')
    account, kvm = ENGINE.vm_identity({'scope': 'agents-vm'})
    work = Path(tempfile.mkdtemp(prefix='openclaw-native-', dir=ENGINE.SCRATCH)); work.chmod(0o700)
    guest = work / 'guest'; guest.mkdir(mode=0o700); os.chown(guest, account.pw_uid, account.pw_gid)
    nonce = str(uuid.uuid4())
    for name, value in {'user-data': seed(nonce, expected),
                        'meta-data': json.dumps({'instance-id': 'hc-openclaw-' + nonce, 'local-hostname': 'agents', 'dsmode': 'local'}),
                        'network-config': json.dumps({'version': 2, 'ethernets': {}})}.items():
        (work / name).write_text(value)
    iso = work / 'probe-seed.iso'
    ENGINE.run([str(XORRISO), '-as', 'mkisofs', '-quiet', '-volid', 'cidata', '-joliet', '-rock',
                '-output', str(iso), str(work / 'user-data'), str(work / 'meta-data'), str(work / 'network-config')])
    disk = target / 'state/agents-vm/agents.qcow2'; ENGINE.engine_input(disk, target)
    before = disk.stat()
    log = work / 'serial.log'; errors = work / 'qemu.stderr'
    result = None
    with ExitStack() as stack:
        streams = [stack.enter_context(path.open('rb')) for path in (disk, iso)]
        output = stack.enter_context(log.open('xb')); error = stack.enter_context(errors.open('xb'))
        with ENGINE.owned_process(command(executable, [f.fileno() for f in streams]), stdin=subprocess.DEVNULL,
                stdout=output, stderr=error, cwd=guest, env={**ENGINE.ENV, 'TMPDIR': '.'},
                pass_fds=[f.fileno() for f in streams], preexec_fn=lambda: child(account.pw_uid, account.pw_gid, kvm)) as process:
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                ENGINE.private_logs_bounded(log, errors)
                result = result_from_log(log.read_bytes(), nonce)
                if result is not None:
                    break
                if process.poll() is not None:
                    raise ENGINE.VerificationError('isolated-native-vm-exited')
                time.sleep(2)
            if result is None:
                raise ENGINE.VerificationError('isolated-native-proof-timeout')
    after = disk.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ENGINE.VerificationError('restored-source-disk-changed')
    report = {**result, 'schema_version': 1, 'source_revision': revision, 'created_at': datetime.now(timezone.utc).isoformat(),
              'snapshot': snapshot, 'archive': archive, 'manifest_sha256': manifest,
              'production_modified': False, 'network': False, 'source_disk_read_only': True,
              'restore_directory': str(target), 'accepted': accepted(result)}
    receipt = work / 'receipt.json'; receipt.write_text(json.dumps(report, sort_keys=True))
    return report, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-directory', required=True, type=Path)
    parser.add_argument('--snapshot', required=True); parser.add_argument('--archive', required=True)
    parser.add_argument('--expected-policy-sha256', required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core' or not re.fullmatch('[0-9a-f]{64}', args.expected_policy_sha256):
        raise ENGINE.VerificationError('trusted-core-operator-and-policy-required')
    os.umask(0o077)
    revision = ENGINE.published_source()
    for name in ('scripts/verify-openclaw-recovery.py', PROBE):
        if ENGINE.run(['git', '-C', str(ROOT), 'show', revision + ':' + name]).stdout != (ROOT / name).read_bytes():
            raise ENGINE.VerificationError('native-probe-source-mismatch')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGHUP, interrupted)
    if not args.worker:
        unit = 'homecompute-openclaw-restore-' + uuid.uuid4().hex + '.scope'
        dispatch = Path(tempfile.mkdtemp(prefix='openclaw-native-dispatch-', dir=ENGINE.SCRATCH))
        dispatch.chmod(0o700)
        command = ['systemd-run', '--scope', '--collect', '--quiet', '--unit', unit,
                   '--property=MemoryHigh=9G', '--property=MemoryMax=10G', '--property=IOWeight=10',
                   '--property=IOReadBandwidthMax=/srv/restore-tests 40M',
                   '--property=IOWriteBandwidthMax=/srv/restore-tests 20M',
                   sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--worker']
        try:
            completed = ENGINE.run(command, timeout=1050, check=False)
            (dispatch / 'worker.stdout').write_bytes(completed.stdout)
            (dispatch / 'worker.stderr').write_bytes(completed.stderr)
        except BaseException as error:
            (dispatch / 'worker.stdout').write_bytes(getattr(error, 'stdout', None) or b'')
            (dispatch / 'worker.stderr').write_bytes(getattr(error, 'stderr', None) or b'')
            raise
        finally:
            ENGINE.run(['systemctl', 'stop', unit], check=False, timeout=60)
            members = Path('/sys/fs/cgroup/system.slice') / unit / 'cgroup.procs'
            if members.exists() and members.read_text().strip():
                raise ENGINE.CleanupError('owned-native-scope-cleanup-unverified')
        sys.stdout.write(completed.stdout.decode())
        if completed.returncode:
            raise SystemExit('isolated native worker failed; private host diagnostics retained')
        return
    group = Path('/proc/self/cgroup').read_text().strip().split('::')[-1]
    if not re.fullmatch(r'/system.slice/homecompute-openclaw-restore-[0-9a-f]{32}\.scope', group):
        raise ENGINE.VerificationError('owned-resource-scope-required')
    with (ENGINE.SCRATCH / '.engine-verification.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, receipt = verify(args.restore_directory, args.snapshot, args.archive, args.expected_policy_sha256, revision)
    print(json.dumps({**result, 'receipt': str(receipt)}, sort_keys=True))
    if not result['accepted']:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('isolated native verification interrupted; owned VM stopped')
