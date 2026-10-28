#!/usr/bin/env python3
"""Stage or activate core communication, preserving private credentials, cursors and receipts."""
from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any
DEST = Path('/opt/homecompute/openclaw-communication')
HOME = Path('/var/lib/homecompute-openclaw')
CONFIG = HOME / 'config'
STATE = HOME / 'communication'
UNIT_DIR = Path('/etc/systemd/system.attached')
REPORTS = Path('/run/homecompute-openclaw-reports')
TMPFILES = Path('/etc/tmpfiles.d/homecompute-openclaw.conf')
USER = 'homecompute-openclaw'
PREFIX = 'homecompute-openclaw-'
PYTHON = Path('/run/current-system/sw/bin/python3')
FILES = (
    'scripts/embedding_client.py', 'scripts/openclaw_speech.py', 'scripts/wyoming_tts_client.py',
    'scripts/semantic_retrieval.py', 'scripts/semantic_sources.py', 'scripts/semantic_operations.py',
    'scripts/semantic-index.py', 'docs/openclaw-operations.md', 'docs/openclaw-tools.md',
    'docs/homecompute-operations.md', 'docs/local-model-access.md',
    'docs/operations/shelfmark-direct-download-repair.md',
    'scripts/openclaw-communication.py', 'scripts/openclaw-telegram.py',
    'scripts/openclaw-chat.py', 'scripts/openclaw_notifications.py',
    'scripts/openclaw_machine_notifications.py', 'scripts/openclaw_tasks.py',
    'scripts/openclaw_maintenance.py', 'scripts/openclaw_access_bridge.py',
    'scripts/openclaw_site_registry.py', 'scripts/openclaw_bitwarden.py',
    'scripts/openclaw_capabilities.py', 'scripts/openclaw_services.py', 'scripts/homeassistant_actions.py',
    'scripts/openclaw_integrations.py', 'scripts/openclaw_integration_policy.py', 'scripts/openclaw_places.py',
    'scripts/openclaw_hotels.py',
    'scripts/openclaw_integration_http.py',
    'config/openclaw-integrations.json', 'docs/openclaw-integrations.md',
    'scripts/openclaw_household.py', 'scripts/openclaw_household_requests.py',
    'scripts/openclaw_household_shopping.py', 'scripts/openclaw_household_storage.py',
    'scripts/openclaw_household_tasks.py', 'scripts/openclaw-household-task-reminders.py',
    'scripts/openclaw_family_task_projection.py', 'scripts/openclaw_task_location.py',
    'scripts/openclaw_location_policy.py', 'scripts/openclaw_location_transport.py',
    'scripts/openclaw_mail.py', 'scripts/openclaw_outlook.py', 'scripts/setup-openclaw-outlook.py',
    'scripts/openclaw_calendar.py', 'scripts/openclaw_calendar_events.py',
    'scripts/openclaw_ha_websocket.py',
    'scripts/infrastructure_maintenance.py', 'scripts/infrastructure_update_policy.py',
    'scripts/maintenance_plan_preparation.py',
    'deploy/codex-worker/mcp_adapter.py', 'deploy/codex-worker/policy.py',
    'scripts/openclaw_infrastructure.py', 'scripts/openclaw-observation-feed.py',
    'scripts/observe-homecompute.py', 'scripts/homecompute.py',
    'scripts/n8n_observations.py',
    'scripts/maintenance_observations.py', 'config/system-monitoring.json',
    'scripts/openclaw-machine-snapshot.py', 'scripts/homeassistant_observations.py',
    'scripts/setup-openclaw-core-communication.py', 'scripts/setup-openclaw-home-assistant.py',
)
NAMES = ('adapter', 'telegram', 'task-feed', 'update-feed', 'machine-status', 'household-task-reminders', 'family-task-reminders')
TIMERS = ('machine-status', 'update-feed', 'household-task-reminders', 'family-task-reminders')
def timer_configured(name: str) -> bool:
    return (CONFIG / {'household-task-reminders': 'household-tasks.json',
                      'family-task-reminders': 'family-tasks.json'}[name]).exists() if name.endswith('task-reminders') else True
