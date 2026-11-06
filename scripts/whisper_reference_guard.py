"""Owner-bound resource guard for the isolated Whisper reference diagnostic."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import time
from pathlib import Path

GIB = 1024 ** 3
LIMIT = 6 * GIB

class Refused(RuntimeError):
    pass

class MonitorUnavailable(Refused):
    pass

class MemoryFloor(Refused):
    def __init__(self, sample):
        super().__init__('Host memory floor observed')
        self.sample = sample

def busy(value):
    return value['inference_busy']

def command(args, *, timeout=30, log=None, env=None):
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE, timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('Command failed or timed out; no automatic destructive recovery') from None
    if result.returncode:
        raise Refused('Command refused; inspect the private lifecycle receipt')
    return '' if log else result.stdout.decode().strip()

def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused('State directory must be owned by this user with mode 0700')

def write_json(path, value, *, replace=False):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6)) if replace else path
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(temporary, path)

def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid() or (info.st_size > 128000):
            raise Refused('Unsafe lifecycle journal')
        return json.load(stream)

def same_trial(first, second):
    return all(first.get(key) == second.get(key) for key in ('owner', 'name', 'container_id', 'image_id'))

def memory(*, loading=False):
    try:
        fields = dict(re.findall('^(MemTotal|MemAvailable):\\s+(\\d+) kB$', Path('/proc/meminfo').read_text(), re.MULTILINE))
        total, available = (int(fields[key]) * 1024 for key in ('MemTotal', 'MemAvailable'))
        if not 0 <= available <= total or total <= 0:
            raise ValueError
    except (OSError, KeyError, ValueError):
        raise MonitorUnavailable('Host memory counters unavailable') from None
    sample = {'total_bytes': total, 'available_bytes': available, 'trial_limit_bytes': LIMIT}
    if available < total / 10 + (LIMIT if loading else 0):
        raise MemoryFloor(sample)
    return sample

def monitor_sample(info, previous=None):
    previous = previous or {}
    minimum = previous.get('minimum_host_available_bytes')
    failure = previous.get('first_observed_failure')
    sample = None
    reason = None
    try:
        sample = memory()
    except MemoryFloor as error:
        sample = error.sample
        reason = 'host_memory_floor'
    except Refused:
        reason = 'monitor_unavailable'
    if sample is not None:
        minimum = min(minimum, sample['available_bytes']) if minimum is not None else sample['available_bytes']
    if reason is not None and failure is None:
        failure = {'reason': reason, 'monotonic': time.monotonic(), 'memory': sample}
    return {**info, 'last_sample_monotonic': time.monotonic(), 'minimum_host_available_bytes': minimum, 'qualification_failed': failure is not None, 'first_observed_failure': failure}


class Guard:
    def __init__(self, runtime, state):
        self.runtime = runtime
        self.state = Path(state)

    @contextlib.contextmanager
    def lock(self):
        fd = os.open(self.state / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def remove_active(self, record):
        path = self.state / 'active.json'
        if path.exists():
            if not same_trial(record, read_json(path)):
                raise Refused('Active journal changed; refusing to remove another trial')
            path.unlink()

    def latch_resource_failure(self, record, failure):
        if not re.fullmatch('[0-9a-f]{24}', record['owner']):
            raise Refused('Invalid failure owner')
        value = {**{k: record[k] for k in ('owner', 'name', 'container_id', 'image_id')}, 'first_observed_failure': failure}
        try:
            write_json(self.state / ('observed-resource-failure-' + record['owner'] + '.json'), value)
        except FileExistsError:
            pass

    def include_latched_failure(self, record, value):
        path = self.state / ('observed-resource-failure-' + record['owner'] + '.json')
        if path.exists():
            failure = read_json(path)
            if not same_trial(record, failure):
                raise Refused('Resource failure belongs to another candidate')
            value = {**value, 'qualification_failed': True, 'first_observed_failure': failure['first_observed_failure']}
            sample = failure['first_observed_failure'].get('memory')
            if sample is not None:
                minimum = value.get('minimum_host_available_bytes')
                value['minimum_host_available_bytes'] = min(minimum, sample['available_bytes']) if minimum is not None else sample['available_bytes']
        return value

    def sample(self, record, *, loading=False):
        try:
            return memory(loading=loading)
        except (MemoryFloor, MonitorUnavailable) as error:
            reason = "host_memory_floor" if isinstance(error, MemoryFloor) else "monitor_unavailable"
            self.latch_resource_failure(record, {"reason": reason, "monotonic": time.monotonic(), "memory": getattr(error, "sample", None)})
            raise

    def emergency_reason(self, record):
        try:
            self.sample(record)
        except MonitorUnavailable:
            self.latch_resource_failure(record, {'reason': 'monitor_unavailable', 'monotonic': time.monotonic(), 'memory': None})
            return 'monitor_unavailable'
        except MemoryFloor as error:
            self.latch_resource_failure(record, {'reason': 'host_memory_floor', 'monotonic': time.monotonic(), 'memory': error.sample})
            return 'host_memory_floor'
        try:
            same_boot = record.get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            same_boot = False
        if not same_boot:
            self.latch_resource_failure(record, {'reason': 'monitor_unavailable', 'monotonic': time.monotonic(), 'memory': None})
            return 'monitor_unavailable'
        now = time.monotonic()
        if now >= record['trial_deadline_monotonic']:
            return 'trial_deadline'
        if record.get('request_deadline_monotonic') is not None and now >= record['request_deadline_monotonic']:
            return 'request_deadline'
        if record['phase'] in ('loading', 'awaiting_guard') and now >= record['loading_deadline_monotonic']:
            return 'startup_deadline'
        return None

    def terminate_owned(self, record, *, loading=False):
        actual = self.runtime.owned_container(record)
        if actual['State']['Running']:
            if not loading and busy(self.runtime.health(record, require_ready=False)):
                raise Refused('Shadow request is active; stop refused without signalling')
            command(['docker', 'kill', '--signal', 'TERM', actual['Id']])
            for _ in range(60):
                actual = self.runtime.owned_container(record)
                if not actual['State']['Running']:
                    break
                time.sleep(1)
            else:
                raise Refused('Cooperative shutdown incomplete; no force kill was attempted')
        command(['docker', 'rm', actual['Id']])
        return {'status': 'stopped', 'container_id': actual['Id'], 'force_kill': False}

    def emergency_abort(self, record):
        reason = self.emergency_reason(record)
        if reason is None:
            raise Refused('No observed resource or recorded deadline emergency; use idle stop')
        actual = self.runtime.owned_container(record)
        evidence = self.runtime.cgroup_snapshot(actual)
        forced = False
        if actual['State']['Running']:
            command(['docker', 'kill', '--signal', 'TERM', actual['Id']])
            for _ in range(10):
                actual = self.runtime.owned_container(record)
                if not actual['State']['Running']:
                    break
                time.sleep(1)
            if actual['State']['Running']:
                command(['docker', 'kill', '--signal', 'KILL', actual['Id']])
                forced = True
                actual = self.runtime.owned_container(record)
                if actual['State']['Running']:
                    raise Refused('Candidate emergency termination not confirmed')
        command(['docker', 'rm', actual['Id']])
        result = {'status': 'failed_emergency', 'reason': reason, 'container_id': actual['Id'], 'force_kill': forced, 'partial_output_valid': False, 'cgroup_before_termination': evidence,
                  'oom_killed': actual['State'].get('OOMKilled'), 'restart_count': actual.get('RestartCount')}
        write_json(self.state / ('emergency-' + secrets.token_hex(8) + '.json'), result)
        self.remove_active(record)
        return result

    def run(self):
        """Foreground companion, required while the private trial submits requests."""
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        with self.lock():
            record = read_json(self.state / 'active.json')
            if self.runtime.source_digest() != record['helper_sha256']:
                raise Refused('Guard helper identity changed')
            self.runtime.owned_container(record)
            try:
                self.alive(record)
            except (Refused, OSError, KeyError):
                pass
            else:
                raise Refused('A verified continuous guard is already active')
            info = {**{k: record[k] for k in ('owner', 'name', 'container_id', 'image_id')},
                    'pid': os.getpid(), 'start_ticks': Path('/proc/self/stat').read_text().split()[21],
                    'helper_sha256': record['helper_sha256']}
            previous = None
            try:
                prior = read_json(self.state / 'guard.json')
                if same_trial(record, prior):
                    previous = prior
            except (OSError, Refused):
                pass
            previous = monitor_sample(info, previous)
            if previous['qualification_failed']:
                self.latch_resource_failure(record, previous['first_observed_failure'])
            previous = self.include_latched_failure(record, previous)
            write_json(self.state / 'guard.json', previous, replace=True)
        print(json.dumps({'guard_ready': True, **info}), flush=True)
        while (self.state / 'active.json').exists():
            try:
                current = read_json(self.state / 'active.json')
            except FileNotFoundError:
                break
            if not same_trial(record, current):
                break
            previous = monitor_sample(info, previous)
            if previous['qualification_failed']:
                self.latch_resource_failure(record, previous['first_observed_failure'])
            previous = self.include_latched_failure(record, previous)
            write_json(self.state / 'guard.json', previous, replace=True)
            if self.emergency_reason(current):
                try:
                    with self.lock():
                        current = read_json(self.state / 'active.json')
                        if not same_trial(record, current):
                            break
                        if self.emergency_reason(current):
                            self.emergency_abort(current)
                            raise Refused('Shadow trial aborted; discard any concurrent request result')
                except (BlockingIOError, FileNotFoundError):
                    pass
            time.sleep(0.1)
        return {'status': 'guard_finished_after_cleanup'}

    def request_mark(self, record, *, finish_id=None):
        if finish_id is None:
            if record['phase'] != 'ready':
                raise Refused('Native startup has not been verified')
            if self.alive(record).get('qualification_failed') is not False:
                raise Refused('Observed resource failure invalidates this trial')
        if self.emergency_reason(record):
            raise Refused('Trial already exceeds a resource or deadline guard')
        if busy(self.runtime.health(record, require_ready=finish_id is None)):
            raise Refused('Request marker changes require an idle adapter')
        if finish_id is None:
            if record.get('request_id'):
                raise Refused('Previous request remains armed; no deadline extension allowed')
            record['request_id'] = secrets.token_hex(12)
            record['request_deadline_monotonic'] = time.monotonic() + 630
            result = {key: record[key] for key in ('request_id', 'request_deadline_monotonic')}
        else:
            if record.get('request_id') != finish_id:
                raise Refused('Request marker identity mismatch')
            del record['request_id']
            del record['request_deadline_monotonic']
            result = {'request_id': finish_id, 'status': 'request_finished_idle'}
        write_json(self.state / 'active.json', record, replace=True)
        return result

    def alive(self, record):
        if self.runtime.source_digest() != record['helper_sha256']:
            raise Refused('Guard helper source identity changed')
        value = read_json(self.state / 'guard.json')
        if not same_trial(record, value) or value.get('helper_sha256') != record['helper_sha256'] or not 0 <= time.monotonic() - value['last_sample_monotonic'] <= 2:
            raise Refused('Continuous guard is not current')
        proc = Path('/proc') / str(value['pid'])
        if proc.stat().st_uid != os.geteuid() or (proc / 'stat').read_text().split()[21] != value['start_ticks']:
            raise Refused('Continuous guard process identity changed')
        return self.include_latched_failure(record, value)
