"""Bounded binary extraction from an owned observer's public tmpfs outputs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import selectors
import subprocess
import time

# The helper opens only the selected regular file and never follows symlinks.
READER = """
import hashlib, os, stat, sys

path, limit, expected = sys.argv[1:]
limit = int(limit)
directory, name = os.path.split(path)
parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
os.close(parent)
with os.fdopen(fd, 'rb') as source:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
        raise ValueError('Invalid public source size or file type')
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
        chunk = source.read(min(remaining, 65536))
        if not chunk:
            raise ValueError('Truncated public source')
        digest.update(chunk)
        sys.stdout.buffer.write(chunk)
        remaining -= len(chunk)
    after = os.fstat(fd)
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns) or digest.hexdigest() != expected:
        raise ValueError('Public source changed or checksum differs')
    sys.stdout.buffer.flush()
"""
PATHS = {'observer.ts': '/tmp/export/observer.ts', 'manifest.json': '/tmp/export/manifest.json',
         'parity.json': '/tmp/parity.json'}
STDERR_LIMIT = 4096


def private_file(path: Path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, 'w+b')


def stream(container: str, destination: Path, name: str, maximum: int, expected: str, deadline: float) -> None:
    source = PATHS[name]
    partial = destination / (name + '.part')
    args = ['docker', 'exec', container, 'python3', '-I', '-B', '-c', READER,
            source, str(maximum), expected]
    size = 0
    digest = hashlib.sha256()
    stderr = bytearray()
    try:
        if deadline <= time.monotonic():
            raise TimeoutError('Public artifact transfer deadline reached')
        with private_file(partial) as target, selectors.DefaultSelector() as selector, subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            try:
                selector.register(child.stdout, selectors.EVENT_READ, False)
                selector.register(child.stderr, selectors.EVENT_READ, True)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Public artifact transfer deadline reached')
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif key.data:
                            stderr.extend(chunk[:STDERR_LIMIT - len(stderr)])
                        else:
                            size += len(chunk)
                            if size > maximum:
                                raise ValueError('Public artifact exceeds byte cap')
                            digest.update(chunk)
                            target.write(chunk)
                code = child.wait(timeout=max(0, deadline - time.monotonic()))
                if code or time.monotonic() >= deadline or not size or digest.hexdigest() != expected:
                    raise ValueError('Public artifact transfer or checksum verification failed')
                target.flush()
                os.fsync(target.fileno())
                target.seek(0)
                stored = hashlib.sha256()
                for chunk in iter(lambda: target.read(65536), b''):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Public artifact transfer deadline reached')
                    stored.update(chunk)
                if (time.monotonic() >= deadline or os.fstat(target.fileno()).st_size != size or
                        stored.hexdigest() != expected):
                    raise ValueError('Public destination size or checksum differs')
            finally:
                if child.poll() is None:
                    child.kill()
        final = destination / name
        os.link(partial, final, follow_symlinks=False)
        partial.unlink()
    except BaseException:
        if stderr:
            with private_file(destination / (name + '.stderr')) as diagnostic:
                diagnostic.write(stderr)
        raise
