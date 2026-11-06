#!/usr/bin/env python3
"""Install the fixed guest relay only after native public authentication is ready."""
import argparse
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UNIT = 'homecompute-hermes-dashboard-relay.service'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or pwd.getpwnam('hermes-operator').pw_uid != 1000:
        raise ValueError('agents guest root required')
    spec = importlib.util.spec_from_file_location('setup_mcp', ROOT / 'scripts/setup-hermes-mcp.py')
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    core.published(args.revision)
    conn = http.client.HTTPConnection('127.0.0.1', 18794, timeout=10)
    conn.request('GET', '/api/config', headers={'Host': 'agents.home.arpa'})
    response = conn.getresponse(); response.read(); conn.close()
    if response.status != 401:
        raise ValueError('native unauthenticated API must reject access before exposure')
    conn = http.client.HTTPConnection('127.0.0.1', 18794, timeout=10)
    conn.request('GET', '/api/auth/providers', headers={'Host': 'agents.home.arpa'})
    response = conn.getresponse(); body = response.read(65537); conn.close()
    if response.status != 200 or len(body) > 65536 or b'"basic"' not in body:
        raise ValueError('native password provider must be available before exposure')
    source = (ROOT / 'deploy/hermes/hermes-dashboard-relay.service').read_bytes()
    path = Path('/etc/systemd/system') / UNIT
    if path.is_symlink() or (path.exists() and path.read_bytes() != source):
        raise ValueError('unknown relay unit must be reconciled explicitly')
    if not path.exists():
        with path.open('xb') as stream:
            os.chmod(path, 0o644); stream.write(source)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', UNIT], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', UNIT], check=True)
    print(json.dumps({'revision': args.revision, 'relay': UNIT, 'native_auth_required': True}))


if __name__ == '__main__':
    main()
