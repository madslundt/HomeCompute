#!/usr/bin/env python3
"""Install a pinned pre-claim Mattermost gate and read-only native activity RPC."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

PLUGIN = Path('/sandbox/.openclaw/npm/projects/openclaw-mattermost-41605c6faa/node_modules/@openclaw/mattermost')
SAFETY = Path('/sandbox/.openclaw/extensions/homecompute-safety')
RUNTIME = 'channel.runtime-CdT1mcEl.js'
BASE_SHA256 = 'a10798f27f560fb92dab02fa92be06300381f2d4185fd6a3810001e9b0d507b5'
IMPORT = 'import { runWhenTextReady } from "/sandbox/.openclaw/extensions/homecompute-safety/llm-queue.mjs";\n'
ANCHOR = '\t\twaitForDeliveryIdleBeforeRepump: true,\n'
GATE = '\t\trunPumpTask: (work) => runWhenTextReady(work, options.abortSignal),\n'
SAFETY_IMPORT = "import { registerLlmActivity } from './llm-queue.mjs';\n"
SAFETY_ANCHOR = '    api.registerService(createBrowserRelay());\n'
SAFETY_REGISTER = '    registerLlmActivity(api);\n'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def overlay(source: bytes) -> bytes:
    text = source.decode()
    if text.startswith(IMPORT) and text.count(GATE) == 1:
        original = text[len(IMPORT):].replace(GATE, '', 1).encode()
        if digest(original) == BASE_SHA256:
            return source
    if digest(source) != BASE_SHA256 or text.count(ANCHOR) != 1:
        raise ValueError('Pinned official Mattermost runtime changed')
    return (IMPORT + text.replace(ANCHOR, ANCHOR + GATE, 1)).encode()


def safety_overlay(source: bytes) -> bytes:
    text = source.decode()
    if text.count(SAFETY_REGISTER) == text.count(SAFETY_IMPORT) == 1:
        return source
    if SAFETY_REGISTER in text or SAFETY_IMPORT in text or text.count(SAFETY_ANCHOR) != 1:
        raise ValueError('Protected safety plugin shape changed')
    return (SAFETY_IMPORT + text.replace(SAFETY_ANCHOR, SAFETY_ANCHOR + SAFETY_REGISTER, 1)).encode()


def read(path: Path, *, protected: bool = False) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or (protected and info.st_uid != 0):
            raise ValueError('Trusted regular input required')
        data = os.read(descriptor, 2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('Input exceeds bounded source budget')
        return data
    finally:
        os.close(descriptor)


def replace(path: Path, data: bytes, mode: int) -> None:
    if path.is_symlink():
        raise ValueError('Refuse installed symlink')
    temporary = path.with_name(path.name + '.llm-queue-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-module', required=True, type=Path)
    parser.add_argument('--module-sha256')
    parser.add_argument('--expected-safety-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    package = json.loads(read(PLUGIN / 'package.json'))
    if package.get('name') != '@openclaw/mattermost' or package.get('version') != '2026.9.1':
        raise ValueError('Pinned official Mattermost package required')
    runtime_path, index_path = PLUGIN / 'dist' / RUNTIME, SAFETY / 'index.mjs'
    before, index = read(runtime_path), read(index_path, protected=True)
    after, after_index = overlay(before), safety_overlay(index)
    module = read(args.source_module)
    report = {'runtime_before_sha256': digest(before), 'runtime_after_sha256': digest(after),
              'safety_before_sha256': digest(index), 'safety_after_sha256': digest(after_index),
              'module_sha256': digest(module), 'applied': False}
    if args.apply:
        if os.geteuid() != 0 or args.expected_safety_sha256 != digest(index) or args.module_sha256 != digest(module):
            raise ValueError('Apply requires root and reviewed source/module digests')
        backup = Path('/sandbox/.openclaw/llm-queue-backups') / uuid.uuid4().hex
        backup.mkdir(mode=0o700, parents=True)
        for name, data in (('mattermost-runtime.js', before), ('safety-index.mjs', index)):
            replace(backup / name, data, 0o600)
        replace(SAFETY / 'llm-queue.mjs', module, 0o444)
        replace(index_path, after_index, 0o444)
        replace(runtime_path, after, 0o444)
        report['applied'] = True
    print(json.dumps(report))


if __name__ == '__main__':
    main()
