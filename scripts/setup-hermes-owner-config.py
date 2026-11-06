#!/usr/bin/env python3
"""Install the selected owner's canonical non-secret config without replacing state."""
from pathlib import Path
import os
import platform
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def install(destination: Path, content: str) -> None:
    if destination.name != 'hermes-personal-v23.env' or not destination.is_absolute():
        raise ValueError('fixed selected-owner configuration required')
    for parent in (destination.parent, *destination.parents):
        if parent.is_symlink():
            raise ValueError('real configuration directory required')
    info = destination.parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private operator-owned directory required')
    if destination.exists() or destination.is_symlink():
        info = destination.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or destination.read_text() != content):
            raise ValueError('existing configuration differs; refusing replacement')
        return
    fd, temporary = tempfile.mkstemp(prefix='.hermes-owner-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        # Publish complete contents exclusively; a competing writer is never replaced.
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)


def main():
    release = platform.freedesktop_os_release()
    if (os.getuid() != 1000 or Path.home() != Path('/home/hermes-operator')
            or release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04'):
        raise ValueError('Ubuntu 24.04 agents guest operator required')
    destination = Path.home() / '.config/homecompute-hermes/hermes-personal-v23.env'
    install(destination, (ROOT / 'config/hermes-agent.env.example').read_text())
    print('Selected Hermes owner configuration installed; prior configuration retained')


if __name__ == '__main__':
    main()
