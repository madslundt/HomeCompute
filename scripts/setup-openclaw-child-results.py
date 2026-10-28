#!/usr/bin/env python3
"""Preserve bounded single-child completion evidence in the pinned runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

RUNTIME = Path('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/subagent-announce-output-CgCr3vGC.js')
BASE_SHA256 = 'df1b753dee73eaf410788ba9b3042b5f3f1cfe9617dd99d643b44c0be8e070ad'
CHANGES = (
    ('function formatChildResultData(resultText) {',
     'function formatChildResultData(resultText, maxEscapedChars = MAX_CHILD_COMPLETION_RESULT_CHARS) {'),
    ('\t\tmaxEscapedChars: MAX_CHILD_COMPLETION_RESULT_CHARS,', '\t\tmaxEscapedChars,'),
    ('\t\t\t\tformatChildResultData(resultText)',
     '\t\t\t\tformatChildResultData(resultText, children.length === 1 ? 3072 : MAX_CHILD_COMPLETION_RESULT_CHARS)'),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def overlay(source: bytes) -> bytes:
    text = source.decode()
    restored = text
    if all(text.count(after) == 1 for _, after in CHANGES):
        for before, after in CHANGES:
            restored = restored.replace(after, before, 1)
        if digest(restored.encode()) == BASE_SHA256:
            return source
    if digest(source) != BASE_SHA256 or any(text.count(before) != 1 for before, _ in CHANGES):
        raise ValueError('Pinned native child result runtime changed')
    for before, after in CHANGES:
        text = text.replace(before, after, 1)
    return text.encode()


def replace(path: Path, data: bytes, mode: int) -> None:
    if path.is_symlink():
        raise ValueError('Symlink refused')
    temporary = path.with_name(path.name + '.child-result-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if RUNTIME.is_symlink():
        raise ValueError('Symlink refused')
    info = RUNTIME.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Root-owned regular runtime required')
    before = RUNTIME.read_bytes()
    if len(before) > 3 * 1024 * 1024:
        raise ValueError('Runtime source exceeds budget')
    after = overlay(before)
    report = {'before_sha256': digest(before), 'after_sha256': digest(after), 'changed': before != after,
              'applied': False, 'restart_required': False, 'single_child_limit': 3072,
              'multiple_child_limit': 512, 'aggregate_limit': 4096}
    if args.apply and before != after:
        if os.geteuid() != 0:
            raise ValueError('Apply requires root')
        backup = Path('/sandbox/.openclaw/child-result-backups') / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        replace(backup / RUNTIME.name, before, 0o600)
        if RUNTIME.read_bytes() != before:
            raise ValueError('Runtime changed since validation')
        replace(RUNTIME, after, stat.S_IMODE(info.st_mode))
        report.update(applied=True, restart_required=True, backup_directory=str(backup))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