def call(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check)
def commands(python: Path = PYTHON) -> dict[str, list[str]]:
    scripts = DEST / 'scripts'
    registry = CONFIG / 'system-monitoring.json' if (CONFIG / 'system-monitoring.json').exists() else DEST / 'config/system-monitoring.json'
    ha_args = ['--ha-transport', str(CONFIG / 'home-assistant.json')] if (CONFIG / 'home-assistant.json').exists() else []
    maintenance_args = ['--homepage-maintenance'] if Path('/opt/homecompute/homepage-maintenance/homepage_update_check.py').is_file() else []
    mcp_args = ['--mcp-tokens', str(CONFIG / 'native-mcp-tokens.json')] if (CONFIG / 'native-mcp-tokens.json').exists() else []
    ha_action_args = ['--ha-actions'] if mcp_args and Path('/etc/systemd/system.attached/homecompute-ha-actions.service').is_file() else []
    service_args = ['--services', str(CONFIG / 'services.json')] if mcp_args and (CONFIG / 'services.json').exists() else []
    integration_args = ['--integrations', str(DEST / 'config/openclaw-integrations.json')] if service_args else []
    household_args = ['--household', str(CONFIG / 'household.json')] if service_args and (CONFIG / 'household.json').exists() else []
    calendar_args = ['--calendar', str(CONFIG / 'calendar.json')] if service_args and (CONFIG / 'calendar.json').exists() else []
    task_args = ['--household-tasks', str(CONFIG / 'household-tasks.json')] if service_args and (CONFIG / 'household-tasks.json').exists() else []
    family_args = ['--family-tasks', str(CONFIG / 'family-tasks.json')] if service_args and (CONFIG / 'family-tasks.json').exists() else []
    # Mail is optional and currently retired. Retained private files cannot
    # silently reconnect a mailbox when the communication bundle is reapplied.
    mail_args: list[str] = []
    access_args = ['--access-config', str(CONFIG / 'access-requests.json')] if mcp_args and (CONFIG / 'access-requests.json').exists() else []
    site_args = ['--site-config', str(CONFIG / 'site-operations.json')] if access_args and (CONFIG / 'site-operations.json').exists() else []
    return {
        'adapter': [str(python), str(scripts / 'openclaw-communication.py'),
                    '--config', str(CONFIG / 'communication.json'),
                    '--tokens', str(CONFIG / 'transport-tokens.json'),
                    '--state', str(STATE / 'adapter'),
                    '--task-transport', str(CONFIG / 'codex-task-transport.json'),
                    '--registry', str(registry),
                    '--infrastructure-registry', str(registry),
                    '--core-host-mode', *ha_args, *maintenance_args, *mcp_args, *ha_action_args, *service_args,
                    *integration_args, *household_args, *calendar_args, *task_args, *family_args, *mail_args, *access_args, *site_args],
        'telegram': [str(python), str(scripts / 'openclaw-telegram.py'), 'run',
                     '--config', str(CONFIG / 'telegram.json'),
                     '--tokens', str(CONFIG / 'telegram-tokens.json'),
                     '--state', str(STATE / 'telegram'), '--supervised'],
        'task-feed': [str(python), str(scripts / 'openclaw_tasks.py'),
                      '--transport', str(CONFIG / 'codex-task-transport.json'),
                      '--communication-config', str(CONFIG / 'communication.json'),
                      '--transport-tokens', str(CONFIG / 'transport-tokens.json'),
                      '--cursor', str(STATE / 'task-feed.cursor.json'), '--follow'],
        'update-feed': [str(python), str(scripts / 'openclaw-observation-feed.py'),
                        '--registry', str(registry),
                        '--tokens', str(CONFIG / 'transport-tokens.json'),
                        '--state', str(STATE / 'update-feed'), '--core-host-mode', *ha_args],
        'machine-status': [str(python), str(scripts / 'openclaw-machine-snapshot.py'), '--publish-core'],
        'household-task-reminders': [str(python), str(scripts / 'openclaw-household-task-reminders.py'),
                                    '--config', str(CONFIG / 'household-tasks.json'), '--state', str(STATE / 'adapter')],
        'family-task-reminders': [str(python), str(scripts / 'openclaw-household-task-reminders.py'),
                                 '--config', str(CONFIG / 'family-tasks.json'), '--state', str(STATE / 'adapter'),
                                 '--scope', 'family', '--project-home-assistant'],
    }
