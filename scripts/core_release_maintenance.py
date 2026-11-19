"""Exact Home Core releases for the root maintenance controller."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

from infrastructure_maintenance import ENV, RELEASE_COMMAND, RELEASE_TIMEOUT, STATE, trusted_path

ORIGIN = 'https://github.com/madslundt/HomeCompute.git'
MASTER = 'refs/heads/master'
BIN = Path('/run/current-system/sw/bin')
RELEASES = Path('/srv/homecompute/releases')
MARKER = Path('/var/lib/homecompute/deployed-revision')
CURRENT = RELEASES.parent / 'current'
REVISION = re.compile(r'[a-f0-9]{40}')
GIT = [str(BIN / 'git'), '-c', 'core.hooksPath=/dev/null']
GIT_ENV = {**ENV, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}


def deployed_revision():
    value = trusted_path(MARKER, 0).read_bytes()
    if not re.fullmatch(rb'[a-f0-9]{40}\n', value):
        raise ValueError('exact deployed revision required')
    revision = value[:-1].decode('ascii')
    trusted_path(RELEASES / revision, 0, directory=True)
    if CURRENT.lstat().st_uid != 0 or CURRENT.readlink() != RELEASES / revision:
        raise ValueError('deployed revision and current release differ')
    return revision


def prepare(controller):
    base = deployed_revision()
    result = subprocess.run([*GIT, 'ls-remote', '--exit-code', ORIGIN, MASTER],
                            env=GIT_ENV, cwd='/', stdin=subprocess.DEVNULL,
                            capture_output=True, check=True, timeout=30)
    match = re.fullmatch(rb'([a-f0-9]{40})\trefs/heads/master\n', result.stdout)
    if not match:
        raise ValueError('exact published master revision required')
    revision = match[1].decode('ascii')
    if revision == base:
        raise ValueError('Home Core is already on the published revision')
    args = [base, revision]
    return {
        'schema_version': 1,
        'policy': {'operation': 'deploy', 'target': 'home-core', 'updates': [],
                   'base_revision': base, 'revision': revision},
        'prechecks': [{'argv': [RELEASE_COMMAND, 'snapshot', *args],
                       'stdout_sha256': hashlib.sha256((base + '\n').encode()).hexdigest()}],
        'steps': [[RELEASE_COMMAND, 'apply', *args]],
        'postchecks': [[RELEASE_COMMAND, 'verify', *args]],
        'timeout_seconds': RELEASE_TIMEOUT,
        'expires_at': controller.clock() + 3 * 3600,
    }


def execute(base, revision):
    # The transient unit runs outside the controller's mount namespace, with a
    # separate lifetime from MCP, but the release wrapper owns the host lock.
    with tempfile.TemporaryDirectory(prefix='published-release-', dir=trusted_path(STATE, 0, directory=True)) as stage:
        subprocess.run([*GIT, 'clone', '--depth=1', '--single-branch', '--branch', 'master',
                        '--', ORIGIN, stage], check=True, env=GIT_ENV, timeout=300)
        head = subprocess.check_output([*GIT, '-C', stage, 'rev-parse', 'HEAD'],
                                       env=GIT_ENV, timeout=10)
        if head != (revision + '\n').encode('ascii'):
            raise ValueError('published master moved; prepare and approve the new revision')
        script = trusted_path(Path(stage) / 'scripts/deploy-home-core.sh', 0)
        subprocess.run([str(BIN / 'bash'), str(script), revision, '--expect-current', base],
                       check=True, cwd='/', env=ENV.copy(), stdin=subprocess.DEVNULL)


def operate(operation, base, revision):
    if not all(REVISION.fullmatch(value) for value in (base, revision)) or base == revision:
        raise ValueError('distinct full revisions required')
    if operation == 'snapshot' and deployed_revision() == base:
        print(base)
    elif operation == 'apply' and deployed_revision() == base:
        subprocess.run([str(BIN / 'systemd-run'), '--quiet', '--wait', '--collect',
                        '--unit=homecompute-approved-release',
                        '--property=Type=exec', '--property=RuntimeMaxSec=7200',
                        '--property=UMask=0077', RELEASE_COMMAND, 'execute', base, revision],
                       check=True, stdin=subprocess.DEVNULL, env=ENV.copy())
    elif operation == 'execute':
        execute(base, revision)
    elif operation == 'verify' and deployed_revision() == revision:
        previous = CURRENT.with_name('previous')
        if previous.lstat().st_uid != 0 or previous.readlink() != RELEASES / base:
            raise ValueError('previous release pointer differs from the approved base')
    else:
        raise ValueError('release request or host state differs from the approved plan')


def main():
    import sys

    try:
        if len(sys.argv) != 4 or os.geteuid() != 0 or socket.gethostname() != 'home-core':
            raise ValueError('exact release command requires root on Home Core')
        operate(*sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Detailed release output stays in the root-managed systemd journal.
        print('Home Core release refused or incomplete: ' + str(error), file=sys.stderr)
        return 2
    return 0
