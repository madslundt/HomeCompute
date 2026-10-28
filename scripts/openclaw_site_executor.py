"""Grant-checked disposable website operations; credentials never enter receipts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import stat
# Fixed immutable container image and system Docker binary, never a shell.
import subprocess  # nosec B404
import threading
import time
from uuid import UUID, uuid4

from openclaw_bitwarden import Login, open_directory
from openclaw_site_registry import SiteRegistry

DOCKER = '/run/current-system/sw/bin/docker'
NETWORK = 'homecompute-site-browser'
PROXY = 'http://172.30.192.2:3128'
SECCOMP = '/opt/homecompute/openclaw-credentials/site-seccomp.json'
# A fresh, per-container memory filesystem, never a shared host temp directory.
TMPFS = '/tmp:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=1777'  # nosec B108


class SiteExecutionRefused(ValueError):
    """Fixed diagnostics only, with no upstream text or private input."""


def reference(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise SiteExecutionRefused('Website access reference refused')
    return value


class GrantClient:
    def __init__(self, tokens: dict[str, str]):
        self.tokens = tokens

    def status(self, actor: str, request_id: str) -> dict:
        reference(request_id)
        if actor not in self.tokens:
            raise SiteExecutionRefused('Website requester refused')
        connection = http.client.HTTPConnection('127.0.0.1', 18812, timeout=0.6)
        try:
            connection.request('GET', '/access/' + request_id,
                               headers={'Authorization': 'Bearer ' + self.tokens[actor]})
            response = connection.getresponse()
            raw = response.read(8193)
            if response.status != 200 or len(raw) > 8192:
                raise SiteExecutionRefused('Website grant unavailable')
            value = json.loads(raw)
            if (value['request_id'] != request_id or value['actor'] != actor
                    or value['transaction_execution_available'] is not False):
                raise SiteExecutionRefused('Website grant differs')
            return value
        except Exception:
            raise SiteExecutionRefused('Website grant unavailable') from None
        finally:
            connection.close()


class ContainerRunner:
    def __init__(self, image: str, *, docker=DOCKER, monotonic=time.monotonic):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise SiteExecutionRefused('Immutable browser image required')
        self.image, self.docker, self.monotonic = image, docker, monotonic

    def run(self, profile: dict, operation: str, parameters: dict, login: Login,
            deadline: float, guard) -> dict:
        name = 'homecompute-site-operation-' + str(uuid4())
        remaining = min(45, int(deadline - self.monotonic()))
        if remaining < 1 or guard() is not True:
            raise SiteExecutionRefused('Website permission unavailable')
        envelope = json.dumps({'profile': profile, 'operation': operation, 'parameters': parameters,
                               'login': {'username': login.username, 'password': login.password},
                               'timeout_seconds': remaining, 'proxy': PROXY}).encode()
        if len(envelope) > 65536:
            raise SiteExecutionRefused('Website input budget refused')
        # memfd prevents credentials appearing in argv, environment or disk files.
        descriptor = os.memfd_create('site-private-input', os.MFD_CLOEXEC)
        process, raw, container_id = None, bytearray(), None
        try:
            os.write(descriptor, envelope)
            os.lseek(descriptor, 0, os.SEEK_SET)
            del envelope
            command = [self.docker, 'create', '-i', '--init', '--name', name,
                       '--label', 'homecompute.site-operation=true', '--network', NETWORK,
                       '--user', '1000:1000', '--read-only', '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges:true', '--security-opt', 'seccomp=' + SECCOMP,
                       '--memory', '768m', '--memory-swap', '768m', '--cpus', '1', '--pids-limit', '192',
                       '--ulimit', 'core=0', '--log-driver', 'none', '--shm-size', '128m',
                       '--tmpfs', TMPFS,
                       '--env', 'HOME=/tmp', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                       self.image, '/usr/bin/timeout', '--signal=KILL', str(remaining),
                       'python', '/opt/site/openclaw_site_worker.py']
            # Create cannot launch a browser and receives no credentials. Bind
            # cleanup to its immutable ID before admitting the separate start.
            created = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,  # nosec B603
                                     timeout=6, env={'PATH': '/run/current-system/sw/bin', 'HOME': '/root'})
            if created.returncode or not re.fullmatch(r'[a-f0-9]{64}', created.stdout.strip()):
                raise SiteExecutionRefused('Website container creation unavailable')
            container_id = created.stdout.strip()
            if self.monotonic() >= deadline or guard() is not True:
                raise SiteExecutionRefused('Website permission unavailable')
            process = subprocess.Popen([self.docker, 'start', '--attach', '--interactive', container_id],  # nosec B603
                                       stdin=descriptor, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, start_new_session=True,
                                       env={'PATH': '/run/current-system/sw/bin', 'HOME': '/root'})
            next_guard = 0.0
            while True:
                now = self.monotonic()
                if now >= deadline:
                    raise SiteExecutionRefused('Website deadline reached')
                if now >= next_guard:
                    if guard() is not True:
                        raise SiteExecutionRefused('Website permission unavailable')
                    next_guard = now + 0.25
                readable, _, _ = select.select([process.stdout], [], [], min(0.1, deadline - now))
                if readable:
                    value = os.read(process.stdout.fileno(), 4096)
                    raw.extend(value)
                    if len(raw) > 4096:
                        raise SiteExecutionRefused('Website output budget refused')
                    if not value:
                        break
                elif process.poll() is not None:
                    break
            process.wait(timeout=2)
            if process.returncode or guard() is not True or self.monotonic() >= deadline:
                raise SiteExecutionRefused('Website result unavailable')
            def unique(pairs):
                value = {}
                for key, entry in pairs:
                    if key in value:
                        raise SiteExecutionRefused('Duplicate website output refused')
                    value[key] = entry
                return value
            result = json.loads(raw, object_pairs_hook=unique)
            if (not isinstance(result, dict) or set(result) != {'status', 'operation', 'results_count'}
                    or result['operation'] != operation
                    or result['status'] not in {'ok', 'human_sign_in_required', 'unavailable', 'expired'}
                    or result['results_count'] is not None
                    and (type(result['results_count']) is not int or not 0 <= result['results_count'] <= 100)
                    or result['status'] != 'ok' and result['results_count'] is not None):
                raise SiteExecutionRefused('Website output refused')
            return result
        except Exception:
            raise SiteExecutionRefused('Website operation stopped; no private details are available') from None
        finally:
            os.close(descriptor)
            # End the attached start client before removal. Late daemon start
            # processing cannot escape force-removal of the captured ID.
            cleanup_failed = False
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                except ProcessLookupError:
                    # The client won the exit race; container removal below
                    # remains mandatory and establishes browser disappearance.
                    with contextlib.suppress(Exception):
                        process.wait(timeout=1)
                except Exception:
                    cleanup_failed = True
            try:
                target = container_id or name
                removed = subprocess.run([self.docker, 'rm', '--force', target], stdin=subprocess.DEVNULL,  # nosec B603
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                if removed.returncode:
                    inspected = subprocess.run([self.docker, 'container', 'inspect', '--format', '{{.Id}}', target],  # nosec B603
                                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2)
                    # --rm commonly wins this race. A daemon error does not
                    # establish disappearance and must suppress the result.
                    cleanup_failed = not (inspected.returncode == 1 and inspected.stderr.strip() in
                                          {'Error: No such container: ' + target, 'Error: No such object: ' + target})
            except Exception:
                cleanup_failed = True
            if process is not None:
                with contextlib.suppress(OSError):
                    process.stdout.close()
            if cleanup_failed:
                raise SiteExecutionRefused('Website browser cleanup could not be verified')


class SiteOperations:
    def __init__(self, directory: Path, registry: SiteRegistry, authorize, provider, runner,
                 *, clock=time.time, monotonic=time.monotonic, sessions=None):
        self.registry, self.authorize, self.provider, self.runner = registry, authorize, provider, runner
        self.clock, self.monotonic = clock, monotonic
        self.sessions = sessions
        os.close(open_directory(directory, create=True))
        self.path = directory / 'operations.sqlite'
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise SiteExecutionRefused('Private website state refused')
        self.pool, self.slots = ThreadPoolExecutor(max_workers=2), threading.BoundedSemaphore(2)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations '
                       '(id TEXT PRIMARY KEY,grant_id TEXT,actor TEXT,account TEXT,operation TEXT,status TEXT,count INTEGER,created INTEGER)')
            db.execute("UPDATE operations SET status='unavailable',count=NULL WHERE status='running'")

    @contextlib.contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _admit(self, actor: str, request_id: str, operation: str) -> dict:
        grant = self.authorize(actor, reference(request_id))
        scope = self.registry.operation_scope(grant['account'], operation)
        if (grant['actor'] != actor or grant['request_id'] != request_id or grant['status'] != 'active'
                or grant['scope'] != scope or type(grant['expires_at']) is not int
                or not grant['approved_at'] <= self.clock() < grant['expires_at']
                or not self.registry.profile(grant['account'])['qualified']):
            raise SiteExecutionRefused('Website permission unavailable')
        return grant

    def _receipt(self, grant: dict, row=None) -> dict:
        active = grant['status'] == 'active' and grant['expires_at'] > self.clock()
        status = row['status'] if row else 'idle'
        if not active:
            status = 'revoked' if grant['status'] in {'revoked', 'denied'} else 'expired' if grant['status'] == 'expired' else 'unavailable'
        return {'request_id': grant['request_id'], 'operation_id': row['id'] if row else None,
                'account': grant['account'], 'operation': row['operation'] if row else None,
                'status': status, 'results_count': row['count'] if row and active else None,
                'account_execution_available': bool(active and self.registry.profile(grant['account'])['qualified']),
                'transaction_execution_available': False}

    def status(self, actor: str, request_id: str) -> dict:
        grant = self.authorize(actor, reference(request_id))
        with self.db() as db:
            row = db.execute('SELECT * FROM operations WHERE grant_id=? AND actor=? ORDER BY created DESC,rowid DESC LIMIT 1',
                             (request_id, actor)).fetchone()
        return self._receipt(grant, row)

    def run(self, actor: str, values: dict) -> dict:
        if not isinstance(values, dict) or set(values) != {'request_id', 'operation', 'parameters'}:
            raise SiteExecutionRefused('Website operation refused')
        grant = self._admit(actor, values['request_id'], values['operation'])
        parameters = self.registry.validate_parameters(grant['account'], values['operation'], values['parameters'])
        if not self.slots.acquire(blocking=False):
            raise SiteExecutionRefused('Website operation capacity reached')
        operation_id = str(uuid4())
        try:
            with self.db() as db:
                if db.execute("SELECT 1 FROM operations WHERE grant_id=? AND status='running'", (grant['request_id'],)).fetchone():
                    raise SiteExecutionRefused('Website operation already running')
                if db.execute('SELECT count(*) FROM operations').fetchone()[0] >= 10000:
                    raise SiteExecutionRefused('Website operation ledger full')
                db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,NULL,?)',
                           (operation_id, grant['request_id'], actor, grant['account'], values['operation'], 'running', int(self.clock())))
            self.pool.submit(self._execute, operation_id, actor, grant, values['operation'], parameters)
        except Exception:
            self.slots.release()
            raise
        return self.status(actor, grant['request_id'])

    def _execute(self, operation_id: str, actor: str, initial: dict, operation: str, parameters: dict):
        status, count = 'unavailable', None
        started = self.monotonic()
        deadline = started + min(45, initial['expires_at'] - self.clock())
        def guard():
            try:
                current = self._admit(actor, initial['request_id'], operation)
                return (self.monotonic() < deadline and all(current.get(key) == initial.get(key) for key in
                        ('request_id', 'actor', 'account', 'scope', 'request_digest', 'approved_at', 'expires_at')))
            except Exception:
                return False
        try:
            if not guard():
                return
            profile = self.registry.profile(initial['account'])
            if profile.get('login_mode') == 'owner':
                result = (self.sessions.run(actor, initial, operation, parameters, deadline, guard) if self.sessions else
                          {'status': 'human_sign_in_required', 'operation': operation, 'results_count': None})
            else:
                login = self.provider(initial['account'])
                if not guard():
                    return
                result = self.runner.run(profile, operation, parameters, login, deadline, guard)
                del login
            if guard():
                status = 'completed' if result['status'] == 'ok' else result['status']
                count = result['results_count']
        except Exception:
            status, count = 'unavailable', None
        finally:
            with self.db() as db:
                db.execute('UPDATE operations SET status=?,count=? WHERE id=?', (status, count, operation_id))
            self.slots.release()
