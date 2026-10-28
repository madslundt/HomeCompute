#!/usr/bin/env python3
"""Repair the pinned native yield-warning collision; restart the gateway after apply.

The native reply dispatcher already owns interactive waiting acknowledgments.
Keep an earlier tool warning from occupying its terminal reply slot when an
intentional yield has both an acknowledgment and a real continuation source.
All ordinary failures, empty yields, and missing acknowledgments keep their
existing warning behavior. No configuration, credentials, or sessions change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

RUNTIME = Path('/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/embedded-agent-DaSvA-Yk.js')
BASE_SHA256 = '128a61c9e10a47d501ad425345f9f05a88c9dc66b665e32f979d12e29675cd67'
BEFORE = '\t\tsuppressToolErrorWarnings: runParams.suppressToolErrorWarnings,\n'
AFTER = ('\t\t// The native dispatcher owns the interactive sessions_yield acknowledgment.\n'
         '\t\tsuppressToolErrorWarnings: runParams.suppressToolErrorWarnings || '\
         '(attempt.yieldDetected === true && Boolean(attempt.yieldAcknowledgment?.trim()) '\
         '&& hasYieldContinuationEvidence(attempt)),\n')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def overlay(source: bytes) -> bytes:
    text = source.decode()
    if text.count(AFTER) == 1 and digest(text.replace(AFTER, BEFORE, 1).encode()) == BASE_SHA256:
        return source
    if digest(source) != BASE_SHA256 or text.count(BEFORE) != 1:
        raise ValueError('Pinned native terminal runtime changed')
    return text.replace(BEFORE, AFTER, 1).encode()


def read(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Root-owned regular runtime required')
        data = os.read(descriptor, 3 * 1024 * 1024 + 1)
        if len(data) > 3 * 1024 * 1024:
            raise ValueError('Runtime source exceeds budget')
        return data
    finally:
        os.close(descriptor)


def write(path: Path, content: bytes, mode: int) -> None:
    temporary = path.with_name(path.name + '.yield-ack-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    before = read(RUNTIME)
    after = overlay(before)
    report = {'before_sha256': digest(before), 'after_sha256': digest(after),
              'changed': before != after, 'applied': False, 'restart_required': False}
    if args.apply and before != after:
        if os.geteuid() != 0:
            raise ValueError('Apply requires root')
        backup = Path('/sandbox/.openclaw/yield-acknowledgment-backups') / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        write(backup / RUNTIME.name, before, 0o600)
        if read(RUNTIME) != before:
            raise ValueError('Runtime changed since validation')
        write(RUNTIME, after, stat.S_IMODE(RUNTIME.stat().st_mode))
        report.update(applied=True, restart_required=True, backup_directory=str(backup))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
