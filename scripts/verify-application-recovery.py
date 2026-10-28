#!/usr/bin/env python3
"""Verify restored engines in isolated disposable guests; never restore production."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import signal
import stat
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path('/srv/restore-tests')
STATE = Path('/var/lib/homecompute/nas-recovery-backup')
QEMU_ROOT = Path('/nix/var/nix/gcroots/homecompute-codex-vm/qemu')
FILES = ('scripts/verify-application-recovery.py', 'config/recovery-verification.json',
         'scripts/application-recovery.py')
PG_SCOPES = {'control-plane', 'mattermost', 'immich', 'chessmate'}
VM_DISKS = {'agents-vm': ['agents.qcow2'], 'codex-vm': ['root.qcow2', 'work.qcow2']}
LABEL = 'org.homecompute.recovery-test'
MAX_SQL = 8 * 1024**3
MAX_LOG = 16 * 1024**2
MAX_VM_FILE = 512 * 1024**2
ENV = {'PATH': '/run/current-system/sw/bin', 'DOCKER_HOST': 'unix:///var/run/docker.sock',
       'DOCKER_CONFIG': '/var/empty', 'HOME': '/var/empty', 'LANG': 'C', 'LC_ALL': 'C'}
TERMINAL_SIGNALS = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}


class VerificationError(Exception):
    """Only fixed categories leave the private workspace."""


class CleanupError(VerificationError):
    """Do not start another engine when owned cleanup cannot be proved."""


def run(arguments: list[str], *, timeout=30, check=True, **kwargs):
    return subprocess.run(arguments, check=check, timeout=timeout, env=ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


@contextmanager
def defer_terminal_signals():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINAL_SIGNALS)
    try: yield previous
    finally: signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def protected(path: Path, *, directory=False, limit=None):
    info = path.lstat()
    if (info.st_uid != 0 or info.st_mode & 0o077 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            limit is not None and info.st_size > limit):
        raise VerificationError('private-input-required')
    return info


def regular(path: Path):
    # Restored files retain original owner/mode inside the private parent.
    if not stat.S_ISREG(path.lstat().st_mode):
        raise VerificationError('regular-restored-file-required')


def engine_input(path: Path, target: Path):
    if target not in path.parents:
        raise VerificationError('engine-input-outside-restore-tree')
    for parent in path.parents:
        if parent == target: break
        if parent.is_symlink(): raise VerificationError('symlinked-engine-input-parent')
    regular(path)


def policy() -> dict:
    value = json.loads((ROOT / 'config/recovery-verification.json').read_bytes())
    if value.get('schema_version') != 1 or set(value.get('postgres', {})) != PG_SCOPES or set(value.get('vms', {})) != set(VM_DISKS):
        raise VerificationError('policy-scope-mismatch')
    for name, item in value['postgres'].items():
        if (not re.fullmatch(r'sha256:[0-9a-f]{64}', item['image_id']) or
                (item['uid'], item['gid'], item['major']) != ((999, 999, 14) if name == 'immich' else (70, 70, 17 if name == 'chessmate' else 16)) or
                not re.fullmatch('[a-z][a-z0-9_]{0,62}', item['database']) or
                not isinstance(item['extensions'], dict) or not item['extensions'] or
                any(not re.fullmatch('[a-z][a-z0-9_-]{0,62}', key) or not re.fullmatch('[0-9]+(?:\\.[0-9]+)*', version)
                    for key, version in item['extensions'].items()) or
                item['preload'] != ('vchord.so,vectors.so' if name == 'immich' else '')):
            raise VerificationError('policy-postgres-mismatch')
    for name, item in value['vms'].items():
        if (item['disks'] != VM_DISKS[name] or item['seed'] != 'seed.iso' or
                item['user'] != 'homecompute-' + name or
                item['hostname'] != ('agents' if name == 'agents-vm' else 'codex-worker')):
            raise VerificationError('policy-vm-mismatch')
    return value


def published_source() -> str:
    revision = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.decode().strip()
    if not re.fullmatch('[0-9a-f]{40}', revision) or ROOT != Path('/srv/homecompute/releases') / revision:
        raise VerificationError('published-release-required')
    run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', revision, 'origin/master'])
    for filename in FILES:
        if run(['git', '-C', str(ROOT), 'show', revision + ':' + filename]).stdout != (ROOT / filename).read_bytes():
            raise VerificationError('source-bytes-mismatch')
    return revision


def restored_input(target: Path, snapshot: str, archive: str) -> str:
    protected(SCRATCH, directory=True)
    if not target.is_absolute() or target.parent != SCRATCH or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', target.name):
        raise VerificationError('isolated-restore-directory-required')
    protected(target, directory=True)
    protected(STATE / 'last-application-verification.json', limit=65536)
    receipt = json.loads((STATE / 'last-application-verification.json').read_bytes())
    if (not re.fullmatch('[0-9a-f]{64}', snapshot) or
            not re.fullmatch(r'homecompute-applications-[0-9]{8}T[0-9]{6}\.[0-9]{6}Z\.tar', archive) or
            receipt.get('snapshot') != snapshot or receipt.get('archive') != archive or
            receipt.get('verification', {}).get('gzip_dumps_checked') != 4 or
            receipt.get('verification', {}).get('qcow2_disks_checked') != 3 or
            receipt.get('verification', {}).get('sqlite_integrity') != 'ok'):
        raise VerificationError('matching-archive-verification-required')
    # Safe incidental relative links are allowed by archive extraction. Engine
    # inputs and all their ancestors are independently required to be real.
    count = 0
    for parent, directories, files in os.walk(target, followlinks=False):
        count += len(directories) + len(files)
        if count > 100000: raise VerificationError('restore-tree-entry-bound-exceeded')
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                link = os.readlink(path)
                if link.startswith('/') or '..' in Path(link).parts or not path.resolve().is_relative_to(target.resolve()):
                    raise VerificationError('external-restored-symlink-requires-review')
    manifest_path = target / 'manifest.json'; engine_input(manifest_path, target)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get('postgres') != sorted(PG_SCOPES) or manifest.get('vms') != sorted(VM_DISKS):
        raise VerificationError('manifest-scope-mismatch')
    seed = target / 'state/agents-vm/seed.iso'; engine_input(seed, target)
    recorded = manifest.get('agents_seed', {})
    if (seed.stat().st_size > 32 * 1024**2 or recorded.get('archive_path') != 'state/agents-vm/seed.iso' or
            hashlib.sha256(seed.read_bytes()).hexdigest() != recorded.get('sha256')):
        raise VerificationError('archived-seed-mismatch')
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if receipt.get('restore_directory') != str(target) or receipt.get('manifest_sha256') != digest:
        raise VerificationError('verified-restore-target-binding-required')
    return digest


def pg_command(item: dict, name: str, nonce: str, bootstrap: str) -> list[str]:
    uid, gid = item['uid'], item['gid']
    # Immich's pinned vendor entrypoint materializes its public config template
    # here before starting PostgreSQL. Preserve that entrypoint with one small
    # private tmpfs, rather than making the image root writable.
    vendor_config = ['--tmpfs', f'/etc/postgresql:rw,nosuid,nodev,noexec,size=1048576,mode=0700,uid={uid},gid={gid}'] if item['major'] == 14 else []
    return ['docker', 'create', '--name', name, '--label', LABEL + '=' + nonce,
            '--pull=never', '--network=none', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
            '--user', f'{uid}:{gid}', '--read-only', '--memory=1g', '--memory-swap=1g', '--cpus=1', '--pids-limit=128',
            '--log-driver=none', '--tmpfs', f'/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=805306368,mode=0700,uid={uid},gid={gid}',
            '--tmpfs', f'/var/run/postgresql:rw,nosuid,nodev,noexec,size=16777216,mode=0700,uid={uid},gid={gid}',
            '--tmpfs', f'/tmp:rw,nosuid,nodev,noexec,size=67108864,mode=0700,uid={uid},gid={gid}',
            *vendor_config,
            '--env', 'PGDATA=/var/lib/postgresql/data', '--env', 'POSTGRES_USER=' + bootstrap,
            '--env', 'POSTGRES_DB=' + bootstrap, '--env', 'POSTGRES_HOST_AUTH_METHOD=trust',
            item['image_id'], 'postgres', '-c', 'listen_addresses=', '-c', 'max_connections=20',
            '-c', 'shared_buffers=64MB', '-c', 'shared_preload_libraries=' + item['preload']]


def remove_owned(name: str, identifier: str | None, nonce: str) -> None:
    output = run(['docker', 'inspect', name, '--format', '{{json .}}'], check=False)
    if output.returncode != 0:
        if identifier is None and b'No such object' in output.stderr:
            return
        raise CleanupError('container-cleanup-inspection-failed')
    item = json.loads(output.stdout)
    found = item.get('Id', '')
    if (not re.fullmatch('[0-9a-f]{64}', found) or identifier is not None and found != identifier or
            item.get('Config', {}).get('Labels', {}).get(LABEL) != nonce):
        raise CleanupError('container-cleanup-ownership-mismatch')
    try: run(['docker', 'rm', '--force', '--volumes', found])
    except Exception as error: raise CleanupError('owned-container-removal-failed') from error


def container_isolated(identifier: str, item: dict, nonce: str) -> None:
    value = json.loads(run(['docker', 'inspect', identifier, '--format', '{{json .}}']).stdout)
    host = value.get('HostConfig', {}); config = value.get('Config', {})
    tmpfs = {'/var/lib/postgresql/data', '/var/run/postgresql', '/tmp'}
    if item['major'] == 14: tmpfs.add('/etc/postgresql')
    if (value.get('Id') != identifier or value.get('Image') != item['image_id'] or
            config.get('User') != f"{item['uid']}:{item['gid']}" or config.get('Labels', {}).get(LABEL) != nonce or
            host.get('NetworkMode') != 'none' or host.get('ReadonlyRootfs') is not True or
            host.get('Privileged') is not False or host.get('CapDrop') != ['ALL'] or
            host.get('PortBindings') not in (None, {}) or host.get('PublishAllPorts') is not False or
            host.get('Binds') not in (None, []) or
            any(mount.get('Type') != 'tmpfs' for mount in value.get('Mounts', [])) or
            set(host.get('Tmpfs', {})) != tmpfs):
        raise VerificationError('temporary-postgres-isolation-mismatch')


def sql_file(source: Path, destination: Path) -> None:
    regular(source)
    total = 0
    with gzip.open(source, 'rb') as data, destination.open('xb') as output:
        while chunk := data.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_SQL:
                raise VerificationError('dump-size-bound-exceeded')
            output.write(chunk)


def postgres_verify(scope: str, item: dict, target: Path, work: Path) -> dict:
    nonce = uuid.uuid4().hex; name = 'homecompute-restore-pg-' + nonce
    bootstrap = 'hc_restore_' + nonce
    image = json.loads(run(['docker', 'image', 'inspect', item['image_id']]).stdout)
    if len(image) != 1 or image[0].get('Id') != item['image_id']:
        raise VerificationError('exact-local-postgres-image-unavailable')
    if set(image[0].get('Config', {}).get('Volumes') or {}) - {'/var/lib/postgresql/data'}:
        raise VerificationError('unexpected-image-volume-declaration')
    dump = target / 'databases' / (scope + '-postgres.sql.gz'); engine_input(dump, target)
    sql = work / (scope + '.sql'); sql_file(dump, sql)
    identifier = None
    try:
        created = run(pg_command(item, name, nonce, bootstrap)).stdout.decode().strip()
        if not re.fullmatch('[0-9a-f]{64}', created):
            raise VerificationError('container-create-identity-missing')
        identifier = created
        container_isolated(identifier, item, nonce)
        run(['docker', 'start', identifier])
        deadline = time.monotonic() + 60
        while True:
            result = run(['docker', 'exec', identifier, 'pg_isready', '-q', '-U', bootstrap, '-d', bootstrap], check=False)
            if result.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise VerificationError('postgres-start-timeout')
            time.sleep(1)
        with sql.open('rb') as data, (work / (scope + '-import.stdout')).open('xb') as output, (work / (scope + '-import.stderr')).open('xb') as errors:
            subprocess.run(['docker', 'exec', '-i', identifier, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', bootstrap, '-d', bootstrap],
                           stdin=data, stdout=output, stderr=errors, env=ENV, timeout=600, check=True, preexec_fn=bounded_output)
        # Query only metadata/counts; never rows. Exact extension versions prove
        # the restored database used the reviewed image's actual engine/plugins.
        query = "SELECT json_build_object('extensions',(SELECT json_object_agg(extname,extversion) FROM pg_extension),'tables',(SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')),'major',current_setting('server_version_num')::int/10000);"
        output = run(['docker', 'exec', identifier, 'psql', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', bootstrap, '-d', item['database'], '-c', query]).stdout
        result = json.loads(output)
        if result.get('extensions') != item['extensions'] or result.get('major') != item['major'] or not isinstance(result.get('tables'), int) or result['tables'] < 1:
            raise VerificationError('restored-postgres-metadata-mismatch')
        return {'verified': True, 'tables': result['tables'], 'extensions': len(result['extensions']), 'image_id': item['image_id']}
    finally:
        with defer_terminal_signals(): remove_owned(name, identifier, nonce)
        sql.unlink(missing_ok=True)


def vm_command(executable: str, scope: str, descriptors: list[int]) -> list[str]:
    args = [executable, '-name', 'homecompute-restore-' + scope, '-machine', 'q35,accel=kvm', '-cpu', 'host',
            '-smp', '1', '-m', '2048', '-nodefaults', '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
            '-no-reboot', '-nic', 'none', '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',
            '-device', 'virtio-rng-pci']
    for index, descriptor in enumerate(descriptors, 1):
        args += ['-add-fd', f'fd={descriptor},set={index}']
    if scope == 'agents-vm':
        args += ['-drive', 'file=/dev/fdset/1,if=virtio,format=qcow2,snapshot=on',
                 '-drive', 'file=/dev/fdset/2,if=virtio,format=raw,readonly=on']
    else:
        args += ['-drive', 'file=/dev/fdset/1,if=none,id=codexroot,format=qcow2,snapshot=on',
                 '-device', 'virtio-blk-pci,drive=codexroot,bootindex=1',
                 '-drive', 'file=/dev/fdset/2,if=none,id=codexwork,format=qcow2,snapshot=on',
                 '-device', 'virtio-blk-pci,drive=codexwork,serial=hc-codex-work',
                 '-drive', 'file=/dev/fdset/3,if=virtio,format=raw,readonly=on']
    return args


def boot_proven(log: bytes, hostname: str) -> bool:
    # A new private serial log must show kernel + userspace + the intended
    # guest's actual serial login prompt. Running QMP is intentionally absent.
    clean = re.sub(rb'\x1b\[[0-9;]*[A-Za-z]', b'', log)
    return (b'Linux version ' in clean and re.search(rb'systemd\[1\]', clean) is not None and
            re.search(rb'(?:^|[\r\n])' + hostname.encode() + rb' login: *', clean) is not None)


def vm_identity(item: dict):
    # Keep the production VM account's signal/file authority out of this test.
    account = pwd.getpwnam('nobody'); kvm = grp.getgrnam('kvm')
    if account.pw_uid == 0 or not Path('/dev/kvm').is_char_device() or not hasattr(os, 'unshare'):
        raise VerificationError('isolated-kvm-prerequisite-unavailable')
    return account, kvm.gr_gid


def bounded_output(file_limit: int = MAX_LOG) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))


def vm_child(uid: int, gid: int, kvm_gid: int) -> None:
    os.unshare(os.CLONE_NEWNET)
    # RLIMIT_FSIZE also governs temporary COW disks, not just the log FDs.
    # Keep SQL/import output at 16 MiB, but allow bounded normal guest writes.
    bounded_output(MAX_VM_FILE)
    resource.setrlimit(resource.RLIMIT_CPU, (360, 360))
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    os.setgroups([kvm_gid]); os.setgid(gid); os.setuid(uid)


def stop_owned(process: subprocess.Popen):
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired as error: raise CleanupError('owned-vm-cleanup-timeout') from error


def private_logs_bounded(*paths: Path) -> None:
    if any(path.stat().st_size > MAX_LOG for path in paths):
        raise VerificationError('vm-private-log-size-bound-exceeded')


@contextmanager
def owned_process(arguments: list[str], **kwargs):
    # Popen may have created the child before returning its handle. Keep all
    # terminal signals pending until that handle is recorded inside the outer
    # finally; child setup restores the original mask before dropping privilege.
    process = None
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, TERMINAL_SIGNALS)
    setup = kwargs.pop('preexec_fn', None)
    def child_setup():
        signal.pthread_sigmask(signal.SIG_SETMASK, previous - TERMINAL_SIGNALS)
        if setup: setup()
    try:
        try:
            process = subprocess.Popen(arguments, preexec_fn=child_setup, **kwargs)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        yield process
    finally:
        if process is not None:
            with defer_terminal_signals(): stop_owned(process)


def vm_verify(scope: str, item: dict, target: Path, work: Path) -> dict:
    executable = QEMU_ROOT.resolve(strict=True) / 'bin/qemu-system-x86_64'
    info = executable.stat()
    if (not str(executable).startswith('/nix/store/') or info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode)):
        raise VerificationError('protected-exact-qemu-required')
    account, kvm_gid = vm_identity(item)
    guest = work / scope; guest.mkdir(mode=0o700); os.chown(guest, account.pw_uid, account.pw_gid)
    inputs = [target / 'state' / scope / name for name in item['disks'] + [item['seed']]]
    for path in inputs:
        engine_input(path, target)
    for path in inputs[:-1]:
        metadata = json.loads(run([str(executable.parent / 'qemu-img'), 'info', '--output=json', str(path)]).stdout)
        if metadata.get('format') != 'qcow2' or metadata.get('backing-filename'):
            raise VerificationError('standalone-restored-qcow2-required')
    log = work / (scope + '-serial.log'); errors = work / (scope + '-qemu.stderr')
    with ExitStack() as stack:
        streams = [stack.enter_context(path.open('rb')) for path in inputs]
        descriptors = [stream.fileno() for stream in streams]
        output = stack.enter_context(log.open('xb')); error = stack.enter_context(errors.open('xb'))
        with owned_process(vm_command(str(executable), scope, descriptors), stdin=subprocess.DEVNULL,
                           stdout=output, stderr=error, cwd=guest, env={**ENV, 'TMPDIR': '.'},
                           pass_fds=descriptors, preexec_fn=lambda: vm_child(account.pw_uid, account.pw_gid, kvm_gid)) as process:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                private_logs_bounded(log, errors)
                if boot_proven(log.read_bytes(), item['hostname']):
                    return {'verified': True, 'kernel_userspace_login_prompt': True, 'network': False,
                            'vcpus': 1, 'memory_mib': 2048, 'application_services_verified': False}
                if process.poll() is not None:
                    raise VerificationError('vm-exited-before-boot-proof')
                time.sleep(1)
            raise VerificationError('vm-boot-proof-timeout')


def error_category(error: Exception) -> str:
    if isinstance(error, VerificationError): return str(error)
    if isinstance(error, subprocess.TimeoutExpired): return 'engine-operation-timeout'
    if isinstance(error, subprocess.CalledProcessError): return 'engine-command-failed'
    if isinstance(error, FileNotFoundError): return 'prerequisite-file-unavailable'
    if isinstance(error, PermissionError): return 'prerequisite-permission-denied'
    return 'engine-verification-error'


def verify(target: Path, snapshot: str, archive: str, revision: str) -> tuple[dict, Path]:
    manifest_hash = restored_input(target, snapshot, archive)
    settings = policy()
    with (SCRATCH / '.engine-verification.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        work = Path(tempfile.mkdtemp(prefix='engines-', dir=SCRATCH)); work.chmod(0o700)
        # VM UID enters its own child workdir before losing root. It does not
        # need traversal access through the private parent or restored tree.
        result = {'schema_version': 1, 'source_revision': revision, 'snapshot': snapshot, 'archive': archive,
                  'created_at': datetime.now(timezone.utc).isoformat(),
                  'manifest_sha256': manifest_hash,
                  'postgres': {scope: {'verified': False, 'error_category': 'not-attempted'} for scope in settings['postgres']},
                  'vms': {scope: {'verified': False, 'error_category': 'not-attempted'} for scope in settings['vms']},
                  'production_modified': False}
        cleanup_failed = False
        for scope, item in settings['postgres'].items():
            started = time.monotonic()
            try: result['postgres'][scope] = postgres_verify(scope, item, target, work)
            except Exception as error:
                result['postgres'][scope] = {'verified': False, 'error_category': error_category(error)}
                if isinstance(error, CleanupError):
                    cleanup_failed = True
                    break
            finally: result['postgres'][scope]['elapsed_seconds'] = round(time.monotonic() - started, 3)
        for scope, item in settings['vms'].items():
            if cleanup_failed: break
            started = time.monotonic()
            try: result['vms'][scope] = vm_verify(scope, item, target, work)
            except Exception as error:
                result['vms'][scope] = {'verified': False, 'error_category': error_category(error)}
                if isinstance(error, CleanupError): cleanup_failed = True
            finally: result['vms'][scope]['elapsed_seconds'] = round(time.monotonic() - started, 3)
        result['postgres_import_verified'] = all(row['verified'] for row in result['postgres'].values())
        result['vm_boot_verified'] = all(row['verified'] for row in result['vms'].values())
        result['application_services_verified'] = False
        receipt = work / 'receipt.json'; receipt.write_text(json.dumps(result, sort_keys=True) + '\n')
        return result, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-directory', required=True, type=Path)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0: raise VerificationError('trusted-root-operator-required')
    os.umask(0o077)
    def interrupted(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    revision = published_source()
    result, receipt = verify(args.restore_directory, args.snapshot, args.archive, revision)
    print(json.dumps({**result, 'receipt': str(receipt)}, sort_keys=True))
    if not result['postgres_import_verified'] or not result['vm_boot_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt:
        print(json.dumps({'verified': False, 'error_category': 'operator-interrupted'}))
        raise SystemExit(130)
    except Exception as error:
        print(json.dumps({'verified': False, 'error_category': error_category(error)}, sort_keys=True))
        raise SystemExit(1)
