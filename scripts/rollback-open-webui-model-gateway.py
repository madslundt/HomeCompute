#!/usr/bin/env python3
"""Restore the previous Open WebUI gateway settings without replacing its database."""
from __future__ import annotations

import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess


DATA = Path('/srv/state/open-webui/data')
LEGACY = 'http://litellm:4000/v1'
CURRENT = 'https://llm.home.arpa/v1'


def settings(db):
    rows = db.execute('SELECT key,value FROM config')
    return {key: json.loads(value) if isinstance(value, str) else value for key, value in rows}


def transport_change(before, url_key, credential_key):
    if before.get(url_key) != LEGACY:
        return {}
    return {url_key: CURRENT, credential_key: 'local'}


def restore(database, backup):
    for path in (database, backup):
        if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError('Expected private Open WebUI database files')
    with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as old:
        before = settings(old)
    desired = transport_change(before, 'rag.openai.api_base_url', 'rag.openai.api_key')
    if not desired:
        raise ValueError('Select a backup from before the gateway transport migration')
    for role in ('stt', 'tts'):
        prefix = f'audio.{role}.openai.'
        desired.update(transport_change(before, prefix + 'api_base_url', prefix + 'api_key'))
    urls = before.get('openai.api_base_urls', [])
    if LEGACY in urls:
        keys = before.get('openai.api_keys', [])
        if urls.count(LEGACY) != 1 or len(keys) != len(urls):
            raise ValueError('The previous managed model connection is ambiguous')
        slot = urls.index(LEGACY)
        desired['openai.api_base_urls'] = [CURRENT if idx == slot else url for idx, url in enumerate(urls)]
        desired['openai.api_keys'] = ['local' if idx == slot else key for idx, key in enumerate(keys)]

    with closing(sqlite3.connect(database, timeout=5)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        current = settings(db)
        if all(current.get(key) == before.get(key) for key in desired):
            return 0
        if any(current.get(key) != value for key, value in desired.items()):
            raise ValueError('Managed gateway settings changed after migration')
        for key in desired:
            if key in before:
                db.execute('UPDATE config SET value=? WHERE key=?', (json.dumps(before[key]), key))
            else:
                db.execute('DELETE FROM config WHERE key=?', (key,))
    return len(desired)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True, help='migration backup basename, including .sqlite3')
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        raise SystemExit('Run as root on home-core')
    if not re.fullmatch(r'[0-9a-f-]{36}\.sqlite3', args.backup):
        raise SystemExit('Use the Open WebUI migration backup basename')
    try:
        with Path('/var/lib/homecompute/deploy.lock').open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            running = subprocess.check_output([
                'docker', 'ps', '--quiet', '--filter', 'label=com.docker.compose.project=homecompute-open-webui',
                '--filter', 'label=com.docker.compose.service=open-webui',
            ], text=True, timeout=10)
            if running.strip():
                raise ValueError('Stop Open WebUI before restoring its gateway settings')
            count = restore(DATA / 'webui.db', DATA / 'embedding-config-backups' / args.backup)
    except ValueError as error:
        # JSON errors can contain database values, so print only our fixed messages.
        raise SystemExit(str(error) if type(error) is ValueError else 'Invalid Open WebUI config data') from None
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise SystemExit('Open WebUI gateway rollback failed; inspect private state') from None
    print(f'Restored {count} Open WebUI gateway settings.')


if __name__ == '__main__':
    main()
