#!/usr/bin/env python3
"""Provision and verify Paperclip's private automation-moe connection."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/homecompute/paperclip')
SERVICE = 'paperclip-inference'
RELAY = f'homecompute-paperclip-{SERVICE}-1'
PAPERCLIP = 'homecompute-paperclip-paperclip-1'
CADDY = 'homecompute-control-plane-caddy-1'
NETWORK = 'homecompute-paperclip'
COMPOSE_PATH = 'deploy/paperclip/compose.yaml'
MODEL = 'automation-moe'
CLIENT_CHECK = r'''
const base = "http://127.0.0.1:18080";
const model = "automation-moe";
const listing = await fetch(base + "/v1/models", { signal: AbortSignal.timeout(6000) });
if (!listing.ok) throw new Error("Model listing failed");
const ids = (await listing.json()).data?.map(entry => entry.id);
if (JSON.stringify(ids) !== JSON.stringify([model])) throw new Error("Unexpected model scope");
const forbidden = await fetch(base + "/mcp", { signal: AbortSignal.timeout(6000) });
if (forbidden.status !== 404) throw new Error("Unexpected tool route");
const response = await fetch(base + "/v1/responses", {
  method: "POST", signal: AbortSignal.timeout(50000),
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ model, input: "Reply with only ready.", max_output_tokens: 256 }),
});
if (!response.ok) throw new Error("Responses probe failed with HTTP " + response.status);
const result = await response.json();
if (result.model !== model || result.status !== "completed"
    || !result.output.some(item => item.type === "message")) throw new Error("Unexpected inference response");
console.log("Paperclip can use automation-moe through the private Responses gateway.");
'''


def existing_network(core):
    value, = json.loads(core.command('docker', 'network', 'inspect', NETWORK))
    names = {row['Name'] for row in value.get('Containers', {}).values()}
    if not value['Internal'] or names != {PAPERCLIP, CADDY}:
        raise ValueError('Expected only Paperclip and Caddy on the private bridge')


def compose(files, *args):
    command = ['docker', 'compose']
    for path in files:
        command.extend(('-f', str(path)))
    subprocess.run([*command, '--profile', 'inference', *args],
                   check=True, timeout=150)


def replace(files):
    compose(files, 'up', '-d', '--no-deps', '--force-recreate',
            '--wait', '--wait-timeout', '120', SERVICE)


def apply(core, revision):
    existing_network(core)
    egress = runpy.run_path(str(ROOT / 'scripts/deploy-paperclip-connector-egress.py'))
    running = core.command('docker', 'ps', '--filter', f'name=^{RELAY}$', '--format', '{{.Names}}')
    previous = egress['managed_files'](core, RELAY) if running == RELAY else None
    if not Path('/run/secrets/paperclip/environment').exists():
        core.protected(STATE / 'environment', private=True)
        os.environ['PAPERCLIP_SECRETS_FILE'] = str(STATE / 'environment')
    core.protected(Path('/etc/homecompute/control-plane/home-core-root.crt'))
    if STATE.is_symlink():
        raise ValueError('Paperclip state directory must be a real directory')
    STATE.mkdir(mode=0o700, exist_ok=True)
    if (STATE.stat().st_uid, STATE.stat().st_mode & 0o777) != (0, 0o700):
        raise ValueError('Paperclip credential directory must be root-only')
    credentials = runpy.run_path(str(ROOT / 'scripts/setup-assistant-context-inference-credentials.py'))
    setup = credentials['InferenceKeySetup'](key_specs={
        'paperclip': {
            'path': STATE / 'inference-key',
            'model': MODEL,
            'owner': 'paperclip-inference',
            'purpose': 'Paperclip local Responses gateway',
            'verify_mcp_denied': True,
        },
    }, root_uid=10001, root_gid=10001)
    plan = setup.plan(revision)
    setup.apply(revision, plan['plan_sha256'])
    files = (ROOT / COMPOSE_PATH,)
    if egress['MARKER'].is_file():
        files += (ROOT / egress['OVERLAY'],)
    try:
        replace(files)
        existing_network(core)
        subprocess.run(['docker', 'exec', PAPERCLIP, 'node', '--input-type=module', '-e', CLIENT_CHECK],
                       check=True, timeout=60)
    except (Exception, KeyboardInterrupt):
        if previous:
            replace(previous)
        else:
            compose(files, 'rm', '--stop', '--force', SERVICE)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    core = SimpleNamespace(**runpy.run_path(str(ROOT / 'scripts/retire-openclaw-core.py')))
    try:
        core.release(args.revision)
        os.umask(0o077)
        with core.LOCK.open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            apply(core, args.revision)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Paperclip inference setup stopped ({type(error).__name__}): {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
