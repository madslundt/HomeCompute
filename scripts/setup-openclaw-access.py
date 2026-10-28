#!/usr/bin/env python3
"""Install the private access-request service; never print credential values."""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
# Root operator installation invokes only the fixed system binaries below.
import subprocess  # nosec B404
import tempfile

from openclaw_bitwarden import open_directory, read_private

INSTALL = Path('/opt/homecompute/openclaw-credentials')
STATE = Path('/var/lib/homecompute-access')
CONFIG = STATE / 'config'
CORE = Path('/var/lib/homecompute-openclaw/config')
UNITS = Path('/etc/systemd/system.attached')
ORIGIN = 'https://home-core.tail479ad.ts.net:18814'
PYTHON = '/run/current-system/sw/bin/python3'
FILES = ('openclaw-access.py', 'openclaw_access_grants.py', 'openclaw_bitwarden.py',
         'openclaw_access_page.py', 'openclaw_access_auth.py', 'openclaw_access_notifications.py',
         'openclaw_site_registry.py', 'openclaw_site_transactions.py', 'openclaw_site_transaction_page.py',
         'openclaw_site_owner_bridge.py', 'openclaw_site_owner_page.py',
         'setup-openclaw-access.py')
PROXY = 'homecompute-openclaw-access-proxy'
STAGE = 'preflight'


def stage(value: str):
    global STAGE
    STAGE = value


def command(args, *, check=True):
    binaries = {name: '/run/current-system/sw/bin/' + name for name in ('docker', 'iptables', 'systemctl')}
    if not args or args[0] not in binaries:
        raise ValueError('Setup executable refused')
    result = subprocess.run(  # nosec B603
        [binaries[args[0]], *args[1:]], stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env={'PATH': '/run/current-system/sw/bin', 'HOME': '/root'}, timeout=120)
    if check and result.returncode:
        raise ValueError('Private setup command failed; inspect operator state')
    return result


