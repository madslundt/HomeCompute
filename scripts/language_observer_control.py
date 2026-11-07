"""Owned, CPU-only diagnostic worker and independent absolute-deadline guardian."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_observer_artifacts', ROOT / 'scripts/language_observer_artifacts.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
PIN, _LOCK = A.pins()
LABEL = 'io.homecompute.language-observer.owner'
LIMIT = PIN['container_memory_bytes']
INNER = '/opt/language-observer/'


def external(path: str) -> Path:
    value = Path(path)
    require(value.is_absolute() and value.resolve(strict=True) == value and ',' not in path and
            ROOT not in (value, *value.parents), 'Canonical external path required')
    return value


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(arguments: list[str], *, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    value = subprocess.run(arguments, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    require(len(value.stdout) + len(value.stderr) <= 2_000_000, 'Bounded command output required')
    require(not check or value.returncode == 0, 'Owned diagnostic command failed')
    return value


def mounts_match(record: dict, row: dict) -> bool:
    mounts = row['Mounts']
    bound = [m for m in mounts if m.get('Type') == 'bind']
    temporary = row['HostConfig'].get('Tmpfs') or {}
    required = {'rw', 'noexec', 'nosuid', 'nodev', 'size=256m', 'uid=10001', 'gid=10001', 'mode=0700'}
    return (set(temporary) == {'/tmp'} and set(temporary['/tmp'].split(',')) == required and
            row['HostConfig'].get('ShmSize') == 32 * 1024 * 1024 and
            all(m.get('Type') == 'bind' or m.get('Type') == 'tmpfs' and m['Destination'] == '/tmp' for m in mounts) and
            not any(m.get('RW') for m in bound) and
            sorted((m['Source'], m['Destination']) for m in bound) == sorted(tuple(x) for x in record['mounts']))


def memory(*, loading: bool = False) -> dict:
    text = Path('/proc/meminfo').read_text()
    fields = dict(re.findall(r'^(MemTotal|MemAvailable):\s+(\d+) kB$', text, re.M))
    require(set(fields) == {'MemTotal', 'MemAvailable'}, 'Host memory counters unavailable')
    total, available = int(fields['MemTotal']) * 1024, int(fields['MemAvailable']) * 1024
    require(available >= total / 10 + (LIMIT if loading else 0), 'Host memory floor/reserve breached')
    return {'total': total, 'available': available}


def process(pid: int) -> str | None:
    try:
        row = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return row[19] if row[0] not in ('Z', 'X') else None
    except (OSError, IndexError):
        return None


def authorization(path: Path, digest: str, revision: str, operation: str) -> dict:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size <= 1_000_000,
            'Private owned authorization required')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == digest, 'Authorization SHA differs')
    value = json.loads(raw)
    require(value.get('root_reviewed') is True and value.get('authorized') is True and
            value.get('scope') == 'auxiliary-language-observer-only' and
            value.get('source_revision') == revision and operation in value.get('operations', []),
            'Explicit root-reviewed operation authorization required')
    require(re.fullmatch('[a-f0-9]{40}', revision), 'Published source SHA required')
    require(run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.decode().strip() == revision and
            not run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']).stdout.strip(),
            'Clean selected source required')
    remote = run(['git', '-C', str(ROOT), 'remote', 'get-url', 'origin']).stdout.decode().strip()
    require(remote in ('https://github.com/madslundt/HomeCompute.git', 'git@github.com:madslundt/HomeCompute.git'),
            'Source origin differs')
    advertised = run(['git', '-C', str(ROOT), 'ls-remote', 'origin',
                      'refs/heads/codex/whisper-language-observer']).stdout.decode().split()
    require(advertised == [revision, 'refs/heads/codex/whisper-language-observer'],
            'Selected experimental source is not the published branch head')
    return value


class Controller:
    def __init__(self, state: Path, auth: dict):
        require(os.geteuid() != 0, 'Use the unprivileged owned diagnostic account')
        require(str(state) == auth.get('lifecycle_directory'), 'Frozen lifecycle directory differs')
        self.state, self.auth = state, auth
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = state.lstat()
        require(not state.is_symlink() and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o700, 'Private owned lifecycle state required')
        self.active = state / 'active.json'

    @contextmanager
    def lock(self):
        fd = os.open(self.state / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self) -> dict:
        info = self.active.lstat()
        require(not self.active.is_symlink() and info.st_uid == os.geteuid() and
                stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600,
                'Lifecycle journal ownership differs')
        value = A.read_json(self.active)
        require(value['source_revision'] == self.auth['source_revision'] and
                value['image_id'] == self.auth['image_id'], 'Lifecycle owner source/image differs')
        return value

    def save(self, value: dict) -> None:
        A.write(self.active, value)

    def inspect(self, record: dict, *, absent: bool = False, isolation: bool = True) -> dict | None:
        result = run(['docker', 'container', 'inspect', record.get('container_id') or record['name']], check=False)
        if result.returncode:
            require(absent and (b'No such container' in result.stderr or b'No such object' in result.stderr),
                    'Owned worker absence is unverified')
            return None
        row = json.loads(result.stdout)[0]
        host, config = row['HostConfig'], row['Config']
        require((not record.get('container_id') or row['Id'] == record['container_id']) and row['Image'] == record['image_id'] and
                row['Name'] == '/' + record['name'] and (config.get('Labels') or {}).get(LABEL) == record['owner'],
                'Worker ownership changed')
        if isolation:
            require(host['Memory'] == LIMIT and host['MemorySwap'] == LIMIT and host['NanoCpus'] == 4_000_000_000 and
                    host['NetworkMode'] == 'none' and not host.get('PortBindings') and
                    not host.get('DeviceRequests') and not host.get('Devices') and host['ReadonlyRootfs'] is True and
                    config['User'] == '10001:10001' and host['RestartPolicy']['Name'] == 'no' and
                    host['LogConfig']['Type'] == 'none' and host['Privileged'] is False and
                    host.get('CapDrop') == ['ALL'] and 'no-new-privileges' in host.get('SecurityOpt', []) and
                    host.get('Init') is True and host['PidsLimit'] == 128 and
                    mounts_match(record, row) and
                    all(item in config['Env'] for item in ['CUDA_VISIBLE_DEVICES=', 'NVIDIA_VISIBLE_DEVICES=void']),
                    'Worker isolation changed')
        return row

    def guard_alive(self, record: dict) -> bool:
        guard = record.get('guardian') or {}
        return (type(guard.get('pid')) is int and process(guard['pid']) == guard.get('start_ticks') and
                guard.get('boot_id') == record['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                and not record.get('failed'))

    def start(self, mode: str) -> dict:
        require(mode in ('export', 'verify', 'classify'), 'Explicit worker mode required')
        image = json.loads(run(['docker', 'image', 'inspect', self.auth['image_id']]).stdout)[0]
        labels = image.get('Config', {}).get('Labels') or {}
        require(image.get('Id') == self.auth['image_id'] and image.get('Os') == 'linux' and
                image.get('Architecture') == 'arm64' and
                labels.get('io.homecompute.source-revision') == self.auth['source_revision'] and
                labels.get('io.homecompute.language-observer.scope') == 'experimental-cpu-classification-only' and
                labels.get('io.homecompute.language-observer.base') == PIN['base_image_id'],
                'Actual worker image provenance differs')
        memory(loading=True)
        with self.lock():
            require(not self.active.exists(), 'Prior worker requires normal close; no retry')
            fd = os.open(self.state / ('attempt-' + mode + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'source_revision': self.auth['source_revision'], 'mode': mode, 'retry_allowed': False}, stream)
            owner = secrets.token_hex(12)
            record = {'owner': owner, 'name': 'hc-language-observer-' + owner,
                      'source_revision': self.auth['source_revision'], 'image_id': self.auth['image_id'],
                      'mode': mode, 'phase': 'awaiting_guard', 'used_ids': [], 'job': None,
                      'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      'deadline': time.monotonic() + PIN['trial_seconds'], 'failed': False, 'mounts': []}
            args = ['docker', 'run', '--detach', '--name', record['name'], '--label', LABEL + '=' + owner,
                    '--init', '--network', 'none', '--read-only', '--memory', str(LIMIT), '--memory-swap', str(LIMIT),
                    '--cpus', '4', '--pids-limit', '128', '--shm-size', '32m', '--log-driver', 'none',
                    '--restart', 'no', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                    '--user', '10001:10001', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700',
                    '--env', 'HOME=/tmp', '--env', 'CUDA_VISIBLE_DEVICES=', '--env', 'NVIDIA_VISIBLE_DEVICES=void']
            if mode == 'export':
                A.verify(external(self.auth['public_artifacts']))
                record['mounts'].append((self.auth['public_artifacts'] + '/model', '/model'))
                args += ['--mount', 'type=bind,src=' + self.auth['public_artifacts'] + '/model,dst=/model,readonly']
            else:
                external(self.auth['graph_directory'])
                record['mounts'].append((self.auth['graph_directory'], '/graph'))
                args += ['--mount', 'type=bind,src=' + self.auth['graph_directory'] + ',dst=/graph,readonly']
                if mode == 'classify':
                    external(self.auth['parity_report'])
                    record['mounts'].append((self.auth['parity_report'], '/parity.json'))
                    args += ['--mount', 'type=bind,src=' + self.auth['parity_report'] + ',dst=/parity.json,readonly']
            args += ['--entrypoint', 'python3', record['image_id'], '-I', '-B', '-c',
                     'import time\nwhile True: time.sleep(1)']
            self.save(record)
            record['container_id'] = run(args).stdout.decode().strip()
            self.save(record)
            self.inspect(record)
            return record

    def fail(self, reason: str) -> None:
        with self.lock():
            record = self.read()
            record.update(failed=True, failure_reason=reason, phase='failed')
            self.save(record)
        row = self.inspect(record, absent=True, isolation=False)
        if row:
            stopped = run(['docker', 'stop', '--time', '5', row['Id']], timeout=15, check=False)
            with self.lock():
                current = self.read()
                current['failure_stop_returncode'] = stopped.returncode
                current['forced_termination_unknown'] = True
                self.save(current)
        # Preserve container/journal on uncertainty; no foreign stop or implicit retry.

    def guard(self) -> dict:
        pid = os.getpid()
        with self.lock():
            record = self.read()
            require(record.get('guardian') is None, 'Guardian may not be replaced or extended')
            identity = {'pid': pid, 'start_ticks': process(pid), 'boot_id': record['boot_id']}
            require(identity['start_ticks'], 'Guardian identity unavailable')
            record['guardian'] = identity
            self.save(record)
        while self.active.exists():
            with self.lock():
                if not self.active.exists():
                    break
                record = self.read()
            require(record['guardian'] == identity, 'Guardian owner changed')
            if record['phase'] == 'stopping':
                if time.monotonic() >= min(record['deadline'], record['stopping_since'] + 30):
                    self.fail('close_or_absolute_deadline')
                    raise ValueError('Owned close deadline failed')
                time.sleep(0.25)
                continue
            if record['failed']:
                self.fail('latched_failure')
                raise ValueError('Diagnostic phase failed')
            reason = None
            try:
                sample = memory()
                row = self.inspect(record)
                require(row['State']['Running'] and not row['State']['OOMKilled'] and row['RestartCount'] == 0,
                        'Worker exited or restarted')
                program = ('from pathlib import Path;import json; p=Path("/sys/fs/cgroup"); '
                           'print(json.dumps({n:(p/n).read_text() for n in '
                           '["memory.events","memory.swap.current","memory.swap.max"]}))')
                counters = json.loads(run(['docker', 'exec', row['Id'], 'python3', '-I', '-B', '-c', program], timeout=5).stdout)
                events = dict(line.split() for line in counters['memory.events'].splitlines())
                require({'max', 'oom', 'oom_kill'} <= set(events) and all(int(v) == 0 for v in events.values()) and
                        int(counters['memory.swap.current']) == int(counters['memory.swap.max']) == 0,
                        'Worker memory/swap event occurred')
                require(time.monotonic() < record['deadline'] and
                        (record['job'] is None or time.monotonic() < record['job']['deadline']), 'Absolute deadline reached')
                with self.lock():
                    current = self.read()
                    current['minimum_host_available'] = min(current.get('minimum_host_available', sample['available']), sample['available'])
                    current['last_guard_sample'] = time.monotonic()
                    self.save(current)
            except Exception as error:
                reason = type(error).__name__
            if reason:
                self.fail(reason)
                raise ValueError('Owned diagnostic guard failed')
            time.sleep(0.25)
        return {'guardian_exit': 0, **identity}

    def command(self, mode: str, case: str) -> list[str]:
        if mode == 'export':
            return ['env', '-u', 'LD_LIBRARY_PATH', '-u', 'LD_PRELOAD', '-u', 'PYTHONPATH', '-u', 'PYTHONHOME',
                    '/opt/observer-export/bin/python', '-I', '-B', INNER + 'export.py',
                    '--model', '/model', '--output', '/tmp/export', '--source', self.auth['source_revision']]
        args = ['python3', '-I', '-B', INNER + 'classify.py', mode,
                '--graph-directory', '/graph', '--manifest-sha256', self.auth['graph_manifest_sha256']]
        if mode == 'verify':
            return [*args, '--report', '/tmp/parity.json']
        return [*args, '--parity-report', '/parity.json', '--parity-sha256', self.auth['parity_sha256'],
                '--approved-warning-sha256', self.auth['approved_warning_sha256']]

    def _job(self, mode: str, case: str, raw: bytes = b'') -> dict:
        require(mode in ('export', 'verify', 'classify') and re.fullmatch('[a-zA-Z0-9_-]{1,64}', case), 'Safe job mode/ID required')
        with self.lock():
            record = self.read()
            require(record['mode'] == mode and record['job'] is None and self.guard_alive(record) and
                    time.monotonic() - record.get('last_guard_sample', 0) <= 5,
                    'Independent live guardian must sample before any model import/load')
            require(case not in record['used_ids'], 'Already attempted; no retry')
            if mode == 'classify':
                allowed = self.auth['inputs'][case]
                require(hashlib.sha256(raw).hexdigest() == allowed['wav_sha256'], 'Frozen private input differs')
                require(len(record['used_ids']) < self.auth['maximum_classifications'] <= 11, 'Classification cap reached')
                spec = importlib.util.spec_from_file_location('_observer_input', A.SOURCE / 'common.py')
                codec = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(codec)
                pcm = codec.pcm(raw)
                require(hashlib.sha256(pcm).hexdigest() == allowed['pcm_sha256'] and
                        len(pcm) // 2 == allowed['frames'], 'Frozen PCM identity differs')
            seconds = PIN[mode + '_seconds']
            require(record['deadline'] - time.monotonic() >= seconds + 5, 'Insufficient absolute phase time')
            record['used_ids'].append(case)
            record['job'] = {'mode': mode, 'case': case, 'deadline': time.monotonic() + seconds}
            record['phase'] = 'working'
            self.save(record)
        self.inspect(record)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            call = subprocess.Popen(['docker', 'exec', '-i', record['container_id'], *self.command(mode, case)],
                                    stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
            self.running = call
            first = True
            while True:
                try:
                    call.communicate(input=raw if first else None, timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    first = False
                    with self.lock():
                        current = self.read()
                    if (not self.guard_alive(current) or time.monotonic() >= current['job']['deadline'] or
                            time.monotonic() - current.get('last_guard_sample', 0) > 15 or
                            os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > 2_000_000):
                        self.fail('guardian_or_job_deadline_or_output_bound')
                        call.terminate()
                        call.wait(timeout=10)
                        raise ValueError('Owned model job aborted; partial output rejected')
            stdout.seek(0)
            payload = stdout.read(1_000_001)
            with self.lock():
                current = self.read()
                if not (call.returncode == 0 and len(payload) <= 1_000_000 and
                        os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size <= 2_000_000 and
                        self.guard_alive(current) and time.monotonic() < current['job']['deadline']):
                    current.update(job=None, phase='failed', failed=True, failure_reason='model_job_failed')
                    self.save(current)
                    raise ValueError('Model job failed; output is not qualified')
                try:
                    result = json.loads(payload)
                except Exception:
                    current.update(job=None, phase='failed', failed=True, failure_reason='invalid_model_output')
                    self.save(current)
                    raise ValueError('Model output is malformed') from None
                current['job'] = None
                current['phase'] = 'idle'
                self.save(current)
                A.create(self.state / ('result-' + record['owner'] + '-' + case + '.json'), result)
            return result

    def job(self, mode: str, case: str, raw: bytes = b'') -> dict:
        try:
            return self._job(mode, case, raw)
        except BaseException:
            # Includes exec launch failures and interrupts. No new model attempt after failure.
            if self.active.exists():
                self.fail('model_job_or_admission_failed')
            call = getattr(self, 'running', None)
            if call is not None and call.poll() is None:
                call.terminate()
                try:
                    call.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    call.kill()
                    call.wait(timeout=5)
            raise

    def collect(self, destination: Path) -> dict:
        with self.lock():
            record = self.read()
            require(record['phase'] == 'idle' and record['job'] is None and not record['failed'] and
                    not record.get('collection') and self.guard_alive(record), 'Successful guarded idle export/verify required')
            require(record['mode'] in ('export', 'verify'), 'Only public model artifacts may be extracted')
        external(str(destination.parent))
        require(not destination.exists() and not destination.is_symlink(), 'New extraction directory required')
        require(A.shutil.disk_usage(destination.parent).free >= PIN['disk_reserve_bytes'] + PIN['maximum_graph_bytes'],
                'Extraction disk reserve insufficient')
        destination.mkdir(mode=0o700)
        try:
            self.inspect(record)
            names = ('observer.ts', 'manifest.json') if record['mode'] == 'export' else ('parity.json',)
            prefix = '/tmp/export/' if record['mode'] == 'export' else '/tmp/'
            for name in names:
                run(['docker', 'cp', record['container_id'] + ':' + prefix + name, str(destination / name)])
            spec = importlib.util.spec_from_file_location('_observer_collect', A.SOURCE / 'common.py')
            codec = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(codec)
            hashes = {name: codec.sha(codec.read(destination / name,
                      PIN['maximum_graph_bytes'] if name == 'observer.ts' else 1_000_000)) for name in names}
            result = A.read_json(self.state / ('result-' + record['owner'] + '-' + record['used_ids'][-1] + '.json'))
            if record['mode'] == 'export':
                manifest = codec.load(destination / 'manifest.json')
                require(hashes['manifest.json'] == result['manifest_sha256'] and
                        hashes['observer.ts'] == manifest['graph_sha256'] == result['graph_sha256'] and
                        manifest['code_sha256'] == codec.code_hashes() and manifest['model'] == PIN and
                        manifest['source_revision'] == record['source_revision'], 'Extracted public graph provenance differs')
            else:
                require(codec.load(destination / 'parity.json') == result and result['passed'] is True,
                        'Extracted runtime parity differs')
            receipt = {'source_revision': record['source_revision'], 'image_id': record['image_id'],
                       'owner': record['owner'], 'container_id': record['container_id'], 'files_sha256': hashes,
                       'copied_before_stop': True, 'directory': str(destination)}
            codec.save(destination / 'extraction.json', receipt)
            for path in destination.iterdir():
                path.chmod(0o444)
            destination.chmod(0o555)
            with self.lock():
                current = self.read()
                require(current['owner'] == record['owner'] and not current['failed'] and self.guard_alive(current),
                        'Guardian failed during extraction')
                current['collection'] = receipt
                self.save(current)
            return receipt
        except BaseException:
            self.fail('public_artifact_extraction_failed')
            raise

    def stop(self) -> dict:
        with self.lock():
            record = self.read()
            require(record['job'] is None or record['failed'], 'Active model job may not be normally stopped')
            require(record['failed'] or record['mode'] == 'classify' or record.get('collection'),
                    'Extract and hash public graph/parity before normal close')
            record['phase'] = 'stopping'
            record['stopping_since'] = time.monotonic()
            self.save(record)
        row = self.inspect(record, absent=True, isolation=not record['failed'])
        if row:
            if row['State']['Running']:
                run(['docker', 'stop', '--time', '10', row['Id']], timeout=20)
            stopped = self.inspect(record, isolation=False)
            require(not stopped['State']['Running'], 'Owned worker stop is unverified')
            record['exit_code'] = stopped['State']['ExitCode']
            record['oom_killed'] = stopped['State']['OOMKilled']
            if record['exit_code'] not in (0, 143) or record['oom_killed'] or stopped['RestartCount'] != 0:
                record.update(failed=True, failure_reason='non_normal_owned_exit')
            run(['docker', 'rm', row['Id']])
        else:
            record.update(failed=True, failure_reason='worker_absent_before_close')
        with self.lock():
            require(self.read()['owner'] == record['owner'], 'Worker ownership changed')
            A.create(self.state / ('closed-' + record['owner'] + '.json'), record)
            self.active.unlink()
        return {'stopped': True, 'owner': record['owner'], 'container_id': record.get('container_id'),
                'qualification_failed': record['failed'], 'normal_idle_close': not record['failed']}
