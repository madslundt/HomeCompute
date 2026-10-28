#!/usr/bin/env python3
"""Stage fixed root-owned guest DNS controller; never launch or change DNS."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
import importlib.util
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path('/etc/homecompute-public-dns')
UNIT = Path('/etc/systemd/system/homecompute-public-dns.service')
SANDBOX_ID = '7bdaf599-aebf-47b9-94cf-f6bb6c287859'
GENERATION = 'da0d7b5a-e7cb-4cb5-90b4-3c14da39b3fa'


def protected(path: Path, *, directory: bool = False) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('root-protected path required')
    if directory and not path.is_dir():
        raise ValueError('directory required')


def write(path: Path, content: bytes, mode: int) -> None:
    protected(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        protected(path)
        if path.read_bytes() == content and path.stat().st_mode & 0o777 == mode:
            return
        backup = DIRECTORY / 'backups'
        backup.mkdir(mode=0o700, exist_ok=True)
        protected(backup, directory=True)
        target = backup / (path.name + '.' + str(time.time_ns()))
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(path.read_bytes())
    temporary = path.with_name('.' + path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(identity_override: dict[str, str] | None = None) -> dict[str, object]:
    if os.geteuid() != 0 or sys.platform != 'linux' or socket.gethostname() != 'agents':
        raise ValueError('root agents guest required')
    module = ROOT / 'deploy/openclaw/safety-plugin/public-dns.mjs'
    controller = ROOT / 'scripts/openclaw-public-dns.py'
    unit = ROOT / 'deploy/openclaw/nemoclaw/homecompute-public-dns.service'
    for source in (module, controller, unit):
        protected(source)
    if DIRECTORY.exists() or DIRECTORY.is_symlink():
        protected(DIRECTORY, directory=True)
    else:
        protected(DIRECTORY.parent, directory=True)
        DIRECTORY.mkdir(mode=0o755)
    identity = {'sandbox_id': SANDBOX_ID, 'generation': GENERATION,
                'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest()}
    if identity_override is not None:
        identity.update(identity_override)
    # Operator-owned manifest is absent on fresh cloud-init; reviewed install
    # enables only this exact current identity, with no CLI target override.
    write(DIRECTORY / 'openclaw-public-dns.py', controller.read_bytes(), 0o555)
    write(DIRECTORY / 'identity.json', (json.dumps(identity, sort_keys=True) + '\n').encode(), 0o600)
    write(UNIT, unit.read_bytes(), 0o644)
    return {'staged': True, 'started': False, 'module_sha256': identity['module_sha256']}


def rebind(sandbox_id: str, generation: str) -> dict[str, object]:
    """Rebind only a positively verified replacement of the same native target."""
    if os.geteuid() != 0 or socket.gethostname() != 'agents':
        raise ValueError('root agents guest required')
    if any(str(uuid.UUID(value)) != value for value in (sandbox_id, generation)):
        raise ValueError('exact native UUIDs required')
    source = ROOT / 'scripts/openclaw-public-dns.py'
    protected(source)
    spec = importlib.util.spec_from_file_location('dns_rebind_controller', source)
    controller = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = controller
    spec.loader.exec_module(controller)
    module = ROOT / 'deploy/openclaw/safety-plugin/public-dns.mjs'
    protected(module)
    identity = {'sandbox_id': sandbox_id, 'generation': generation,
                'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest()}
    observe_ready(controller, identity)  # supervisor, registry, image, volume, UID and listeners
    container = 'openshell-default--agent-openclaw-' + sandbox_id
    def docker(*args):
        subprocess.run(['/usr/bin/docker', *args], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
    docker('exec', '--user', '0:0', container, 'install', '-d', '-o', '0', '-g', '0', '-m', '0755', '/opt/homecompute-public-dns')
    docker('cp', str(module), container + ':/opt/homecompute-public-dns/public-dns.mjs')
    docker('exec', '--user', '0:0', container, 'chown', '0:0', '/opt/homecompute-public-dns/public-dns.mjs')
    docker('exec', '--user', '0:0', container, 'chmod', '0555', '/opt/homecompute-public-dns/public-dns.mjs')
    observe_ready(controller, identity)
    return install({'sandbox_id': sandbox_id, 'generation': generation})


def observe_ready(controller, identity):
    # The just-completed native CLI probe briefly shares the truncated gateway
    # process title. Wait for one positively verified gateway; never select one
    # from an ambiguous set or retry an identity/permission failure.
    for attempt in range(30):
        try:
            return controller.observe(identity)
        except controller.Refused as error:
            if str(error)!='gateway-cardinality' or attempt==29:
                raise
            time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replacement-id')
    parser.add_argument('--replacement-generation')
    args = parser.parse_args()
    if bool(args.replacement_id) != bool(args.replacement_generation):
        parser.error('both replacement UUIDs required')
    if args.replacement_id:
        if os.geteuid() != 0 or socket.gethostname() != 'agents':
            raise ValueError('root agents guest required')
        print(json.dumps(rebind(args.replacement_id, args.replacement_generation)))
    else:
        print(json.dumps(install()))


if __name__ == '__main__':
    main()