def atomic(path: Path, body: bytes, *, mode=0o600, uid=0, gid=0):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Managed file symlink refused')
    if path.exists() and (not path.is_file() or path.stat().st_uid != uid):
        raise ValueError('Managed file ownership refused')
    fd, name = tempfile.mkstemp(prefix='.access-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fixed_json(path: Path, value: dict):
    if path.exists():
        if json.loads(read_private(path.parent, path.name, 16384)) != value:
            raise ValueError('Retained access identity differs; operator review required')
    else:
        atomic(path, json.dumps(value, sort_keys=True).encode() + b'\n')


def boot_dependency(name: str):
    # NixOS /etc/systemd/system is immutable. Follow this repository's existing
    # Mattermost/core adapter convention in the writable attached-unit path.
    directory = UNITS / 'multi-user.target.wants'
    if directory.is_symlink():
        raise ValueError('Attached boot directory symlink refused')
    directory.mkdir(mode=0o755, exist_ok=True)
    link, target = directory / name, UNITS / name
    if link.is_symlink():
        if link.resolve() != target.resolve():
            raise ValueError('Retained boot dependency differs')
    elif link.exists():
        raise ValueError('Unexpected boot dependency refused')
    else:
        link.symlink_to('../' + name)


def service_unit(mattermost_network: str = '172.30.9.0/24') -> str:
    network = ipaddress.ip_network(mattermost_network, strict=True)
    if network.version != 4 or not network.is_private or network.prefixlen != 24:
        raise ValueError('Mattermost private application network refused')
    site_args = ' --transactions-state ' + str(STATE / 'transactions')
    if (CONFIG / 'sites.json').exists():
        site_args += ' --sites-config ' + str(CONFIG / 'sites.json')
        if Path('/var/lib/homecompute-sites/config/runtime.json').exists():
            site_args += ' --site-worker-ready'
    if (CONFIG / 'site-owner.json').exists():
        site_args += ' --site-owner-config ' + str(CONFIG / 'site-owner.json')
    return f'''[Unit]
Description=HomeCompute private account access requests and approvals
After=network-online.target homecompute-mattermost.service
Wants=network-online.target
[Service]
Type=simple
User=root
UMask=0077
WorkingDirectory={INSTALL}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH=/run/current-system/sw/bin
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
ExecStart={PYTHON} {INSTALL}/openclaw-access.py --state {STATE}/ledger --assistant-tokens-file {CONFIG}/assistant-tokens.json --owner-config {CONFIG}/owner.json --notify-config {CONFIG}/mattermost.json --notify-state {STATE}/notifications --operator-origin {ORIGIN} --lan-operator{site_args}
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_UNIX
CapabilityBoundingSet=
AmbientCapabilities=
IPAddressDeny=any
IPAddressAllow=localhost
IPAddressAllow={network}
ReadWritePaths={STATE}/ledger {STATE}/notifications {STATE}/transactions
InaccessiblePaths=/var/lib/homecompute-credential-secrets /srv/state/mattermost/private /var/lib/homecompute-openclaw
MemoryMax=128M
TasksMax=64
LimitCORE=0
StandardOutput=null
StandardError=journal
SyslogIdentifier=homecompute-openclaw-access
[Install]
WantedBy=multi-user.target
'''


def proxy_unit() -> str:
    return f'''[Unit]
Description=Private tailnet HTTPS for OpenClaw account approvals
After=docker.service tailscaled.service homecompute-openclaw-access.service
Requires=docker.service homecompute-openclaw-access.service
Wants=tailscaled.service
[Service]
Type=oneshot
RemainAfterExit=yes
Environment=PATH=/run/current-system/sw/bin
ExecStartPre={PYTHON} {INSTALL}/setup-openclaw-access.py firewall
ExecStart=/run/current-system/sw/bin/docker compose -f {INSTALL}/access-compose.yaml up -d --no-build --force-recreate
ExecStop=/run/current-system/sw/bin/docker compose -f {INSTALL}/access-compose.yaml down
TimeoutStartSec=90
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
'''


def caddyfile(address: str) -> str:
    if ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Tailnet address refused')
    return f'''{{
 admin unix//tmp/caddy-admin.sock
 auto_https off
 log default {{
  output discard
 }}
}}
https://home-core.tail479ad.ts.net:18814 {{
 bind {address}
 tls /certificates/current/cert.pem /certificates/current/key.pem
 @review {{
  method GET POST
  path / /review/* /decision/* /transactions/review/* /transactions/decision/* /browser/*
 }}
 handle @review {{
  request_body {{
   max_size 4KB
  }}
  reverse_proxy 127.0.0.1:18813
 }}
 handle {{
  respond "Not found" 404
 }}
}}
https://mattermost.home.arpa:18814 {{
 bind 192.168.30.122
 tls /lan-certificates/mattermost.home.arpa.crt /lan-certificates/mattermost.home.arpa.key
 @review {{
  method GET POST
  path / /review/* /decision/* /transactions/review/* /transactions/decision/* /browser/*
 }}
 handle @review {{
  request_body {{
   max_size 4KB
  }}
  reverse_proxy 127.0.0.1:18815
 }}
 handle {{
  respond "Not found" 404
 }}
}}
'''


def compose(image: str) -> str:
    if not re.fullmatch(r'caddy:[0-9.]+-alpine@sha256:[0-9a-f]{64}', image):
        raise ValueError('Pinned official Caddy image required')
    return f'''name: homecompute-openclaw-access
services:
  proxy:
    container_name: {PROXY}
    image: {image}
    restart: unless-stopped
    network_mode: host
    read_only: true
    cap_drop: [ALL]
    cap_add: [NET_BIND_SERVICE]
    security_opt: [no-new-privileges:true]
    environment:
      XDG_CONFIG_HOME: /tmp/caddy-config
      XDG_DATA_HOME: /tmp/caddy-data
    volumes:
      - {INSTALL}/access.Caddyfile:/etc/caddy/Caddyfile:ro
      - /srv/state/control-plane/caddy-data/tailscale-tls:/certificates:ro
      - /srv/state/control-plane/caddy-data/caddy/certificates/local/mattermost.home.arpa:/lan-certificates:ro
    tmpfs: ["/tmp:rw,nosuid,nodev,noexec,size=32m"]
    command: [caddy, run, --config, /etc/caddy/Caddyfile, --adapter, caddyfile]
    cpus: 0.25
    mem_limit: 96m
    pids_limit: 64
    logging: {{driver: none}}
'''


def settings() -> dict:
    values = {}
    for line in read_private(Path('/srv/state/mattermost'), 'runtime.env', 8192).decode().splitlines():
        if line and not line.startswith('#'):
            key, _, value = line.partition('=')
            values[key] = value
    if values.get('MATTERMOST_FQDN') != 'home-core.tail479ad.ts.net':
        raise ValueError('Mattermost tailnet identity differs')
    return values


def firewall():
    address = settings()['MATTERMOST_TAILSCALE_ADDRESS']
    caddyfile(address)
    suffix = ['-d', address + '/32', '-p', 'tcp', '--dport', '18814', '-m', 'comment',
              '--comment', 'homecompute-openclaw-access']
    allow = ['-i', 'tailscale0', '-s', '100.64.0.0/10', *suffix, '-j', 'ACCEPT']
    local = ['-i', 'lo', *suffix, '-j', 'ACCEPT']
    deny = [*suffix, '-j', 'DROP']
    # Only our exact dedicated-port rules are reconciled.
    for rule in (allow, local, deny):
        while command(['iptables', '-w', '-C', 'INPUT', *rule], check=False).returncode == 0:
            command(['iptables', '-w', '-D', 'INPUT', *rule])
    command(['iptables', '-w', '-I', 'INPUT', '1', *deny])
    command(['iptables', '-w', '-I', 'INPUT', '1', *local])
    command(['iptables', '-w', '-I', 'INPUT', '1', *allow])
    lan_suffix = ['-d', '192.168.30.122/32', '-p', 'tcp', '--dport', '18814', '-m', 'comment',
                  '--comment', 'homecompute-openclaw-access-lan']
    # Reuse home.arpa split DNS and the approved LAN subnet route. The same
    # hostname/cookie/origin works at home and over Tailscale; other VLANs and
    # non-Tailscale sources remain denied on this dedicated approval port.
    lan_rules = [(['-i', 'enp44s0', '-s', source, *lan_suffix, '-j', 'ACCEPT'])
                 for source in ('192.168.10.0/24', '192.168.30.30/32')]
    lan_rules.append(['-i', 'tailscale0', '-s', '100.64.0.0/10', *lan_suffix, '-j', 'ACCEPT'])
    lan_local, lan_deny = ['-i', 'lo', *lan_suffix, '-j', 'ACCEPT'], [*lan_suffix, '-j', 'DROP']
    for rule in (*lan_rules, lan_local, lan_deny):
        while command(['iptables', '-w', '-C', 'INPUT', *rule], check=False).returncode == 0:
            command(['iptables', '-w', '-D', 'INPUT', *rule])
    for rule in (lan_deny, lan_local, *lan_rules):
        command(['iptables', '-w', '-I', 'INPUT', '1', *rule])


def configure(source: Path) -> dict:
    stage('root_preflight')
    if os.geteuid() != 0:
        raise ValueError('Trusted root installer required')
    for directory in (INSTALL, STATE, CONFIG, STATE / 'ledger', STATE / 'notifications', STATE / 'transactions'):
        os.close(open_directory(directory, create=True))
    for name in FILES:
        atomic(INSTALL / name, (source / 'scripts' / name).read_bytes())
    stage('mattermost_identity')
    spec = importlib.util.spec_from_file_location('mattermost_bootstrap', '/opt/homecompute/mattermost/bootstrap-mattermost.py')
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    bootstrap_state = Path('/srv/state/mattermost/private')
    credentials = json.loads(read_private(bootstrap_state, 'credentials.json', 16384))
    delivery = json.loads(read_private(bootstrap_state, 'n8n-integration.json', 32768))
    owner_id, bot_id = credentials['operator_id'], delivery['bot_id']
    api = bootstrap.API(delivery['bot_token'])
    bot, _ = api.request('GET', '/users/me')
    if (bot.get('id') != bot_id or bot.get('is_bot') is not True or bot.get('delete_at', 0) != 0
            or set(bot.get('roles', '').split()) != {'system_user'} or owner_id == bot_id):
        raise ValueError('Retained notifier identity refused')
    existing = CONFIG / 'mattermost.json'
    if existing.exists():
        retained = json.loads(read_private(CONFIG, existing.name, 4096))
        channel_id = retained['channel_id']
    else:
        channel, _ = api.request('POST', '/channels/direct', [owner_id, bot_id])
        channel_id = channel['id']
    fixed_json(CONFIG / 'owner.json', {'schema_version': 1, 'owner_id': owner_id})
    fixed_json(existing, {'schema_version': 1, 'bot_id': bot_id, 'owner_id': owner_id, 'channel_id': channel_id,
                          'bot_token': delivery['bot_token'], 'review_origin': ORIGIN})
    stage('owner_dm_validation')
    from openclaw_access_notifications import MattermostNotifier
    MattermostNotifier(existing, STATE / 'notifications')._target()
    stage('request_bridge_configuration')
    token_path = CONFIG / 'assistant-tokens.json'
    if token_path.exists():
        tokens = json.loads(read_private(CONFIG, token_path.name, 4096))
        if set(tokens) != {'openclaw'} or not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', tokens['openclaw']):
            raise ValueError('Retained request identity refused')
    else:
        tokens = {'openclaw': secrets.token_urlsafe(48)}
        fixed_json(token_path, tokens)
    adapter = pwd.getpwnam('homecompute-openclaw')
    atomic(CORE / 'access-request-token', (tokens['openclaw'] + '\n').encode(), uid=adapter.pw_uid, gid=adapter.pw_gid)
    atomic(CORE / 'access-requests.json', json.dumps({'schema_version': 1, 'principal': 'openclaw',
           'endpoint': 'http://127.0.0.1:18812', 'token_file': str(CORE / 'access-request-token')}).encode(),
           uid=adapter.pw_uid, gid=adapter.pw_gid)
    runtime = settings()
    # cgroup IP filtering observes the destination after Docker's loopback DNAT.
    # Permit only Mattermost's application /24, never its database network.
    networks = json.loads(command(['docker', 'inspect', 'homecompute-mattermost-mattermost-1']).stdout)[0]['NetworkSettings']['Networks']
    application = [name for name in networks if name.endswith('_application')]
    if len(application) != 1:
        raise ValueError('Mattermost application network identity refused')
    network_data = json.loads(command(['docker', 'network', 'inspect', application[0]]).stdout)[0]
    subnets = [value['Subnet'] for value in network_data['IPAM']['Config'] if ':' not in value['Subnet']]
    if len(subnets) != 1:
        raise ValueError('Mattermost application subnet refused')
    unit = service_unit(subnets[0])
    stage('proxy_configuration')
    image = runtime['CADDY_IMAGE']
    # The pinned existing image must be present. Installation never pulls an image.
    command(['docker', 'image', 'inspect', image])
    atomic(INSTALL / 'access.Caddyfile', caddyfile(runtime['MATTERMOST_TAILSCALE_ADDRESS']).encode())
    atomic(INSTALL / 'access-compose.yaml', compose(image).encode())
    command(['docker', 'compose', '-f', str(INSTALL / 'access-compose.yaml'), 'config', '--quiet'])
    stage('proxy_validation')
    # Validate TLS/config against real certificate mounts before any listener starts.
    command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
             '-v', str(INSTALL / 'access.Caddyfile') + ':/etc/caddy/Caddyfile:ro',
             '-v', '/srv/state/control-plane/caddy-data/tailscale-tls:/certificates:ro',
             '-v', '/srv/state/control-plane/caddy-data/caddy/certificates/local/mattermost.home.arpa:/lan-certificates:ro', image,
             'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
    stage('unit_installation')
    atomic(UNITS / 'homecompute-openclaw-access.service', unit.encode(), mode=0o644)
    atomic(UNITS / 'homecompute-openclaw-access-proxy.service', proxy_unit().encode(), mode=0o644)
    reload_unit = f'''[Unit]
Description=Reload private approval TLS after certificate renewal
After=homecompute-openclaw-access-proxy.service
[Service]
Type=oneshot
ExecStart=/run/current-system/sw/bin/docker exec {PROXY} caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address unix//tmp/caddy-admin.sock
StandardOutput=null
StandardError=null
'''
    path_unit = '''[Unit]
Description=Watch private approval certificate renewal
[Path]
PathChanged=/srv/state/control-plane/caddy-data/tailscale-tls/current
PathChanged=/srv/state/control-plane/caddy-data/caddy/certificates/local/mattermost.home.arpa
Unit=homecompute-openclaw-access-certificate.service
[Install]
WantedBy=multi-user.target
'''
    atomic(UNITS / 'homecompute-openclaw-access-certificate.service', reload_unit.encode(), mode=0o644)
    atomic(UNITS / 'homecompute-openclaw-access-certificate.path', path_unit.encode(), mode=0o644)
    for name in ('homecompute-openclaw-access.service', 'homecompute-openclaw-access-proxy.service',
                 'homecompute-openclaw-access-certificate.path'):
        boot_dependency(name)
    command(['systemctl', 'daemon-reload'])
    for name in ('homecompute-openclaw-access.service', 'homecompute-openclaw-access-proxy.service',
                 'homecompute-openclaw-access-certificate.path'):
        stage({'homecompute-openclaw-access.service': 'backend_start',
               'homecompute-openclaw-access-proxy.service': 'proxy_start',
               'homecompute-openclaw-access-certificate.path': 'certificate_watch_start'}[name])
        command(['systemctl', 'restart', name])
    return {'installed': True, 'review_origin': ORIGIN, 'owner_only': True,
            'mattermost_notification_configured': True, 'vault_values_accessible_to_service': False,
            'account_execution_available': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'firewall'])
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'firewall':
            firewall()
        else:
            if args.source_root is None:
                raise ValueError('Source root required')
            print(json.dumps(configure(args.source_root)))
    except Exception as error:
        # Phase and exception class are diagnostic metadata, never secret values.
        print(json.dumps({'setup_failed': True, 'stage': STAGE, 'error_type': type(error).__name__}))
        raise SystemExit('Private access setup failed; no credentials are displayed.')
