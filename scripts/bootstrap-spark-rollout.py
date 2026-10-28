#!/usr/bin/env python3
"""Materialize one published HomeCompute release and exec its reviewed wizard.

The human authenticates with sudo once. No package, sudo policy, current
release pointer, mutable checkout, or privileged remote interface is changed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile

REPOSITORY = 'https://github.com/madslundt/HomeCompute.git'
RELEASES = Path('/srv/homecompute/releases')


def run(arguments: list[str]) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=240,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        raise RuntimeError('Published release verification failed')
    return result.stdout.strip()


def protected(directory: Path) -> None:
    for path in [*reversed(directory.absolute().parents), directory]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Untrusted release path')


def verify(directory: Path, revision: str) -> None:
    protected(directory)
    if run(['git', '-C', str(directory), 'rev-parse', 'HEAD']) != revision:
        raise ValueError('Existing release revision mismatch')
    if run(['git', '-C', str(directory), 'status', '--porcelain']):
        raise ValueError('Existing release is modified; nothing was overwritten')
    if run(['git', '-C', str(directory), 'remote', 'get-url', 'origin']) != REPOSITORY:
        raise ValueError('Unexpected release repository')
    run(['git', '-C', str(directory), 'merge-base', '--is-ancestor', revision, 'origin/master'])


def materialize(revision: str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact published SHA is required')
    RELEASES.mkdir(parents=True, exist_ok=True, mode=0o755)
    protected(RELEASES)
    target = RELEASES / revision
    if target.exists() or target.is_symlink():
        verify(target, revision)
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.spark-rollout-', dir=RELEASES))
    try:
        checkout = temporary / 'checkout'
        run(['git', 'clone', '--filter=blob:none', '--no-checkout', '--single-branch',
             '--branch', 'master', REPOSITORY, str(checkout)])
        run(['git', '-C', str(checkout), 'checkout', '--detach', revision])
        verify(checkout, revision)
        checkout.rename(target)
    finally:
        shutil.rmtree(temporary)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--corpus-archive', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-spark':
        raise ValueError('Run with sudo on home-spark')
    archive = args.corpus_archive.absolute()
    if not re.fullmatch(r'/tmp/homecompute-spark-rollout\.[A-Za-z0-9]+/corpus.tar', str(archive)):
        raise ValueError('Use the reviewed private staging directory and corpus.tar')
    if archive.is_symlink() or not archive.is_file() or archive.parent.is_symlink():
        raise ValueError('Expected a regular staged public corpus')
    if (archive.parent.stat().st_mode & 0o077 or
            archive.parent.stat().st_uid != int(os.environ.get('SUDO_UID',str(os.getuid())))):
        raise ValueError('The staging directory must be private')
    release = materialize(args.revision)
    wizard = release / 'scripts/spark-rollout-wizard.sh'
    os.execv('/bin/bash', ['bash', str(wizard), '--revision', args.revision,
                         '--corpus-archive', str(archive)])


if __name__ == '__main__':
    main()
