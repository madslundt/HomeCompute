#!/usr/bin/env python3
"""Guarded Hermes-only ingress rollout, retaining unrelated routes and maintenance."""
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


core = load('office_ingress', 'setup-agent-office-ingress.py')
homepage = load('hermes_homepage', 'setup-hermes-homepage.py')
SERVE_PORT = '18796'
SERVE_TARGET = 'http://10.77.20.2:18804'
SERVE_HOST = 'home-core.tail479ad.ts.net'


LEGACY_OFFICE = 'agents.home.arpa {\n\ttls internal\n\tencode zstd gzip\n\t@private remote_ip 192.168.30.0/24 192.168.10.0/24 100.64.0.0/10\n\thandle @private {\n\t\t# The observer still validates Origin and requires its own password.\n\t\t# Its native listener and both relay hops remain on private interfaces.\n\t\treverse_proxy 172.28.200.1:18802 {\n\t\t\theader_up Host 127.0.0.1:18802\n\t\t\theader_up X-HomeCompute-Office-Origin https://agents.home.arpa\n\t\t\ttransport http {\n\t\t\t\tdial_timeout 3s\n\t\t\t\tresponse_header_timeout 10s\n\t\t\t}\n\t\t}\n\t}\n\trespond "Forbidden" 403\n}\n'
PRELOAD_REDIRECT = (
    "\t\t# Vite's lazy preloads use root-relative hashed script/style URLs.\n"
    "\t\t# Return only those chunks to Hermes; Office keeps its own assets/API.\n"
    "\t\t@hermesPreload path_regexp hermes_preload ^/assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\\.(js|css)$\n"
    "\t\tredir @hermesPreload /hermes{uri} 308\n"
)
KEEPALIVE_OFF = ('\t\t\t\t\t# Avoid stale sockets across the native forwarding chain.\n'
                 '\t\t\t\t\tkeepalive off\n')
CONNECTION_LIMIT = ('\t\t\t\t\t# Reserve native tunnel slots for chat, speech and operator access.\n'
                    '\t\t\t\t\tmax_conns_per_host 8\n')
API_ROUTE = ('\t\t# Limit Hermes API requests to the Home Assistant host and the endpoints\n'
             '\t\t# used by the vendored conversation integration.\n'
             '\t\t@hermesApi {\n'
             '\t\t\tremote_ip 192.168.30.30\n'
             '\t\t\tpath /p/default/v1/models /p/default/v1/chat/completions /p/hermes-conversation-probe-does-not-exist/v1/models\n'
             '\t\t}\n'
             '\t\thandle @hermesApi {\n'
             '\t\t\treverse_proxy 172.28.200.1:18805 {\n'
             '\t\t\t\ttransport http {\n'
             '\t\t\t\t\tmax_conns_per_host 8\n'
             '\t\t\t\t\tkeepalive off\n'
             '\t\t\t\t\tdial_timeout 3s\n'
             '\t\t\t\t\tresponse_header_timeout 10m\n'
             '\t\t\t\t}\n'
             '\t\t\t}\n'
             '\t\t}\n')


def patch(caddy, current_homepage, source_caddy, source_homepage):
    blocks = re.findall(r'^agents\.home\.arpa \{\n.*?^\}\n', source_caddy, re.M | re.S)
    if (len(blocks) != 1 or 'handle_path /hermes/*' not in blocks[0]
            or PRELOAD_REDIRECT not in blocks[0] or CONNECTION_LIMIT not in blocks[0]):
        raise ValueError('canonical-Hermes-source-shape')
    block = blocks[0]
    if block not in caddy:
        no_api = block.replace(API_ROUTE, '', 1)
        previous = no_api.replace(CONNECTION_LIMIT, '', 1)
        older = previous.replace(PRELOAD_REDIRECT, '', 1)
        previous_with_api = block.replace(CONNECTION_LIMIT, '', 1)
        older_with_api = previous_with_api.replace(PRELOAD_REDIRECT, '', 1)
        candidates = [value for value in dict.fromkeys((LEGACY_OFFICE,
                      previous_with_api, older_with_api,
                      older_with_api.replace(KEEPALIVE_OFF, '', 1),
                      no_api, previous, older,
                      older.replace(KEEPALIVE_OFF, '', 1))) if caddy.count(value) == 1]
        if len(candidates) != 1:
            raise ValueError('existing-office-route-differs')
        caddy = caddy.replace(candidates[0], block, 1)
    return caddy, homepage.patch(current_homepage, source_homepage)


