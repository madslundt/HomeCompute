#!/usr/bin/env python3
"""Provision one persistent embedding-only LiteLLM key for Open WebUI.

Run as root on home-core; default is a read-only plan. --apply creates or
validates the private key and patches only its env-file reference. It does not
restart Open WebUI or change its document indexes. Secrets never enter argv.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Any, Callable
import uuid

KEY_FILE = Path('/srv/state/open-webui/embedding-api-key')
ENV_FILE = Path('/etc/homecompute/open-webui.env')
CONTAINER = 'homecompute-control-plane-litellm-1'
SECRET_GID = 989
OWNER = 'open-webui-embeddings'


class Refused(Exception):
    """Operator-safe diagnostics only."""


def private_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def api(path: str, payload: dict[str, Any], *, query: bool = False,
        runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    source = '''import json, urllib.parse, urllib.request
master = open('/run/secrets/litellm_master_key').read().strip()
payload = %r
url = 'http://127.0.0.1:4000' + %r
query = %r
if query:
    url += '?' + urllib.parse.urlencode(payload)
request = urllib.request.Request(url, data=None if query else json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
print(json.dumps(json.load(urllib.request.urlopen(request, timeout=30))))
''' % (payload, path, query)
    try:
        result = runner(['docker', 'exec', '-i', CONTAINER, 'python3', '-'],
                        input=source, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('litellm-key-request-unconfirmed') from None
    if result.returncode:
        raise Refused('litellm-key-request-refused')
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Refused('litellm-key-response-refused') from None


def validate_key(key: str, runner: Callable[..., Any]) -> None:
    info = api('/key/info', {'key': key}, query=True, runner=runner).get('info', {})
    if (info.get('models') != ['embedding'] or info.get('user_id') != OWNER
            or info.get('expires') is not None):
        raise Refused('embedding-only-key-scope-required')


def environment_candidate(text: str, key_file: Path) -> str:
    line = 'OPEN_WEBUI_EMBEDDING_API_KEY_FILE=' + str(key_file)
    pattern = r'^OPEN_WEBUI_EMBEDDING_API_KEY_FILE=.*$'
    if len(re.findall(pattern, text, flags=re.MULTILINE)) > 1:
        raise Refused('duplicate-environment-key-refused')
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, lambda _: line, text, flags=re.MULTILINE)
    return text.rstrip('\n') + '\n' + line + '\n'


def setup(*, apply: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise Refused('run-as-root-on-home-core')
    for directory in (KEY_FILE.parent, ENV_FILE.parent):
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise Refused('symlink-parent-refused')
    if (not ENV_FILE.is_file() or ENV_FILE.stat().st_uid != 0
            or ENV_FILE.stat().st_mode & 0o022):
        raise Refused('root-owned-environment-required')
    if ENV_FILE.is_symlink() and not str(ENV_FILE.resolve()).startswith('/nix/store/'):
        raise Refused('untrusted-environment-symlink')
    original_env = ENV_FILE.read_bytes()
    patched_env = environment_candidate(original_env.decode(), KEY_FILE).encode()
    exists = KEY_FILE.exists() or KEY_FILE.is_symlink()
    if exists:
        info = KEY_FILE.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != SECRET_GID
                or stat.S_IMODE(info.st_mode) != 0o440):
            raise Refused('private-embedding-key-file-required')
        key = KEY_FILE.read_text().strip()
        if not key.startswith('sk-') or re.search(r'\s', key):
            raise Refused('embedding-key-format-refused')
        validate_key(key, runner)
    summary = {'key_exists': exists, 'key_file': str(KEY_FILE),
               'environment_changed': patched_env != original_env, 'applied': False}
    if not apply:
        return summary
    if ENV_FILE.read_bytes() != original_env:
        raise Refused('environment-changed-since-validation')
    if not exists:
        data = api('/key/generate', {'models': ['embedding'], 'key_alias': OWNER,
                   'user_id': OWNER, 'duration': None,
                   'metadata': {'purpose': 'local Open WebUI document embeddings'}}, runner=runner)
        key = data.get('key', '')
        if (data.get('models') != ['embedding'] or data.get('expires') is not None
                or not isinstance(key, str) or not key.startswith('sk-') or re.search(r'\s', key)):
            raise Refused('generated-key-scope-refused')
        validate_key(key, runner)
        private_write(KEY_FILE, (key + '\n').encode(), 0o440)
        os.chown(KEY_FILE, 0, SECRET_GID)
        os.chmod(KEY_FILE, 0o440)
    if patched_env != original_env:
        backups = KEY_FILE.parent / 'embedding-config-backups'
        if backups.is_symlink():
            raise Refused('symlink-backup-refused')
        backups.mkdir(mode=0o700, exist_ok=True)
        private_write(backups / ('environment-before-' + str(uuid.uuid4())), original_env)
        temporary = ENV_FILE.with_name('.embedding-env-' + str(uuid.uuid4()))
        private_write(temporary, patched_env)
        os.replace(temporary, ENV_FILE)
    summary['applied'] = True
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(setup(apply=args.apply), sort_keys=True))
        return 0
    except (Refused, OSError, ValueError):
        print('Open WebUI embedding key setup refused; inspect private paths and LiteLLM key scope.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
