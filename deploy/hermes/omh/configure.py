"""Sandbox payload for the guarded, pinned HomeCompute OMH lifecycle.

Input is private JSON on stdin. Output contains no configuration or credentials.
The immutable Hermes environment and conversation/memory databases are untouched.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HOME = Path('/sandbox/.hermes')
OMH = Path('/sandbox/.omh')
ASSETS = Path('/sandbox/.local/share/homecompute/omh')
CATEGORIES = ('ultrabrain', 'deep', 'architect', 'unspecified-high', 'unspecified-low',
              'quick', 'writing', 'visual-engineering', 'artistry', 'capable', 'simple-work',
              'deep-work', 'google-search', 'x-search', 'review', 'swarm', 'forensics', 'synthesize')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def real(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink in managed path')


def write(path, data, mode=0o600):
    real(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.homecompute-new')
    with temporary.open('xb') as stream:
        os.chmod(temporary, mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run(args, env=None):
    # Installer diagnostics may contain household paths: retain them privately.
    with (ASSETS / 'operation.log').open('ab') as log:
        os.chmod(ASSETS / 'operation.log', 0o600)
        result = subprocess.run(args, env=env, stdout=log, stderr=log, timeout=240)
    if result.returncode:
        raise ValueError('OMH operation failed; inspect private operation.log')


def configure(request):
    import yaml
    pin = request['pin']
    path = HOME / 'config.yaml'
    real(path)
    before_bytes = path.read_bytes()
    before = yaml.safe_load(before_bytes)
    if before['model']['default'] != pin['model'] or before['model']['api_key'] != '${COMPATIBLE_API_KEY}':
        raise ValueError('approved deferred local model required')
    receipt = ASSETS / 'receipt.json'
    if receipt.exists():
        previous = json.loads(receipt.read_bytes())
        if previous['commit'] != pin['commit'] or previous['config_after'] != sha(before_bytes):
            raise ValueError('managed settings changed; inspect receipt before reapplying')
        if request.get('rollback'):
            backup = Path(previous['backup'])
            real(backup)
            original = backup.read_bytes()
            if backup.parent != ASSETS or sha(original) != previous['config_before']:
                raise ValueError('verified private config preimage required')
            write(path, original, 0o640)
            previous['rolled_back'] = True
            write(receipt, (json.dumps(previous, indent=2) + '\n').encode())
            return {'rolled_back': True, 'commit': pin['commit'], 'omh_data_retained': True}
        return {'applied': False, 'already_current': True, 'commit': pin['commit']}
    if request.get('rollback'):
        raise ValueError('managed add-on receipt required for rollback')
    pending_path = ASSETS / 'pending.json'
    pending = json.loads(pending_path.read_bytes()) if pending_path.exists() else None
    resumable = (pending and pending['commit'] == pin['commit']
                 and pending['config_before'] == sha(before_bytes))
    if 'omh' in before.get('plugins', {}).get('enabled', []) or (
            ((HOME / 'plugins/omh').exists() or OMH.exists()) and not resumable):
        raise ValueError('unknown existing OMH installation requires review')
    if before.get('dashboard'):
        raise ValueError('existing dashboard settings require review')
    for asset, expected in ((pin['wheel']['name'], pin['wheel']['sha256']), ('install.sh', pin['installer_sha256'])):
        p = ASSETS / asset
        real(p)
        if sha(p.read_bytes()) != expected:
            raise ValueError('pinned OMH artifact mismatch')
    backup = ASSETS / ('before-' + str(time.time_ns()) + '.yaml')
    write(backup, before_bytes)
    write(pending_path, json.dumps({'commit': pin['commit'], 'config_before': sha(before_bytes),
                                    'backup': str(backup)}).encode())
    environment = {**os.environ, 'OMH_CHANNEL': 'local', 'OMH_SOURCE_REF': pin['commit'],
                   'OMH_PACKAGE_URL': str(ASSETS / pin['wheel']['name']),
                   'OMH_PYTHON': '/opt/hermes/python-runtime/bin/python3.14',
                   'OMH_VENV_DIR': '/sandbox/.local/share/omh/venv',
                   'OMH_BIN_DIR': '/sandbox/.local/bin', 'OMH_RUN_SETUP': '0',
                   'OMH_RUN_DOCTOR': '0', 'OMH_PROVISION_PYTHON': '0',
                   'OMH_PIP_ARGS': '--no-index --no-deps', 'PIP_NO_INDEX': '1'}
    try:
        run(['sh', str(ASSETS / 'install.sh')], environment)
        cli = ['/sandbox/.local/bin/omh', '--omh-home', str(OMH), '--hermes-home', str(HOME)]
        run(cli + ['setup', '--core', '--no-interactive', '--json', '--memory-mode', 'off',
                   '--default-executor', 'hermes', '--no-menubar'])
        after = yaml.safe_load(path.read_bytes())
        # The supported setup may add only these registration fields. Everything
        # else (including native memory, browser, Mattermost and MCP) must survive.
        expected = copy.deepcopy(before)
        expected.setdefault('plugins', {}).setdefault('enabled', []).insert(0, 'omh')
        expected['plugins'].setdefault('entries', {})['omh'] = {'settings': {'omh_home': str(OMH)}}
        expected.setdefault('skills', {}).setdefault('external_dirs', []).append(str(OMH / 'skills'))
        if after != expected:
            raise ValueError('OMH setup changed settings outside its registration')
        after['plugins']['entries']['omh']['settings']['dreaming_mode'] = 'off'
        after.setdefault('delegation', {})['max_concurrent_children'] = pin['max_concurrency']
        sys.path.insert(0, '/opt/hermes')
        from plugins.dashboard_auth.basic import hash_password
        credential = request['dashboard']
        after['dashboard'] = {'public_url': pin['dashboard_url'], 'basic_auth': {
            'username': pin['dashboard_username'], 'password_hash': hash_password(credential['password']),
            'secret': credential['secret']}}
        chains = {'schema_version': 'mixture_chain_overrides/v1', 'categories': {
            category: [{'model': pin['model'], 'reasoning_effort': pin['reasoning_effort']}] for category in CATEGORIES}}
        write(OMH / 'routing/model-chains.json', (json.dumps(chains, indent=2) + '\n').encode())
        routes = {'schema_version': 'model_provider_routes/v1', 'models': {
            pin['model']: {'provider': 'custom', 'model': pin['model']}}}
        write(OMH / 'routing/model-providers.json', (json.dumps(routes, indent=2) + '\n').encode())
        profile = OMH / 'setup-profile.json'
        value = json.loads(profile.read_bytes())
        value['parallelism'] = {'default_concurrency': 1, 'global_concurrency': 1,
                                'lane_budget_default': 1, 'per_owner': {}}
        write(profile, (json.dumps(value, indent=2) + '\n').encode())
        run(cli + ['update-check', 'set', '--mode', 'off', '--json'])
        run(cli + ['theme', 'update-check', 'off'])
        encoded = yaml.safe_dump(after, sort_keys=False).encode()
        write(path, encoded, 0o640)
        record = {'commit': pin['commit'], 'version': pin['version'], 'config_before': sha(before_bytes),
                  'config_after': sha(encoded), 'backup': str(backup), 'native_memory_preserved': True,
                  'local_categories': len(CATEGORIES), 'max_concurrent_children': 1,
                  'dashboard_url': pin['dashboard_url']}
        write(receipt, (json.dumps(record, indent=2) + '\n').encode())
        return {'applied': True, **{k: v for k, v in record.items() if k != 'backup'}}
    except Exception:
        write(path, before_bytes, 0o640)
        # Preserve partially installed assets for diagnosis; do not retry blindly.
        raise


if __name__ == '__main__':
    try:
        print(json.dumps(configure(json.load(sys.stdin))))
    except Exception as error:
        # Never echo values from a secret-bearing request or config.
        import traceback
        with (ASSETS / 'failure.log').open('a') as log:
            os.chmod(ASSETS / 'failure.log', 0o600)
            traceback.print_exc(file=log)
        print('Hermes add-on held: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
