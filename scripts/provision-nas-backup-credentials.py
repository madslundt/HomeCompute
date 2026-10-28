#!/usr/bin/env python3
"""Provision the dedicated NAS credential through an operator's private terminal."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import stat
import tempfile

POLICY = Path(__file__).resolve().parents[1] / 'config/synology-backup.json'


def credential(password: str, confirmation: str, username: str) -> bytes:
    if password != confirmation:
        raise ValueError('password confirmation differs')
    if not 12 <= len(password) <= 1024 or any(x in password for x in ['\n', '\r', '\0']):
        raise ValueError('password must be 12–1024 characters without line breaks or NUL')
    if username != 'homecompute-backup':
        raise ValueError('only the dedicated backup account is admitted')
    return f'username={username}\npassword={password}\n'.encode()


def validate(path: Path, username: str) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('credential must be a root-owned mode-0600 regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        value = stream.read(4097)
    if len(value) > 4096:
        raise ValueError('credential file exceeds its bound')
    rows = value.decode().splitlines()
    if (len(rows) != 2 or rows[0] != f'username={username}' or not rows[1].startswith('password=')
            or len(rows[1].removeprefix('password=')) < 12):
        raise ValueError('credential file does not match the dedicated account')
    return {'credential_ready': True, 'username': username, 'path': str(path)}


def provision(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError('existing credentials are preserved; use a separately reviewed rotation')
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError('credential parent must be an existing real directory')
    parent = path.parent.stat()
    if parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ValueError('credential parent must be protected from non-root writes')
    fd, temporary = tempfile.mkstemp(prefix='.nas-credentials.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-replacement publication, including a concurrent creator.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['provision', 'check'])
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'home-core':
        parser.error('run as root on home-core')
    policy = json.loads(POLICY.read_text())
    path, username = Path(policy['credentials_file']), policy['username']
    if args.action == 'provision':
        if not os.isatty(0):
            parser.error('use an interactive terminal; passwords are never command-line arguments')
        password = getpass.getpass('Synology homecompute-backup password: ')
        confirmation = getpass.getpass('Confirm Synology backup password: ')
        provision(path, credential(password, confirmation, username))
    print(json.dumps(validate(path, username)))


if __name__ == '__main__':
    main()
