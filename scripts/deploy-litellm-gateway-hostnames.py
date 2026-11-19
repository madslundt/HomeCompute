#!/usr/bin/env python3
"""Apply the published LiteLLM hostname migration to the active Caddy edge."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/litellm-gateway-hostnames')
CONTAINER = 'homecompute-control-plane-caddy-1'
ENVIRONMENT = '/etc/homecompute/control-plane.env'
MOUNT = '/etc/caddy/Caddyfile'
CA = '/etc/homecompute/control-plane/home-core-root.crt'
MONITOR = Path('/opt/homecompute/certificate-maintenance/current/scripts/certificate-maintenance.py')
HOSTNAMES = ('llm.home.arpa', 'mcp.home.arpa')
CANONICAL_HEAD = '{$LLM_FQDN}, {$MCP_LEGACY_FQDN} {'
LEGACY_HEAD = '{$MCP_FQDN} {'


def render(active, published):
    pattern = rf'^{re.escape(CANONICAL_HEAD)}\n.*?^\}}\n'
    blocks = re.findall(pattern, published, re.MULTILINE | re.DOTALL)
    if len(blocks) != 1:
        raise ValueError('Expected one published LiteLLM virtual host')
    canonical = blocks[0]
    legacy = canonical.replace(CANONICAL_HEAD, LEGACY_HEAD, 1)
    scoped = canonical.replace(CANONICAL_HEAD, ', '.join(HOSTNAMES) + ' {', 1)
    if active.count(canonical) == 1 or active.count(scoped) == 1:
        return active
    if active.count(LEGACY_HEAD) != 1 or active.count(legacy) != 1:
        raise ValueError('Active Caddy config lacks the expected legacy LiteLLM virtual host')
    return active.replace(legacy, scoped, 1)


def install(core, path, data):
    if path.exists() or path.is_symlink():
        if core.protected(path).decode() != data:
            raise ValueError(f'Existing scoped operation file differs: {path.name}')
        return
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        candidate = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            candidate.replace(path)
        finally:
            candidate.unlink(missing_ok=True)


def active_edge(core):
    row, = json.loads(core.command('docker', 'inspect', CONTAINER))
    labels = row['Config']['Labels']
    files = labels['com.docker.compose.project.config_files'].split(',')
    if (labels.get('com.docker.compose.project') != 'homecompute-control-plane'
            or labels.get('com.docker.compose.service') != 'caddy'
            or not row['State']['Running']
            or not re.fullmatch(r'/srv/homecompute/releases/[a-f0-9]{40}/deploy/control-plane/compose.yaml', files[0])
            or any(not re.fullmatch(
                r'/var/lib/homecompute/(?:paperclip|litellm-gateway-hostnames)/[a-f0-9]{40}/caddy.json', path)
                for path in files[1:])):
        raise ValueError('Expected the released control-plane Caddy service and its managed overlays')
    compose = ['docker', 'compose', '--env-file', ENVIRONMENT]
    for path in files:
        core.protected(Path(path))
        compose.extend(('-f', path))
    if core.command(*compose, 'config', '--hash', 'caddy') != f"caddy {labels['com.docker.compose.config-hash']}":
        raise ValueError('Running Caddy differs from its rendered Compose configuration')
    mount, = [item for item in row['Mounts'] if item['Destination'] == MOUNT]
    if mount['Type'] != 'bind' or mount['RW']:
        raise ValueError('Expected a read-only Caddyfile bind mount')
    return row, compose, Path(mount['Source'])


def verify(core):
    record = json.loads((ROOT / 'config/mcp-gateway-dns.json').read_text())['record']
    if record['name'] != HOSTNAMES[0]:
        raise ValueError('Published LiteLLM DNS record differs')
    for hostname in HOSTNAMES:
        status = core.command('curl', '--silent', '--show-error', '--max-time', '8',
                              '--cacert', CA, '--resolve', f"{hostname}:443:{record['value']}",
                              '--output', '/dev/null', '--write-out', '%{http_code}',
                              f'https://{hostname}/v1/models')
        if status != '401':
            raise ValueError(f'LiteLLM HTTPS check for {hostname} returned {status}')
    # Run the installed probe to exercise the exact forced SSH command and Bun
    # trust bundle used by the systemd monitor, with no alert side effects.
    core.protected(MONITOR.resolve(strict=True))
    if runpy.run_path(str(MONITOR))['probe']() != 'https-ok':
        raise ValueError('OpenBot agent runtime still cannot reach LiteLLM over HTTPS')


def replace(core, compose):
    core.command(*compose, 'up', '-d', '--no-deps', '--pull', 'never',
                 '--wait', '--wait-timeout', '60', 'caddy')


def apply(core):
    row, current, source = active_edge(core)
    before = core.protected(source).decode()
    published = (ROOT / 'deploy/control-plane/Caddyfile').read_text()
    candidate = render(before, published)
    if candidate == before:
        verify(core)
        return
    if 'MCP_FQDN=mcp.home.arpa' not in row['Config']['Env']:
        raise ValueError('Live legacy LiteLLM hostname differs from the release convention')
    subprocess.run(['docker', 'exec', '-i', CONTAINER, '/bin/sh', '/opt/homecompute/caddy-entrypoint.sh',
                    'caddy', 'validate', '--config', '-', '--adapter', 'caddyfile'],
                   input=candidate, text=True, check=True, stdout=subprocess.DEVNULL, timeout=30)

    bundle = STATE / ROOT.name
    bundle.mkdir(parents=True, exist_ok=True, mode=0o700)
    caddyfile = bundle / 'Caddyfile'
    overlay = bundle / 'caddy.json'
    install(core, caddyfile, candidate)
    install(core, overlay, json.dumps({'services': {'caddy': {'volumes': [
        {'type': 'bind', 'source': str(caddyfile), 'target': MOUNT, 'read_only': True},
    ]}}}) + '\n')
    install(core, bundle / 'receipt.json', json.dumps({
        'previous_compose': current,
        'previous_config': str(source),
        'previous_sha256': hashlib.sha256(before.encode()).hexdigest(),
        'installed_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
    }, indent=2) + '\n')
    next_compose = [*current, '-f', str(overlay)]
    core.command(*next_compose, 'config', '--quiet')
    try:
        replace(core, next_compose)
        live, _, mounted = active_edge(core)
        if live['Image'] != row['Image'] or mounted != caddyfile or core.protected(mounted).decode() != candidate:
            raise ValueError('Active Caddy mount or image differs from the candidate')
        for attempt in range(4):
            try:
                verify(core)
                break
            except (ValueError, subprocess.CalledProcessError):
                if attempt == 3:
                    raise
                time.sleep(2)
    except (Exception, KeyboardInterrupt) as error:
        try:
            replace(core, current)
        except subprocess.SubprocessError as rollback:
            raise RuntimeError('Caddy hostname rollout and rollback failed; inspect the scoped receipt') from rollback
        raise RuntimeError('Caddy hostname rollout failed; prior Caddy config restored') from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    core = SimpleNamespace(**runpy.run_path(str(ROOT / 'scripts/retire-openclaw-core.py')))
    try:
        core.release(args.revision)
        os.umask(0o077)
        with core.LOCK.open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            apply(core)
        print(f'LiteLLM hostnames and OpenBot runtime verified from release {ROOT.name}.')
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'LiteLLM hostname rollout stopped: {error}', file=sys.stderr)
        if error.__cause__ is not None:
            print(f'Cause: {error.__cause__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
