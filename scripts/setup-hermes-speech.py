#!/usr/bin/env python3
"""Plan/apply local Hermes speech through the existing managed credential boundary."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/sandbox/.local/share/homecompute/speech'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy_candidate(before, sandbox):
    after = copy.deepcopy(before)
    mcp = after['network_policies']['mcp_bridge_homecompute']['endpoints'][0]
    if (mcp['host'] != 'homecompute-capabilities.home.arpa' or mcp['port'] != 19445
            or mcp['allowed_ips'] != ['10.77.20.1']
            or mcp['credential_binding'] != {'provider': sandbox + '-mcp-homecompute'}):
        raise ValueError('verified existing owner MCP target required')
    for key, path in (('homecompute_transcription', '/v1/audio/transcriptions'),
                      ('homecompute_speech', '/v1/audio/speech')):
        desired = {'name': key, 'binaries': [{'path': '/opt/hermes/python-runtime/bin/python3.14'}],
            'endpoints': [{'host': mcp['host'], 'port': 19445, 'path': path,
                'allowed_ips': ['10.77.20.1'], 'credential_binding': mcp['credential_binding'],
                'protocol': 'rest', 'enforcement': 'enforce',
                'rules': [{'allow': {'method': 'POST', 'path': path}}]}]}
        if key in after['network_policies'] and after['network_policies'][key] != desired:
            raise ValueError('existing speech network grant differs')
        after['network_policies'][key] = desired
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--reconcile-privacy-only', action='store_true')
    args = parser.parse_args()
    if args.rollback and args.reconcile_privacy_only:
        raise ValueError('privacy reconciliation cannot roll back speech')
    core = load('speech_core', 'scripts/setup-hermes-mcp.py')
    core.published(args.revision)
    if core.call(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no']):
        raise ValueError('clean published release required')
    addons = load('speech_transfer', 'scripts/setup-hermes-addons.py')
    gates = load('speech_gates', 'scripts/connect-hermes-mcp.py')
    gates.gates()
    pin = json.loads((ROOT / 'config/hermes-speech.json').read_bytes())
    cli = ['nemohermes', pin['sandbox']]
    state = json.loads(addons.call(cli + ['status', '--json']))
    if state.get('phase') != 'Ready' or state.get('agent') != 'hermes' or state.get('hostMounts'):
        raise ValueError('ready isolated owner required')
    qualified = json.loads((ROOT / 'config/hermes-omh.json').read_bytes())['runtime_image_id']
    rows = addons.call(['docker', 'ps', '--filter', 'name=openshell-default--' + pin['sandbox'] + '-', '--format', '{{.ID}}']).decode().split()
    if len(rows) != 1 or addons.call(['docker', 'inspect', rows[0], '--format', '{{.Image}}']).decode().strip() != qualified:
        raise ValueError('qualified owner image required')
    policy_cli = ['openshell', '-g', 'nemoclaw', 'policy']
    query = ['get', pin['sandbox'], '--base', '-o', 'json']
    before = json.loads(addons.call(policy_cli + query))['policy']
    after = policy_candidate(before, pin['sandbox'])
    if args.reconcile_privacy_only and before != after:
        raise ValueError('privacy reconciliation requires already installed speech grants')
    unit = Path.home() / '.config/systemd/user/homecompute-hermes-dashboard-watch.service'
    if unit.is_symlink() or (unit.exists() and not unit.read_text().startswith(
            '# HomeCompute-managed authenticated Hermes frontend forwarding\n')):
        raise ValueError('unknown existing dashboard watcher requires review')
    for asset in pin['assets']:
        path = args.assets / asset['name']
        addons.private(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
            raise ValueError('pinned offline speech asset required')
    if not args.apply:
        print(json.dumps({'revision': args.revision, 'applied': False, 'languages': ['da', 'en'],
                          'network_changed': before != after, 'local_only': True})); return
    directory = Path.home() / '.local/state/homecompute/hermes-speech'
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('real private state directory required')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError('private state directory required')
    # Serialize policy and config transactions across operator invocations.
    import fcntl
    with (directory / 'install.lock').open('a') as lock:
        os.chmod(directory / 'install.lock', 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.reconcile_privacy_only:
            payload = (ROOT / 'deploy/hermes/speech/configure.py').read_text()
            result = addons.call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', payload],
                json.dumps({'pin': pin, 'revision': args.revision, 'reconcile_privacy_only': True}).encode())
            print(result.decode()); return
        baseline = directory / 'policy-before.json'
        selected = directory / 'policy-after.json'
        if args.rollback:
            addons.call(['sudo', '-n', 'systemctl', 'stop', 'homecompute-hermes-dashboard-relay.service'])
            if json.loads(selected.read_bytes()) != before:
                raise ValueError('policy changed since speech installation; preserve for review')
            payload = (ROOT / 'deploy/hermes/speech/configure.py').read_text()
            result = addons.call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', payload],
                json.dumps({'pin': pin, 'revision': args.revision, 'rollback': True}).encode())
            addons.call(policy_cli + ['set', pin['sandbox'], '--policy', str(baseline), '--wait', '--timeout', '45'])
        else:
            if not baseline.exists():
                for path, value in ((baseline, before), (selected, after)):
                    with path.open('x') as stream:
                        os.chmod(path, 0o600); json.dump(value, stream)
            elif json.loads(selected.read_bytes()) != after:
                raise ValueError('policy changed since the retained speech transaction')
            if json.loads(addons.call(policy_cli + query))['policy'] != before:
                raise ValueError('policy changed during preparation')
            if before != after:
                addons.call(policy_cli + ['set', pin['sandbox'], '--policy', str(selected), '--wait', '--timeout', '45'])
            if json.loads(addons.call(policy_cli + query))['policy'] != after:
                raise ValueError('speech policy readback mismatch')
            addons.REMOTE = REMOTE
            for asset in pin['assets']:
                addons.upload_asset(cli, args.assets / asset['name'], asset['sha256'])
            client = ROOT / 'deploy/hermes/speech/client.py'
            addons.upload_asset(cli, client, hashlib.sha256(client.read_bytes()).hexdigest())
            payload = (ROOT / 'deploy/hermes/speech/configure.py').read_text()
            result = addons.call(cli + ['exec', '--stdin', '--', '/opt/hermes/.venv/bin/python', '-c', payload],
                json.dumps({'pin': pin, 'revision': args.revision}).encode())
        if unit.exists():
            addons.call(['systemctl', '--user', 'stop', unit.name])
        addons.call(cli + ['stop'])
        addons.call(cli + ['start'])
        addons.call(['python3', str(ROOT / 'scripts/recover-hermes-dashboard.py')])
        rendered = (ROOT / 'deploy/hermes/hermes-dashboard-watch.service.in').read_text().replace(
            '@RELEASE@', str(ROOT)).replace('@OPERATOR_HOME@', str(Path.home()))
        if args.rollback:
            addons.call(['systemctl', '--user', 'disable', '--now', unit.name])
        else:
            if unit.exists() and unit.read_text() != rendered:
                backup = directory / ('watcher-before-' + str(time.time_ns()) + '.service')
                backup.write_bytes(unit.read_bytes()); backup.chmod(0o600)
            unit.write_text(rendered); unit.chmod(0o644)
            addons.call(['systemd-analyze', '--user', 'verify', str(unit)])
            addons.call(['systemctl', '--user', 'daemon-reload'])
            addons.call(['systemctl', '--user', 'enable', '--now', unit.name])
            addons.call(['systemctl', '--user', 'restart', unit.name])
        print(result.decode())


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else type(error).__name__) from None
