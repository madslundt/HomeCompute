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


def patch(caddy, current_homepage, source_caddy, source_homepage):
    blocks = re.findall(r'^agents\.home\.arpa \{\n.*?^\}\n', source_caddy, re.M | re.S)
    if len(blocks) != 1 or 'handle_path /hermes/*' not in blocks[0]:
        raise ValueError('canonical-Hermes-source-shape')
    block = blocks[0]
    if block not in caddy:
        previous = block.replace('\t\t\t\t\t# Avoid stale sockets across the native forwarding chain.\n'
                                 '\t\t\t\t\tkeepalive off\n', '', 1)
        candidates = [value for value in (LEGACY_OFFICE, previous) if caddy.count(value) == 1]
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