def service(name: str, python: Path = PYTHON) -> str:
    if name == 'machine-status':
        return machine_service(python)
    command = commands(python)[name]
    after = ('network-online.target homecompute-agents-vm.service ' + PREFIX + 'machine-status.service' if name == 'adapter'
             else PREFIX + 'adapter.service network-online.target')
    wants = 'network-online.target' + (' ' + PREFIX + 'machine-status.service' if name == 'adapter' else '')
    restart = 'no' if name.endswith('task-reminders') else 'on-failure' if name in {'telegram', 'update-feed'} else 'always'
    kind = 'oneshot' if name == 'update-feed' or name.endswith('task-reminders') else 'simple'
    task_config = {'household-task-reminders': 'household-tasks.json', 'family-task-reminders': 'family-tasks.json'}.get(name)
    condition = f'ConditionPathExists={CONFIG}/{task_config}\n' if task_config else ''
    pre = (f'ExecStartPre=+{python} {DEST}/scripts/openclaw-observation-feed.py --project-core-model\n'
           if name == 'update-feed' else '')
    writable = f'{HOME} {REPORTS}' if name == 'update-feed' else str(HOME)
    return f'''[Unit]
Description=HomeCompute OpenClaw {name}
{condition}After={after}
Wants={wants}
StartLimitIntervalSec=0
[Service]
Type={kind}
User={USER}
Group={USER}
WorkingDirectory={DEST}
Environment=HOMECOMPUTE_OPENCLAW_TRANSPORT=home-core
Environment=HOME={HOME}
Environment=PATH=/run/current-system/sw/bin
Environment=SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSH_AUTH_SOCK
{pre}ExecStart={' '.join(command)}
Restart={restart}
RestartSec=15s
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectClock=yes
ProtectControlGroups=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths={writable}
MemoryMax=256M
TasksMax=64
LimitCORE=0
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
'''
def machine_service(python: Path = PYTHON) -> str:
    return f'''[Unit]
Description=HomeCompute finite public machine status projection
After=docker.service
[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory={DEST}
Environment=PATH=/run/current-system/sw/bin
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={' '.join(commands(python)['machine-status'])}
TimeoutStartSec=12s
UMask=0022
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectClock=yes
ProtectControlGroups=yes
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX
ReadWritePaths={REPORTS}
MemoryMax=128M
TasksMax=32
LimitCORE=0
StandardOutput=journal
StandardError=journal
'''
def timer(name: str = 'update-feed') -> str:
    boot, interval = (('5s', '30s') if name == 'machine-status' else
                      ('60s', '60s') if name.endswith('task-reminders') else ('30s', '300s'))
    return f'''[Unit]
Description=HomeCompute OpenClaw {name} observations
[Timer]
OnBootSec={boot}
OnUnitActiveSec={interval}
Persistent=true
Unit={PREFIX}{name}.service
[Install]
WantedBy=timers.target
'''
def protected_directory(path: Path, uid: int, private: bool, create: bool = False,
                        gid: int | None = None) -> None:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('directory symlinks prohibited')
    if not path.exists() and create:
        path.mkdir(mode=0o700 if private else 0o755)
        os.chown(path, uid, uid if gid is None else gid)
    info = path.lstat()
    prohibited = 0o077 if private else 0o022
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & prohibited:
        raise ValueError('directory owner or permissions differ from service boundary')

def private_file(path: Path, uid: int, *, json_object: bool = True) -> dict[str, Any]:
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('private file parent symlinks prohibited')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise ValueError('private files require service owner and mode0600')
        content = os.read(fd, 65537)
        if len(content) > 65536:
            raise ValueError('private file exceeds budget')
        value = json.loads(content) if json_object else {}
        if not isinstance(value, dict):
            raise ValueError('private JSON object required')
        return value
    finally:
        os.close(fd)


def account(create: bool = False) -> pwd.struct_passwd:
    try:
        info = pwd.getpwnam(USER)
    except KeyError:
        if not create:
            raise ValueError('dedicated service account unavailable') from None
        call('/run/current-system/sw/bin/useradd', '--system', '--user-group',
             '--no-create-home', '--home-dir', str(HOME), '--shell',
             '/run/current-system/sw/bin/nologin', USER)
        info = pwd.getpwnam(USER)
    if info.pw_uid == 0 or info.pw_dir != str(HOME) or info.pw_gid != grp.getgrnam(USER).gr_gid:
        raise ValueError('existing account differs from dedicated service boundary')
    return info


