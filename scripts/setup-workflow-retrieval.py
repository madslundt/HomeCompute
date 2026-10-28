#!/usr/bin/env python3
"""Install the private scheduled-workflow retrieval service on home-core."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess

ROOT = Path('/opt/homecompute/workflow-retrieval')
CONFIG = Path('/var/lib/homecompute-openclaw/config')
UNITS = Path('/etc/systemd/system.attached')
BIN = '/run/current-system/sw/bin'
N8N = 'homecompute-automation-n8n-1'
PROFILES = ('shopping', 'storage', 'research', 'news', 'calendar', 'school', 'battery')
FILES = ('workflow-retrieval-server.py', 'workflow_retrieval.py', 'embedding_client.py',
         'semantic_retrieval.py', 'semantic_sources.py', 'openclaw_services.py',
         'openclaw_household.py', 'openclaw_household_requests.py', 'openclaw_ha_websocket.py', 'semantic-index.py')
ROUTE = '''
    # Private scheduled workflow retrieval; profile credentials checked upstream.
    @workflow_retrieval {
        remote_ip 172.28.201.2
        method POST
        path /internal/workflow-retrieval
    }
    handle @workflow_retrieval {
        request_body {
            max_size 256KB
        }
        reverse_proxy 172.28.200.1:18795 {
            transport http {
                dial_timeout 3s
                response_header_timeout 30s
            }
        }
    }
'''
FIREWALL = '''#!/run/current-system/sw/bin/bash
set -euo pipefail
# Docker raw ingress guards run before conntrack. Permit only ACK-bearing
# replies from Caddy's TLS listener; the filter rule still requires REPLY state.
iptables -w -t raw -C PREROUTING -i br-hc-ctrl -s 172.28.200.2/32 -d 172.28.201.2/32 -p tcp --sport 8443 --tcp-flags ACK ACK -j ACCEPT 2>/dev/null || \
  iptables -w -t raw -I PREROUTING 1 -i br-hc-ctrl -s 172.28.200.2/32 -d 172.28.201.2/32 -p tcp --sport 8443 --tcp-flags ACK ACK -j ACCEPT
iptables -w -C HC-CADDY-LAN -i br-hc-ctrl -o br-hc-n8n -s 172.28.200.2/32 -d 172.28.201.2/32 -p tcp --sport 8443 -m conntrack --ctstate ESTABLISHED --ctdir REPLY -j RETURN 2>/dev/null || \
  iptables -w -I HC-CADDY-LAN 1 -i br-hc-ctrl -o br-hc-n8n -s 172.28.200.2/32 -d 172.28.201.2/32 -p tcp --sport 8443 -m conntrack --ctstate ESTABLISHED --ctdir REPLY -j RETURN
# Keep n8n's exact source identity across its permitted HTTPS gateway hop.
# Docker's general bridge masquerade would otherwise hide it as the host.
iptables -w -t nat -C POSTROUTING -o br-hc-ctrl -s 172.28.201.2/32 -d 172.28.200.2/32 -p tcp --dport 8443 -j ACCEPT 2>/dev/null || \
  iptables -w -t nat -I POSTROUTING 1 -o br-hc-ctrl -s 172.28.201.2/32 -d 172.28.200.2/32 -p tcp --dport 8443 -j ACCEPT
iptables -w -N HC-WORKFLOW 2>/dev/null || true
iptables -w -F HC-WORKFLOW
iptables -w -A HC-WORKFLOW -i br-hc-ctrl -s 172.28.200.2/32 -j ACCEPT
iptables -w -A HC-WORKFLOW -s 172.28.200.1/32 -j ACCEPT
iptables -w -A HC-WORKFLOW -j REJECT
iptables -w -C INPUT -d 172.28.200.1/32 -p tcp --dport 18795 -j HC-WORKFLOW 2>/dev/null || \
  iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp --dport 18795 -j HC-WORKFLOW
'''


def command(argv: list[str], content: str | None = None) -> str:
    result = subprocess.run(argv, input=content, capture_output=True, text=True, timeout=120)
    if result.returncode:
        save(CONFIG / 'workflow-retrieval-setup-failure.json', {'command': argv, 'stdout': result.stdout, 'stderr': result.stderr})
        raise ValueError('Provisioning command failed: ' + argv[0])
    return result.stdout


def save(path: Path, value: dict) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(value, handle)


def read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Private setup state required')
    return json.loads(path.read_text())


def provision_credentials() -> dict:
    token_path, receipt = CONFIG / 'workflow-retrieval.json', CONFIG / 'workflow-retrieval-n8n.json'
    if not token_path.exists():
        save(token_path, {'schema_version': 1, 'tokens': {p: secrets.token_urlsafe(48) for p in PROFILES}})
    tokens = read(token_path)
    if (tokens.get('schema_version') != 1 or set(tokens.get('tokens', {})) != set(PROFILES)
            or len(set(tokens['tokens'].values())) != len(PROFILES)):
        raise ValueError('Independent profile tokens required')
    state = read(receipt) if receipt.exists() else {
        p: {'id': secrets.token_hex(8), 'name': 'HomeCompute retrieval · ' + p, 'type': 'httpHeaderAuth'} for p in PROFILES}
    if set(state) != set(PROFILES):
        raise ValueError('Finite credential receipt required')
    save(receipt, state)  # Stable identities survive an interrupted CLI import.
    with sqlite3.connect('file:/srv/state/automation/n8n/database.sqlite?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        projects = db.execute("SELECT p.id FROM project p JOIN project_relation r ON r.projectId=p.id "
            "JOIN user u ON u.id=r.userId WHERE p.type='personal' AND u.roleSlug='global:owner' "
            "AND r.role='project:personalOwner'").fetchall()
        if len(projects) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', projects[0][0]):
            raise ValueError('Exact n8n owner project required')
        project = projects[0][0]
        incoming = []
        for profile, credential in state.items():
            if (not re.fullmatch(r'[a-f0-9]{16}', credential['id'])
                    or credential != {'id': credential['id'], 'name': 'HomeCompute retrieval · ' + profile, 'type': 'httpHeaderAuth'}):
                raise ValueError('Fixed credential identity required')
            existing = db.execute('SELECT id,name,type FROM credentials_entity WHERE id=? OR name=?',
                                  (credential['id'], credential['name'])).fetchall()
            if existing and existing != [(credential['id'], credential['name'], credential['type'])]:
                raise ValueError('Credential collision refused')
            if not existing:
                incoming.append({**credential, 'data': {'name': 'Authorization', 'value': 'Bearer ' + tokens['tokens'][profile]}})
        prefix = '/tmp/homecompute-retrieval-' + secrets.token_hex(8)
        imports, exported = prefix + '-in.json', prefix + '-out.json'
        node = ['docker', 'exec', '-i', N8N, 'node', '-e']
        create = "require('fs').writeFileSync(process.argv[1],require('fs').readFileSync(0),{mode:0o600,flag:'wx'})"
        try:
            if incoming:
                command(node + [create, imports], json.dumps(incoming))
                command(['docker', 'exec', N8N, 'n8n', 'import:credentials', '--input=' + imports, '--projectId=' + project])
            command(node + [create, exported], '')
            for profile, credential in state.items():
                command(['docker', 'exec', N8N, 'n8n', 'export:credentials', '--id=' + credential['id'], '--decrypted', '--output=' + exported])
                raw = command(node + ["process.stdout.write(require('fs').readFileSync(process.argv[1]))", exported])
                saved = json.loads(raw)
                expected = {'name': 'Authorization', 'value': 'Bearer ' + tokens['tokens'][profile]}
                if len(saved) != 1 or saved[0]['id'] != credential['id'] or saved[0]['data'] != expected:
                    raise ValueError('Encrypted credential readback differs')
                encrypted = db.execute('SELECT data FROM credentials_entity WHERE id=?', (credential['id'],)).fetchone()
                owners = db.execute('SELECT projectId,role FROM shared_credentials WHERE credentialsId=?', (credential['id'],)).fetchall()
                if not encrypted or tokens['tokens'][profile] in encrypted[0] or owners != [(project, 'credential:owner')]:
                    raise ValueError('Credential encryption or ownership differs')
        finally:
            command(node + ["for(const p of process.argv.slice(1))try{require('fs').unlinkSync(p)}catch(e){if(e.code!=='ENOENT')throw e}", imports, exported])
    owner = pwd.getpwnam('homecompute-openclaw')
    os.chown(token_path, owner.pw_uid, owner.pw_gid)
    return state


def patch_caddy(value: str) -> str:
    if '@workflow_retrieval' in value:
        if 'reverse_proxy 172.28.200.1:18795' not in value or 'remote_ip 172.28.201.2' not in value:
            raise ValueError('Existing workflow route differs')
        return value
    marker = '(ai_api) {'
    if value.count(marker) != 1:
        raise ValueError('Exact AI gateway snippet required')
    return value.replace(marker, marker + '\n' + ROUTE, 1)


def install(source: Path) -> dict:
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'home-core':
        raise ValueError('Run as root on home-core')
    os.umask(0o077)
    ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    (ROOT / 'scripts').mkdir(mode=0o755, exist_ok=True)
    (ROOT / 'config').mkdir(mode=0o755, exist_ok=True)
    for directory in (ROOT, ROOT / 'scripts', ROOT / 'config'):
        directory.chmod(0o755)
    for name in FILES:
        shutil.copyfile(source / 'scripts' / name, ROOT / 'scripts' / name)
        (ROOT / 'scripts' / name).chmod(0o644)
    shutil.copyfile('/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt', ROOT / 'config/embedding-ca.crt')
    (ROOT / 'config/embedding-ca.crt').chmod(0o644)
    state = provision_credentials()
    owner = pwd.getpwnam('homecompute-openclaw')
    archive = CONFIG / 'retrieval'
    if not archive.exists():
        archive.mkdir(mode=0o700)
        os.chown(archive, owner.pw_uid, owner.pw_gid)
    if archive.is_symlink() or archive.stat().st_uid != owner.pw_uid or archive.stat().st_mode & 0o077:
        raise ValueError('Private service-owned retrieval archive required')
    (ROOT / 'firewall.sh').write_text(FIREWALL)
    (ROOT / 'firewall.sh').chmod(0o700)
    firewall = f'''[Unit]
Description=Private workflow retrieval firewall
After=docker.service firewall.service homecompute-automation-network.service
PartOf=firewall.service docker.service homecompute-automation-network.service
[Service]
Type=oneshot
RemainAfterExit=yes
Environment=PATH={BIN}
ExecStart={BIN}/bash {ROOT}/firewall.sh
[Install]
WantedBy=multi-user.target
'''
    server = f'''[Unit]
Description=Isolated scheduled workflow embedding retrieval
After=network-online.target docker.service homecompute-workflow-retrieval-firewall.service
Requires=homecompute-workflow-retrieval-firewall.service
[Service]
User=homecompute-openclaw
Group=homecompute-openclaw
UMask=0077
ExecStart={BIN}/python3 {ROOT}/scripts/workflow-retrieval-server.py --config {CONFIG}/workflow-retrieval.json --state {CONFIG}/retrieval
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths={CONFIG}/retrieval
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=256M
TasksMax=32
[Install]
WantedBy=multi-user.target
'''
    UNITS.mkdir(exist_ok=True)
    for name, unit in [('homecompute-workflow-retrieval-firewall', firewall), ('homecompute-workflow-retrieval', server)]:
        target = UNITS / (name + '.service')
        target.write_text(unit); target.chmod(0o644)
        wants = UNITS / 'multi-user.target.wants'
        wants.mkdir(exist_ok=True)
        link = wants / target.name
        if link.is_symlink() and link.resolve() != target:
            raise ValueError('Unmanaged service unit refused')
        if not link.exists():
            link.symlink_to(target)
    command(['systemd-analyze', 'verify', str(UNITS / 'homecompute-workflow-retrieval.service'), str(UNITS / 'homecompute-workflow-retrieval-firewall.service')])
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'restart', 'homecompute-workflow-retrieval-firewall.service', 'homecompute-workflow-retrieval.service'])
    mounts = json.loads(command(['docker', 'inspect', 'homecompute-control-plane-caddy-1']))[0]['Mounts']
    paths = [Path(row['Source']) for row in mounts if row['Destination'] == '/etc/caddy/Caddyfile']
    if len(paths) != 1:
        raise ValueError('Exact gateway mount required')
    path = paths[0]
    before = path.read_text(); after = patch_caddy(before)
    if after != before:
        command(['docker', 'exec', '-i', 'homecompute-control-plane-caddy-1', 'caddy', 'validate', '--config', '/dev/stdin', '--adapter', 'caddyfile'], after)
        backup = CONFIG / ('workflow-caddy-backup-' + secrets.token_hex(6))
        backup.write_text(before); backup.chmod(0o600)
        if path.read_text() != before:
            raise ValueError('Gateway changed during validation')
        path.write_text(after)
        command(['docker', 'restart', 'homecompute-control-plane-caddy-1'])
    return {'credentials': state, 'gateway_changed': after != before, 'service_installed': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.source)))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        raise SystemExit('Workflow retrieval setup failed; private state retained for reconciliation.') from None
