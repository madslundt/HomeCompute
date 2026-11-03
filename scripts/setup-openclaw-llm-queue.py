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


def runtime_profile(version: str):
    if version == '2026.9.1': return RUNTIME, BASE_SHA256, None
    if version == '2026.9.2':
        return ('channel.runtime-B454n78J.js',
                '17f9cc55ddbbd3f2393ba459746dec1c711ae79255b0c18afe16908f780d45eb',
                'a749d8665eab1280fac1247713c72a3b356f0936b04dafbd72e82bad74247284')
    raise ValueError('Unqualified Mattermost runtime version')


def activity_module(source: bytes, version='2026.9.1') -> bytes:
    runtime_profile(version)
    if version == '2026.9.1': return source
    replacements = (
        ("/usr/local/lib/node_modules/openclaw/dist/gateway-active-work-DoISxXUF.js",
         "/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw/dist/gateway-active-work-DdwRky6I.js"),
        ('762f2ad2930d6445b77487ecf878c985dca7181238f5301d37fc054a71a56966',
         '286cd71c28dd945b266bd96df9c6b2242e419de0052203e070d4e838c8997a48'))
    text = source.decode()
    for before, after in replacements:
        if text.count(before) != 1: raise ValueError('Reviewed native activity source changed')
        text = text.replace(before, after, 1)
    return text.encode()


def plugin_root(version, explicit=None):
    if explicit is not None:
        root = explicit
        relative = root.relative_to(Path('/sandbox/.openclaw/npm/projects'))
        if len(relative.parts) != 4 or relative.parts[1:] != ('node_modules','@openclaw','mattermost'):
            raise ValueError('Native Mattermost project path required')
        if any(p.is_symlink() for p in (root,*root.parents)):
            raise ValueError('Native Mattermost project symlink refused')
        return root
    if version == '2026.9.1': return PLUGIN
    matches = []
    for candidate in Path('/sandbox/.openclaw/npm/projects').glob('openclaw-mattermost-*/node_modules/@openclaw/mattermost'):
        if len(matches) > 8: raise ValueError('Native Mattermost inventory exceeds bound')
        if json.loads(read(candidate/'package.json')).get('version') == version: matches.append(candidate)
    if len(matches) != 1: raise ValueError('Exactly one candidate Mattermost project required')
    return plugin_root(version, matches[0])


def overlay(source: bytes, version='2026.9.1') -> bytes:
    _, expected, patched = runtime_profile(version)
    text = source.decode()
    if text.startswith(IMPORT) and text.count(GATE) == 1:
        original = text[len(IMPORT):].replace(GATE, '', 1).encode()
        if digest(original) == expected:
            if patched is not None and digest(source) != patched:raise ValueError('Candidate patched digest changed')
            return source
    if digest(source) != expected or text.count(ANCHOR) != 1:
        raise ValueError('Pinned official Mattermost runtime changed')
    result = (IMPORT + text.replace(ANCHOR, ANCHOR + GATE, 1)).encode()
    if patched is not None and digest(result) != patched: raise ValueError('Candidate Mattermost output digest changed')
    return result


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
    parser.add_argument('--runtime-version', choices=('2026.9.1','2026.9.2'), default='2026.9.1')
    parser.add_argument('--core-version', choices=('2026.9.1','2026.9.2'), help='Explicit core profile when retaining a compatible channel package')
    parser.add_argument('--plugin-root', type=Path)
    args = parser.parse_args()
    plugin = plugin_root(args.runtime_version, args.plugin_root)
    runtime, _, _ = runtime_profile(args.runtime_version)
    package = json.loads(read(plugin / 'package.json'))
    if package.get('name') != '@openclaw/mattermost' or package.get('version') != args.runtime_version:
        raise ValueError('Pinned official Mattermost package required')
    runtime_path, index_path = plugin / 'dist' / runtime, SAFETY / 'index.mjs'
    before, index = read(runtime_path), read(index_path, protected=True)
    after, after_index = overlay(before, args.runtime_version), safety_overlay(index)
    original_module = read(args.source_module)
    core_version = args.core_version or args.runtime_version
    module = activity_module(original_module, core_version)
    report = {'runtime_version':args.runtime_version,'core_version':core_version,'module_source_sha256':digest(original_module),'runtime_before_sha256': digest(before), 'runtime_after_sha256': digest(after),
              'safety_before_sha256': digest(index), 'safety_after_sha256': digest(after_index),
              'module_sha256': digest(module), 'applied': False}
    if args.apply:
        if os.geteuid() != 0 or args.expected_safety_sha256 != digest(index) or args.module_sha256 != digest(original_module):
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
