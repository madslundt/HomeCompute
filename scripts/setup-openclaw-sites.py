#!/usr/bin/env python3
"""Install isolated registered-site operations, never provision or display secrets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess  # nosec B404
import time

from openclaw_bitwarden import BitwardenLogin, open_directory, qualified_bws, read_private
from openclaw_site_executor import ContainerRunner, GrantClient
from openclaw_site_registry import DEFAULT_HOTELS, SiteRegistry

INSTALL = Path('/opt/homecompute/openclaw-credentials')
STATE = Path('/var/lib/homecompute-sites')
CONFIG = STATE / 'config'
ACCESS = Path('/var/lib/homecompute-access/config')
CORE = Path('/var/lib/homecompute-openclaw/config')
UNITS = Path('/etc/systemd/system.attached')
BIN = '/run/current-system/sw/bin/'
NETWORKS = [('homecompute-site-browser', '172.30.192.0/24', True),
            ('homecompute-site-egress', '172.30.193.0/24', False)]
PROXY = 'homecompute-site-egress'
FILES = ('openclaw-site-service.py', 'openclaw_site_executor.py', 'openclaw_site_egress.py',
         'openclaw_site_worker.py', 'openclaw_site_drivers.py', 'openclaw_site_registry.py', 'openclaw_site_network_health.py',
         'openclaw_site_session_worker.py', 'openclaw_site_sessions.py', 'openclaw_site_view_transport.py',
         'openclaw_bitwarden.py', 'provision-openclaw-site.py', 'setup-openclaw-sites.py')
TMPFS = '/tmp:rw,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=1777'  # nosec B108
PHASE = 'preflight'


def phase(value: str):
    global PHASE
    PHASE = value


def profile_digest(profile: dict) -> str:
    value = dict(profile)
    value.pop('qualified', None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def native_token_hashes(uid: int) -> list[str]:
    path = CORE / 'native-mcp-tokens.json'
    if not path.exists() and not path.is_symlink():
        return []
    parent = os.open(CORE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (info.st_uid != uid or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1 or info.st_size > 4096):
                raise ValueError('Retained native transport identity refused')
            identities = json.loads(os.read(descriptor, 4097))
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)
    if (not isinstance(identities, dict) or not identities or len(identities) > 4
            or any(not isinstance(key, str) or not 32 <= len(key) <= 512 for key in identities.values())):
        raise ValueError('Retained native transport identity refused')
    return [hashlib.sha256(key.encode()).hexdigest() for key in identities.values()]


def _helper():
    spec = importlib.util.spec_from_file_location('retained_site_setup', INSTALL / 'setup-openclaw-access.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def invalidate_grants(directory: Path):
    """A changed browser implementation/profile must never inherit old grants."""
    if not (directory / 'access.sqlite').exists():
        return
    from openclaw_access_grants import AccessGrants
    ledger = AccessGrants(directory)
    with ledger.connect() as db:
        rows = db.execute("SELECT id,status FROM requests WHERE status IN ('active','pending')").fetchall()
        for row in rows:
            state = 'revoked' if row['status'] == 'active' else 'expired'
            db.execute('UPDATE requests SET status=? WHERE id=?', (state, row['id']))
            db.execute('DELETE FROM challenges WHERE request_id=?', (row['id'],))
            db.execute('INSERT INTO events(request_id,action,at) VALUES(?,?,?)',
                       (row['id'], 'configuration_changed', int(time.time())))


def register(profile_file: Path) -> dict:
    candidates = SiteRegistry.from_file(profile_file)
    if len(candidates.accounts()) != 1:
        raise ValueError('Exactly one reviewed new website required')
    account = candidates.accounts()[0]
    retained = SiteRegistry.from_file(ACCESS / 'sites.json')
    if account in retained.accounts():
        raise ValueError('Existing website changes require explicit operator review')
    profile = candidates.profile(account)
    profile['qualified'] = False
    profiles = [retained.profile(name) for name in retained.accounts()] + [profile]
    invalidate_grants(ACCESS.parent / 'ledger')
    _helper().atomic(ACCESS / 'sites.json', json.dumps({'schema_version': 1, 'sites': profiles}).encode())
    return {'account_registered': account, 'qualified': False, 'profile_sha256': profile_digest(profile),
            'network_reconfigure_required': True, 'credential_values_saved': False}


def qualify(account: str, request_id: str, operation: str, parameters: dict, expected_digest: str) -> dict:
    """Operator-only live qualification; no model can enable an unreviewed site."""
    registry = SiteRegistry.from_file(ACCESS / 'sites.json')
    profile = registry.profile(account)
    if profile_digest(profile) != expected_digest:
        raise ValueError('Reviewed website profile changed')
    arguments = registry.validate_parameters(account, operation, parameters)
    runtime = json.loads(read_private(CONFIG, 'runtime.json', 8192))
    tokens = json.loads(read_private(ACCESS, 'assistant-tokens.json', 4096))
    client = GrantClient(tokens)
    initial = client.status('openclaw', request_id)
    scope = registry.operation_scope(account, operation)
    if (initial['account'] != account or initial['status'] != 'active' or initial['scope'] != scope
            or not initial['approved_at'] <= time.time() < initial['expires_at']):
        raise ValueError('Owner-approved qualification grant required')
    deadline = time.monotonic() + min(45, initial['expires_at'] - time.time())
    def guard():
        current = client.status('openclaw', request_id)
        return (time.monotonic() < deadline and current['status'] == 'active'
                and all(current.get(key) == initial.get(key) for key in
                        ('request_id', 'actor', 'account', 'scope', 'request_digest', 'approved_at', 'expires_at'))
                and profile_digest(SiteRegistry.from_file(ACCESS / 'sites.json').profile(account)) == expected_digest)
    if not guard():
        raise ValueError('Qualification grant unavailable')
    login = BitwardenLogin(Path(runtime['custody_directory']), Path(runtime['bws_executable']), account=account).retrieve()
    result = ContainerRunner(runtime['image']).run(profile, operation, arguments, login, deadline, guard)
    del login
    if result['status'] != 'ok' or not guard():
        return {'account': account, 'qualified': False, 'status': result['status']}
    profile['qualified'] = True
    profiles = [profile if name == account else registry.profile(name) for name in registry.accounts()]
    invalidate_grants(ACCESS.parent / 'ledger')
    _helper().atomic(ACCESS / 'sites.json', json.dumps({'schema_version': 1, 'sites': profiles}).encode())
    for unit in ('homecompute-openclaw-access.service', 'homecompute-openclaw-sites.service'):
        command(['systemctl', 'restart', unit])
    return {'account': account, 'qualified': True, 'fresh_access_request_required': True,
            'transaction_execution_available': False}


def command(args, *, check=True, timeout=30):
    if not args or args[0] not in {'docker', 'iptables', 'systemctl'}:
        raise ValueError('Installer command refused')
    result = subprocess.run([BIN + args[0], *args[1:]], stdin=subprocess.DEVNULL,  # nosec B603
                            capture_output=True, text=True, timeout=timeout,
                            env={'PATH': BIN, 'HOME': '/root'})
    if check and result.returncode:
        raise ValueError('Private website installation command failed')
    return result


def firewall():
    # Rules are confined to these two dedicated networks. No existing service's
    # port grants, primary firewall chains or other container rules are replaced.
    worker, proxy = '172.30.192.0/24', '172.30.193.0/24'
    rules = [('INPUT', ['-s', worker, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']),
             ('INPUT', ['-s', worker, '-j', 'DROP']), ('INPUT', ['-s', proxy, '-j', 'DROP']),
             ('DOCKER-USER', ['-s', worker, '-d', '172.30.192.2/32', '-p', 'tcp', '--dport', '3128', '-j', 'ACCEPT']),
             ('DOCKER-USER', ['-s', '172.30.192.2/32', '-d', worker, '-m', 'conntrack',
                              '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']),
             ('DOCKER-USER', ['-s', worker, '-j', 'DROP'])]
    private_rules = []
    for slot in range(2):
        browser_ip, proxy_ip = '172.30.192.' + str(20 + slot), '172.30.192.' + str(10 + slot)
        private_rules.extend([
            ('OUTPUT', ['-d', browser_ip, '-p', 'tcp', '--dport', '6080', '-m', 'owner', '!', '--uid-owner', '0', '-j', 'DROP']),
            ('DOCKER-USER', ['-s', browser_ip, '-d', proxy_ip, '-p', 'tcp', '--dport', '3128', '-j', 'ACCEPT']),
            ('DOCKER-USER', ['-s', proxy_ip, '-d', browser_ip, '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT']),
            ('DOCKER-USER', ['-s', browser_ip, '-j', 'DROP'])])
    rules = private_rules + rules
    for destination in ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
                        '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/3'):
        rules.append(('DOCKER-USER', ['-s', proxy, '-d', destination, '-j', 'DROP']))
    tagged = [(chain, [*rule[:-2], '-m', 'comment', '--comment', 'homecompute-site-browser', *rule[-2:]])
              for chain, rule in rules]
    for chain, rule in tagged:
        for _ in range(16):
            if command(['iptables', '-w', '-C', chain, *rule], check=False).returncode:
                break
            command(['iptables', '-w', '-D', chain, *rule])
        else:
            raise ValueError('Duplicate dedicated browser firewall rules refused')
    for chain, rule in reversed(tagged):
        command(['iptables', '-w', '-I', chain, '1', *rule])


def proxy_unit() -> str:
    return f'''[Unit]
Description=Registered-site private public-IP-pinning TLS egress
Requires=docker.service
After=docker.service network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre={BIN}python3 {INSTALL}/setup-openclaw-sites.py firewall
ExecStart={BIN}docker start {PROXY}
ExecStop={BIN}docker stop --time 2 {PROXY}
StandardOutput=null
StandardError=null
[Install]
WantedBy=multi-user.target
'''


def runner_unit() -> str:
    return f'''[Unit]
Description=Private grant-checked disposable website operations
Requires=docker.service homecompute-openclaw-site-egress.service homecompute-openclaw-access.service
After=docker.service homecompute-openclaw-site-egress.service homecompute-openclaw-access.service
[Service]
Type=simple
User=root
UMask=0077
WorkingDirectory={INSTALL}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PATH={BIN}
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
ExecStart={BIN}python3 {INSTALL}/openclaw-site-service.py serve --config {CONFIG}/runtime.json
ExecStopPost={BIN}python3 {INSTALL}/openclaw-site-service.py cleanup
Restart=on-failure
RestartSec=1
TimeoutStopSec=15
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectClock=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
AmbientCapabilities=
ReadOnlyPaths=/var/lib/homecompute-credential-secrets
ReadWritePaths={STATE}/operations {STATE}/sessions /run/docker.sock
InaccessiblePaths=/srv/state/mattermost/private /var/lib/homecompute-openclaw {ACCESS}/mattermost.json
IPAddressDeny=10.0.0.0/8 100.64.0.0/10 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16
IPAddressAllow=localhost
IPAddressAllow=172.30.192.0/24
MemoryMax=256M
TasksMax=64
LimitCORE=0
StandardOutput=null
StandardError=null
[Install]
WantedBy=multi-user.target
'''


def install(source: Path, image: str) -> dict:
    phase('preflight')
    if os.geteuid() != 0 or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Root installer and immutable reviewed image required')
    spec = importlib.util.spec_from_file_location('site_access_setup', source / 'scripts/setup-openclaw-access.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    for directory in (STATE, CONFIG, STATE / 'operations', STATE / 'sessions'):
        os.close(open_directory(directory, create=True))
    phase('artifact')
    inspected = json.loads(command(['docker', 'image', 'inspect', image]).stdout)[0]
    if (inspected['Id'] != image or inspected['Os'] != 'linux' or inspected['Architecture'] != 'amd64'
            or inspected['Config']['Labels'].get('org.homecompute.site-worker') != '1'):
        raise ValueError('Website worker artifact refused')
    phase('quiesce')
    # A failed stop must not leave old workers during network reconciliation.
    loaded = command(['systemctl', 'show', 'homecompute-openclaw-sites.service', '--property=LoadState', '--value'], check=False)
    if loaded.stdout.strip() == 'loaded':
        command(['systemctl', 'stop', 'homecompute-openclaw-sites.service'])
        active = command(['systemctl', 'is-active', 'homecompute-openclaw-sites.service'], check=False)
        if active.stdout.strip() not in {'inactive', 'failed'}:
            raise ValueError('Website runner did not stop')
    elif loaded.stdout.strip() != 'not-found':
        raise ValueError('Website runner lifecycle unavailable')
    retained = command(['docker', 'ps', '-aq', '--filter', 'label=homecompute.site-operation=true']).stdout.splitlines()
    if len(retained) > 16 or any(not re.fullmatch(r'[a-f0-9]{12,64}', entry) for entry in retained):
        raise ValueError('Retained browser identity refused')
    if retained:
        command(['docker', 'rm', '--force', *retained])
    if command(['docker', 'ps', '-aq', '--filter', 'label=homecompute.site-operation=true']).stdout.strip():
        raise ValueError('Retained browser cleanup unverified')
    invalidate_grants(ACCESS.parent / 'ledger')
    phase('source_install')
    for name in FILES:
        helper.atomic(INSTALL / name, (source / 'scripts' / name).read_bytes())
    seccomp = (source / 'deploy/openclaw/site-worker/seccomp.json').read_bytes()
    policy = json.loads(seccomp)
    if policy.get('defaultAction') != 'SCMP_ACT_ERRNO':
        raise ValueError('Browser syscall sandbox required')
    helper.atomic(INSTALL / 'site-seccomp.json', seccomp)
    # Qualify real non-root Chromium sandbox/pipe before advertising a driver.
    probe = ('from playwright.sync_api import sync_playwright; from openclaw_site_session_worker import Display; '
             'd=Display(); d.start(); p=sync_playwright().start(); '
             'b=p.chromium.launch(headless=False,chromium_sandbox=True); page=b.new_page(); '
             'page.set_content("<div data-fixture>fixture</div>"); '
             'assert page.locator("[data-fixture]").count()==1; b.close(); p.stop(); d.close()')
    phase('sandbox_probe')
    command(['docker', 'run', '--rm', '--init', '--network', 'none', '--user', '1000:1000',
             '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
             '--security-opt', 'seccomp=' + str(INSTALL / 'site-seccomp.json'), '--shm-size', '128m',
             '--tmpfs', TMPFS,
             '--env', 'HOME=/tmp', image, 'python', '-c', probe], timeout=30)
    phase('networks')
    for name, subnet, internal in NETWORKS:
        result = command(['docker', 'network', 'inspect', name], check=False)
        if result.returncode:
            command(['docker', 'network', 'create', '--subnet', subnet,
                     *(['--internal'] if internal else []), name])
        else:
            network = json.loads(result.stdout)[0]
            if (network['Internal'] is not internal or network.get('EnableIPv6')
                    or network['IPAM']['Config'] != [{'Subnet': subnet, 'Gateway': str(ipaddress.ip_network(subnet)[1])}]):
                raise ValueError('Retained private browser network differs')
    phase('profiles')
    sites_path = ACCESS / 'sites.json'
    if sites_path.exists():
        registry = SiteRegistry.from_file(sites_path)
        profiles = [registry.profile(account) for account in registry.accounts()]
    else:
        profiles = [dict(DEFAULT_HOTELS)]
    # Owner mode never submits sign-in automatically. It exposes only a private
    # human display; each owner must attest sign-in under an approved live grant.
    for profile in profiles:
        if profile['account'] == 'hotels.com':
            profile['login_mode'] = 'owner'
            profile['driver_config']['login_url'] = 'https://da.hotels.com/login'
            profile['qualified'] = True
    registry = SiteRegistry(profiles)
    helper.atomic(sites_path, json.dumps({'schema_version': 1, 'sites': profiles}).encode())
    hosts = sorted({host for account in registry.accounts() for host in registry.profile(account)['domains']})
    helper.atomic(INSTALL / 'site-hosts.json', json.dumps(hosts).encode(), mode=0o644)
    phase('proxy')
    command(['docker', 'rm', '--force', PROXY], check=False)
    command(['docker', 'create', '--name', PROXY, '--label', 'homecompute.site-egress=true',
             '--restart', 'unless-stopped', '--init', '--network', 'homecompute-site-egress',
             '--user', '1000:1000', '--read-only', '--cap-drop', 'ALL',
             '--security-opt', 'no-new-privileges:true', '--log-driver', 'none',
             '--memory', '96m', '--memory-swap', '96m', '--cpus', '0.25', '--pids-limit', '64',
             '--mount', f'type=bind,src={INSTALL}/site-hosts.json,dst=/run/site-hosts.json,readonly',
             image, 'python', '/opt/site/openclaw_site_egress.py', '--hosts', '/run/site-hosts.json'])
    command(['docker', 'network', 'connect', '--ip', '172.30.192.2', 'homecompute-site-browser', PROXY])
    phase('request_transport')
    path = CONFIG / 'operation-tokens.json'
    if path.exists():
        identities = json.loads(read_private(CONFIG, path.name, 4096))
        if set(identities) != {'openclaw'} or not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', identities['openclaw']):
            raise ValueError('Retained website requester refused')
    else:
        identities = {'openclaw': secrets.token_urlsafe(48)}
        helper.atomic(path, json.dumps(identities).encode())
    user = pwd.getpwnam('homecompute-openclaw')
    helper.atomic(CORE / 'site-operation-token', identities['openclaw'].encode(), uid=user.pw_uid, gid=user.pw_gid)
    bridge = {'schema_version': 1, 'principal': 'openclaw', 'endpoint': 'http://127.0.0.1:18816',
              'token_file': str(CORE / 'site-operation-token')}
    helper.atomic(CORE / 'site-operations.json', json.dumps(bridge).encode(), uid=user.pw_uid, gid=user.pw_gid)
    owner_path = ACCESS / 'browser-owner-token'
    forbidden_hashes = native_token_hashes(user.pw_uid)
    request_identities = json.loads(read_private(ACCESS, 'assistant-tokens.json', 4096))
    if owner_path.exists():
        owner_key = read_private(ACCESS, owner_path.name, 1024).decode().strip()
    else:
        owner_key = secrets.token_urlsafe(48)
    if (not re.fullmatch(r'[A-Za-z0-9_-]{32,512}', owner_key) or owner_key in identities.values()
            or owner_key in request_identities.values() or hashlib.sha256(owner_key.encode()).hexdigest() in forbidden_hashes):
        raise ValueError('Distinct private owner identity required')
    if not owner_path.exists():
        helper.atomic(owner_path, owner_key.encode())
    helper.atomic(ACCESS / 'site-owner.json', json.dumps({'schema_version': 1,
                  'endpoint': 'http://127.0.0.1:18816', 'token_file': str(owner_path)}).encode())
    bws = qualified_bws(INSTALL / 'bws-package/bin/bws')
    runtime = {'schema_version': 1, 'image': image, 'sites_file': str(sites_path),
               'state_directory': str(STATE / 'operations'), 'assistant_tokens_file': str(path),
               'request_tokens_file': str(ACCESS / 'assistant-tokens.json'), 'bws_executable': str(bws),
               'custody_directory': '/var/lib/homecompute-credential-secrets',
               'owner_token_file': str(owner_path), 'sessions_directory': str(STATE / 'sessions'),
               'forbidden_key_digests': forbidden_hashes}
    helper.atomic(CONFIG / 'runtime.json', json.dumps(runtime).encode())
    phase('units')
    helper.atomic(UNITS / 'homecompute-openclaw-site-egress.service', proxy_unit().encode(), mode=0o644)
    helper.atomic(UNITS / 'homecompute-openclaw-sites.service', runner_unit().encode(), mode=0o644)
    for name in ('homecompute-openclaw-site-egress.service', 'homecompute-openclaw-sites.service'):
        helper.boot_dependency(name)
    # Install shared registry/UI sources before restarting the metadata daemon.
    phase('metadata_activation')
    helper.configure(source)
    command(['systemctl', 'daemon-reload'])
    for name in ('homecompute-openclaw-site-egress.service', 'homecompute-openclaw-sites.service'):
        phase(name)
        command(['systemctl', 'restart', name])
    return {'installed': True, 'registered_sites': list(registry.accounts()), 'private_browser_pipe': True,
            'credentials_or_cookies_in_tool_results': False, 'transaction_execution_available': False,
            'real_website_sign_in_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'firewall', 'register', 'inspect', 'qualify'])
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--image-id')
    parser.add_argument('--profile-file', type=Path)
    parser.add_argument('--account')
    parser.add_argument('--request-id')
    parser.add_argument('--operation', choices=['read', 'search', 'prepare'], default='read')
    parser.add_argument('--parameters-file', type=Path)
    parser.add_argument('--profile-sha256')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError('Trusted root operator required')
        if args.action == 'firewall':
            firewall()
        elif args.action == 'register':
            print(json.dumps(register(args.profile_file)))
        elif args.action == 'inspect':
            profile = SiteRegistry.from_file(ACCESS / 'sites.json').profile(args.account)
            print(json.dumps({'account': args.account, 'qualified': profile['qualified'],
                              'profile_sha256': profile_digest(profile)}))
        elif args.action == 'qualify':
            parameters = json.loads(read_private(args.parameters_file.parent, args.parameters_file.name, 4096)) if args.parameters_file else {}
            print(json.dumps(qualify(args.account, args.request_id, args.operation, parameters, args.profile_sha256)))
        else:
            print(json.dumps(install(args.source_root, args.image_id)))
    except Exception as error:
        print(json.dumps({'setup_failed': True, 'stage': PHASE, 'error_type': type(error).__name__}))
        raise SystemExit('Private website installation stopped; no secrets are displayed.')
