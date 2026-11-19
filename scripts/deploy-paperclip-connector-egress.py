#!/usr/bin/env python3
"""Enable or disable the allowlisted Paperclip connector proxy on home-core."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BASE = 'deploy/paperclip/compose.yaml'
OVERLAY = 'deploy/paperclip/connector-egress.compose.yaml'
STATE = Path('/var/lib/homecompute/paperclip')
MARKER = STATE / 'connector-egress-enabled'
APP = 'homecompute-paperclip-paperclip-1'
RELAY = 'homecompute-paperclip-paperclip-inference-1'
PROXY = 'paperclip-connector-egress'
LABEL = '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
RELEASE_PATH = re.compile(r'/srv/homecompute/releases/[a-f0-9]{40}/deploy/paperclip/(?:compose|connector-egress.compose)\.yaml')

CHECK = r'''
for (const host of ["my.paperclip.app", "api.github.com", "github.com"]) {
  const response = await fetch(`https://${host}/`, {method: "HEAD", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error(`Connector destination ${host} returned ${response.status}`);
  console.log(`Paperclip connector destination reachable: ${host}`);
}
const denied = await fetch("https://example.com/", {signal: AbortSignal.timeout(5000)}).catch(() => null);
if (denied !== null) throw new Error("Unlisted HTTPS destination was reachable");
console.log("Unlisted HTTPS destination blocked");
const { lookup } = await import("node:dns/promises");
const { guardedRemoteHttpFetch } = await import("/app/server/dist/services/remote-http-fetch.js");
for (const [host, ip, path, expected] of [
  ["api.githubcopilot.com", "172.30.198.2", "/mcp/", 401],
  ["github.com", "172.30.198.10", "/.well-known/oauth-authorization-server/login/oauth", 200],
]) {
  const { address } = await lookup(host);
  if (address !== ip) throw new Error(`${host} must use the private TLS relay`);
  const response = await guardedRemoteHttpFetch(`https://${host}${path}`, {
    signal: AbortSignal.timeout(8000),
  }, {allowPrivateNetwork: true, error: message => new Error(message)});
  if (response.status !== expected) throw new Error(`${host} TLS probe returned ${response.status}`);
  await response.arrayBuffer();
  console.log(`Paperclip pinned TLS route verified: ${host}`);
}
'''


def run(*args):
    subprocess.run(args, check=True, timeout=240)


def compose(files, *args, inference=False):
    command = ['docker', 'compose']
    for path in files:
        command.extend(('-f', str(path)))
    if inference:
        command.extend(('--profile', 'inference'))
    run(*command, *args)


def managed_files(core, container):
    files = tuple(Path(value) for value in core.command('docker', 'inspect', '--format', LABEL, container).split(','))
    if (not 1 <= len(files) <= 2
            or any(not RELEASE_PATH.fullmatch(str(path)) for path in files)
            or files[0].name != 'compose.yaml'
            or (len(files) == 2 and files[1] != files[0].with_name(Path(OVERLAY).name))):
        raise ValueError('Running Paperclip configuration is outside the managed release')
    for path in files:
        core.protected(path)
    return files


def start(files, service, *, inference=False):
    compose(files, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', service, inference=inference)


def check_private(core):
    if core.command('docker', 'network', 'inspect', '--format', '{{.Internal}}', 'homecompute-paperclip') != 'true':
        raise ValueError('Paperclip default bridge must be internal')
    if core.command('docker', 'inspect', '--format', '{{len .HostConfig.PortBindings}}', APP) != '0':
        raise ValueError('Paperclip must publish no ports')
    names = core.command('docker', 'inspect', '--format',
                         '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}', APP).split()
    if set(names) - {'homecompute-paperclip', 'homecompute-paperclip_connector', 'homecompute-paperclip_github-metadata'}:
        raise ValueError('Paperclip has an unexpected network attachment')
    for name in names:
        if core.command('docker', 'network', 'inspect', '--format', '{{.Internal}}', name) != 'true':
            raise ValueError('Paperclip network attachments must be internal')


def apply(core, disable):
    check_private(core)
    if STATE.is_symlink() or (STATE.stat().st_uid, STATE.stat().st_mode & 0o777) != (0, 0o700):
        raise ValueError('Paperclip credential directory must be root-only')
    if MARKER.exists() or MARKER.is_symlink():
        core.protected(MARKER, private=True)
    previous = managed_files(core, APP)
    relay_running = core.command('docker', 'ps', '--filter', f'name=^{RELAY}$', '--format', '{{.Names}}') == RELAY
    previous_relay = managed_files(core, RELAY) if relay_running else None
    secret = Path('/run/secrets/paperclip/environment')
    if not secret.exists():
        secret = STATE / 'environment'
        core.protected(secret, private=True)
    os.environ['PAPERCLIP_SECRETS_FILE'] = str(secret)
    base = (ROOT / BASE,)
    enabled = (*base, ROOT / OVERLAY)
    desired = base if disable else enabled
    compose(desired, 'config', '--quiet')
    compose(desired, 'pull', *(['paperclip'] if disable else ['paperclip', PROXY]))
    if relay_running:
        compose(desired, 'pull', 'paperclip-inference', inference=True)
    try:
        if not disable:
            start(enabled, PROXY)
        if relay_running:
            compose(previous_relay, 'rm', '--stop', '--force', 'paperclip-inference', inference=True)
        start(desired, 'paperclip')
        if relay_running:
            start(desired, 'paperclip-inference', inference=True)
        check_private(core)
        if disable:
            compose(enabled, 'rm', '--stop', '--force', PROXY)
            MARKER.unlink(missing_ok=True)
        else:
            run('docker', 'exec', APP, 'node', '--input-type=module', '-e', CHECK)
            MARKER.touch(mode=0o600)
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        if len(previous) == 2:
            start(previous, PROXY)
        if relay_running:
            compose(desired, 'rm', '--stop', '--force', 'paperclip-inference', inference=True)
        start(previous, 'paperclip')
        if previous_relay:
            start(previous_relay, 'paperclip-inference', inference=True)
        if len(previous) == 1:
            compose(enabled, 'rm', '--stop', '--force', PROXY)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--disable', action='store_true')
    args = parser.parse_args()
    core = SimpleNamespace(**runpy.run_path(str(ROOT / 'scripts/retire-openclaw-core.py')))
    try:
        core.release(args.revision)
        core.protected(Path(__file__))
        os.umask(0o077)
        with core.LOCK.open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            apply(core, args.disable)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Paperclip connector egress setup stopped ({type(error).__name__}): {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
