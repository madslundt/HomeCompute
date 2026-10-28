#!/usr/bin/env python3
"""Root controller for one operator-approved, unprivileged native DNS worker."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

DIRECTORY = Path('/etc/homecompute-public-dns')
IDENTITY = DIRECTORY / 'identity.json'
REGISTRY = Path('/home/hermes-operator/.nemoclaw/gateways/9123/sandboxes.json')
SUPERVISOR_IDENTITY = Path('/etc/homecompute-openclaw/supervisor-identity.env')
IMAGE = 'sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a'
NODE = '/usr/local/bin/node'
MODULE = '/opt/homecompute-public-dns/public-dns.mjs'
NAMESPACE = 'nemoclaw-openshell-docker-gateway-9123-eabebb0f51d0'
LIBC = ctypes.CDLL(None, use_errno=True)


class Refused(Exception):
    """Only fixed error identifiers are suitable for public diagnostics."""


def trusted_path(path: Path) -> None:
    """Reject symlinks and writable ancestry for root-owned executable paths."""
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('protected-path-invalid')


def trusted_file(path: Path, *, digest: str | None = None) -> bytes:
    trusted_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('protected-file-invalid')
        if digest is not None and info.st_mode & 0o222:
            raise Refused('protected-code-writable')
        with os.fdopen(os.dup(fd), 'rb') as source:
            data = source.read(512 * 1024 + 1)
        if len(data) > 512 * 1024 or (digest is not None and hashlib.sha256(data).hexdigest() != digest):
            raise Refused('protected-file-digest')
        return data
    finally:
        os.close(fd)


def manifest() -> dict[str, str]:
    value = json.loads(trusted_file(IDENTITY))
    if set(value) != {'sandbox_id', 'generation', 'module_sha256'}:
        raise Refused('identity-fields')
    if any(str(uuid.UUID(value[key])) != value[key] for key in ('sandbox_id', 'generation')):
        raise Refused('identity-uuid')
    if not re.fullmatch(r'[0-9a-f]{64}', value['module_sha256']):
        raise Refused('identity-digest')
    return value


def command(args: list[str]) -> bytes:
    result = subprocess.run(['/usr/bin/docker', *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=2, check=False, env={'PATH': '/usr/bin:/bin'})
    if result.returncode or len(result.stdout) > 65536:
        raise Refused('container-observation-unavailable')
    return result.stdout


def bounded_observation(path: Path, limit: int) -> bytes:
    """Operator-owned metadata can deny launch, never select root commands."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise Refused('metadata-file-type')
        data = source.read(limit + 1)
    if len(data) > limit:
        raise Refused('metadata-budget')
    return data


def process_identity(pid: int) -> tuple[int, int, int, int]:
    base = Path('/proc') / str(pid)
    # /proc/PID/stat comm may contain spaces or parentheses.
    start = int((base / 'stat').read_text().rsplit(')', 1)[1].split()[19])
    status = (base / 'status').read_text()
    uid = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
    gid = next(line.split()[1:] for line in status.splitlines() if line.startswith('Gid:'))
    if uid != ['998'] * 4 or gid != ['998'] * 4:
        raise Refused('gateway-user')
    if (base / 'cmdline').read_bytes().split(b'\0')[0] != b'openclaw-gateway':
        raise Refused('gateway-command')
    if os.readlink(base / 'exe') != NODE:
        raise Refused('gateway-executable')
    namespace = (base / 'ns/net').stat()
    root = (base / 'root').stat()
    return start, namespace.st_ino, root.st_dev, root.st_ino


@dataclass(frozen=True)
class Snapshot:
    container_id: str
    init_pid: int
    gateway_pid: int
    process: tuple[int, int, int, int]


def owns_gateway_listeners(pid: int, *, proc_root: Path = Path('/proc')) -> bool:
    """Distinguish the canonical server from short-lived gateway CLI probes."""
    base=proc_root/str(pid)
    try:
        sockets=set()
        for index,path in enumerate((base/'fd').iterdir()):
            if index>=8192:return False
            try:
                target=str(path.readlink())
            except FileNotFoundError:
                continue
            if target.startswith('socket:['):sockets.add(target)
        with (base/'net/tcp').open() as stream:
            table=stream.read(1024*1024+1)
        if len(table)>1024*1024:return False
        owned=set()
        for line in table.splitlines()[1:]:
            fields=line.split()
            if len(fields)>=10 and fields[3]=='0A' and 'socket:['+fields[9]+']' in sockets:
                owned.add(fields[1])
        return {'00000000:4967','0100007F:4970'}<=owned
    except OSError:
        return False


