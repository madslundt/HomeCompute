#!/usr/bin/env python3
"""Restore the native authenticated dashboard forward after owner sandbox startup."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import time


def protected_api(port=18796):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/api/config', headers={'Host': 'agents.home.arpa'})
        response = connection.getresponse()
        response.read(65536)
        return response.status == 401
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def launch_forward():
    path = Path.home() / '.local/state/homecompute/hermes-speech/forward.log'
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('real private forwarding log required')
    with path.open('ab') as log:
        os.chmod(path, 0o600)
        return subprocess.Popen(['openshell', '--gateway', 'nemoclaw', '--gateway-endpoint',
            'https://127.0.0.1:8080', '--workspace', 'default', 'forward', 'service',
            'hermes-personal-v23', '--target-port', '18796', '--target-host', '127.0.0.1',
            '--local', '127.0.0.1:18796'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def ensure_forward(probe=protected_api, run=subprocess.run, launch=launch_forward, wait=time.sleep):
    if probe():
        return False
    env = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
           'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
    result = run(['nemohermes', 'hermes-personal-v23', 'recover'], env=env,
                 capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('native authenticated dashboard forward remains unavailable')
    # NemoClaw reserves 8646 for its API socat listener. The Webapp has a
    # separate port, forwarded by the same native OpenShell service command.
    child = launch()
    for _ in range(40):
        if probe():
            return True
        if child.poll() is not None:
            break
        wait(0.25)
    raise RuntimeError('native authenticated Webapp forward remains unavailable')


if __name__ == '__main__':
    # Initial core onboarding precedes the separately guarded dashboard setup.
    credential = Path.home() / '.config/homecompute-hermes/secrets/dashboard-login.json'
    if credential.exists():
        while True:
            print(json.dumps({'dashboard_forward_recovered': ensure_forward(), 'native_auth_required': True}), flush=True)
            if '--watch' not in __import__('sys').argv:
                break
            time.sleep(20)
