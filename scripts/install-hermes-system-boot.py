#!/usr/bin/env python3
"""Order the agents user manager before Docker shutdown; never restart it live."""
import json
import os
from pathlib import Path
import pwd
import platform
import subprocess
import tempfile
import time

CONTENT = ('# HomeCompute-managed agents user ordering\n'
           '[Unit]\nAfter=docker.service\n')


def install(root=Path('/')):
    path = root / 'etc/systemd/system/user@1000.service.d/90-homecompute-docker-ordering.conf'
    for parent in (root / 'etc/systemd/system', path.parent):
        if parent.is_symlink():
            raise ValueError('real system unit directory required')
    if path.is_symlink():
        raise ValueError('real managed ordering file required')
    previous = path.read_text() if path.exists() else None
    if previous is not None and (not previous.startswith('# HomeCompute-managed agents user ordering\n')
                                 or path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
        raise ValueError('unknown system ordering must be reconciled explicitly')
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if previous != CONTENT:
        if previous is not None:
            directory = root / 'var/lib/homecompute-hermes-system/boot-backups'
            for parent in (directory.parent, directory):
                if parent.is_symlink():
                    raise ValueError('real private system backup directory required')
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = directory / (str(time.time_ns()) + '.conf')
            backup.write_text(previous); backup.chmod(0o600)
        fd, temporary = tempfile.mkstemp(prefix='.ordering-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            stream.write(CONTENT)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    return path


def main():
    if os.geteuid() != 0:
        raise PermissionError('guest root required')
    owner = pwd.getpwnam('hermes-operator')
    if owner.pw_uid != 1000 or owner.pw_dir != '/home/hermes-operator':
        raise ValueError('the declared agents guest operator is required')
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04':
        raise ValueError('Ubuntu 24.04 agents guest required')
    subprocess.run(['systemctl', 'is-enabled', '--quiet', 'docker.service'], check=True)
    path = install()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    after = subprocess.check_output(['systemctl', 'show', 'user@1000.service', '-p', 'After', '--value'], text=True).split()
    if 'docker.service' not in after:
        raise ValueError('effective user manager Docker ordering is absent')
    print(json.dumps({'system_ordering': str(path), 'applied': True, 'user_manager_restarted': False}))


if __name__ == '__main__':
    main()
