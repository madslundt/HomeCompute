#!/usr/bin/env python3
"""Root-registered, one-attempt maintenance plans; assistant requests only IDs.

The operator owns command selection, version evidence and SSH forced-command
provisioning. Classification is not a package resolver or a sandbox for root.
No command output, credentials or argv are returned across the agent socket.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import pwd
import queue
import re
import selectors
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import threading
import time
import uuid

from infrastructure_update_policy import classify_plan
STATE = Path('/var/lib/homecompute-infrastructure-maintenance')
SOCKET = Path('/run/homecompute-infrastructure-maintenance/agent.sock')
ID = re.compile(r'[a-f0-9]{32}')
HASH = re.compile(r'[a-f0-9]{64}')
MAX_JSON = 524288
MAX_OUTPUT = 1048576
BINARIES = {'docker', 'apt-get', 'dpkg-query', 'sha256sum', 'nixos-rebuild', 'nix-env', 'readlink', 'ssh'}
BIN_DIRS = {'/usr/bin', '/bin', '/run/current-system/sw/bin'}
NIX_SYSTEM = r'/nix/store/[a-z0-9]{32}-nixos-system-home-core-[A-Za-z0-9.+_-]+'
HA_COMMAND = '/usr/local/libexec/homecompute-home-assistant-maintenance'
CONTAINER_COMMAND = '/usr/local/libexec/homecompute-container-maintenance'
SPARK_COMMAND = '/usr/local/libexec/homecompute-infrastructure-maintenance-spark'
ENV = {'HOME': '/root', 'PATH': '/run/current-system/sw/bin:/usr/bin:/bin',
       'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'DEBIAN_FRONTEND': 'noninteractive',
       'DOCKER_HOST': 'unix:///var/run/docker.sock', 'DOCKER_CONTEXT': 'default'}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('invalid fields')

def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('invalid plan identity')
    return value

def trusted_path(path, uid, *, directory=False):
    """Inspect every ancestor, including /; never traverse writable or symlink paths."""
    path = Path(os.path.abspath(path))
    for parent in [*reversed(path.parents), path]:
        info = parent.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid not in {0, uid}
                or info.st_mode & 0o022):
            raise ValueError('path must be privately controlled by root')
        if parent != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('trusted parent must be a directory')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('trusted file must be regular with one link')
    return path

def root_json(path, uid):
    path = trusted_path(path, uid)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if info.st_uid != uid or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError('plan must be root-owned')
        data = os.read(fd, MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise ValueError('plan exceeds budget')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate JSON key')
                value[key] = item
            return value
        return json.loads(data, object_pairs_hook=unique)
    finally:
        os.close(fd)

def argv_scope(argv, target, local_target='home-core'):
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128
            or any(not isinstance(arg, str) or not arg or '\0' in arg or len(arg.encode()) > 4096 for arg in argv)
            or sum(len(arg.encode()) for arg in argv) > 16384):
        raise ValueError('invalid bounded argv')
    executable = Path(argv[0])
    if argv[0] == CONTAINER_COMMAND:
        if target != local_target or len(argv) != 3 or argv[1] not in {'snapshot', 'restart', 'verify'} or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', argv[2]):
            raise ValueError('finite local container maintenance command required')
        return
    if re.fullmatch(NIX_SYSTEM + '/bin/switch-to-configuration', argv[0]):
        if target != 'home-core' or local_target != 'home-core' or argv[1:] != ['switch']:
            raise ValueError('only the immutable local core system may switch')
        return
    if target == 'home-assistant':
        if argv[0] != HA_COMMAND:
            raise ValueError('Home Assistant requires its trusted maintenance executable')
        return
    if executable.name not in BINARIES or str(executable.parent) not in BIN_DIRS:
        raise ValueError('executable outside maintenance scope')
    if executable.name in {'nix-env', 'readlink'} and (target != 'home-core' or local_target != 'home-core'):
        raise ValueError('Nix profile checks are local core only')
    if executable.name == 'nix-env' and (len(argv) != 5 or argv[1:4] != ['--profile', '/nix/var/nix/profiles/system', '--set'] or not re.fullmatch(NIX_SYSTEM, argv[4])):
        raise ValueError('Nix profile set requires the exact immutable core system')
    if target == local_target:
        remote = executable.name == 'docker' and any(arg in {'-H', '-c', '--host', '--context'} or arg.startswith(('-H', '-c', '--host=', '--context=')) for arg in argv[1:])
        if executable.name == 'ssh' or remote:
            raise ValueError('local plan cannot select a remote target')
        return
    if target != 'home-spark' or executable.name != 'ssh':
        raise ValueError('Spark plan requires fixed SSH transport')
    # Remote strings are parsed by a separately provisioned forced-command
    # dispatcher. Neither ProxyCommand nor arbitrary SSH configuration is allowed.
    prefix = ['-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
              '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=8',
              '-o', 'UserKnownHostsFile=/var/lib/homecompute-infrastructure-maintenance/ssh/known_hosts',
              '-i', '/var/lib/homecompute-infrastructure-maintenance/ssh/id_ed25519']
    if (argv[1:1 + len(prefix)] != prefix or len(argv) != len(prefix) + 3
            or argv[-2] not in {'root@home-spark', 'home-spark', 'root@192.168.30.126'}
            or not re.fullmatch(re.escape(SPARK_COMMAND) + r' (?:check|run|status|verify) [a-f0-9]{32}', argv[-1])):
        raise ValueError('Spark requires the fixed root SSH forced-command transport')

def validate_plan(value, local_target='home-core'):
    required = {'schema_version', 'policy', 'prechecks', 'steps', 'postchecks', 'timeout_seconds', 'expires_at'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'requires_approval'}:
        raise ValueError('invalid plan fields')
    if type(value.get('requires_approval', False)) is not bool:
        raise ValueError('invalid root approval override')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('invalid schema')
    classification = classify_plan(value['policy'])
    if type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 900:
        raise ValueError('invalid plan timeout')
    expiry = value['expires_at']
    if type(expiry) not in {int, float} or not math.isfinite(expiry) or expiry <= 0:
        raise ValueError('invalid expiry')
    for key in ('prechecks', 'steps', 'postchecks'):
        if not isinstance(value[key], list) or not 1 <= len(value[key]) <= 16:
            raise ValueError('each phase requires bounded commands')
        for row in value[key]:
            if key == 'prechecks' or key == 'postchecks' and isinstance(row, dict):
                exact(row, {'argv', 'stdout_sha256'})
                if not isinstance(row['stdout_sha256'], str) or not HASH.fullmatch(row['stdout_sha256']):
                    raise ValueError('precheck requires exact output digest')
                row = row['argv']
            argv_scope(row, value['policy']['target'], local_target)
    if len(canonical(value).encode()) > MAX_JSON:
        raise ValueError('plan exceeds budget')
    if value.get('requires_approval') or classification['classification'] == 'unchanged':
        classification = dict(classification, approval_required=True, reason='root_plan_requires_approval')
    return classification

def bounded_run(argv, *, timeout, **_):
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=ENV.copy(), cwd='/',
                             close_fds=True, start_new_session=True)
    streams = {child.stdout: bytearray(), child.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                for key, _events in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        streams[key.fileobj].extend(chunk)
                        if sum(map(len, streams.values())) > MAX_OUTPUT:
                            raise ValueError('command output exceeded budget')
        code = child.wait(timeout=max(.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, code, bytes(streams[child.stdout]), bytes(streams[child.stderr]))
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        for stream in streams:
            stream.close()

class Controller:
    def __init__(self, state_dir=STATE, *, runner=bounded_run, clock=time.time, uid=0, local_target='home-core', deploy_lock='default', preparer=None):
        if os.geteuid() != uid:
            raise PermissionError('maintenance controller requires root')
        self.state_dir, self.runner, self.clock, self.uid = Path(state_dir), runner, clock, uid
        if local_target not in {'home-core', 'home-spark'}:
            raise ValueError('invalid local host')
        self.local_target = local_target
        self.preparer = preparer
        self.preparation_lock, self.preparation_status = threading.Lock(), None
        if deploy_lock == 'default':
            name = 'home-spark-deploy.lock' if local_target == 'home-spark' else 'deploy.lock'
            deploy_lock = Path('/var/lib/homecompute') / name if uid == 0 else None
        self.deploy_lock = Path(deploy_lock) if deploy_lock is not None else None
        trusted_path(self.state_dir.parent, uid, directory=True)
        self.state_dir.mkdir(mode=0o700, exist_ok=True)
        trusted_path(self.state_dir, uid, directory=True)
        if self.state_dir.stat().st_uid != uid or stat.S_IMODE(self.state_dir.stat().st_mode) != 0o700:
            raise ValueError('state directory must be root-owned mode 0700')
        self.database = self.state_dir / 'plans.sqlite'
        with self.locked():
            if self.database.exists() or self.database.is_symlink():
                trusted_path(self.database, uid)
                if self.database.stat().st_uid != uid or self.database.stat().st_mode & 0o077:
                    raise ValueError('state database must be private')
            old = os.umask(0o077)
            try:
                with self.connect() as db:
                    db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, digest TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL, approved_digest TEXT, reason TEXT)')
                    db.execute("UPDATE plans SET status='uncertain', reason='interrupted_attempt_requires_operator_reconciliation' WHERE status='executing'")
            finally:
                os.umask(old)
    @contextmanager
    def locked(self, blocking=True):
        fd = os.open(self.state_dir / 'operation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != self.uid or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise ValueError('operation lock must be private')
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield
        finally:
            os.close(fd)
    @contextmanager
    def deployment_locked(self):
        if self.deploy_lock is None:
            yield
            return
        trusted_path(self.deploy_lock.parent, self.uid, directory=True)
        fd = os.open(self.deploy_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != self.uid or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
                raise ValueError('deployment lock must be root-controlled')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()
    def row(self, db, plan_id):
        row = db.execute('SELECT * FROM plans WHERE id=?', (identifier(plan_id),)).fetchone()
        if row is None:
            raise ValueError('unknown plan')
        if hashlib.sha256(row['plan'].encode()).hexdigest() != row['digest']:
            raise ValueError('stored plan digest mismatch')
        return row
    def public(self, row, compact=False):
        plan = json.loads(row['plan'])
        classification = validate_plan(plan, self.local_target)
        policy = dict(plan['policy'])
        limit = 10 if compact else 16
        policy['updates'] = policy['updates'][:limit]
        classification['updates'] = classification['updates'][:limit]
        if compact:
            classification = {key: val for key, val in classification.items() if key != 'updates'}
        return {'plan_id': row['id'], 'digest': row['digest'], 'status': row['status'],
                'reason': row['reason'], 'expires_at': plan['expires_at'], 'policy': policy,
                'updates_truncated': len(policy['updates']) != len(plan['policy']['updates']),
                'requires_approval': plan.get('requires_approval', False),
                'classification': classification, 'approved': row['approved_digest'] == row['digest']}
    def register(self, path):
        plan = root_json(path, self.uid)
        validate_plan(plan, self.local_target)
        if plan['expires_at'] <= self.clock():
            raise ValueError('cannot register expired plan')
        encoded = canonical(plan)
        digest, plan_id = hashlib.sha256(encoded.encode()).hexdigest(), uuid.uuid4().hex
        with self.locked(), self.connect() as db:
            db.execute("INSERT INTO plans VALUES (?, ?, ?, 'registered', NULL, NULL)", (plan_id, digest, encoded))
            return self.public(self.row(db, plan_id))
    def prepare(self, target, component, _async=False):
        if not isinstance(target, str) or target not in {'home-core', 'home-spark', 'home-assistant'} or not isinstance(component, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', component):
            raise ValueError('finite maintenance selector required')
        if _async:
            if not self.preparation_lock.acquire(False):
                return self.preparation_status
            receipt = {'status': 'preparing', 'target': target, 'component': component}
            self.preparation_status = receipt
            def worker():
                try:
                    result = self.prepare(target, component)
                    self.preparation_status = dict(receipt, status='prepared', plan_id=result['plan_id'])
                except Exception:
                    self.preparation_status = dict(receipt, status='preparation_failed')
                finally:
                    self.preparation_lock.release()
            threading.Thread(target=worker, name='trusted-preparation', daemon=False).start()
            return receipt
        if self.preparer is None:
            from maintenance_plan_preparation import prepare
        else:
            prepare = self.preparer
        return prepare(self, target, component)
    def status(self, plan_id):
        with self.connect() as db:
            return self.public(self.row(db, plan_id))
    def list(self):
        with self.connect() as db:
            result = []
            for row in db.execute('SELECT * FROM plans ORDER BY rowid DESC LIMIT 16'):
                value = self.public(row, compact=True)
                if len(canonical(result + [value]).encode()) > 65536:
                    break
                result.append(value)
            return result
    def approve(self, plan_id, digest):
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ValueError('invalid approval digest')
        with self.locked(), self.connect() as db:
            row = self.row(db, plan_id)
            if row['status'] != 'registered' or row['digest'] != digest or json.loads(row['plan'])['expires_at'] <= self.clock():
                raise ValueError('approval must bind a live unattempted exact plan')
            if self.local_target != 'home-spark' and json.loads(row['plan'])['policy']['target'] == 'home-spark':
                from maintenance_plan_preparation import approve_remote
                approve_remote(self, row)
            db.execute('UPDATE plans SET approved_digest=? WHERE id=?', (digest, plan_id))
            return self.public(self.row(db, plan_id))
    def run(self, plan_id, _started=None, _blocking=True):
        with self.locked(_blocking), self.deployment_locked(), self.connect() as db:
            row = self.row(db, plan_id)
            if row['status'] != 'registered':
                return self.public(row)
            plan = json.loads(row['plan'])
            classification = validate_plan(plan, self.local_target)
            if plan['expires_at'] <= self.clock():
                db.execute("UPDATE plans SET status='expired', reason='plan_expired' WHERE id=?", (plan_id,))
                return self.public(self.row(db, plan_id))
            if classification['approval_required'] and row['approved_digest'] != row['digest']:
                return dict(self.public(row), reason='exact_root_approval_required')
            db.execute("UPDATE plans SET status='executing', reason=NULL WHERE id=?", (plan_id,))
            db.commit()  # Durable one-attempt marker precedes every subprocess.
            if _started:
                _started(self.public(self.row(db, plan_id)))
            deadline, effects = time.monotonic() + plan['timeout_seconds'], False
            status, reason = 'succeeded', None
            try:
                for phase in ('prechecks', 'steps', 'postchecks'):
                    for command in plan[phase]:
                        if self.clock() >= plan['expires_at']:
                            raise ValueError('plan_expired')
                        argv = command['argv'] if isinstance(command, dict) else command
                        timeout = deadline - time.monotonic()
                        if timeout <= 0:
                            raise ValueError('plan_timeout')
                        if phase == 'steps':
                            effects = True
                        result = self.runner(argv, timeout=timeout, env=ENV.copy(), cwd='/', shell=False)
                        if result.returncode != 0:
                            raise ValueError('command_failed')
                        if isinstance(command, dict):
                            output = result.stdout
                            if not isinstance(output, bytes) or len(output) > MAX_OUTPUT or hashlib.sha256(output).hexdigest() != command['stdout_sha256']:
                                raise ValueError('snapshot_changed')
            except (OSError, ValueError, subprocess.SubprocessError):
                status = 'uncertain' if effects else 'precheck_failed'
                reason = 'operator_reconciliation_required' if effects else 'snapshot_or_precheck_failed'
            db.execute('UPDATE plans SET status=?, reason=? WHERE id=?', (status, reason, plan_id))
            return self.public(self.row(db, plan_id))
    def run_async(self, plan_id):
        identifier(plan_id)
        receipt = queue.Queue(maxsize=2)
        def worker():
            try:
                result = self.run(plan_id, _started=receipt.put, _blocking=False)
            except (OSError, ValueError):
                result = {'error': 'maintenance_busy_or_refused'}
            receipt.put(result)
        threading.Thread(target=worker, name='trusted-maintenance', daemon=False).start()
        return receipt.get(timeout=5)

def serve(controller, path=SOCKET, actor_uid=None):
    if not hasattr(socket, 'SO_PEERCRED'):
        raise RuntimeError('agent socket requires Linux SO_PEERCRED')
    actor_uid = pwd.getpwnam('homecompute-openclaw').pw_uid if actor_uid is None else actor_uid
    path = Path(path)
    trusted_path(path.parent, controller.uid, directory=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != controller.uid:
            raise ValueError('refusing replacement of untrusted socket')
        path.unlink()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(5)
            try:
                _pid, uid, _gid = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid not in {controller.uid, actor_uid}:
                    raise PermissionError('untrusted actor')
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') not in {str(i) for i in range(1, 97)} | {'0'}:
                    raise ValueError('invalid request framing')
                size = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(size)
                if self.path == '/list' and body in {b'', b'{}'}:
                    result = controller.list()
                elif self.path == '/prepare':
                    value = json.loads(body); exact(value, {'target', 'component'})
                    result = controller.prepare(value['target'], value['component'], _async=True)
                elif self.path == '/preparation' and body in {b'', b'{}'}:
                    result = controller.preparation_status or {'status': 'idle'}
                elif self.path in {'/run', '/status'}:
                    value = json.loads(body)
                    exact(value, {'plan_id'})
                    method = controller.run_async if self.path == '/run' else controller.status
                    result = method(identifier(value['plan_id']))
                else:
                    raise ValueError('unsupported request')
                payload, code = canonical(result).encode(), 200
            except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError):
                payload, code = b'{"error":"request_refused"}', 400
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class Server(HTTPServer):
        address_family = socket.AF_UNIX
        def get_request(self):
            connection, address = self.socket.accept()
            connection.settimeout(5)
            return connection, address
        def server_bind(self):
            self.socket.bind(self.server_address)
            self.server_name, self.server_port = 'localhost', 0

    with Server(str(path), Handler) as server:
        os.chown(path, controller.uid, pwd.getpwuid(actor_uid).pw_gid)
        os.chmod(path, 0o660)
        server.serve_forever()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE)
    parser.add_argument('--local-target', choices=('home-core', 'home-spark'), default='home-core')
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('register').add_argument('file', type=Path)
    approval = commands.add_parser('approve')
    approval.add_argument('plan_id'); approval.add_argument('digest')
    for verb in ('run', 'status', 'review', 'reconcile'):
        commands.add_parser(verb).add_argument('plan_id')
    commands.add_parser('list')
    preparation = commands.add_parser('prepare')
    preparation.add_argument('target', choices=('home-core', 'home-spark', 'home-assistant')); preparation.add_argument('component')
    commands.add_parser('serve').add_argument('--socket', type=Path, default=SOCKET)
    args = parser.parse_args()
    try:
        controller = Controller(args.state_dir, local_target=args.local_target)
        if args.operation == 'serve':
            serve(controller, args.socket)
            return 0
        if args.operation in {'review', 'reconcile'}:
            from infrastructure_maintenance_operator import operate
            result = operate(controller, args.operation, args.plan_id)
        else:
            values = [args.target, args.component] if args.operation == 'prepare' else [args.file] if args.operation == 'register' else [args.plan_id, args.digest] if args.operation == 'approve' else [args.plan_id] if args.operation in {'run', 'status'} else []
            result = getattr(controller, args.operation)(*values)
        print(canonical(result))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError):
        print('{"error":"trusted_maintenance_operation_refused"}')
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