def validate_ready(uid: int, telegram: bool = False) -> None:
    for path in (HOME, CONFIG, STATE, STATE / 'adapter', STATE / 'telegram', STATE / 'update-feed'):
        protected_directory(path, uid, True)
    for name in ('communication.json', 'transport-tokens.json', 'codex-task-transport.json'):
        private_file(CONFIG / name, uid)
    if (CONFIG / 'native-mcp-tokens.json').exists():
        private_file(CONFIG / 'native-mcp-tokens.json', uid)
    for filename, port, token_name in (('access-requests.json', 18812, 'access-request-token'),
                                       ('site-operations.json', 18816, 'site-operation-token')):
        if not (CONFIG / filename).exists():
            continue
        expected = {'schema_version': 1, 'principal': 'openclaw', 'endpoint': f'http://127.0.0.1:{port}',
                    'token_file': str(CONFIG / token_name)}
        actual = private_file(CONFIG / filename, uid)
        if (actual != expected or type(actual.get('schema_version')) is not int
                or not (CONFIG / 'native-mcp-tokens.json').exists()
                or filename == 'site-operations.json' and not (CONFIG / 'access-requests.json').exists()):
            raise ValueError('fixed authenticated account bridge configuration required')
        private_file(CONFIG / token_name, uid, json_object=False)
    if (CONFIG / 'services.json').exists():
        services = private_file(CONFIG / 'services.json', uid)
        if services != {'schema_version': 1, 'tavily_token_file': str(CONFIG / 'tavily-search-token')}:
            raise ValueError('fixed private service configuration required')
        private_file(CONFIG / 'tavily-search-token', uid, json_object=False)
    if (CONFIG / 'household.json').exists():
        household = private_file(CONFIG / 'household.json', uid)
        if household != {'schema_version': 1, 'ha_token_file': str(CONFIG / 'household-ha-token')}:
            raise ValueError('fixed private household configuration required')
        private_file(CONFIG / 'household-ha-token', uid, json_object=False)
    if (CONFIG / 'calendar.json').exists():
        calendar = private_file(CONFIG / 'calendar.json', uid)
        if calendar != {'schema_version': 1, 'bridge_token_file': str(CONFIG / 'calendar-route-token')}:
            raise ValueError('fixed private calendar configuration required')
        if not (CONFIG / 'services.json').exists() or not (CONFIG / 'native-mcp-tokens.json').exists():
            raise ValueError('calendar tools require authenticated native service transport')
        private_file(CONFIG / 'calendar-route-token', uid, json_object=False)
    for scope, filename in (('household', 'household-tasks.json'), ('family', 'family-tasks.json')):
        if not (CONFIG / filename).exists():
            continue
        tasks = private_file(CONFIG / filename, uid)
        token_file = CONFIG / (scope + '-task-mattermost-token')
        if (set(tasks) != {'schema_version', 'channel_id', 'bot_id', 'token_file'}
                or type(tasks['schema_version']) is not int or tasks['schema_version'] != 1
                or tasks['token_file'] != str(token_file)
                or any(not isinstance(tasks[k], str) or len(tasks[k]) != 26
                       or not tasks[k].isascii() or not tasks[k].isalnum() or tasks[k] != tasks[k].lower()
                       for k in ('channel_id', 'bot_id'))):
            raise ValueError('fixed private household task configuration required')
        required = ('services.json', 'native-mcp-tokens.json') + (('household.json',) if scope == 'family' else ())
        if any(not (CONFIG / name).exists() for name in required):
            raise ValueError('household task tools require authenticated service transport')
        private_file(token_file, uid, json_object=False)
    if (CONFIG / 'mail.json').exists():
        mail = private_file(CONFIG / 'mail.json', uid)
        if (type(mail.get('schema_version')) is not int
                or mail['principal_scope'] != 'owner' or mail['data_domain'] != 'personal'
                or mail['accounts'] not in ([], ['outlook_mads'])):
            raise ValueError('fixed private mail configuration required')
        if not (CONFIG / 'services.json').exists() or not (CONFIG / 'native-mcp-tokens.json').exists():
            raise ValueError('mail tools require authenticated native service transport')
        if mail['schema_version'] == 2:
            if (set(mail) != {'schema_version', 'provider', 'outlook_config_file', 'principal_scope', 'data_domain', 'accounts'}
                    or mail['provider'] != 'outlook-direct'
                    or mail['outlook_config_file'] != str(CONFIG / 'outlook-owner/client.json')):
                raise ValueError('fixed direct Outlook configuration required')
            if mail['accounts']:
                private_file(CONFIG / 'outlook-owner/client.json', uid)
                private_file(CONFIG / 'outlook-owner/token.json', uid)
        elif mail['schema_version'] == 1:
            if (set(mail) != {'schema_version', 'bridge_token_file', 'principal_scope', 'data_domain', 'accounts'}
                    or mail['bridge_token_file'] != str(CONFIG / 'mail-owner-route-token')):
                raise ValueError('fixed private mail configuration required')
            if mail['accounts']:
                private_file(CONFIG / 'mail-owner-route-token', uid, json_object=False)
        else:
            raise ValueError('unsupported mail configuration')
    if (CONFIG / 'home-assistant.json').exists():
        ha = private_file(CONFIG / 'home-assistant.json', uid)
        token_path = Path(ha.get('token_file', ''))
        if token_path != CONFIG / 'ha-metadata-reader-token':
            raise ValueError('fixed private HA transport required')
        private_file(token_path, uid, json_object=False)
        private_file(CONFIG / 'system-monitoring.json', uid)
    transport = private_file(CONFIG / 'codex-task-transport.json', uid)
    for name in ('tokens_file', 'project_policy'):
        path = Path(transport.get(name, ''))
        if not path.is_absolute() or path.parent != CONFIG:
            raise ValueError('task transport must use the dedicated private config directory')
        private_file(path, uid)
    protected_directory(HOME / '.ssh', uid, True)
    for name in ('id_ed25519', 'known_hosts'):
        private_file(HOME / '.ssh' / name, uid, json_object=False)
    if telegram:
        private_file(CONFIG / 'telegram.json', uid)
        private_file(CONFIG / 'telegram-tokens.json', uid)
        cursor = private_file(STATE / 'telegram/telegram-cursor.json', uid)
        guard = STATE / 'telegram/telegram-admission.json'
        if cursor.get('paused') is not False or guard.exists() or guard.is_symlink():
            raise ValueError('receiver requires reconciliation; cursor and admission guard retained')


