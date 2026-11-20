#!/usr/bin/env python3
"""Prepare and verify the household OpenBot's scoped LiteLLM MCP keys."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GUEST_KEY = Path.home() / ".ssh/id_ed25519_ai-services-01"
GUEST = "hermes-operator@10.77.20.2"
CONTAINER = "homecompute-openbot-partner"
SCOPES = {
    "public": {"context7": "docs-public", "tilbudstrolden": "offers-read"},
    "aula": {"aula": "aula-read"},
}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"]
GUEST_SSH = [*SSH, "-J", "home-core", "-o", "IdentitiesOnly=yes", "-i", str(GUEST_KEY), GUEST]
MODEL_DENY = ["no-default-models"]

GUEST_FILES = r'''import os, pathlib, stat, sys
action, scope = sys.argv[1:]
assert scope in {'aula', 'public'}
path = pathlib.Path.home() / '.config/homecompute-openbot-partner/secrets' / (scope + '-mcp-key')
assert not any(part.is_symlink() for part in (path, *path.parents))
if action == 'install':
    raw = sys.stdin.buffer.read(4097)
    assert raw.startswith(b'sk-') and raw.endswith(b'\n') and raw[:-1].split() == [raw[:-1]]
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    assert parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600), 'wb') as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    print('installed')
elif not path.exists():
    print('absent')
else:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as file:
        info = os.fstat(file.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
        assert stat.S_IMODE(info.st_mode) == 0o600
        raw = file.read(4097)
    assert raw.startswith(b'sk-') and raw.endswith(b'\n') and raw[:-1].split() == [raw[:-1]]
    sys.stdout.buffer.write(raw)
'''

KEY_API = r'''import json, sys, urllib.parse, urllib.request
action, name, groups, models = json.loads(sys.argv[1])
master = open('/run/secrets/litellm_master_key', encoding='utf-8').read().strip()
def request(path, data=None):
    req = urllib.request.Request('http://127.0.0.1:4000' + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)
def find():
    page = 1
    matches = []
    while True:
        query = urllib.parse.urlencode({'page': page, 'size': 100, 'return_full_object': 'true'})
        listing = request('/key/list?' + query)
        matches.extend(row for row in listing['keys'] if row.get('key_alias') == name)
        if page >= listing['total_pages']:
            return matches
        page += 1
def verify(key):
    info = request('/key/info?' + urllib.parse.urlencode({'key': key}))['info']
    policy = info.get('object_permission') or {}
    assert info['models'] == models and info['key_alias'] == name
    assert info['expires'] is None and not info.get('team_id')
    assert sorted(policy.get('mcp_access_groups') or []) == sorted(groups)
    assert not policy.get('mcp_servers') and not (policy.get('mcp_tool_permissions') or {})
if action == 'check':
    verify(sys.stdin.read().strip())
    print('verified')
elif action == 'revoke-new':
    key = sys.stdin.read().strip()
    verify(key)
    request('/key/delete', {'keys': [key]})
    print('revoked')
else:
    assert not find(), 'A matching key exists in LiteLLM; recover or revoke it before creating another'
    key = request('/key/generate', {'models': models, 'key_alias': name, 'duration': None,
        'object_permission': {'mcp_access_groups': groups},
        'metadata': {'purpose': 'Household OpenBot MCP only'}})['key']
    try:
        verify(key)
    except Exception:
        request('/key/delete', {'keys': [key]})
        raise
    print(key)
'''

VERIFY_GATEWAY = r'''import json, re, sys, urllib.error, urllib.request
base = 'https://llm.home.arpa'
key = sys.stdin.read().strip()
expected = json.loads(sys.argv[1])

def request(path, headers, data=None):
    req = urllib.request.Request(base + path, headers=headers,
        data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.headers, response.read().decode()
    except urllib.error.HTTPError as exc:
        step = data['method'] if data else 'model list'
        raise SystemExit(f'{step} HTTP {exc.code}') from None
    except urllib.error.URLError:
        raise SystemExit('gateway connection failed') from None

def list_tools(servers=None):
    headers = {'x-litellm-api-key': 'Bearer ' + key, 'accept': 'application/json, text/event-stream',
               'content-type': 'application/json'}
    if servers:
        headers['x-mcp-servers'] = ','.join(servers)
    def send(**body):
        return request('/mcp', headers, {'jsonrpc': '2.0', **body})
    session, _ = send(id=1, method='initialize', params={
        'protocolVersion': '2025-03-26', 'capabilities': {},
        'clientInfo': {'name': 'household-mcp-verification', 'version': '1'},
    })
    if session.get('mcp-session-id'):
        headers['mcp-session-id'] = session['mcp-session-id']
    send(method='notifications/initialized')
    response, body = send(id=2, method='tools/list', params={})
    if 'text/event-stream' in response.get('content-type', ''):
        messages = [json.loads(line[6:]) for line in body.splitlines() if line.startswith('data: ')]
    else:
        messages = [json.loads(body)]
    result = next((message.get('result') for message in messages if message.get('id') == 2), None)
    if not isinstance((result or {}).get('tools'), list):
        raise SystemExit('tools/list response has no tool list')
    return sorted(tool['name'] for tool in result['tools'])

def matches(tool, server):
    return tool.startswith((server + '-', server + '_'))

_, models = request('/v1/models', {'Authorization': 'Bearer ' + key})
if json.loads(models)['data']:
    raise SystemExit('model catalog is not empty')
unfiltered = list_tools()
selected = list_tools(expected)
if (not unfiltered or any(not any(matches(tool, server) for server in expected) for tool in unfiltered)
        or any(not any(matches(tool, server) for tool in unfiltered) for server in expected)
        or unfiltered != selected):
    raise SystemExit('unexpected MCP catalog')
if 'aula' in expected and any(re.search('raw_request|set_template|mark_read|send_message', name) for name in selected):
    raise SystemExit('Aula write tool present')
print(f"Verified only {', '.join(expected)} ({len(selected)} tools), with zero model access.")
'''


def run(command: list[str], *, data: str | None = None, timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(command, input=data, text=True, capture_output=True, check=False, timeout=timeout)


def checked(command: list[str], message: str, *, data: str | None = None, timeout: int = 45) -> str:
    result = run(command, data=data, timeout=timeout)
    if result.returncode:
        raise RuntimeError(message)
    return result.stdout.strip()


def require_published() -> None:
    revision = checked(["git", "-C", str(ROOT), "rev-parse", "HEAD"], "Cannot read source revision")
    published = checked(["git", "-C", str(ROOT), "ls-remote", "--exit-code", "origin", "refs/heads/master"],
                        "Cannot verify the published revision").split()[0]
    dirty = checked(["git", "-C", str(ROOT), "status", "--porcelain"], "Cannot check source state")
    if dirty or revision != published or not GUEST_KEY.is_file() or GUEST_KEY.is_symlink():
        raise RuntimeError("Use a clean checkout of current published master and the dedicated guest SSH identity")


def guest(args: list[str], message: str, *, data: str | None = None, timeout: int = 45) -> str:
    return checked([*GUEST_SSH, shlex.join(args)], message, data=data, timeout=timeout)


def lite_api(action: str, scope: str, key: str | None = None) -> str:
    params = json.dumps([action, "openbot-partner-mcp-" + scope, list(SCOPES[scope].values()), MODEL_DENY])
    remote = shlex.join(["sudo", "-n", "docker", "exec", "-i", "homecompute-control-plane-litellm-1",
                         "python3", "-c", KEY_API, params])
    return checked([*SSH, "home-core", remote],
                   f"LiteLLM could not {action} the {scope} key. Inspect its protected alias state.",
                   data=key, timeout=150)


def file_action(action: str, scope: str, key: str | None = None) -> str:
    return guest(["python3", "-c", GUEST_FILES, action, scope],
                 f"Could not {action} the protected {scope} key on the agents VM",
                 data=key + "\n" if key else None)


def check_gateway(scope: str, key: str) -> str:
    args = ["sudo", "-n", "docker", "exec", "-i", CONTAINER,
            "python3", "-c", VERIFY_GATEWAY, json.dumps(list(SCOPES[scope]))]
    result = run([*GUEST_SSH, shlex.join(args)], data=key, timeout=145)
    if result.returncode:
        statuses = ("model list", "initialize", "notifications/initialized", "tools/list")
        reasons = {"model catalog is not empty", "tools/list response has no tool list",
                   "unexpected MCP catalog", "Aula write tool present", "gateway connection failed"}
        reason = result.stderr.strip()
        prefix, _, status = reason.rpartition(" HTTP ")
        if reason in reasons or (prefix in statuses and status.isascii() and status.isdecimal()):
            raise RuntimeError(f"{scope} gateway verification failed ({reason})")
        raise RuntimeError(f"The partner container could not verify the {scope} MCP catalog or model denial")
    return result.stdout.strip()


def apply(action: str, scope: str) -> None:
    require_published()
    key = file_action("read", scope)
    if key == "absent":
        if action != "prepare":
            raise RuntimeError(f"Prepare the {scope} credential first")
        key = lite_api("create", scope)
        try:
            evidence = check_gateway(scope, key)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            lite_api("revoke-new", scope, key)
            raise
        # On an ambiguous SSH failure keep the key valid until receipt can be checked.
        # A successful write may have occurred even if the SSH response was lost.
        if file_action("install", scope, key) != "installed":
            raise RuntimeError("Credential delivery is unverified; rerun check before proceeding")
    else:
        lite_api("check", scope, key)
        evidence = check_gateway(scope, key)
    print(evidence)
    if action == "copy":
        if sys.platform != "darwin" or not sys.stdin.isatty():
            raise RuntimeError("Run copy from an interactive macOS terminal")
        checked(["/usr/bin/pbcopy"], "Could not copy the scoped header to the clipboard", data="Bearer " + key)
        print("Copied the header value. Paste it directly into OpenBot and clear the clipboard after saving.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "copy"))
    parser.add_argument("scope", choices=SCOPES)
    args = parser.parse_args()
    try:
        apply(args.action, args.scope)
    except RuntimeError as exc:
        sys.exit(str(exc))
    except (OSError, ValueError, subprocess.SubprocessError):
        sys.exit("Household MCP provisioning could not complete. Check the operator connection and try again.")
