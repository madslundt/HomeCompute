#!/usr/bin/env python3
"""Guarded Hermes-only ingress rollout, retaining unrelated routes and maintenance."""
import importlib.util
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


core = load('office_ingress', 'setup-agent-office-ingress.py')
homepage = load('hermes_homepage', 'setup-hermes-homepage.py')


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
        candidates = [value for value in dict.fromkeys((LEGACY_OFFICE,
                      block.replace(CONNECTION_LIMIT, '', 1), no_api, previous, older,
                      older.replace(KEEPALIVE_OFF, '', 1))) if caddy.count(value) == 1]
        if len(candidates) != 1:
            raise ValueError('existing-office-route-differs')
        caddy = caddy.replace(candidates[0], block, 1)
    return caddy, homepage.patch(current_homepage, source_homepage)


def main():
    # Reuse the proven exact-file/mount/hash, release, lock, Nix-unit, private
    # backup and rollback lifecycle. Only these two source-owned units change.
    core.__doc__ = __doc__
    deployed = Path('/srv/homecompute/current').resolve()
    if deployed.parent != Path('/srv/homecompute/releases') or not re.fullmatch('[0-9a-f]{40}', deployed.name):
        raise ValueError('canonical-deployed-release-required')
    core.CADDY_PATHS = core.CADDY_PATHS | {deployed / 'deploy/control-plane/Caddyfile'}
    core.STATE = Path('/var/lib/homecompute-hermes-dashboard')
    core.UNITS = ('homecompute-hermes-dashboard-firewall.service',
                  'homecompute-hermes-dashboard-proxy.service')
    core.office_patch = patch
    core.main()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise SystemExit('Hermes ingress held: ' + detail + '. Preserve its private receipt before retry.')