def source_bundle(source: Path) -> tuple[dict[str, bytes], str]:
    if any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError('source directory symlinks prohibited')
    if not source.is_dir() or source.stat().st_mode & 0o022:
        raise ValueError('protected source directory required')
    files, digest = {}, hashlib.sha256()
    for name in FILES:
        path = source / name
        if (any(p.is_symlink() for p in (path, path.parent)) or not path.is_file()
                or path.stat().st_mode & 0o022 or path.stat().st_size > 1024 * 1024):
            raise ValueError('unsafe source bundle file')
        content = path.read_bytes()
        if name.endswith('.py'):
            compile(content, name, 'exec')
        elif name.endswith('.md'):
            content.decode('utf-8')
        elif not isinstance(json.loads(content), dict):
            raise ValueError('registry object required')
        files[name] = content
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
    return files, digest.hexdigest()


def write(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_uid != 0)):
        raise ValueError('managed output must be a regular root-owned file')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def enable(name: str, enabled: bool, *, timer_unit: bool = False) -> None:
    directory = 'timers.target.wants' if timer_unit else 'multi-user.target.wants'
    suffix = '.timer' if timer_unit else '.service'
    link = UNIT_DIR / directory / (PREFIX + name + suffix)
    target = UNIT_DIR / link.name
    if link.is_symlink():
        if os.readlink(link) != str(target):
            raise ValueError('unmanaged boot dependency')
        if not enabled:
            link.unlink()
    elif link.exists():
        raise ValueError('boot dependency must be a managed symlink')
    elif enabled:
        link.symlink_to(target)


