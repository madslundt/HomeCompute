#!/usr/bin/env python3
"""Restore the native authenticated dashboard forward after owner sandbox startup."""
import http.client
import json
import os
from pathlib import Path
import stat
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


def protected_ha_api(port=18642):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', '/health', headers={'Host': '127.0.0.1:8642'})
        response = connection.getresponse()
        response.read(65537)
        return response.status == 200
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


def launch_ha_api_forward():
    path = Path.home() / '.local/state/homecompute/hermes-speech/api-forward.log'
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('real private API forwarding log required')
    with path.open('ab') as log:
        os.chmod(path, 0o600)
        return subprocess.Popen(['openshell', '--gateway', 'nemoclaw', '--gateway-endpoint',
            'https://127.0.0.1:8080', '--workspace', 'default', 'forward', 'service',
            'hermes-personal-v23', '--target-port', '8642', '--target-host', '127.0.0.1',
            '--local', '127.0.0.1:18642'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)


def wait_forward(probe, launch, error, wait=time.sleep):
    child = launch()
    for _ in range(40):
        if probe():
            return True
        if child.poll() is not None:
            break
        wait(0.25)
    raise RuntimeError(error)


def ensure_forward(probe=protected_api, api_probe=protected_ha_api, run=subprocess.run,
                   launch=launch_forward, launch_api=launch_ha_api_forward, wait=time.sleep):
    changed = False
    if not probe():
        env = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
               'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
        result = run(['nemohermes', 'hermes-personal-v23', 'recover'], env=env,
                     capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError('native authenticated dashboard forward remains unavailable')
        # The Webapp and the integration API are distinct services. Port 8646
        # remains reserved by NemoClaw; the API service has its own guest-local
        # forwarded socket on 18642.
        wait_forward(probe, launch,
                     'native authenticated Webapp forward remains unavailable', wait)
        changed = True
    enabled_marker = Path.home() / '.config/homecompute-hermes/hermes-ha-api-enabled'
    api_enabled = False
    if enabled_marker.exists() or enabled_marker.is_symlink():
        info = enabled_marker.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or enabled_marker.read_text() != 'enabled\n'):
            raise ValueError('private Hermes API enable marker required')
        api_enabled = True
    if api_enabled and not api_probe():
        wait_forward(api_probe, launch_api,
                     'native Hermes API forward remains unavailable', wait)
        changed = True
    return changed


if __name__ == '__main__':
    # Initial core onboarding precedes the separately guarded dashboard setup.
    credential = Path.home() / '.config/homecompute-hermes/secrets/dashboard-login.json'
    if credential.exists():
        while True:
            print(json.dumps({'dashboard_forward_recovered': ensure_forward(), 'native_auth_required': True}), flush=True)
            if '--watch' not in __import__('sys').argv:
                break
            time.sleep(20)
