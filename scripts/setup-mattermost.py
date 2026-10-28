#!/usr/bin/env python3
"""Private, dedicated Mattermost lifecycle. Never reconciles other Compose projects."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/srv/state/mattermost')
INSTALL = Path('/opt/homecompute/mattermost')
UNIT = 'homecompute-mattermost'
PRIMARY_SITE = 'https://mattermost.home.arpa'
PYTHON = '/run/current-system/sw/bin/python3'
HELPERS = ('setup-mattermost.py', 'bootstrap-mattermost.py', 'mattermost_agent_accounts.py',
           'setup-mattermost-n8n.py', 'verify-mattermost-team.py', 'sync-mattermost-atlas.py',
           'organize-mattermost.py')
DEFAULTS = {
    'MATTERMOST_IMAGE': 'mattermost/mattermost-team-edition:11.7.14@sha256:6c15d74ded3cbec1e4fa4e27587a3341315405b58956e344d9d329f5c6367de5',
    'POSTGRES_IMAGE': 'postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea',
    'CADDY_IMAGE': 'caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f',
    'MATTERMOST_CERTIFICATE_ROOT': '/srv/state/control-plane/caddy-data/tailscale-tls',
}
KEYS = set(DEFAULTS) | {'MATTERMOST_STATE_ROOT', 'MATTERMOST_SITE_URL',
                         'MATTERMOST_FQDN', 'MATTERMOST_TAILSCALE_ADDRESS'}


class SetupError(Exception):
    """Diagnostics never include private configuration or process output."""


def no_links(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise SetupError('absolute non-symlink managed path required')


def private_dir(path: Path, uid: int = 0, gid: int = 0) -> None:
    no_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (uid, gid):
        # Only adopt a newly created, empty directory; never change existing data.
        if any(path.iterdir()) or (info.st_uid, info.st_gid) != (0, 0):
            raise SetupError('managed directory ownership differs')
        os.chown(path, uid, gid)
    os.chmod(path, 0o700)


def atomic(path: Path, body: str, uid: int = 0, gid: int = 0, mode: int = 0o600) -> None:
    no_links(path)
    fd, name = tempfile.mkstemp(prefix='.mattermost-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'w') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def private_read(path: Path, uid: int = 0, mode: int = 0o600) -> str:
    no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        raise SetupError('private file ownership or mode differs')
    return path.read_text()


def validate_settings(value: dict[str, str], state: Path) -> None:
    if set(value) != KEYS or value['MATTERMOST_STATE_ROOT'] != str(state):
        raise SetupError('runtime settings do not match dedicated state root')
    fqdn = value['MATTERMOST_FQDN']
    if (not re.fullmatch(r'[a-z0-9][a-z0-9.-]+\.ts\.net', fqdn)
            or value['MATTERMOST_SITE_URL'] not in {PRIMARY_SITE, f'https://{fqdn}:9443'}):
        raise SetupError('reviewed primary URL and independent Tailscale transport required')
    address = ipaddress.ip_address(value['MATTERMOST_TAILSCALE_ADDRESS'])
    if address not in ipaddress.ip_network('100.64.0.0/10'):
        raise SetupError('exact Tailscale IPv4 address required')
    no_links(Path(value['MATTERMOST_CERTIFICATE_ROOT']))
    for key, prefix in [('MATTERMOST_IMAGE', 'mattermost/mattermost-team-edition:'),
                        ('POSTGRES_IMAGE', 'postgres:16.'), ('CADDY_IMAGE', 'caddy:')]:
        if not value[key].startswith(prefix) or not re.search(r'@sha256:[0-9a-f]{64}$', value[key]):
            raise SetupError('reviewed immutable official service images required')
    if any('\n' in v or '\r' in v or not v or any(c.isspace() for c in v) for v in value.values()):
        raise SetupError('invalid runtime setting')


def settings(state: Path) -> dict[str, str]:
    value: dict[str, str] = {}
    for line in private_read(state / 'runtime.env').splitlines():
        if line and not line.startswith('#'):
            key, sep, item = line.partition('=')
            if not sep or key in value:
                raise SetupError('invalid runtime environment')
            value[key] = item
    validate_settings(value, state)
    return value


def initialize(state: Path, site_url: str | None, address: str | None,
               transport_fqdn: str | None = None) -> dict[str, str]:
    private_dir(state)
    if (state / 'runtime.env').exists():
        value = settings(state)
        if (site_url and site_url.rstrip('/') != value['MATTERMOST_SITE_URL']
                or address and address != value['MATTERMOST_TAILSCALE_ADDRESS']
                or transport_fqdn and transport_fqdn != value['MATTERMOST_FQDN']):
            raise SetupError('existing URL or address differs; review retained configuration')
    else:
        if not site_url or not address:
            raise SetupError('initialization requires --site-url and --tailscale-address')
        value = {**DEFAULTS, 'MATTERMOST_STATE_ROOT': str(state),
                 'MATTERMOST_SITE_URL': site_url.rstrip('/'),
                 'MATTERMOST_FQDN': transport_fqdn or urlsplit(site_url).hostname or '',
                 'MATTERMOST_TAILSCALE_ADDRESS': address}
        validate_settings(value, state)
    for name in ['config', 'data', 'logs', 'plugins', 'client-plugins']:
        private_dir(state / name, 2000, 2000)
    private_dir(state / 'secrets')
    # The official alpine PostgreSQL image uses postgres UID/GID 70.
    private_dir(state / 'postgres', 70, 70)
    password_path = state / 'secrets/database-password'
    config_path = state / 'config/config.json'
    if password_path.exists():
        password = private_read(password_path, mode=0o440).strip()
        if not re.fullmatch(r'[a-zA-Z0-9_-]{48,128}', password):
            raise SetupError('database secret format differs')
    else:
        if config_path.exists() or any((state / 'postgres').iterdir()):
            raise SetupError('retained application/database has no matching secret')
        password = secrets.token_urlsafe(48)
        atomic(password_path, password + '\n', gid=70, mode=0o440)
    datasource = f'postgres://mattermost:{password}@postgres:5432/mattermost?sslmode=disable&connect_timeout=10'
    if config_path.exists():
        config = json.loads(private_read(config_path, uid=2000))
        if config.get('SqlSettings', {}).get('DataSource') != datasource:
            raise SetupError('retained application database credential differs')
    else:
        config = {
            'ServiceSettings': {
                'SiteURL': value['MATTERMOST_SITE_URL'], 'ListenAddress': ':8065',
                'AllowCorsFrom': f"{PRIMARY_SITE} https://{value['MATTERMOST_FQDN']}:9443",
                'EnableLocalMode': True, 'LocalModeSocketLocation': '/var/tmp/mattermost_local.socket',
                'EnableUserAccessTokens': True, 'EnableBotAccountCreation': True,
                'EnableIncomingWebhooks': True, 'EnableOutgoingWebhooks': False,
            },
            'SqlSettings': {'DriverName': 'postgres', 'DataSource': datasource},
            'TeamSettings': {'SiteName': 'HomeCompute', 'EnableOpenServer': False, 'EnableUserCreation': False},
            'EmailSettings': {'RequireEmailVerification': False, 'SendEmailNotifications': False,
                              'SendPushNotifications': True, 'PushNotificationContents': 'generic',
                              'PushNotificationServer': 'https://push-test.mattermost.com'},
            'LogSettings': {'EnableConsole': True, 'ConsoleLevel': 'ERROR', 'EnableFile': False,
                            'EnableDiagnostics': False, 'EnableSentry': False},
            'PrivacySettings': {'ShowEmailAddress': False},
        }
        atomic(config_path, json.dumps(config, indent=2) + '\n', 2000, 2000)
    if not (state / 'runtime.env').exists():
        atomic(state / 'runtime.env', ''.join(f'{key}={value[key]}\n' for key in sorted(value)))
    return value


def set_site_url(state: Path, site_url: str) -> dict[str, str]:
    """Change the canonical URL without changing the TLS transport or credentials."""
    value = settings(state)
    target = {**value, 'MATTERMOST_SITE_URL': site_url.rstrip('/')}
    validate_settings(target, state)
    config_path = state / 'config/config.json'
    config = json.loads(private_read(config_path, uid=2000))
    origins = [PRIMARY_SITE, f"https://{value['MATTERMOST_FQDN']}:9443"]
    service = config['ServiceSettings']
    if (service.get('SiteURL') not in origins
            or not set(service.get('AllowCorsFrom', '').split()).issubset(origins)):
        raise SetupError('retained URLs require explicit reconciliation')
    exports = {}
    private_dir(state / 'private')
    for name in ['credentials.json', 'operator-login.json']:
        path = state / 'private' / name
        if path.exists():
            content = json.loads(private_read(path))
            if content.get('site_url') not in origins:
                raise SetupError('retained login URL requires explicit reconciliation')
            exports[path] = content
    desired_origins = ' '.join(origins)
    if (value == target and service.get('SiteURL') == site_url.rstrip('/')
            and service.get('AllowCorsFrom') == desired_origins
            and all(content['site_url'] == target['MATTERMOST_SITE_URL'] for content in exports.values())):
        return target
    backup = state / 'private' / f'site-url-before-{time.time_ns()}'
    private_dir(backup)
    for path in [config_path, state / 'runtime.env', *exports]:
        atomic(backup / path.name, path.read_text())
    command = ['docker', 'exec', '--user', '2000:2000', 'homecompute-mattermost-mattermost-1',
               '/mattermost/bin/mmctl', '--local', '--quiet', 'config', 'set']
    for name, desired in [('AllowCorsFrom', desired_origins), ('SiteURL', target['MATTERMOST_SITE_URL'])]:
        if service.get(name) != desired:
            run(command + ['ServiceSettings.' + name, desired], diagnostics=state)
    after = json.loads(private_read(config_path, uid=2000))
    expected = json.loads(json.dumps(config))
    expected['ServiceSettings'].update(SiteURL=target['MATTERMOST_SITE_URL'], AllowCorsFrom=desired_origins)
    if after != expected:
        raise SetupError('URL change differed from the protected configuration; inspect backup')
    atomic(state / 'runtime.env', ''.join(f'{key}={target[key]}\n' for key in sorted(target)))
    for path, content in exports.items():
        atomic(path, json.dumps({**content, 'site_url': target['MATTERMOST_SITE_URL']}, indent=2) + '\n')
    atomic(backup / 'receipt.json', json.dumps({'before': value['MATTERMOST_SITE_URL'],
           'after': target['MATTERMOST_SITE_URL'], 'transport_fqdn': value['MATTERMOST_FQDN'],
           'protected_configuration_unchanged': True}, indent=2) + '\n')
    return target


def record_diagnostics(state: Path, argv: list[str], result: subprocess.CompletedProcess[str] | None,
                       error: Exception | None = None) -> None:
    def bounded(value: Any) -> str:
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        return str(value or '')[-262144:]
    no_links(state)
    value = {'recorded_at': time.time(), 'command': argv,
             'returncode': result.returncode if result else None,
             'stdout': bounded(result.stdout if result else getattr(error, 'stdout', '')),
             'stderr': bounded(result.stderr if result else getattr(error, 'stderr', '')),
             'exception': type(error).__name__ if error else None}
    atomic(state / 'last-operation-failure.json', json.dumps(value, indent=2) + '\n')


def run(argv: list[str], *, timeout: int = 300, check: bool = True,
        diagnostics: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        if diagnostics:
            record_diagnostics(diagnostics, argv, None, error)
        raise SetupError('service operation failed; inspect the root-only service diagnostics') from None
    if check and result.returncode:
        if diagnostics:
            record_diagnostics(diagnostics, argv, result)
        raise SetupError('service operation failed; inspect the root-only service diagnostics')
    return result


def compose(state: Path, install: Path) -> list[str]:
    settings(state)
    no_links(install)
    return ['docker', 'compose', '--env-file', str(state / 'runtime.env'),
            '-f', str(install / 'compose.yaml')]


def service_unit(state: Path, install: Path) -> str:
    prefix = f'{PYTHON} {install}/setup-mattermost.py --state-root {state} --install-root {install}'
    return f'''[Unit]
Description=HomeCompute Mattermost Team Edition
After=docker.service network-online.target tailscaled.service homecompute-gateway-certificate.service
Wants=docker.service network-online.target tailscaled.service
StartLimitIntervalSec=0
[Service]
Type=oneshot
RemainAfterExit=yes
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStartPre={prefix} firewall
ExecStart={prefix} start
ExecReload={prefix} start
ExecStop={prefix} stop
Restart=on-failure
RestartSec=20s
TimeoutStartSec=600
TimeoutStopSec=90
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
'''


def certificate_unit(state: Path, install: Path) -> str:
    return f'''[Unit]
Description=Reload Mattermost proxy after public certificate renewal
After={UNIT}.service homecompute-gateway-certificate.service
[Service]
Type=oneshot
Environment=PATH=/run/current-system/sw/bin
ExecStart={PYTHON} {install}/setup-mattermost.py --state-root {state} --install-root {install} reload-proxy
UMask=0077
'''


def atlas_unit(state: Path, install: Path) -> str:
    return f'''[Unit]
Description=Join Atlas to active owner HomeCompute channels
After={UNIT}.service
ConditionPathExists={state}/private/agent-account-state.json
ConditionPathExists={state}/private/n8n-channels.json
[Service]
Type=oneshot
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={PYTHON} {install}/sync-mattermost-atlas.py --private-root {state}/private
TimeoutStartSec=45s
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths={state}/private
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
'''


def atlas_timer() -> str:
    return f'''[Unit]
Description=Automatically join Atlas to new owner channels every 30 seconds
[Timer]
OnBootSec=30s
OnUnitInactiveSec=30s
AccuracySec=1s
Unit={UNIT}-atlas-membership.service
[Install]
WantedBy=timers.target
'''


def boot_dependency(unitdir: Path, target: str, name: str) -> None:
    directory = unitdir / (target + '.wants')
    no_links(directory)
    directory.mkdir(mode=0o755, exist_ok=True)
    link = directory / name
    if link.is_symlink():
        if link.resolve(strict=False) != unitdir / name:
            raise SetupError('existing boot dependency differs')
    elif link.exists():
        raise SetupError('boot dependency is not a managed symlink')
    else:
        link.symlink_to('../' + name)


def install_files(state: Path, install: Path, source: Path = ROOT,
                  unitdir: Path = Path('/etc/systemd/system.attached')) -> None:
    settings(state)
    private_dir(install)
    for name in ['compose.yaml', 'Caddyfile']:
        atomic(install / name, (source / 'deploy/mattermost' / name).read_text(), mode=0o644)
    for name in HELPERS:
        atomic(install / name, (source / 'scripts' / name).read_text(), mode=0o700)
    no_links(unitdir)
    unitdir.mkdir(exist_ok=True)
    atomic(unitdir / f'{UNIT}.service', service_unit(state, install), mode=0o644)
    atomic(unitdir / f'{UNIT}-certificate.service', certificate_unit(state, install), mode=0o644)
    atomic(unitdir / f'{UNIT}-atlas-membership.service', atlas_unit(state, install), mode=0o644)
    atomic(unitdir / f'{UNIT}-atlas-membership.timer', atlas_timer(), mode=0o644)
    atomic(unitdir / f'{UNIT}-certificate.timer', f'''[Unit]
Description=Refresh Mattermost proxy certificate daily
[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
Persistent=yes
Unit={UNIT}-certificate.service
[Install]
WantedBy=timers.target
''', mode=0o644)
    # NixOS /etc/systemd/system points into the immutable store. Attached units
    # are already discoverable; persist wants in their writable search path.
    boot_dependency(unitdir, 'multi-user.target', f'{UNIT}.service')
    boot_dependency(unitdir, 'timers.target', f'{UNIT}-certificate.timer')
    boot_dependency(unitdir, 'timers.target', f'{UNIT}-atlas-membership.timer')
    run(['systemctl', 'daemon-reload'], diagnostics=state)


def firewall(value: dict[str, str]) -> None:
    for interface, source in [('tailscale0', []), ('br-hc-agents', ['-s', '10.77.20.2/32']),
                              ('br-hc-ctrl', ['-s', '172.28.200.2/32'])]:
        rule = ['INPUT', '-i', interface, *source, '-d', value['MATTERMOST_TAILSCALE_ADDRESS'] + '/32',
                '-p', 'tcp', '--dport', '9443', '-m', 'conntrack', '--ctstate', 'NEW,ESTABLISHED',
                '-m', 'comment', '--comment', 'homecompute-mattermost-private', '-j', 'ACCEPT']
        state = Path(value['MATTERMOST_STATE_ROOT'])
        if run(['iptables', '-w', '-C', *rule], check=False, diagnostics=state).returncode:
            run(['iptables', '-w', '-I', 'INPUT', '1', *rule[1:]], diagnostics=state)


@contextmanager
def lifecycle_lock(state: Path) -> Iterator[None]:
    private_dir(state)
    path = state / '.lifecycle.lock'
    no_links(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, default=STATE)
    parser.add_argument('--install-root', type=Path, default=INSTALL)
    parser.add_argument('action', choices=['init', 'install', 'start', 'stop', 'status', 'firewall', 'reload-proxy', 'set-site-url'])
    parser.add_argument('--site-url')
    parser.add_argument('--tailscale-address')
    parser.add_argument('--transport-fqdn')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise SetupError('run the dedicated lifecycle as root')
        if any(not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(p)) for p in [args.state_root, args.install_root]):
            raise SetupError('managed paths must be absolute without whitespace')
        with lifecycle_lock(args.state_root):
            if args.action == 'init':
                value = initialize(args.state_root, args.site_url, args.tailscale_address, args.transport_fqdn)
                print(json.dumps({'initialized': True, 'site_url': value['MATTERMOST_SITE_URL']}))
            elif args.action == 'set-site-url':
                if not args.site_url:
                    raise SetupError('URL reconciliation requires --site-url')
                value = set_site_url(args.state_root, args.site_url)
                print(json.dumps({'site_url': value['MATTERMOST_SITE_URL'], 'transport_unchanged': True}))
            elif args.action == 'install':
                install_files(args.state_root, args.install_root)
                print(json.dumps({'installed': True, 'started': False}))
            elif args.action == 'firewall':
                firewall(settings(args.state_root))
            elif args.action == 'status':
                request = urllib.request.Request('http://127.0.0.1:18065/api/v4/system/ping')
                with urllib.request.urlopen(request, timeout=10) as response:
                    value = json.load(response)
                print(json.dumps({'healthy': value.get('status') == 'OK',
                                  'site_url': settings(args.state_root)['MATTERMOST_SITE_URL']}))
            else:
                command = compose(args.state_root, args.install_root)
                if args.action == 'start':
                    run(command + ['config', '--quiet'], diagnostics=args.state_root)
                    run(command + ['up', '-d', '--wait', '--wait-timeout', '360'], timeout=540,
                        diagnostics=args.state_root)
                    run(['systemctl', 'start', f'{UNIT}-atlas-membership.timer'], diagnostics=args.state_root)
                elif args.action == 'stop':
                    run(command + ['stop'], timeout=90, diagnostics=args.state_root)
                elif args.action == 'reload-proxy':
                    run(command + ['exec', '-T', 'proxy', 'caddy', 'reload', '--config',
                                   '/etc/caddy/Caddyfile', '--adapter', 'caddyfile',
                                   '--address', 'unix//tmp/caddy-admin.sock', '--force'], diagnostics=args.state_root)
                print(json.dumps({'operation': args.action, 'complete': True}))
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError):
        print('Mattermost operation refused or failed; retain state and inspect root-only diagnostics.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
