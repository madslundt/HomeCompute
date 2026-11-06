#!/usr/bin/env python3
"""Restore the native authenticated dashboard forward after owner sandbox startup."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import time


def protected_api(port=8646):
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


def ensure_forward(probe=protected_api, run=subprocess.run):
    if probe():
        return False
    env = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
           'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
    result = run(['nemohermes', 'hermes-personal-v23', 'recover'], env=env,
                 capture_output=True, timeout=120)
    if result.returncode or not probe():
        raise RuntimeError('native authenticated dashboard forward remains unavailable')
    return True


if __name__ == '__main__':
    # Initial core onboarding precedes the separately guarded dashboard setup.
    credential = Path.home() / '.config/homecompute-hermes/secrets/dashboard-login.json'
    if credential.exists():
        while True:
            print(json.dumps({'dashboard_forward_recovered': ensure_forward(), 'native_auth_required': True}), flush=True)
            if '--watch' not in __import__('sys').argv:
                break
            time.sleep(20)