def install(files: dict[str, bytes]) -> None:
    unit_names = [PREFIX + name + '.service' for name in NAMES] + [PREFIX + name + '.timer' for name in TIMERS]
    for unit in unit_names:
        if call('systemctl', 'is-active', unit, check=False).stdout.strip() in {
                'active', 'activating', 'reloading'}:
            raise ValueError('stop existing communication units before replacing code')
    info = account(create=True)
    for path in (HOME, CONFIG, STATE, STATE / 'adapter', STATE / 'telegram', STATE / 'update-feed'):
        protected_directory(path, info.pw_uid, True, True, info.pw_gid)
    for path in (DEST.parent.parent, DEST.parent, DEST, DEST / 'scripts', DEST / 'config',
                 DEST / 'docs', DEST / 'docs/operations',
                 DEST / 'deploy', DEST / 'deploy/codex-worker',
                 UNIT_DIR, UNIT_DIR / 'multi-user.target.wants', UNIT_DIR / 'timers.target.wants'):
        protected_directory(path, 0, False, True, 0)
    for name, content in files.items():
        write(DEST / name, content)
    certificate = Path('/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt')
    if certificate.is_file() and not certificate.is_symlink():
        info = certificate.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Trusted gateway CA required')
        write(DEST / 'config/embedding-ca.crt', certificate.read_bytes())
    for name in NAMES:
        # Never recreate or replace the retired receiver, including /dev/null
        # masks. Boot disablement below also applies to older regular units.
        if name != 'telegram':
            write(UNIT_DIR / (PREFIX + name + '.service'), service(name).encode())
        enable(name, name in {'adapter', 'task-feed'})
    for name in TIMERS:
        write(UNIT_DIR / (PREFIX + name + '.timer'), timer(name).encode())
        enable(name, timer_configured(name), timer_unit=True)
    protected_directory(TMPFILES.parent, 0, False)
    write(TMPFILES, f'd {REPORTS} 0755 root root -\n'.encode())
    call('systemd-tmpfiles', '--create', str(TMPFILES))
    protected_directory(REPORTS, 0, False)
    # The retired inbound receiver can be intentionally masked by its owner.
    # Verify every supported service, preserving that stronger retirement fence.
    call('systemd-analyze', 'verify', *(str(UNIT_DIR / (PREFIX + name + '.service')) for name in NAMES if name != 'telegram'),
         *(str(UNIT_DIR / (PREFIX + name + '.timer')) for name in TIMERS))
    call('systemctl', 'daemon-reload')


def validate_units(unit_names: list[str]) -> None:
    for name in unit_names:
        path = UNIT_DIR / (PREFIX + name)
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ValueError('install trusted units before activation')


def activate(telegram: bool = False) -> None:
    if telegram:
        raise ValueError('Telegram is outbound-only; inbound receiver activation is retired')
    validate_ready(account().pw_uid, telegram)
    selected = ('telegram',) if telegram else ('machine-status', 'adapter', 'task-feed', 'update-feed')
    timers = () if telegram else tuple(name for name in TIMERS if timer_configured(name))
    validate_units([name + '.service' for name in selected] + [name + '.timer' for name in timers])
    for name in selected:
        if name not in {'update-feed', 'machine-status'}:
            enable(name, True)
    for name in timers:
        enable(name, True, timer_unit=True)
    call('systemctl', 'daemon-reload')
    for name in selected:
        call('systemctl', 'start', PREFIX + name + '.service', check=name != 'machine-status')
    for name in timers:
        call('systemctl', 'start', PREFIX + name + '.timer')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install', action='store_true', help='stage and enable adapter/feeds; start nothing; Telegram disabled')
    actions.add_argument('--activate-services', action='store_true', help='start adapter/task/update feeds after migration')
    actions.add_argument('--activate-telegram', action='store_true', help='retired: Telegram is outbound-only')
    parser.add_argument('--source', type=Path, help='trusted checkout containing scripts/config; required for install')
    args = parser.parse_args(argv)
    try:
        if not any((args.install, args.activate_services, args.activate_telegram)):
            print(json.dumps({**{name: service(name) for name in NAMES},
                              **{name + '.timer': timer(name) for name in TIMERS}}, indent=2))
            return 0
        if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
            raise ValueError('requires root on home-core')
        if not PYTHON.is_file():
            raise ValueError('NixOS Python runtime unavailable')
        if args.install:
            if args.source is None:
                raise ValueError('--install requires --source')
            files, digest = source_bundle(args.source)
            install(files)
            result = {'installed': True, 'started': [], 'telegram_enabled': False, 'source_sha256': digest}
        else:
            activate(args.activate_telegram)
            result = {'started': ['telegram'] if args.activate_telegram else
                      ['machine-status', 'adapter', 'task-feed', 'update-feed',
                       *[name + '.timer' for name in TIMERS if timer_configured(name)]]}
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Core communication setup refused; preserve private state and reconcile fixed inputs.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
