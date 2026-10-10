#!/usr/bin/env python3
"""Install the dedicated browser worker in the agents guest without Compose."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

BROWSER = 'homecompute-openclaw-browser'
EGRESS = 'homecompute-openclaw-browser-egress'
INGRESS = 'homecompute-openclaw-browser-ingress'
PROFILE = 'homecompute-openclaw-browser-profile'


def docker(*args):
    r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise RuntimeError('Docker operation failed: ' + r.stderr[-2000:])
    return r.stdout.strip()


def install(directory: Path, image: str):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('immutable qualified image ID required')
    directory = directory.resolve()
    if not (directory / 'tools/browser-squid.conf').is_file():
        raise ValueError('staged browser sources required')
    private = directory / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    token = private / 'browser-token'
    if not token.exists():
        fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, 'w') as out:
            out.write(secrets.token_urlsafe(48) + '\n')
    if token.is_symlink() or private.stat().st_mode & 0o077:
        raise ValueError('private token directory required')
    for name, subnet, internal in [(BROWSER, '172.30.188.0/24', True), (EGRESS, '172.30.189.0/24', False)]:
        r = subprocess.run(['docker', 'network', 'inspect', name], capture_output=True, text=True)
        if r.returncode:
            docker('network', 'create', '--subnet', subnet, *(['--internal'] if internal else []), name)
        else:
            network = json.loads(r.stdout)[0]
            if network['Internal'] != internal or network['IPAM']['Config'][0]['Subnet'] != subnet:
                raise ValueError('existing network differs from browser contract')
    existing = docker('ps', '-a', '--format', '{{.Names}}').splitlines()
    if any(name in existing for name in (BROWSER, EGRESS, INGRESS)):
        raise ValueError('browser containers already exist; inspect before replacement')
    common = ['--init', '--restart', 'unless-stopped', '--user', '10001:10001', '--read-only',
              '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
              '--log-driver', 'local', '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2']
    docker('create', '--name', EGRESS, *common, '--network', EGRESS,
        '--cpus', '0.25', '--memory', '128m', '--memory-swap', '128m', '--pids-limit', '64',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=1777',
        '--mount', 'type=bind,src=' + str(directory / 'tools/browser-squid.conf') + ',dst=/etc/homecompute/browser-squid.conf,readonly',
        image, '/usr/sbin/squid', '-N', '-f', '/etc/homecompute/browser-squid.conf')
    docker('network', 'connect', '--alias', 'browser-egress', BROWSER, EGRESS)
    docker('start', EGRESS)
    docker('volume', 'create', PROFILE)
    docker('create', '--name', BROWSER, *common, '--network', BROWSER,
        '--cpus', '1', '--memory', '4g', '--memory-swap', '4g', '--pids-limit', '256',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=1777',
        '--mount', 'type=bind,src=' + str(token) + ',dst=/run/browser-token,readonly',
        '--mount', 'type=volume,src=' + PROFILE + ',dst=/home/browser/profile',
        '--env', 'HOME=/tmp', '--env', 'XDG_CONFIG_HOME=/tmp/config', '--env', 'XDG_CACHE_HOME=/tmp/cache',
        '--env', 'BROWSER_TOKEN_FILE=/run/browser-token',
        '--env', 'BROWSER_PUBLIC_ORIGIN=http://172.18.0.1:18800', image)
    docker('start', BROWSER)
    docker('create', '--name', INGRESS, *common, '--network', EGRESS,
        '--cpus', '0.25', '--memory', '64m', '--memory-swap', '64m', '--pids-limit', '64',
        '--mount', 'type=bind,src=' + str(directory / 'tools/tcp_relay.py') + ',dst=/opt/homecompute/tcp_relay.py,readonly',
        '--publish', '172.18.0.1:18800:18800',
        '--env', 'BROWSER_UPSTREAM=homecompute-openclaw-browser:18800',
        image, '/usr/bin/python3', '/opt/homecompute/tcp_relay.py')
    docker('network', 'connect', BROWSER, INGRESS)
    docker('start', INGRESS)
    receipt = {'browser': BROWSER, 'egress': EGRESS, 'image': image,
               'listener': '172.18.0.1:18800', 'private_networks': 'proxy denied'}
    (private / 'installation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--image-id', required=True)
    args = p.parse_args()
    install(args.directory, args.image_id)
