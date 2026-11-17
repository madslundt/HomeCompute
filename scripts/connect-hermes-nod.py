#!/usr/bin/env python3
"""Register only the private Nod tools in the selected Hermes owner sandbox."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = 'hermes-personal-v2'
HOST = 'homecompute-capabilities.home.arpa'
PATH = '/mcp/hermes-nod'
URL = f'https://{HOST}:19445{PATH}'
KEY = 'HOMECOMPUTE_NOD_BRIDGE_TOKEN'
FILES = ('scripts/connect-hermes-nod.py', 'scripts/connect-hermes-mcp.py')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    try:
        if not re.fullmatch('[a-f0-9]{40}', args.revision):
            raise ValueError('full published revision required')
        git = ['git', '-C', str(ROOT)]
        if subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() != args.revision:
            raise ValueError('selected checkout revision differs')
        subprocess.run(git + ['merge-base', '--is-ancestor', args.revision, 'origin/master'], check=True)
        subprocess.run(git + ['diff', '--quiet', 'HEAD', '--', *FILES], check=True)
        native = runpy.run_path(str(ROOT / 'scripts/connect-hermes-mcp.py'))
        env = {**os.environ, 'NEMOCLAW_GATEWAY_PORT': '8080', 'OPENSHELL_GATEWAY': 'nemoclaw',
               'NEMOCLAW_NO_AUTO_AUDIT_LOGS': '1'}
        env.pop(KEY, None)
        cli = ['nemohermes', SANDBOX]
        call = native['call']
        state = json.loads(call(cli + ['status', '--json'], env=env))
        if state.get('agent') != 'hermes' or state.get('phase') != 'Ready' or state.get('hostMounts'):
            raise ValueError('ready isolated owner sandbox required')
        entries = json.loads(call(cli + ['mcp', 'list', '--json'], env=env))['bridges']
        matches = [row for row in entries if row.get('server') == 'nod']
        if matches and (len(matches) != 1 or matches[0].get('url') != URL
                        or matches[0].get('env', {}).get('names') != [KEY]):
            raise ValueError('existing Nod tool registration differs')
        if args.remove:
            native['enable_api_server_toolset'](SANDBOX, env, server='nod', enabled=False)
            if matches:
                call(cli + ['mcp', 'remove', 'nod', '--force'], env=env)
            print('Nod tools removed from Hermes.')
            return 0
        native['gates']()
        if not matches:
            token = sys.stdin.read(128).strip()
            if not re.fullmatch(r'nod_[A-Za-z0-9_-]{43}', token):
                raise ValueError('dedicated Nod bridge credential required on stdin')
            # The native CLI receives the secret only via its environment and
            # stores an OpenShell credential binding. Suppress native output.
            subprocess.run(cli + ['mcp', 'add', 'nod', '--url', URL, '--env', KEY,
                                  '--trusted-private-host', HOST],
                           env={**env, KEY: token}, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=240)
        native['network_setup'](SANDBOX, env, server='nod', path=PATH)
        row = native['status_row'](json.loads(call(cli + ['mcp', 'status', 'nod', '--json'], env=env)))
        if (row.get('url') != URL or row.get('provider', {}).get('credentialResolution', {}).get('ok') is not True
                or row.get('adapter', {}).get('registered') is not True
                or row.get('trustedPrivateTarget', {}).get('state') != 'match'):
            raise ValueError('native Nod credential, adapter and private pin did not verify')
        native['enable_api_server_toolset'](SANDBOX, env, server='nod')
        print('Nod tools registered for Hermes. Verify discovery and a synthetic task before use.')
        return 0
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
        print('Nod registration failed or is uncertain. Inspect native status and the private readiness records before retrying.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
