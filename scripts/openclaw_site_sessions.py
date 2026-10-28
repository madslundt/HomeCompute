"""Owner-controlled private browser sessions with grant-bound lifetime.

Only the owner bridge calls start/fill/ready/close. Model tools receive status
and counts, never display streams, input events, cookies or browser primitives.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess  # nosec B404
import threading
import time
from uuid import uuid4

from openclaw_bitwarden import open_directory
from openclaw_site_executor import DOCKER, NETWORK, SECCOMP, SiteExecutionRefused, reference
from openclaw_site_registry import account_name

TMPFS = '/tmp:rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=1777'  # nosec B108


def docker(arguments, *, timeout=6):
    result = subprocess.run([DOCKER, *arguments], stdin=subprocess.DEVNULL, capture_output=True,  # nosec B603
                            text=True, timeout=timeout, env={'PATH': '/run/current-system/sw/bin', 'HOME': '/root'})
    if result.returncode:
        raise SiteExecutionRefused('Private browser lifecycle unavailable')
    return result.stdout.strip()


class PrivateBrowser:
    def __init__(self, directory: Path, image: str, profile: dict, expires: int, slot: int, guard):
        self.directory, self.image, self.profile, self.expires = directory, image, profile, expires
        self.guard, self.slot = guard, slot
        self.proxy_ip, self.browser_ip = '172.30.192.' + str(10 + slot), '172.30.192.' + str(20 + slot)
        self.ids, self.process, self.buffer = [], None, bytearray()
        self.closed, self.lock = threading.Event(), threading.RLock()
        self.policy_lock = threading.RLock()
        self.ready = False

    def policy(self, allow_public: bool, hosts: list[str]):
        if len(hosts) > 64:
            raise SiteExecutionRefused('Private browser host budget reached')
        for host in hosts:
            account_name(host)
        with self.policy_lock:
            if self.closed.is_set():
                allow_public, hosts = False, []
            path = self.directory / 'policy'
            path.mkdir(mode=0o755, parents=True, exist_ok=True)
            # Only public hostname policy is shared read-only with UID1000;
            # the containing session/state directories remain root-private.
            os.chmod(path, 0o755)  # nosec B103
            temporary = path / 'next.json'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'allow_public': allow_public, 'hosts': hosts}, stream)
            os.replace(temporary, path / 'policy.json')

    def create(self, name: str, network: str, arguments: list[str], *, ip=None, policy=False) -> str:
        if self.closed.is_set() or self.guard() is not True:
            raise SiteExecutionRefused('Private browser grant unavailable')
        command = ['create', '-i', '--init', '--name', name,
                   '--label', 'homecompute.site-operation=true', '--network', network,
                   '--user', '1000:1000', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges:true', '--security-opt', 'seccomp=' + SECCOMP,
                   '--memory', '1g' if not policy else '96m', '--cpus', '1' if not policy else '0.25',
                   '--pids-limit', '256' if not policy else '64', '--ulimit', 'core=0', '--log-driver', 'none',
                   '--shm-size', '128m', '--tmpfs', TMPFS, '--env', 'HOME=/tmp',
                   '--env', 'PYTHONDONTWRITEBYTECODE=1']
        if ip:
            command += ['--ip', ip]
        if policy:
            command += ['--mount', f'type=bind,src={self.directory}/policy,dst=/run/site-policy,readonly']
        result = docker([*command, self.image, *arguments])
        if not re.fullmatch(r'[a-f0-9]{64}', result):
            raise SiteExecutionRefused('Private browser container identity refused')
        self.ids.append(result)
        if self.closed.is_set() or self.guard() is not True:
            self.stop()
            raise SiteExecutionRefused('Private browser grant unavailable')
        return result

    def start(self, request_id: str):
        self.policy(True, [])
        remaining = max(1, min(86400, int(self.expires - time.time())))
        prefix = ['/usr/bin/timeout', '--signal=KILL', str(remaining)]
        proxy = self.create('homecompute-site-view-proxy-' + request_id, 'homecompute-site-egress',
                            [*prefix, 'python', '/opt/site/openclaw_site_egress.py', '--policy',
                             '/run/site-policy/policy.json', '--listen', self.proxy_ip], policy=True)
        docker(['network', 'connect', '--ip', self.proxy_ip, NETWORK, proxy])
        if self.closed.is_set() or self.guard() is not True:
            raise SiteExecutionRefused('Private browser grant unavailable')
        docker(['start', proxy])
        browser = self.create('homecompute-site-view-' + request_id, NETWORK,
                              [*prefix, 'python', '/opt/site/openclaw_site_session_worker.py'], ip=self.browser_ip)
        if self.closed.is_set() or self.guard() is not True:
            raise SiteExecutionRefused('Private browser grant unavailable')
        self.process = subprocess.Popen([DOCKER, 'start', '--attach', '--interactive', browser],  # nosec B603
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        start_new_session=True,
                                        env={'PATH': '/run/current-system/sw/bin', 'HOME': '/root'})
        return self.send({'profile': self.profile, 'expires_at': self.expires,
                          'proxy': 'http://' + self.proxy_ip + ':3128'}, timeout=20)

    def send(self, value: dict, *, timeout=12) -> dict:
        with self.lock:
            if self.closed.is_set() or self.guard() is not True or self.process is None:
                raise SiteExecutionRefused('Private browser unavailable')
            raw = json.dumps(value).encode() + b'\n'
            if len(raw) > 16384:
                raise SiteExecutionRefused('Private browser input refused')
            self.process.stdin.write(raw)
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not self.closed.is_set():
                if b'\n' in self.buffer:
                    line, _, rest = self.buffer.partition(b'\n')
                    self.buffer = bytearray(rest)
                    result = json.loads(line)
                    if not isinstance(result, dict) or len(result) > 4:
                        raise SiteExecutionRefused('Private browser output refused')
                    return result
                readable, _, _ = select.select([self.process.stdout], [], [], 0.1)
                if readable:
                    chunk = os.read(self.process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    self.buffer.extend(chunk)
                    if len(self.buffer) > 32768:
                        raise SiteExecutionRefused('Private browser output budget exceeded')
            raise SiteExecutionRefused('Private browser response unavailable')

    def stop(self):
        self.closed.set()
        self.ready = False
        failed = False
        try:
            # This also closes existing proxy tunnels, independently of Docker
            # cleanup, so a revoked session cannot continue network activity.
            self.policy(False, [])
        except Exception:
            failed = True
        if self.process is not None:
            with contextlib.suppress(Exception):
                if self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
        # Always remove captured container IDs even after a client exit race.
        for container_id in list(reversed(self.ids)):
            try:
                docker(['rm', '--force', container_id], timeout=3)
            except Exception:
                try:
                    inspected = subprocess.run([DOCKER, 'container', 'inspect', '--format', '{{.Id}}', container_id],  # nosec B603
                                               capture_output=True, text=True, timeout=2)
                    failed |= not (inspected.returncode == 1 and inspected.stderr.strip() in
                                   {'Error: No such object: ' + container_id, 'Error: No such container: ' + container_id})
                except Exception:
                    failed = True
        if not failed:
            shutil.rmtree(self.directory, ignore_errors=True)
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout):
                with contextlib.suppress(OSError):
                    stream.close()
        if failed:
            raise SiteExecutionRefused('Private browser cleanup unverified')


class SiteSessions:
    def __init__(self, directory: Path, image: str, registry, authorize, provider):
        os.close(open_directory(directory, create=True))
        self.directory, self.image, self.registry = directory, image, registry
        self.authorize, self.provider = authorize, provider
        self.entries, self.lock, self.slots = {}, threading.RLock(), set()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.stopping = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def grant(self, request_id: str) -> dict:
        grant = self.authorize('openclaw', reference(request_id))
        if (grant['status'] != 'active' or grant['actor'] != 'openclaw'
                or not grant['approved_at'] <= time.time() < grant['expires_at']):
            raise SiteExecutionRefused('Private browser grant unavailable')
        return grant

    def valid(self, initial: dict, deadline: float) -> bool:
        try:
            current = self.grant(initial['request_id'])
            return time.monotonic() < deadline and all(current.get(key) == initial.get(key) for key in
                ('request_id', 'actor', 'account', 'scope', 'request_digest', 'approved_at', 'expires_at'))
        except Exception:
            return False

    def state(self, request_id: str) -> dict:
        grant = self.authorize('openclaw', reference(request_id))
        with self.lock:
            entry = self.entries.get(request_id)
        status = entry['status'] if entry else 'idle'
        if grant['status'] != 'active' or grant['expires_at'] <= time.time():
            status = ('revoked' if grant['status'] in {'revoked', 'denied'} else
                      'expired' if grant['status'] == 'expired' or grant['expires_at'] is not None
                      and grant['expires_at'] <= time.time() else 'idle')
            if entry:
                self._close(entry, status)
        return {'status': status, 'account': grant['account'], 'request_id': request_id}

    def start(self, request_id: str) -> dict:
        initial = self.grant(request_id)
        profile = self.registry.profile(initial['account'])
        if profile.get('login_mode') != 'owner' or not profile['qualified']:
            raise SiteExecutionRefused('Private owner sign-in is not registered')
        with self.lock:
            old = self.entries.get(request_id)
            if old and old['status'] not in {'closed', 'expired', 'revoked', 'unavailable'}:
                if old['status'] == 'ready':
                    old['browser'].ready, old['status'] = False, 'starting'
                    self.pool.submit(self._manual, old)
                return {'status': old['status'], 'account': initial['account'], 'request_id': request_id}
            available = [slot for slot in range(2) if slot not in self.slots]
            if not available:
                raise SiteExecutionRefused('Private browser capacity reached')
            slot = available[0]
            self.slots.add(slot)
            deadline = time.monotonic() + initial['expires_at'] - time.time()
            browser = PrivateBrowser(self.directory / ('session-' + str(uuid4())), self.image, profile,
                                     initial['expires_at'], slot, lambda: self.valid(initial, deadline))
            entry = {'browser': browser, 'initial': initial, 'deadline': deadline, 'status': 'starting',
                     'slot': slot, 'start_done': False, 'slot_released': False, 'lock': threading.RLock()}
            self.entries[request_id] = entry
            self.pool.submit(self._start, entry)
        return self.state(request_id)

    def _start(self, entry):
        try:
            result = entry['browser'].start(entry['initial']['request_id'])
            if result.get('status') != 'human_sign_in_required' or not entry['browser'].guard():
                raise SiteExecutionRefused('Private browser startup unavailable')
            with entry['lock']:
                if entry['browser'].closed.is_set():
                    raise SiteExecutionRefused('Private browser already closed')
                entry['status'] = 'awaiting_owner'
        except Exception:
            self._close(entry, 'unavailable')
        finally:
            entry['start_done'] = True
            if entry['browser'].closed.is_set():
                self._close(entry, entry['status'])

    def _manual(self, entry):
        try:
            result = entry['browser'].send({'command': 'manual'}, timeout=45)
            if (result.get('status') != 'human_sign_in_required' or entry['browser'].closed.is_set()
                    or not entry['browser'].guard()):
                raise SiteExecutionRefused('Private browser handoff unavailable')
            # Existing automation has finished and acknowledged the transition.
            entry['browser'].policy(True, [])
            with entry['lock']:
                if entry['browser'].closed.is_set() or not entry['browser'].guard():
                    raise SiteExecutionRefused('Private browser already closed')
                entry['status'] = 'awaiting_owner'
        except Exception:
            self._close(entry, 'unavailable')

    def fill(self, request_id: str) -> dict:
        grant = self.grant(request_id)
        entry = self.entries.get(request_id)
        if not entry or entry['status'] != 'awaiting_owner':
            raise SiteExecutionRefused('Private sign-in is not waiting')
        login = self.provider(grant['account'])
        self.grant(request_id)
        result = entry['browser'].send({'command': 'fill', 'login': {'username': login.username, 'password': login.password}})
        del login
        if (set(result) != {'status', 'filled_fields'} or result['status'] not in {'filled', 'human_sign_in_required'}
                or type(result['filled_fields']) is not int or not 0 <= result['filled_fields'] <= 2):
            raise SiteExecutionRefused('Private fill unavailable')
        # This owner-only action receipt never enters model tools or GET state.
        return {**self.state(request_id), 'filled_fields': result['filled_fields']}

    def ready(self, request_id: str) -> dict:
        self.grant(request_id)
        entry = self.entries.get(request_id)
        if not entry or entry['status'] != 'awaiting_owner':
            raise SiteExecutionRefused('Private sign-in is not waiting')
        result = entry['browser'].send({'command': 'ready'})
        if set(result) != {'status', 'observed_hosts'} or result['status'] != 'ready':
            raise SiteExecutionRefused('Private sign-in not ready')
        hosts = result['observed_hosts']
        if not isinstance(hosts, list) or not 1 <= len(hosts) <= 64:
            raise SiteExecutionRefused('Private browser hosts unavailable')
        entry['browser'].policy(False, hosts)
        self.grant(request_id)
        with entry['lock']:
            if entry['browser'].closed.is_set() or entry['status'] != 'awaiting_owner':
                raise SiteExecutionRefused('Private browser already closed')
            entry['browser'].ready, entry['status'] = True, 'ready'
        return self.state(request_id)

    def is_ready(self, request_id: str) -> bool:
        entry = self.entries.get(request_id)
        return bool(entry and entry['status'] == 'ready' and entry['browser'].ready and entry['browser'].guard())

    def diagnostics(self, request_id: str) -> dict:
        """Restricted operator metadata; no private viewer content or model tool."""
        from openclaw_site_network_health import validate_report
        self.grant(request_id)
        entry = self.entries.get(request_id)
        if not entry or entry['status'] != 'awaiting_owner' or not entry['browser'].guard():
            raise SiteExecutionRefused('Private network diagnostic unavailable')
        result = entry['browser'].send({'command': 'diagnostics'})
        self.grant(request_id)
        if entry['status'] != 'awaiting_owner' or not entry['browser'].guard():
            raise SiteExecutionRefused('Private network diagnostic unavailable')
        return validate_report(result, known_domains=entry['browser'].profile['domains'])

    def run(self, actor: str, initial: dict, operation: str, parameters: dict, deadline: float, guard):
        if actor != 'openclaw' or not self.is_ready(initial['request_id']):
            return {'status': 'human_sign_in_required', 'operation': operation, 'results_count': None}
        entry = self.entries[initial['request_id']]
        # Serialize admission with the private pipe: a queued operation must not
        # run after the preceding operation discovers a new MFA challenge.
        with entry['browser'].lock:
            if not self.is_ready(initial['request_id']):
                return {'status': 'human_sign_in_required', 'operation': operation, 'results_count': None}
            result = entry['browser'].send({'command': 'run', 'operation': operation, 'parameters': parameters}, timeout=45)
            if (not guard() or time.monotonic() >= deadline or not self.is_ready(initial['request_id'])
                    or set(result) != {'status', 'operation', 'results_count'} or result['operation'] != operation
                    or result['status'] not in {'ok', 'unavailable', 'human_sign_in_required', 'expired'}
                    or result['status'] != 'ok' and result['results_count'] is not None
                    or result['results_count'] is not None and (type(result['results_count']) is not int or not 0 <= result['results_count'] <= 100)):
                raise SiteExecutionRefused('Private browser result unavailable')
            if result['status'] == 'human_sign_in_required':
                with entry['lock']:
                    if entry['browser'].closed.is_set():
                        raise SiteExecutionRefused('Private browser already closed')
                    entry['browser'].ready, entry['status'] = False, 'starting'
                self.pool.submit(self._manual, entry)
        return result

    def target(self, request_id: str):
        self.grant(request_id)
        entry = self.entries.get(request_id)
        if not entry or entry['status'] not in {'awaiting_owner', 'ready'} or not entry['browser'].guard():
            raise SiteExecutionRefused('Private browser display unavailable')
        return entry['browser'].browser_ip, 6080

    def close(self, request_id: str) -> dict:
        entry = self.entries.get(reference(request_id))
        if entry:
            self._close(entry, 'closed')
        return self.state(request_id)

    def _close(self, entry, status):
        entry['browser'].closed.set()
        with entry['lock']:
            if entry['status'] not in {'closed', 'expired', 'revoked'}:
                entry['status'] = status
        verified = False
        try:
            entry['browser'].stop()
            verified = True
        except Exception:
            entry['status'] = 'unavailable'
        finally:
            if verified and entry['start_done']:
                with self.lock:
                    if not entry['slot_released']:
                        self.slots.discard(entry['slot'])
                        entry['slot_released'] = True

    def _watch(self):
        while not self.stopping.wait(0.25):
            with self.lock:
                entries = list(self.entries.values())
            for entry in entries:
                if entry['status'] in {'starting', 'awaiting_owner', 'ready'} and not entry['browser'].guard():
                    self._close(entry, 'expired')
                elif entry['browser'].closed.is_set() and entry['start_done'] and not entry['slot_released']:
                    self._close(entry, entry['status'])