def serve_port_state(payload):
    """Refuse to claim a Tailscale Serve port already owned by another route."""
    if not isinstance(payload, dict):
        raise ValueError('tailscale-serve-status-invalid')
    tcp = payload.get('TCP', {})
    web = payload.get('Web', {})
    funnels = payload.get('AllowFunnel', {})
    if not all(isinstance(item, dict) for item in (tcp, web, funnels)):
        raise ValueError('tailscale-serve-status-invalid')
    keys = [name for name in web if name.rsplit(':', 1)[-1] == SERVE_PORT]
    funnel_keys = [name for name, enabled in funnels.items()
                   if enabled and name.rsplit(':', 1)[-1] == SERVE_PORT]
    if funnel_keys:
        raise ValueError('hermes-tailnet-port-funnel-refused')
    if SERVE_PORT not in tcp and not keys:
        return 'free'
    expected = {'/': {'Proxy': SERVE_TARGET}}
    if (len(keys) == 1 and keys[0].startswith(SERVE_HOST + ':')
            and tcp.get(SERVE_PORT, {}).get('HTTPS') is True
            and web[keys[0]].get('Handlers') == expected):
        return 'already-configured'
    raise ValueError('hermes-tailnet-port-occupied')


def inspect_serve_port():
    payload = core.call(['tailscale', 'serve', 'status', '--json'])
    return serve_port_state(json.loads(payload))


def rollback(receipt_dir):
    attached = Path('/etc/systemd/system.attached')
    wants = attached / 'multi-user.target.wants'
    unit = 'homecompute-hermes-dashboard-serve.service'
    receipt_dir = Path(receipt_dir)
    if (os.geteuid() != 0 or socket.gethostname() != 'home-core'
            or receipt_dir.parent != Path('/var/lib/homecompute-hermes-dashboard')
            or not receipt_dir.name.startswith('ingress-')):
        raise ValueError('managed-hermes-ingress-receipt-required')
    receipt_path = receipt_dir / 'receipt.json'
    report = json.loads(core.protected_file(receipt_path))
    unit_key = str(attached / unit)
    wants_path = wants / unit
    marker = receipt_dir / 'rollback.json'
    expected_unit = report.get('units', {}).get(unit)
    if (report.get('applied') is not True
            or marker.exists() or marker.is_symlink()
            or report.get('previous_units', {}).get(unit_key) is not None
            or report.get('before') != report.get('after')
            or not isinstance(expected_unit, str)
            or not (attached / unit).is_symlink()
            or str((attached / unit).readlink()) != expected_unit
            or not wants_path.is_symlink()
            or wants_path.readlink() != attached / unit):
        raise ValueError('hermes-serve-rollback-baseline-changed')
    if inspect_serve_port() != 'already-configured':
        raise ValueError('hermes-serve-rollback-route-changed')
    core.call(['systemctl', 'stop', unit])
    if inspect_serve_port() != 'free':
        raise ValueError('hermes-serve-rollback-port-remains-configured')
    wants_path.unlink()
    (attached / unit).unlink()
    core.call(['systemctl', 'daemon-reload'])
    marker.write_text(json.dumps({'rolled_back': True, 'unit': unit,
                                  'serve_port': SERVE_PORT}, indent=2) + '\n')
    marker.chmod(0o600)
    print(json.dumps({'rolled_back': True, 'unit': unit, 'serve_port': SERVE_PORT}))


def main():
    # Reuse the proven exact-file/mount/hash, release, lock, Nix-unit, private
    # backup and rollback lifecycle. Only the selected Hermes ingress units change.
    core.__doc__ = __doc__
    deployed = Path('/srv/homecompute/current').resolve()
    if deployed.parent != Path('/srv/homecompute/releases') or not re.fullmatch('[0-9a-f]{40}', deployed.name):
        raise ValueError('canonical-deployed-release-required')
    core.CADDY_PATHS = core.CADDY_PATHS | {deployed / 'deploy/control-plane/Caddyfile'}
    core.STATE = Path('/var/lib/homecompute-hermes-dashboard')
    core.UNITS = ('homecompute-hermes-dashboard-firewall.service',
                  'homecompute-hermes-dashboard-proxy.service',
                  'homecompute-hermes-dashboard-serve.service')
    core.office_patch = patch
    serve_state = inspect_serve_port()
    core.EXTRA_EVIDENCE = {'tailscale_serve_18796': serve_state}
    # Recheck after the selected Nix units build and before any live edits.
    if '--apply' in sys.argv:
        before_serve_state = serve_state
        original_plan = core.plan
        def guarded_plan():
            current = original_plan()
            if inspect_serve_port() != before_serve_state:
                raise ValueError('tailscale-serve-port-changed-since-plan')
            return current
        core.plan = guarded_plan
    core.main()


if __name__ == '__main__':
    try:
        if '--rollback' in sys.argv:
            if (len(sys.argv) != 3 or sys.argv[1] != '--rollback'):
                raise ValueError('usage: setup-hermes-dashboard-ingress.py --rollback RECEIPT_DIR')
            rollback(sys.argv[2])
        else:
            main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise SystemExit('Hermes ingress held: ' + detail + '. Preserve its private receipt before retry.')