def observe(identity: dict[str, str]) -> Snapshot:
    expected_env = ('OPENCLAW_EXPECTED_SANDBOX_ID=' + identity['sandbox_id'] + '\n'
                    'OPENCLAW_EXPECTED_GENERATION=' + identity['generation'] + '\n')
    if bounded_observation(SUPERVISOR_IDENTITY, 1024).decode() != expected_env:
        raise Refused('supervisor-identity-drift')
    entry = json.loads(bounded_observation(REGISTRY, 2 * 1024 * 1024))['sandboxes']['agent-openclaw']
    if (entry.get('lifecycleGeneration') != identity['generation'] or
            entry.get('gatewayName') != 'nemoclaw-9123' or entry.get('name') != 'agent-openclaw' or
            entry.get('agent') != 'openclaw' or entry.get('model') != 'automation-moe' or
            entry.get('workload', {}).get('reference') != 'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@' + IMAGE):
        raise Refused('registry-identity-drift')
    container = 'openshell-default--agent-openclaw-' + identity['sandbox_id']
    template = '{{json .Id}}\n{{json .Image}}\n{{json .State}}\n{{json .Config.Labels}}\n{{json .Mounts}}'
    lines = command(['inspect', '--format', template, container]).splitlines()
    if len(lines) != 5:
        raise Refused('container-metadata')
    identifier, image, state, labels, mounts = map(json.loads, lines)
    image_records = command(['image', 'inspect', 'ghcr.io/nvidia/nemoclaw/openclaw-sandbox@' + IMAGE,
                             '--format', '{{json .Id}}\n{{json .RepoDigests}}']).splitlines()
    if len(image_records) != 2:
        raise Refused('image-metadata')
    image_id, digests = map(json.loads, image_records)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) or ('ghcr.io/nvidia/nemoclaw/openclaw-sandbox@' + IMAGE) not in digests:
        raise Refused('image-digest-drift')
    expected_labels = {'openshell.ai/sandbox-id': identity['sandbox_id'],
                       'openshell.ai/sandbox-name': 'agent-openclaw',
                       'openshell.ai/sandbox-namespace': NAMESPACE,
                       'openshell.ai/sandbox-workspace': 'default',
                       'openshell.ai/managed-by': 'openshell', 'io.nvidia.nemoclaw.agent': 'openclaw'}
    if (not re.fullmatch(r'[0-9a-f]{64}', identifier) or image != image_id or
            state.get('Running') is not True or type(state.get('Pid')) is not int or state['Pid'] <= 1 or
            any(labels.get(key) != value for key, value in expected_labels.items())):
        raise Refused('container-identity-drift')
    volumes = [row for row in mounts if row.get('Destination') == '/sandbox/.openclaw']
    if len(volumes) != 1 or volumes[0].get('Name') != 'nemoclaw-openclaw-state-v1-agent-openclaw':
        raise Refused('container-volume-drift')
    candidates = []
    for line in command(['top', container, '-eo', 'pid,uid,comm']).decode().splitlines()[1:]:
        fields = line.split()
        if len(fields) == 3 and fields[1:] == ['998', 'openclaw-gatewa']:
            pid=int(fields[0])
            if owns_gateway_listeners(pid):candidates.append(pid)
    if len(candidates) != 1:
        raise Refused('gateway-cardinality')
    pid = candidates[0]
    observed = process_identity(pid)
    listeners = Path(f'/proc/{pid}/net/tcp').read_text().splitlines()[1:]
    addresses = {line.split()[1] for line in listeners if line.split()[3] == '0A'}
    if not {'00000000:4967', '0100007F:4970'} <= addresses:
        raise Refused('gateway-listeners')  # 18791 + authenticated loopback 18800.
    return Snapshot(identifier, state['Pid'], pid, observed)


def syscall(result: int) -> None:
    if result != 0:
        raise Refused('privilege-transition-failed')


def prctl(option: int, value: int = 0) -> int:
    return LIBC.prctl(ctypes.c_int(option), ctypes.c_ulong(value), 0, 0, 0)


class CapHeader(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]


