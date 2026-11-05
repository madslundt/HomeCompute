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
DNS = 'homecompute-openclaw-browser-dns'
PROFILE = 'homecompute-openclaw-browser-profile'


def docker(*args):
    r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise RuntimeError('Docker operation failed: ' + r.stderr[-2000:])
    return r.stdout.strip()


def install(directory: Path, image: str, *, egress_only: bool = False, principal: str = "openclaw"):
    if principal not in {"openclaw", "hermes"}:
        raise ValueError("reviewed browser principal required")
    BROWSER = f"homecompute-{principal}-browser"
    EGRESS, INGRESS, DNS, PROFILE = (BROWSER + suffix for suffix in ("-egress", "-ingress", "-dns", "-profile"))
    browser_octet, egress_octet, listener = (188, 189, 18800) if principal == "openclaw" else (190, 191, 18810)
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('immutable qualified image ID required')
    directory = directory.resolve()
    if not (directory / 'tools/browser-squid.conf').is_file():
        raise ValueError('staged browser sources required')
    existing = docker('ps', '-a', '--format', '{{.Names}}').splitlines()
    if any(name in existing for name in ((EGRESS,) if egress_only else (BROWSER, EGRESS, INGRESS, DNS))):
        raise ValueError('browser containers already exist; inspect before replacement')
    if egress_only and not all(name in existing for name in (BROWSER, INGRESS, DNS)):
        raise ValueError('existing browser, ingress and DNS required for egress-only reconciliation')
    private = directory / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    token = private / 'browser-token'
    if not egress_only and not token.exists():
        fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        with os.fdopen(fd, 'w') as out:
            out.write(secrets.token_urlsafe(48) + '\n')
    if token.is_symlink() or private.stat().st_mode & 0o077:
        raise ValueError('private token directory required')
    for name, subnet, internal in [(BROWSER, f'172.30.{browser_octet}.0/24', True), (EGRESS, f'172.30.{egress_octet}.0/24', False)]:
        r = subprocess.run(['docker', 'network', 'inspect', name], capture_output=True, text=True)
        if r.returncode:
            docker('network', 'create', '--subnet', subnet, *(['--internal'] if internal else []), name)
        else:
            network = json.loads(r.stdout)[0]
            if network['Internal'] != internal or network['IPAM']['Config'][0]['Subnet'] != subnet:
                raise ValueError('existing network differs from browser contract')
    proxy_config = directory / 'tools/browser-squid.conf'
    if principal == 'hermes':
        proxy_config = private / 'browser-squid.conf'
        proxy_config.write_text((directory / 'tools/browser-squid.conf').read_text().replace('172.30.188.0/24', '172.30.190.0/24'))
        proxy_config.chmod(0o644)
    common = ['--init', '--restart', 'unless-stopped', '--user', '10001:10001', '--read-only',
              '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
              '--log-driver', 'local', '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2']
    if principal == 'hermes':
        common += ['--mount', 'type=bind,src=' + str(directory / 'tools/browser_worker.py') + ',dst=/opt/homecompute/browser_worker.py,readonly']
    docker('create', '--name', EGRESS, *common, '--network', EGRESS,
        '--cpus', '0.25', '--memory', '128m', '--memory-swap', '128m', '--pids-limit', '64',
        '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=1777',
        '--mount', 'type=bind,src=' + str(proxy_config) + ',dst=/etc/homecompute/browser-squid.conf,readonly',
        image, '/usr/sbin/squid', '-N', '-f', '/etc/homecompute/browser-squid.conf')
    # Every internal endpoint is fixed: dynamic peers can otherwise claim a
    # stopped worker's address first during Docker/VM restart.
    docker('network', 'connect', '--ip', f'172.30.{browser_octet}.3', '--alias', 'browser-egress', BROWSER, EGRESS)
    docker('start', EGRESS)
    if egress_only:
        receipt = {'egress': EGRESS, 'image': image, 'egress_only': True,
                   'private_networks': 'proxy denied'}
        (private / 'installation-egress.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt))
        return
    docker('create', '--name', DNS, *common, '--network', BROWSER,
        '--network-alias', 'browser-dns', '--ip', f'172.30.{browser_octet}.5',
        '--cpus', '0.25', '--memory', '64m', '--memory-swap', '64m', '--pids-limit', '64',
        '--mount', 'type=bind,src=' + str(token) + ',dst=/run/browser-token,readonly',
        '--env', 'BROWSER_TOKEN_FILE=/run/browser-token', '--env', f'BROWSER_DNS_BIND=172.30.{browser_octet}.5', image,
        '/usr/bin/python3', '/opt/homecompute/browser_worker.py', '--dns-only')
    docker('network', 'connect', EGRESS, DNS)
    docker('start', DNS)
    docker('volume', 'create', PROFILE)
    docker('create', '--name', BROWSER, '--hostname', BROWSER, *common, '--network', BROWSER,
        '--ip', f'172.30.{browser_octet}.2',
        '--cpus', '1', '--memory', '4g', '--memory-swap', '4g', '--pids-limit', '256',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=1777',
        '--mount', 'type=bind,src=' + str(token) + ',dst=/run/browser-token,readonly',
        '--mount', 'type=volume,src=' + PROFILE + ',dst=/home/browser/profile',
        '--env', 'HOME=/tmp', '--env', 'XDG_CONFIG_HOME=/tmp/config', '--env', 'XDG_CACHE_HOME=/tmp/cache',
        '--env', 'BROWSER_TOKEN_FILE=/run/browser-token',
        '--env', f'BROWSER_PUBLIC_ORIGIN=http://172.18.0.1:{listener}' if principal == 'hermes' else 'BROWSER_PUBLIC_ORIGIN=http://127.0.0.1:18800', image)
    docker('start', BROWSER)
    docker('create', '--name', INGRESS, *common, '--network', EGRESS,
        '--cpus', '0.25', '--memory', '64m', '--memory-swap', '64m', '--pids-limit', '64',
        '--mount', 'type=bind,src=' + str(directory / 'tools/tcp_relay.py') + ',dst=/opt/homecompute/tcp_relay.py,readonly',
        '--publish', f'172.18.0.1:{listener}:18800',
        '--env', f'BROWSER_UPSTREAM={BROWSER}:18800',
        image, '/usr/bin/python3', '/opt/homecompute/tcp_relay.py')
    docker('network', 'connect', '--ip', f'172.30.{browser_octet}.4', BROWSER, INGRESS)
    docker('start', INGRESS)
    hostname = json.loads(docker('inspect', BROWSER))[0]['Config']['Hostname']
    receipt = {'browser': BROWSER, 'egress': EGRESS, 'dns': DNS, 'dns_listener': f'172.30.{browser_octet}.5:18802',
               'image': image, 'hostname': hostname,
               'listener': f'172.18.0.1:{listener}', 'private_networks': 'proxy denied'}
    (private / 'installation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--image-id', required=True)
    p.add_argument('--principal', choices=['openclaw', 'hermes'], default='openclaw')
    p.add_argument('--egress-only', action='store_true', help='reconcile a retained/replaced proxy without recreating browser state')
    args = p.parse_args()
    install(args.directory, args.image_id, egress_only=args.egress_only, principal=args.principal)
