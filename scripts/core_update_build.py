"""Build a package-refresh candidate from the deployed, approved core release.

Only a private clone and the Nix store change. Existing closure preparation
decides whether the resulting exact system requires approval; no activation,
profile update, repository publication or release-tree edit occurs here.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from infrastructure_maintenance import ENV, MAX_OUTPUT, root_json, trusted_path

CURRENT = Path('/srv/homecompute/current')
RELEASES = Path('/srv/homecompute/releases')
STAGING = Path('/var/lib/homecompute-host-maintenance/nixos')
GIT = '/run/current-system/sw/bin/git'
NIX = '/run/current-system/sw/bin/nix'
READLINK = '/run/current-system/sw/bin/readlink'
ACTIVE = '/run/current-system'
REVISION = re.compile(r'[a-f0-9]{40}\Z')
MAX_FILE = 1024 * 1024


def core_helper():
    path = Path(__file__).with_name('prepare-core-maintenance.py')
    if not path.exists():
        path = Path('/opt/homecompute/infrastructure-maintenance/prepare-core-maintenance.py')
        trusted_path(path, 0)
    spec = importlib.util.spec_from_file_location('hc_core_update_preparation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(controller, argv, *, timeout=60, diagnostic=None):
    result = controller.runner(argv, timeout=timeout, env=ENV.copy(), cwd='/', shell=False)
    if (not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
            or len(result.stdout) + len(result.stderr) > MAX_OUTPUT):
        raise ValueError('bounded core package build command failed')
    if diagnostic is not None:
        trusted_path(diagnostic.parent, 0, directory=True)
        fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(result.stderr)
            stream.flush()
            os.fsync(stream.fileno())
    if result.returncode:
        raise ValueError('bounded core package build command failed')
    return result


def git(controller, directory, *args):
    return command(controller, [GIT, '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                '-C', str(directory), *args]).stdout


def file_bytes(path):
    path = trusted_path(path, 0)
    if path.stat().st_size > MAX_FILE:
        raise ValueError('bounded approved source file required')
    return path.read_bytes()


def source_release(controller):
    trusted_path(CURRENT.parent, 0, directory=True)
    info = CURRENT.lstat()
    if info.st_uid != 0 or not stat.S_ISLNK(info.st_mode):
        raise ValueError('root-owned deployed release link required')
    source = Path(os.readlink(CURRENT))
    if source.parent != RELEASES or not REVISION.fullmatch(source.name):
        raise ValueError('deployed source must be an exact approved release')
    trusted_path(source, 0, directory=True)
    trusted_path(source / '.git', 0, directory=True)
    if git(controller, source, 'rev-parse', 'HEAD') != (source.name + '\n').encode():
        raise ValueError('approved release HEAD differs from its pinned revision')
    if git(controller, source, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching'):
        raise ValueError('approved release tree must be completely clean')
    return source


def lock_identity(lock):
    """Keep approved URL/channel/follow edges, allowing only artifact refreshes."""
    if (not isinstance(lock, dict) or set(lock) != {'version', 'root', 'nodes'}
            or lock['version'] != 7 or not isinstance(lock['root'], str)
            or not isinstance(lock['nodes'], dict) or not 1 <= len(lock['nodes']) <= 128
            or lock['root'] not in lock['nodes']):
        raise ValueError('bounded approved flake lock graph required')
    mutable = {'rev', 'narHash', 'lastModified', 'lastModifiedDate', 'revCount'}
    result = {}
    for name, node in lock['nodes'].items():
        if not isinstance(name, str) or not isinstance(node, dict):
            raise ValueError('explicit flake input objects required')
        if 'locked' in node:
            locked = node['locked']
            if not isinstance(locked, dict) or not isinstance(node.get('original'), dict):
                raise ValueError('explicit locked input origin required')
            result[name] = {**node, 'locked': {key: value for key, value in locked.items() if key not in mutable}}
        else:
            result[name] = node
    return {'root': lock['root'], 'nodes': result, 'version': lock['version']}


def private_staging():
    # The scheduler owns the parent. Never create through an uncontrolled path.
    trusted_path(STAGING.parent, 0, directory=True)
    if not STAGING.exists():
        STAGING.mkdir(mode=0o700)
    trusted_path(STAGING, 0, directory=True)
    if STAGING.stat().st_mode & 0o077:
        raise ValueError('private root build staging required')
    return STAGING / uuid.uuid4().hex


def unchanged_sources(controller, stage, original_flake):
    if file_bytes(stage / 'flake.nix') != original_flake:
        raise ValueError('package refresh cannot change approved flake declarations')
    changed = git(controller, stage, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching')
    if changed not in {b'', b' M flake.lock\n'}:
        raise ValueError('only the private flake lock may change')


def prepare(controller, output):
    """Write an existing-policy exact plan and return bounded build provenance."""
    if os.geteuid() != 0 or controller.uid != 0 or controller.local_target != 'home-core':
        raise ValueError('root core scheduler required')
    output = Path(output)
    trusted_path(output.parent, 0, directory=True)
    if output.exists() or output.is_symlink():
        raise ValueError('new private plan destination required')
    source = source_release(controller)
    original_flake = file_bytes(source / 'flake.nix')
    original_lock = lock_identity(root_json(source / 'flake.lock', 0))
    stage = private_staging()
    command(controller, [GIT, '-c', 'core.hooksPath=/dev/null', 'clone', '--no-hardlinks',
                         '--no-checkout', '--', str(source), str(stage)], timeout=180)
    trusted_path(stage, 0, directory=True)
    os.chmod(stage, 0o700)
    git(controller, stage, 'checkout', '--detach', source.name)
    if git(controller, stage, 'rev-parse', 'HEAD') != (source.name + '\n').encode():
        raise ValueError('private clone does not match approved source')
    unchanged_sources(controller, stage, original_flake)
    if lock_identity(root_json(stage / 'flake.lock', 0)) != original_lock:
        raise ValueError('private clone lock origin differs from approved source')
    command(controller, [NIX, 'flake', 'update', '--accept-flake-config', '--flake', 'path:' + str(stage)], timeout=3600)
    unchanged_sources(controller, stage, original_flake)
    if lock_identity(root_json(stage / 'flake.lock', 0)) != original_lock:
        raise ValueError('flake refresh changed approved input URLs, channels or dependency edges')
    lock_hash = hashlib.sha256(file_bytes(stage / 'flake.lock')).hexdigest()
    build_log = stage.with_suffix('.build.log')
    result = command(controller, [NIX, 'build', '--accept-flake-config',
                                  'path:' + str(stage) + '#nixosConfigurations.home-core.config.system.build.toplevel',
                                  '--no-link', '--print-out-paths'], timeout=3600, diagnostic=build_log)
    unchanged_sources(controller, stage, original_flake)
    if hashlib.sha256(file_bytes(stage / 'flake.lock')).hexdigest() != lock_hash:
        raise ValueError('build changed the reviewed candidate lock')
    helper = core_helper()
    candidate = helper.resolved_system(result.stdout)
    current = helper.resolved_system(command(controller, [READLINK, '-f', ACTIVE]).stdout)
    if candidate == current:
        raise ValueError('no NixOS package updates')
    plan, reasons = helper.prepare(candidate, runner=controller.runner, clock=controller.clock)
    helper.write_plan(output, plan)
    return {'plan_file': str(output), 'plan_written': True, 'candidate': candidate,
            'source_pinned_sha': source.name, 'staged_lock_sha256': lock_hash,
            'staged_source': str(stage), 'build_log': str(build_log),
            'review_reasons': reasons, 'installed_changes': False}
