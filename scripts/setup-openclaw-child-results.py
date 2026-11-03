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


def runtime_profile(version: str):
    if version == '2026.9.1': return RUNTIME, BASE_SHA256, None
    if version == '2026.9.2':
        return (RUNTIME.with_name('subagent-announce-output-Cy2Rx3_K.js'),
                '71523669a7f466e8c3a240f7d0a8485a8fa8882f5920add16176685cb5d34dc1',
                'ed09c0b16e452ec159085ef35b3475cd20c15b6ffbf8620c4b24517588c86bad')
    raise ValueError('Unqualified native child-result version')


def overlay(source: bytes, version='2026.9.1') -> bytes:
    _, expected, patched = runtime_profile(version)
    text = source.decode()
    restored = text
    if all(text.count(after) == 1 for _, after in CHANGES):
        for before, after in CHANGES:
            restored = restored.replace(after, before, 1)
        if digest(restored.encode()) == expected:
            if patched is not None and digest(source) != patched:raise ValueError('Candidate patched digest changed')
            return source
    if digest(source) != expected or any(text.count(before) != 1 for before, _ in CHANGES):
        raise ValueError('Pinned native child result runtime changed')
    for before, after in CHANGES:
        text = text.replace(before, after, 1)
    result = text.encode()
    if patched is not None and digest(result) != patched:
        raise ValueError('Candidate child-result output digest changed')
    return result


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
    parser.add_argument('--runtime-version', choices=('2026.9.1','2026.9.2'), default='2026.9.1')
    args = parser.parse_args()
    runtime, _, _ = runtime_profile(args.runtime_version)
    if runtime.is_symlink():
        raise ValueError('Symlink refused')
    info = runtime.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Root-owned regular runtime required')
    before = runtime.read_bytes()
    if len(before) > 3 * 1024 * 1024:
        raise ValueError('Runtime source exceeds budget')
    after = overlay(before, args.runtime_version)
    report = {'runtime_version':args.runtime_version,'before_sha256': digest(before), 'after_sha256': digest(after), 'changed': before != after,
              'applied': False, 'restart_required': False, 'single_child_limit': 3072,
              'multiple_child_limit': 512, 'aggregate_limit': 4096}
    if args.apply and before != after:
        if os.geteuid() != 0:
            raise ValueError('Apply requires root')
        backup = Path('/sandbox/.openclaw/child-result-backups') / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        replace(backup / runtime.name, before, 0o600)
        if runtime.read_bytes() != before:
            raise ValueError('Runtime changed since validation')
        replace(runtime, after, stat.S_IMODE(info.st_mode))
        report.update(applied=True, restart_required=True, backup_directory=str(backup))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
