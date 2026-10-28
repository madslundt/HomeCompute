#!/usr/bin/env python3
"""Install the finite maintenance service or Spark forced-command dispatcher.

Only code, a private ledger, and optional restricted SSH key access are staged.
No package/image/application update is performed. Source contents must match the
explicit bundle hash; existing plans, approvals, SSH keys and state are retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile

FILES = ('infrastructure_maintenance.py', 'infrastructure_update_policy.py',
         'prepare-spark-maintenance.py', 'prepare-docker-maintenance.py', 'docker_image_updates.py',
         'infrastructure-maintenance-spark.py', 'prepare-core-maintenance.py',
         'maintenance_plan_preparation.py', 'homeassistant-maintenance.py', 'container-maintenance.py',
         'infrastructure_maintenance_operator.py', 'mcp_maintenance.py',
         'docker_maintenance.py', 'docker_maintenance_backup.py',
         'host_maintenance_sources.py', 'core_update_build.py')
DEST = Path('/opt/homecompute/infrastructure-maintenance')
STATE = Path('/var/lib/homecompute-infrastructure-maintenance')
NAME = 'homecompute-infrastructure-maintenance'


def bundle(source):
    contents, digest = {}, hashlib.sha256()
    for name in FILES:
        path = Path(source) / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 131072 or info.st_nlink != 1:
            raise ValueError('bounded regular source files required')
        data = path.read_bytes()
        compile(data, name, 'exec')
        contents[name] = data
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return contents, digest.hexdigest()


def directory(path, mode=0o755):
    from infrastructure_maintenance import trusted_path
    path = Path(path)
    if not path.exists():
        if not path.parent.exists():
            directory(path.parent)
        trusted_path(path.parent, 0, directory=True)
        path.mkdir(mode=mode)
    trusted_path(path, 0, directory=True)
    if path.stat().st_uid != 0:
        raise ValueError('root managed directory required')
    os.chmod(path, mode)


def write(path, contents, mode=0o644):
    if path.is_symlink() or path.exists() and (not path.is_file() or path.stat().st_uid != 0):
        raise ValueError('untrusted destination')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def unit(python):
    return f'''[Unit]
Description=HomeCompute exact infrastructure maintenance plans
After=network-online.target docker.service
Wants=network-online.target
[Service]
User=root
Group=homecompute-openclaw
UMask=0077
ExecStart={python} {DEST}/infrastructure_maintenance.py serve
Restart=on-failure
RestartSec=5s
RuntimeDirectory={NAME}
RuntimeDirectoryMode=0750
NoNewPrivileges=yes
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectClock=yes
RestrictSUIDSGID=yes
LimitCORE=0
TasksMax=256
TimeoutStopSec=15s
[Install]
WantedBy=multi-user.target
'''


def install(source, expected, public_key=None):
    if os.geteuid() != 0:
        raise ValueError('root installation required')
    host = socket.gethostname().split('.')[0]
    if host not in {'home-core', 'home-spark'}:
        raise ValueError('reviewed compute host required')
    files, actual = bundle(source)
    if actual != expected:
        raise ValueError('exact reviewed bundle hash required')
    directory(DEST); directory(STATE, 0o700)
    if not Path('/var/lib/homecompute').exists():
        directory(Path('/var/lib/homecompute'), 0o700)
    backup = STATE / 'code-backups'
    directory(backup, 0o700)
    for name, data in files.items():
        previous = DEST / name
        if previous.exists():
            from infrastructure_maintenance import trusted_path
            trusted_path(previous, 0)
            old = previous.read_bytes()
            write(backup / (name + '.' + hashlib.sha256(old).hexdigest()), old, 0o600)
        write(previous, data)
    directory(Path('/usr/local/libexec'))
    python = '/run/current-system/sw/bin/python3' if host == 'home-core' else '/usr/bin/python3'
    container_helper = '#!' + python + ' -I\n' + files['container-maintenance.py'].decode().split('\n', 1)[1]
    write(Path('/usr/local/libexec/homecompute-container-maintenance'), container_helper.encode(), 0o755)
    docker_helper = '#!' + python + ' -I\nimport sys\nsys.path.insert(0, ' + repr(str(DEST)) + ')\nfrom docker_maintenance import main\nraise SystemExit(main())\n'
    write(Path('/usr/local/libexec/homecompute-docker-maintenance'), docker_helper.encode(), 0o755)
    dispatcher = '#!' + python + ' -I\n' + files['infrastructure-maintenance-spark.py'].decode().split('\n', 1)[1]
    if host == 'home-spark':
        write(Path('/usr/local/libexec/homecompute-infrastructure-maintenance-spark'), dispatcher.encode(), 0o755)
        if public_key is not None:
            import re
            text = Path(public_key).read_text().strip()
            if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{40,100}(?: [A-Za-z0-9_.@-]{1,100})?', text):
                raise ValueError('one explicit Ed25519 public key required')
            directory(Path('/root/.ssh'), 0o700)
            authorized = Path('/root/.ssh/authorized_keys')
            existing = authorized.read_bytes() if authorized.exists() else b''
            if authorized.exists():
                from infrastructure_maintenance import trusted_path
                trusted_path(authorized, 0)
            line = b'restrict,command="/usr/local/libexec/homecompute-infrastructure-maintenance-spark" ' + text.encode()
            if text.split()[1].encode() not in existing:
                write(authorized, existing.rstrip(b'\n') + (b'\n' if existing else b'') + line + b'\n', 0o600)
        return {'host': host, 'installed': True, 'root_key_installed': public_key is not None, 'updates_run': 0}
    pwd.getpwnam('homecompute-openclaw')
    attached = Path('/etc/systemd/system.attached')
    directory(attached); directory(attached / 'multi-user.target.wants')
    service = attached / (NAME + '.service')
    write(service, unit(python).encode())
    link = attached / 'multi-user.target.wants' / service.name
    if link.is_symlink() and os.readlink(link) != str(service) or link.exists() and not link.is_symlink():
        raise ValueError('unmanaged boot dependency')
    if not link.is_symlink():
        link.symlink_to(service)
    subprocess.run(['systemd-analyze', 'verify', str(service)], check=True, capture_output=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True)
    subprocess.run(['systemctl', 'restart', service.name], check=True, capture_output=True)
    return {'host': host, 'installed': True, 'service': service.name, 'updates_run': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--bundle-sha256'); p.add_argument('--public-key', type=Path)
    a = p.parse_args()
    if a.bundle_sha256 is None:
        print(json.dumps({'bundle_sha256': bundle(a.source)[1], 'installed': False})); return 0
    try:
        print(json.dumps(install(a.source, a.bundle_sha256, a.public_key))); return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Maintenance setup refused; preserve existing private state.'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