def drop_privileges() -> None:
    os.setgroups([])
    # Clear ambient capabilities and the complete current-kernel bounding set.
    syscall(LIBC.prctl(47, 4, 0, 0, 0))  # PR_CAP_AMBIENT/CLEAR_ALL.
    for capability in range(64):
        current = prctl(23, capability)  # PR_CAPBSET_READ; EINVAL ends kernel list.
        if current == -1 and ctypes.get_errno() == 22:
            break
        if current < 0:
            raise Refused('capability-read-failed')
        syscall(prctl(24, capability))  # PR_CAPBSET_DROP while CAP_SETPCAP held.
    os.setgid(998)
    os.setuid(998)
    data = (CapData * 2)()
    header = CapHeader(0x20080522, 0)
    syscall(LIBC.capset(ctypes.byref(header), ctypes.byref(data)))
    syscall(prctl(38, 1))  # PR_SET_NO_NEW_PRIVS.
    syscall(LIBC.capget(ctypes.byref(header), ctypes.byref(data)))
    if (os.getresuid() != (998, 998, 998) or os.getresgid() != (998, 998, 998) or os.getgroups() or
            any(item.effective or item.permitted or item.inheritable for item in data) or prctl(39) != 1):
        raise Refused('privilege-drop-incomplete')
    for capability in range(64):
        if prctl(23, capability) == -1 and ctypes.get_errno() == 22:
            break
        if prctl(23, capability) != 0 or LIBC.prctl(47, 1, capability, 0, 0) != 0:
            raise Refused('capability-drop-incomplete')


def child(namespace_fd: int, root_fd: int, parent: int, identity: dict[str, str]) -> None:
    syscall(prctl(1, signal.SIGKILL))  # Parent death fails closed, including abrupt exit.
    if os.getppid() != parent:
        raise Refused('controller-exited')
    syscall(LIBC.setns(namespace_fd, 0x40000000))  # ONLY CLONE_NEWNET.
    os.fchdir(root_fd)
    os.chroot('.')
    os.chdir('/')
    # Revalidate protected code within the actual inherited container root.
    trusted_path(Path(NODE))
    if not stat.S_ISREG(Path(NODE).stat().st_mode) or not os.access(NODE, os.X_OK):
        raise Refused('protected-node-invalid')
    trusted_file(Path(MODULE), digest=identity['module_sha256'])
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    udp.bind(('127.0.0.11', 53))
    tcp.bind(('127.0.0.11', 53))
    tcp.listen(16)
    # Duplicates avoid collisions when old namespace/root descriptors are 3/4.
    udp_fd = fcntl.fcntl(udp.fileno(), fcntl.F_DUPFD_CLOEXEC, 10)
    tcp_fd = fcntl.fcntl(tcp.fileno(), fcntl.F_DUPFD_CLOEXEC, 10)
    os.dup2(udp_fd, 3, inheritable=True)
    os.dup2(tcp_fd, 4, inheritable=True)
    # No namespace, root, pidfd, Docker, or unrelated controller descriptor survives.
    os.closerange(5, 1_048_576)
    drop_privileges()
    # setuid clears parent-death signal: restore it after dropping privileges.
    syscall(prctl(1, signal.SIGKILL))
    if os.getppid() != parent:
        raise Refused('controller-exited')
    os.execve(NODE, [NODE, MODULE], {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'})


def terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def run() -> None:
    if sys.platform != 'linux' or os.geteuid() != 0 or socket.gethostname() != 'agents':
        raise Refused('root-agents-host-required')
    identity = manifest()
    expected = observe(identity)
    base = Path(f'/proc/{expected.gateway_pid}')
    pidfd = os.pidfd_open(expected.gateway_pid, 0)
    watcher = select.poll()
    watcher.register(pidfd, select.POLLIN)
    namespace_fd = os.open(base / 'ns/net', os.O_RDONLY | os.O_CLOEXEC)
    root_fd = os.open(base / 'root', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        root_info = os.fstat(root_fd)
        if (watcher.poll(0) or os.fstat(namespace_fd).st_ino != expected.process[1] or
                (root_info.st_dev, root_info.st_ino) != expected.process[2:] or
                observe(identity) != expected):
            raise Refused('gateway-prelaunch-drift')
        parent = os.getpid()
        pid = os.fork()
        if pid == 0:
            try:
                child(namespace_fd, root_fd, parent, identity)
            except BaseException as error:
                trace = error.__traceback__
                while trace is not None and trace.tb_next is not None:
                    trace = trace.tb_next
                print('public-dns-child-refused-' + type(error).__name__ + '-line-' +
                      str(trace.tb_lineno if trace is not None else 0) + '-errno-' +
                      str(error.errno if isinstance(error, OSError) else 0), flush=True)
                os._exit(125)
        stopping = False
        def stop(*_: object) -> None:
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            print('public-dns-controller-started', flush=True)
            while not stopping:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    raise Refused('dns-child-exited')
                if watcher.poll(0) or manifest() != identity or observe(identity) != expected:
                    raise Refused('gateway-runtime-drift')
                time.sleep(1)
        finally:
            terminate(pid)
    finally:
        os.close(namespace_fd)
        os.close(root_fd)
        os.close(pidfd)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise Refused('arguments-forbidden')
        run()
    except (Refused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('public-dns-controller-refused', flush=True)
        sys.exit(1)
