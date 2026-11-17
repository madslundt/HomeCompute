#!/usr/bin/env python3
"""Withdraw the OpenClaw MCP alias and block its dedicated LiteLLM key."""
import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('retirement', 'retire-openclaw-core.py')
gateway = load('litellm', 'deploy-litellm-chatgpt.py')
MCP_ALIAS = (
    '  homecompute_openclaw:\n'
    '    url: https://homecompute-capabilities.home.arpa:19445/mcp\n'
    '    transport: http\n'
    '    auth_type: oauth_delegate\n'
    '    description: OpenClaw capability principal; caller supplies its role-scoped bearer\n'
    '    allow_all_keys: false\n'
    '    access_groups: [openclaw-agent-tools, all-harness-mcps]\n'
)
KEY_OPERATION = r'''
import json
import re
import sys
import urllib.parse
import urllib.request

alias = 'homecompute-agent-openclaw-synthetic-20261009'
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
headers = {'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'}

def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request('http://127.0.0.1:4000' + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)

matches = []
for page in range(1, 21):
    query = urllib.parse.urlencode({'page': page, 'size': 100, 'return_full_object': 'true'})
    result = request('/key/list?' + query)
    matches.extend(row for row in result['keys'] if row.get('key_alias') == alias)
    if page >= result['total_pages']:
        break
else:
    raise ValueError('key inventory exceeds review bound')
if len(matches) != 1 or matches[0].get('models') != ['automation-moe']:
    raise ValueError('expected one OpenClaw-only model key')
identifier = matches[0]['token']
if not re.fullmatch('[a-f0-9]{64}', identifier):
    raise ValueError('expected a hashed key identifier')
blocked = matches[0].get('blocked') is True
if sys.argv[1] == 'block' and not blocked:
    request('/key/block', {'key': identifier})
    info = request('/key/info?' + urllib.parse.urlencode({'key': identifier}))['info']
    blocked = info.get('blocked') is True
    if not blocked:
        raise ValueError('key block did not verify')
print(json.dumps({'alias': alias, 'blocked': blocked}))
'''


def key(operation):
    result = subprocess.run(
        ['docker', 'exec', '-i', gateway.CONTAINER, 'python3', '-', operation],
        input=KEY_OPERATION, text=True, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise ValueError('dedicated LiteLLM key operation failed')
    return json.loads(result.stdout)


def patch(baseline, source):
    if 'homecompute_openclaw:' in source or baseline.count(MCP_ALIAS) > 1:
        raise ValueError('unexpected OpenClaw MCP catalog shape')
    candidate = baseline.replace(MCP_ALIAS, '')
    if 'homecompute_openclaw:' in candidate:
        raise ValueError('unrecognized active OpenClaw MCP catalog entry')
    return candidate


def plan(revision):
    deployed = (gateway.STATE / 'deployed-revision').read_text().strip()
    release = gateway.RELEASES / deployed
    if (not gateway.SHA.fullmatch(deployed)
            or Path('/srv/homecompute/current').resolve() != release
            or gateway.run('git', '-C', str(release), 'rev-parse', 'HEAD').strip() != deployed):
        raise ValueError('deployed release must match its marker')
    gateway.run('git', '-C', str(release), 'diff', '--exit-code', 'HEAD', '--',
                str(gateway.COMPOSE), str(gateway.CONFIG))
    container = gateway.inspection()
    before = gateway.mounted_source(container)
    if (before != release / gateway.CONFIG
            and not re.fullmatch(f'/var/lib/homecompute/litellm-(?:chatgpt|retirement)/{deployed}-[a-f0-9]{{40}}.yaml',
                                 str(before))):
        raise ValueError('unrecognized LiteLLM config mount')
    original = core.protected(before).decode()
    candidate = patch(original, (ROOT / gateway.CONFIG).read_text())
    after = before if candidate == original else (
        gateway.STATE / 'litellm-retirement' / f'{deployed}-{revision}.yaml')
    report = {'revision': revision, 'container': container['Id'],
              'before': str(before), 'after': str(after),
              'config_sha256': gateway.verify_container(release, before, container),
              'key': key('status')}
    return report, release, before, after, candidate, container['Image']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--apply-plan-sha256')
    args = parser.parse_args()
    core.release(args.revision)
    with core.LOCK.open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report, release, before, after, candidate, image = plan(args.revision)
        digest = core.digest(report)
        if not args.apply_plan_sha256:
            print(json.dumps({**report, 'plan_sha256': digest}))
            return
        if args.apply_plan_sha256 != digest:
            raise ValueError('exact reviewed gateway plan required')
        if after != before:
            gateway.install_config(after, candidate)
            gateway.change_config(release, before, after, image)
        print(json.dumps({**report, 'key': key('block'), 'applied': True}))


if __name__ == '__main__':
    main()
