#!/usr/bin/env python3
"""Plan or activate the owner-only context tools on the existing OpenClaw bridge."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = '0774a4a72f15cf9f162a7999720ce555ea42c627'
BASELINE_COMMUNICATION_SHA256 = '678e6443afe237dc14f7401aafbac3735169f48f5c49d8886b01129bb674589b'
USER = 'homecompute-openclaw'
HOME = Path('/var/lib/homecompute-openclaw')
CONFIG = HOME / 'config'
DEST = Path('/opt/homecompute/openclaw-communication')
UNIT = Path('/etc/systemd/system.attached/homecompute-openclaw-adapter.service')
STATE = Path('/var/lib/homecompute-assistant-context-bridge')
CONTEXT_SERVICE = 'homecompute-assistant-context.service'
CONTEXT_SERVICE_FRAGMENT = '/etc/systemd/system/homecompute-assistant-context.service'
CONTEXT_CLIENTS_SOURCE = '/etc/homecompute/assistant-context-clients.json'
CONTEXT_ALLOWED_CREDENTIALS = {
    'clients': CONTEXT_CLIENTS_SOURCE,
    'inference-ca': '/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt',
    'embedding': '/etc/homecompute/assistant-context-embedding.json',
    'briefing': '/etc/homecompute/assistant-context-briefing.json',
    'openclaw_worker_ssh_key': '/etc/homecompute/assistant-context-worker/id_ed25519',
    'openclaw_worker_known_hosts': '/etc/homecompute/assistant-context-worker/known_hosts',
    'delivery': '/etc/homecompute-assistant-context/delivery-auth.json',
}
ADAPTER_SERVICE = 'homecompute-openclaw-adapter.service'
CONTEXT_ENDPOINT = ('127.0.0.1', 18826)
BRIDGE_ENDPOINT = ('127.0.0.1', 18793)
CONTEXT_ARG = '--assistant-context'
CONTEXT_CONFIG = CONFIG / 'assistant-context.json'
CONTEXT_TOKEN = CONFIG / 'assistant-context-token'
NATIVE_TOKENS = CONFIG / 'native-mcp-tokens.json'
CLIENT_SOURCE_CANDIDATE = Path('/etc/homecompute/assistant-context-clients.json')
SYSTEMD_CREDENTIAL = Path('/run/credentials/homecompute-assistant-context.service/clients')
RENDERER = DEST / 'scripts/setup-openclaw-core-communication.py'
MANAGED_FILES = {
    'scripts/openclaw-communication.py': {
        'source': ROOT / 'scripts/openclaw-communication.py',
        'target': DEST / 'scripts/openclaw-communication.py',
        'before': BASELINE_COMMUNICATION_SHA256,
    },
    'scripts/assistant_context_tools.py': {
        'source': ROOT / 'scripts/assistant_context_tools.py',
        'target': DEST / 'scripts/assistant_context_tools.py',
        'before': None,
    },
}
CONTEXT_TOOL_NAMES = frozenset({
    'homecompute_context_search', 'homecompute_context_today', 'homecompute_context_source',
    'homecompute_context_decision', 'homecompute_context_feedback', 'homecompute_context_memory',
    'homecompute_context_briefing', 'homecompute_context_routine', 'homecompute_context_delegate',
    'homecompute_context_improvements',
})
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{40,256}\Z')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def run(args: list[str], *, check: bool = True, timeout: int = 30,
        binary: bool = False) -> str | bytes:
    result = subprocess.run(args, capture_output=True, text=not binary, timeout=timeout)
    if check and result.returncode:
        raise ValueError('fixed bridge lifecycle command failed; inspect private state')
    return result.stdout if binary else result.stdout.strip()


def _safe_file(path: Path, uid: int | set[int], mode: int | set[int], limit: int = 65536) -> bytes:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('private file symlinks refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        uids = uid if isinstance(uid, set) else {uid}
        modes = mode if isinstance(mode, set) else {mode}
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in uids
                or stat.S_IMODE(info.st_mode) not in modes or info.st_size > limit):
            raise ValueError('private bridge file owner or mode differs')
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise ValueError('private bridge file exceeds budget')
        return raw
    finally:
        os.close(fd)


def _safe_json(path: Path, uid: int, mode: int = 0o600) -> dict[str, Any]:
    try:
        value = json.loads(_safe_file(path, uid, mode))
    except (ValueError, UnicodeError) as error:
        raise ValueError('private bridge JSON is invalid') from error
    if not isinstance(value, dict):
        raise ValueError('private bridge JSON object required')
    return value


def _safe_directory(path: Path, uid: int, mode: int = 0o700) -> None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('private bridge directory symlink refused')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('private bridge directory owner or mode differs')


def _write_atomic(path: Path, raw: bytes, uid: int, gid: int, mode: int) -> None:
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError('managed bridge path is not a regular managed path')
    if path.exists():
        old = path.lstat()
        if not stat.S_ISREG(old.st_mode) or old.st_uid != uid or old.st_nlink != 1:
            raise ValueError('managed bridge file owner differs')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _remove_exact(path: Path, expected_hash: str | None) -> None:
    if expected_hash is None:
        if path.exists() or path.is_symlink():
            raise ValueError('unexpected bridge file blocks rollback')
        return
    if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected_hash:
        raise ValueError('later bridge edits block rollback')
    path.unlink()


@dataclass(frozen=True)
class Layout:
    root: Path = ROOT
    home: Path = HOME
    config: Path = CONFIG
    destination: Path = DEST
    unit: Path = UNIT
    state: Path = STATE
    renderer: Path = RENDERER
    context_token: Path = CONTEXT_TOKEN
    context_config: Path = CONTEXT_CONFIG
    native_tokens: Path = NATIVE_TOKENS
    credential_source: Path = CLIENT_SOURCE_CANDIDATE
    systemd_credential: Path = SYSTEMD_CREDENTIAL


class BridgeLifecycle:
    """Hash-bound installer for two code files, one client file, and one unit argument."""

    def __init__(self, *, layout: Layout = Layout(), command: Callable[..., str | bytes] = run,
                 service_uid: int | None = None, service_gid: int | None = None,
                 root_uid: int = 0, root_gid: int = 0,
                 releases_dir: Path = Path('/srv/homecompute/releases'),
                 context_health: Callable[[str], dict[str, Any]] | None = None,
                 tool_inventory: Callable[[str], set[str]] | None = None,
                 renderer: Callable[[], str] | None = None):
        self.layout = layout
        self.command = command
        self.service_uid = service_uid if service_uid is not None else pwd.getpwnam(USER).pw_uid
        self.service_gid = service_gid if service_gid is not None else pwd.getpwnam(USER).pw_gid
        self.root_uid, self.root_gid = root_uid, root_gid
        self.releases_dir = releases_dir
        self._health = context_health or self._context_health
        self._inventory = tool_inventory or self._tool_inventory
        self._render = renderer or self._render_installed_adapter_unit

    def published(self, revision: str) -> None:
        if (not isinstance(revision, str) or len(revision) != 40
                or any(char not in '0123456789abcdef' for char in revision)):
            raise ValueError('full lowercase published revision required')
        root = self.layout.root
        try:
            root_info = root.lstat()
        except OSError:
            raise ValueError('exact published source release is unavailable') from None
        if (root != self.releases_dir / revision
                or not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
                or root_info.st_uid != self.root_uid or stat.S_IMODE(root_info.st_mode) & 0o022):
            raise ValueError('root-owned immutable published source release required')
        if self.command(['git', '-C', str(self.layout.root), 'rev-parse', 'HEAD']) != revision:
            raise ValueError('selected source checkout revision differs')
        self.command(['git', '-C', str(self.layout.root), 'merge-base', '--is-ancestor', revision, 'origin/master'])
        paths = ['scripts/openclaw-communication.py', 'scripts/assistant_context_tools.py',
                 'scripts/setup-assistant-context-bridge.py']
        for rel in paths:
            source = self.layout.root / rel
            try:
                info = source.lstat()
                if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or info.st_uid != self.root_uid or stat.S_IMODE(info.st_mode) & 0o022):
                    raise ValueError('published context bridge source ownership or mode differs')
                tracked = self.command(['git', '-C', str(root), 'show', f'{revision}:{rel}'], binary=True)
                local = source.read_bytes()
            except (OSError, subprocess.SubprocessError):
                raise ValueError('selected published context bridge source cannot be verified') from None
            if not isinstance(tracked, bytes) or local != tracked:
                raise ValueError('selected context bridge source differs from published bytes')

    def _adapter_state(self) -> str:
        return self.command(['systemctl', 'is-active', ADAPTER_SERVICE], check=False)

    def _require_context_service(self) -> None:
        if self.command(['systemctl', 'is-active', CONTEXT_SERVICE], check=False) != 'active':
            raise ValueError('assistant context service must be active and authenticated')

    def _loaded_client_bytes(self) -> bytes:
        fragment = self.command(['systemctl', 'show', '--property=FragmentPath', '--value', CONTEXT_SERVICE])
        dropins = self.command(['systemctl', 'show', '--property=DropInPaths', '--value', CONTEXT_SERVICE])
        if fragment != CONTEXT_SERVICE_FRAGMENT or dropins:
            raise ValueError('context service fragment or drop-ins differ from the exact Nix unit')
        rendered = self.command(['systemctl', 'cat', CONTEXT_SERVICE])
        headers = [line for line in rendered.splitlines() if line.startswith('# ')]
        if (len(headers) != 1 or not re.fullmatch(
                r'# /etc/systemd/system/homecompute-assistant-context\.service(?: -> /nix/store/[0-9a-z]{32}-unit-homecompute-assistant-context\.service/homecompute-assistant-context\.service)?',
                headers[0])):
            raise ValueError('context service cat output is not one exact source fragment')
        credential_directives = []
        section = ''
        for line in rendered.splitlines():
            if not line or line.startswith(('#', ';')) or '=' not in line:
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1]
                continue
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1]
                continue
            directive, value = line.split('=', 1)
            if directive in {'LoadCredential', 'LoadCredentialEncrypted'}:
                if section != 'Service' or directive != 'LoadCredential' or not value:
                    raise ValueError('context service credential directives are malformed')
                credential_directives.append((directive, value))
        expected_clients = 'clients:' + str(self.layout.credential_source)
        allowed_order = [f'{name}:{path}' for name, path in CONTEXT_ALLOWED_CREDENTIALS.items()]
        allowed_order[0] = expected_clients
        actual = [value for _directive, value in credential_directives]
        if (not actual or actual[0] != expected_clients or len(actual) != len(set(actual))
                or any(value not in allowed_order for value in actual)
                or [value for value in allowed_order if value in actual] != actual):
            raise ValueError('context service must load clients and only declared optional credentials')
        source = self.layout.credential_source
        if not source.is_absolute() or str(source).startswith('/nix/store/'):
            raise ValueError('external context credential path required')
        source_bytes = _safe_file(source, self.root_uid, 0o600)
        loaded_bytes = _safe_file(self.layout.systemd_credential,
                                  {self.root_uid, self.service_uid}, {0o400, 0o440, 0o600})
        if not hmac.compare_digest(digest(source_bytes), digest(loaded_bytes)):
            raise ValueError('loaded context credentials differ from their external source')
        return loaded_bytes

    def _context_token(self) -> tuple[str, str]:
        raw = _safe_file(self.layout.context_token, self.service_uid, 0o600, 512)
        token = raw.decode('ascii').strip()
        if not TOKEN_PATTERN.fullmatch(token) or token.startswith('replace'):
            raise ValueError('independent external context assistant token required')
        client_file = json.loads(self._loaded_client_bytes())
        if (not isinstance(client_file, dict) or set(client_file) != {'schema_version', 'clients'}
                or type(client_file['schema_version']) is not int or client_file['schema_version'] != 1
                or not isinstance(client_file['clients'], list)):
            raise ValueError('context client credential schema differs')
        matching = [row for row in client_file['clients']
                    if isinstance(row, dict) and row.get('name') == 'hermes-owner']
        allowed_sources = {'home-assistant', 'daythread', 'aula', 'calendar'}
        if (len(matching) != 1 or set(matching[0]) - {'origin_id'} != {'name', 'token', 'role', 'principal_scope',
                'data_domain', 'visibility', 'sources'}
                or 'origin_id' in matching[0] and (not isinstance(matching[0]['origin_id'], str)
                    or re.fullmatch(r'[A-Za-z0-9._:-]{1,160}', matching[0]['origin_id']) is None)
                or matching[0]['role'] != 'assistant' or matching[0]['principal_scope'] != 'owner'
                or matching[0]['data_domain'] != 'personal' or matching[0]['visibility'] != 'private'
                or not isinstance(matching[0]['token'], str) or not TOKEN_PATTERN.fullmatch(matching[0]['token'])
                or not isinstance(matching[0]['sources'], list)
                or any(not isinstance(source, str) for source in matching[0]['sources'])
                or set(matching[0]['sources']) != allowed_sources
                or not hmac.compare_digest(matching[0]['token'], token)):
            raise ValueError('context service does not admit this independent owner assistant token')
        configured_tokens = [row.get('token') for row in client_file['clients'] if isinstance(row, dict)]
        if sum(isinstance(value, str) and hmac.compare_digest(value, token) for value in configured_tokens) != 1:
            raise ValueError('context assistant credential is not independent')
        native = _safe_json(self.layout.native_tokens, self.service_uid)
        if 'hermes-owner' not in native or not isinstance(native['hermes-owner'], str):
            raise ValueError('independent existing Hermes bridge credential required')
        other_tokens = list(native.values())
        transport_path = self.layout.config / 'transport-tokens.json'
        if transport_path.exists():
            transport = _safe_json(transport_path, self.service_uid)
            other_tokens.extend(value for value in transport.values() if isinstance(value, str))
        task_config_path = self.layout.config / 'codex-task-transport.json'
        if task_config_path.exists():
            task_config = _safe_json(task_config_path, self.service_uid)
            tokens_file = task_config.get('tokens_file')
            if isinstance(tokens_file, str):
                task_tokens_path = Path(tokens_file)
                if task_tokens_path.parent != self.layout.config:
                    raise ValueError('fixed task token file path required')
                task_tokens = _safe_json(task_tokens_path, self.service_uid)
                other_tokens.extend(value for value in task_tokens.values() if isinstance(value, str))
        if any(isinstance(value, str) and hmac.compare_digest(value, token) for value in other_tokens):
            raise ValueError('context token must differ from all existing communication credentials')
        return token, digest(raw)

    def _context_health(self, token: str) -> dict[str, Any]:
        connection = http.client.HTTPConnection(*CONTEXT_ENDPOINT, timeout=5)
        try:
            connection.request('GET', '/v1/health', headers={'Authorization': 'Bearer ' + token,
                                                              'Accept': 'application/json'})
            response = connection.getresponse()
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                raise ValueError('authenticated assistant context health check failed')
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get('ready') is not True
                    or value.get('mode') not in {'shadow', 'active'}):
                raise ValueError('authenticated assistant context health check failed')
            return {'ready': True, 'mode': value['mode']}
        finally:
            connection.close()

    def _native_tokens(self) -> dict[str, str]:
        value = _safe_json(self.layout.native_tokens, self.service_uid)
        if (not isinstance(value, dict) or not {'openclaw', 'hermes-owner'} <= set(value)
                or any(not isinstance(token, str) or len(token) < 32 for token in value.values())):
            raise ValueError('existing independent bridge tokens required')
        return value

    @staticmethod
    def _decode_tools(raw: bytes) -> set[str]:
        text = raw.decode('utf-8')
        if text.startswith('event:') or '\ndata:' in text:
            lines = [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
            if len(lines) != 1:
                raise ValueError('bounded MCP inventory response required')
            text = lines[0]
        value = json.loads(text)
        result = value.get('result') if isinstance(value, dict) and 'error' not in value else None
        tools = result.get('tools') if isinstance(result, dict) else None
        if not isinstance(tools, list) or len(tools) > 256:
            raise ValueError('bounded MCP inventory response required')
        names = [row.get('name') for row in tools if isinstance(row, dict)]
        if len(names) != len(tools) or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
            raise ValueError('invalid MCP tool inventory')
        return set(names)

    def _tool_inventory(self, token: str) -> set[str]:
        connection = http.client.HTTPConnection(*BRIDGE_ENDPOINT, timeout=8)
        try:
            connection.request('POST', '/mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}),
                               {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                                'Accept': 'application/json, text/event-stream'})
            response = connection.getresponse()
            raw = response.read(262145)
            if response.status != 200 or len(raw) > 262144:
                raise ValueError('authenticated OpenClaw bridge inventory failed')
            return self._decode_tools(raw)
        finally:
            connection.close()

    def _render_installed_adapter_unit(self) -> str:
        path = self.layout.renderer
        raw = _safe_file(path, self.root_uid, 0o644, 1024 * 1024)
        module = types.ModuleType('assistant_context_installed_renderer')
        module.__file__ = str(path)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        rendered = module.service('adapter')
        if not isinstance(rendered, str) or len(rendered.encode()) > 65536:
            raise ValueError('installed communication unit renderer returned invalid data')
        # The renderer is root-owned installed source; do not trust a source
        # renderer from this checkout to bless a host-side unit preimage.
        if not path.is_file() or digest(path.read_bytes()) != digest(raw):
            raise ValueError('installed communication renderer changed during inspection')
        return rendered

    def _baseline_unit(self) -> str:
        rendered = self._render()
        lines = rendered.splitlines(keepends=True)
        exec_lines = [line for line in lines if line.startswith('ExecStart=')]
        if len(exec_lines) != 1:
            raise ValueError('exactly one adapter ExecStart is required')
        line = exec_lines[0]
        context_pair = ' ' + CONTEXT_ARG + ' ' + str(self.layout.context_config)
        if line.count(context_pair) > 1:
            raise ValueError('duplicate assistant context ExecStart argument')
        if context_pair in line:
            line = line.replace(context_pair, '', 1)
        if CONTEXT_ARG in line:
            raise ValueError('unknown assistant context ExecStart argument')
        return ''.join(line if candidate.startswith('ExecStart=') else candidate for candidate in lines)

    def _enabled_unit(self, baseline: str) -> str:
        lines = baseline.splitlines(keepends=True)
        exec_indices = [index for index, line in enumerate(lines) if line.startswith('ExecStart=')]
        if len(exec_indices) != 1:
            raise ValueError('exactly one adapter ExecStart is required')
        index = exec_indices[0]
        lines[index] = lines[index].rstrip('\n') + ' ' + CONTEXT_ARG + ' ' + str(self.layout.context_config) + '\n'
        return ''.join(lines)

    def _config_bytes(self) -> bytes:
        return canonical({'schema_version': 1, 'token_file': str(self.layout.context_token)})

    def _status_path(self, path: Path, uid: int, allowed_before: str | None, after_hash: str,
                    mode: int) -> dict[str, Any]:
        if path.is_symlink():
            raise ValueError('managed bridge symlink refused')
        if not path.exists():
            before = None
        else:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                    or stat.S_IMODE(info.st_mode) != mode):
                raise ValueError('managed bridge file owner differs')
            before = digest(path.read_bytes())
        if before not in {allowed_before, after_hash}:
            raise ValueError('installed bridge source differs from reviewed preimage')
        return {'before': before, 'after': after_hash}

    def _receipt(self) -> dict[str, Any] | None:
        path = self.layout.state / 'receipt.json'
        if not path.exists() and not path.is_symlink():
            return None
        raw = _safe_file(path, self.root_uid, 0o600)
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise ValueError('private bridge receipt is incomplete')
        return value

    def _validate_receipt(self, receipt: dict[str, Any], state: str) -> None:
        files = receipt.get('files')
        backup = receipt.get('backup')
        hashes = (receipt.get('unit_after_sha256'), receipt.get('config_after_sha256'),
                  receipt.get('context_token_sha256'))
        if (receipt.get('state') != state or receipt.get('revision') is None
                or not isinstance(files, dict) or set(files) != set(MANAGED_FILES)
                or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                       for value in [*files.values(), *hashes])
                or not isinstance(backup, str) or not isinstance(receipt.get('hermes_tools_before'), list)
                or not isinstance(receipt.get('hermes_tools_after'), list)
                or not isinstance(receipt.get('openclaw_tools_before'), list)):
            raise ValueError('private bridge receipt is incomplete')

    def _load_metadata(self, backup: Path) -> dict[str, Any]:
        value = json.loads(_safe_file(backup / 'metadata.json', self.root_uid, 0o600))
        required_paths = set(MANAGED_FILES) | {'unit', 'config'}
        row_keys = {'backup', 'exists', 'uid', 'gid', 'mode', 'sha256'}
        if not isinstance(value, dict) or set(value) != required_paths:
            raise ValueError('private bridge before-image metadata is incomplete')
        for name, row in value.items():
            if not isinstance(row, dict) or set(row) != row_keys or not isinstance(row['exists'], bool):
                raise ValueError('private bridge before-image metadata is invalid')
            expected_uid = self.service_uid if name == 'config' else self.root_uid
            expected_gid = self.service_gid if name == 'config' else self.root_gid
            expected_mode = 0o600 if name == 'config' else 0o644
            if row['exists'] and (row['uid'], row['gid'], row['mode']) != (
                    expected_uid, expected_gid, expected_mode):
                raise ValueError('private bridge before-image permissions differ')
            if (not isinstance(row['backup'], str) or not row['backup'].isdigit()
                    or row['exists'] and (type(row['uid']) is not int or type(row['gid']) is not int
                        or type(row['mode']) is not int or not isinstance(row['sha256'], str)
                        or not re.fullmatch(r'[a-f0-9]{64}', row['sha256']))
                    or not row['exists'] and any(row[key] is not None for key in ('uid', 'gid', 'mode', 'sha256'))):
                raise ValueError('private bridge before-image metadata is invalid')
        return value

    def plan(self, revision: str) -> dict[str, Any]:
        self.published(revision)
        _safe_directory(self.layout.home, self.service_uid)
        _safe_directory(self.layout.config, self.service_uid)
        for path in (self.layout.destination, self.layout.destination / 'scripts'):
            _safe_directory(path, self.root_uid, 0o755)
        if self.layout.state.exists():
            _safe_directory(self.layout.state, self.root_uid)
        if (self.layout.state / 'reconciliation.json').exists():
            raise ValueError('failed bridge reconciliation is unfinished')
        if (self.layout.state / 'transaction.json').exists():
            raise ValueError('unfinished private bridge transaction requires operator recovery')
        receipt = self._receipt() if self.layout.state.exists() else None
        if receipt and receipt.get('state') in {'failed', 'rollback-incomplete'}:
            raise ValueError('failed bridge marker requires operator reconciliation')
        if receipt and receipt.get('state') not in {'applied', 'rolled-back'}:
            raise ValueError('private bridge receipt has an unsupported state')
        if self._adapter_state() != 'active':
            raise ValueError('existing adapter must be active for authenticated readiness checks')
        self._require_context_service()
        for path in (self.layout.context_token, self.layout.native_tokens,
                     self.layout.config / 'transport-tokens.json', self.layout.config / 'codex-task-transport.json'):
            if path == self.layout.context_token or path == self.layout.native_tokens:
                _safe_file(path, self.service_uid, 0o600)
            elif path.exists():
                _safe_file(path, self.service_uid, 0o600)
        token, token_hash = self._context_token()
        health = self._health(token)
        native_tokens = self._native_tokens()
        inventories = {actor: sorted(self._inventory(native_tokens[actor])) for actor in ('hermes-owner', 'openclaw')}
        if set(inventories['openclaw']) & CONTEXT_TOOL_NAMES:
            raise ValueError('OpenClaw already exposes owner context tools')
        baseline_unit = self._baseline_unit()
        enabled_unit = self._enabled_unit(baseline_unit)
        try:
            current_unit_bytes = _safe_file(self.layout.unit, self.root_uid, 0o644, 65536)
        except FileNotFoundError as error:
            raise ValueError('installed adapter unit is missing') from error
        current_unit = current_unit_bytes.decode('utf-8')
        current_unit_hash = digest(current_unit_bytes)
        before_unit_hash = digest(baseline_unit.encode())
        after_unit_hash = digest(enabled_unit.encode())
        if current_unit_hash not in {before_unit_hash, after_unit_hash}:
            raise ValueError('installed adapter unit differs from its exact renderer output')
        unit_enabled = current_unit_hash == after_unit_hash
        config_after = self._config_bytes()
        config_after_hash = digest(config_after)
        config_status = self._status_path(self.layout.context_config, self.service_uid, None,
                                          config_after_hash, 0o600)
        config_enabled = config_status['before'] == config_after_hash
        file_status = {}
        for name, row in MANAGED_FILES.items():
            source = self.layout.root / name
            target = self.layout.destination / name
            source_bytes = source.read_bytes()
            if len(source_bytes) > 1024 * 1024:
                raise ValueError('bounded context bridge source required')
            after_hash = digest(source_bytes)
            file_status[name] = self._status_path(target, self.root_uid, row['before'], after_hash, 0o644)
        files_enabled = all(row['before'] == row['after'] for row in file_status.values())
        contexts = set(inventories['hermes-owner']) & CONTEXT_TOOL_NAMES
        if contexts and contexts != CONTEXT_TOOL_NAMES:
            raise ValueError('incomplete Hermes context tool inventory')
        enabled = unit_enabled and config_enabled and files_enabled and contexts == CONTEXT_TOOL_NAMES
        if any((unit_enabled, config_enabled, files_enabled, bool(contexts))) and not enabled:
            # The only accepted mixed state is source code staged by the full
            # communication bundle before this lifecycle materializes the opt-in.
            staged_source_only = (files_enabled and not unit_enabled and not config_enabled and not contexts)
            if not staged_source_only:
                raise ValueError('incomplete context bridge activation requires reconciliation')
        if enabled:
            if not receipt or receipt.get('state') != 'applied':
                raise ValueError('active context bridge lacks its complete private apply receipt')
            self._validate_receipt(receipt, 'applied')
            if (receipt.get('files') != {name: row['after'] for name, row in file_status.items()}
                    or receipt.get('unit_after_sha256') != after_unit_hash
                    or receipt.get('config_after_sha256') != config_after_hash):
                raise ValueError('active context bridge differs from its private receipt')
        if not enabled and receipt and receipt.get('state') == 'applied':
            raise ValueError('applied context bridge was edited after its receipt')
        if not enabled and contexts:
            raise ValueError('Hermes context inventory is active without the reviewed opt-in')
        return {
            'schema_version': 1,
            'revision': revision,
            'action': 'already-active' if enabled else 'enable-owner-context',
            'adapter_state': 'active',
            'context_health': health,
            'context_token_sha256': token_hash,
            'files': file_status,
            'config': config_status,
            'unit': {'before': current_unit_hash, 'after': after_unit_hash},
            'unit_baseline_sha256': before_unit_hash,
            'hermes_tools_before': inventories['hermes-owner'],
            'openclaw_tools_before': inventories['openclaw'],
        }

    @staticmethod
    def plan_sha256(reviewed: dict[str, Any]) -> str:
        return digest(json.dumps(reviewed, sort_keys=True, separators=(',', ':')).encode())

    def _save_private(self, path: Path, raw: bytes, mode: int = 0o600) -> None:
        _write_atomic(path, raw, self.root_uid, self.root_gid, mode)

    def _snapshot(self, backup: Path, reviewed: dict[str, Any]) -> dict[str, Any]:
        _safe_directory(backup.parent, self.root_uid)
        backup.mkdir(mode=0o700)
        os.chown(backup, self.root_uid, self.root_gid)
        sources = {name: self.layout.destination / name
                   for name in MANAGED_FILES}
        sources.update({'unit': self.layout.unit, 'config': self.layout.context_config})
        metadata = {}
        for index, (name, target) in enumerate(sources.items()):
            if target.exists():
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('private before-image target is not a regular file')
                raw = target.read_bytes()
                metadata[name] = {'backup': str(index), 'exists': True, 'uid': info.st_uid,
                                  'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode),
                                  'sha256': digest(raw)}
                self._save_private(backup / str(index), raw)
            else:
                metadata[name] = {'backup': str(index), 'exists': False, 'uid': None, 'gid': None,
                                  'mode': None, 'sha256': None}
        self._save_private(backup / 'metadata.json', canonical(metadata))
        return metadata

    def _write_managed(self, target: Path, raw: bytes, uid: int, gid: int, mode: int) -> None:
        _write_atomic(target, raw, uid, gid, mode)

    def _check_hash(self, path: Path, expected: str | None) -> None:
        if expected is None:
            if path.exists() or path.is_symlink():
                raise ValueError('concurrent bridge file creation detected')
        elif path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError('concurrent bridge file change detected')

    def _restore(self, backup: Path, metadata: dict[str, Any], after_hashes: dict[str, str | None],
                 *, guard_after: bool = True) -> None:
        paths = {name: self.layout.destination / name for name in MANAGED_FILES}
        paths.update({'unit': self.layout.unit, 'config': self.layout.context_config})
        if guard_after:
            for name, path in paths.items():
                after = after_hashes[name]
                before = metadata[name]['sha256'] if metadata[name]['exists'] else None
                if path.is_symlink():
                    raise ValueError('changed bridge symlink blocks rollback')
                if not path.exists():
                    current = None
                elif not path.is_file():
                    raise ValueError('changed bridge file type blocks rollback')
                else:
                    current = digest(path.read_bytes())
                if current not in {before, after}:
                    raise ValueError('concurrent bridge edit blocks automatic rollback')
        for name, path in paths.items():
            row = metadata[name]
            if guard_after:
                after = after_hashes[name]
                before = row['sha256'] if row['exists'] else None
                if path.is_symlink():
                    raise ValueError('changed bridge symlink blocks rollback')
                current = digest(path.read_bytes()) if path.exists() and path.is_file() else None
                if current not in {before, after}:
                    raise ValueError('concurrent bridge edit blocks rollback')
            if row['exists']:
                raw = (backup / row['backup']).read_bytes()
                if digest(raw) != row['sha256']:
                    raise ValueError('private before-image checksum differs')
                self._write_managed(path, raw, row['uid'], row['gid'], row['mode'])
            else:
                path.unlink(missing_ok=True)

    def _activate(self, reviewed: dict[str, Any], expected_plan: str) -> dict[str, Any]:
        if self.plan_sha256(reviewed) != expected_plan:
            raise ValueError('reviewed bridge plan hash differs')
        self.layout.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.layout.state.exists():
            raise ValueError('private bridge state directory unavailable')
        os.chown(self.layout.state, self.root_uid, self.root_gid)
        _safe_directory(self.layout.state, self.root_uid)
        lock_path = self.layout.state / 'lock'
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchown(fd, self.root_uid, self.root_gid)
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fresh = self.plan(reviewed['revision'])
            if fresh != reviewed or self.plan_sha256(fresh) != expected_plan:
                raise ValueError('installed bridge changed after plan review')
            if fresh['action'] == 'already-active':
                receipt = self._receipt()
                if receipt is None:
                    raise ValueError('active bridge receipt unavailable')
                return receipt
            backup = self.layout.state / ('before-' + str(time.time_ns()))
            metadata = self._snapshot(backup, reviewed)
            before_hashes = {name: row['before'] for name, row in reviewed['files'].items()}
            before_hashes.update({'unit': reviewed['unit']['before'], 'config': reviewed['config']['before']})
            after_hashes = {name: row['after'] for name, row in reviewed['files'].items()}
            after_hashes.update({'unit': reviewed['unit']['after'], 'config': reviewed['config']['after']})
            transaction = {'schema_version': 1, 'state': 'applying', 'revision': reviewed['revision'],
                           'plan_sha256': expected_plan, 'backup': str(backup), 'before': before_hashes,
                           'after': after_hashes, 'metadata': metadata,
                           'context_token_sha256': reviewed['context_token_sha256'],
                           'hermes_tools_before': reviewed['hermes_tools_before'],
                           'openclaw_tools_before': reviewed['openclaw_tools_before']}
            self._save_private(self.layout.state / 'transaction.json', canonical(transaction))
            stopped = False
            try:
                self.command(['systemctl', 'stop', ADAPTER_SERVICE])
                stopped = True
                # Recheck every exact preimage after the service is quiescent.
                for name, target in ((name, self.layout.destination / name) for name in MANAGED_FILES):
                    self._check_hash(target, before_hashes[name])
                self._check_hash(self.layout.unit, before_hashes['unit'])
                self._check_hash(self.layout.context_config, before_hashes['config'])
                for name, row in MANAGED_FILES.items():
                    source = self.layout.root / name
                    raw = source.read_bytes()
                    if digest(raw) != reviewed['files'][name]['after']:
                        raise ValueError('published bridge source changed during activation')
                    previous = metadata[name]
                    uid = previous['uid'] if previous['exists'] else self.root_uid
                    gid = previous['gid'] if previous['exists'] else self.root_gid
                    mode = previous['mode'] if previous['exists'] else 0o644
                    target = self.layout.destination / name
                    self._check_hash(target, before_hashes[name])
                    self._write_managed(target, raw, uid, gid, mode)
                self._check_hash(self.layout.context_config, before_hashes['config'])
                self._write_managed(self.layout.context_config, self._config_bytes(),
                                    self.service_uid, self.service_gid, 0o600)
                unit_bytes = self._enabled_unit(self._baseline_unit()).encode()
                if digest(unit_bytes) != reviewed['unit']['after']:
                    raise ValueError('installed unit renderer changed during activation')
                self._check_hash(self.layout.unit, before_hashes['unit'])
                self._write_managed(self.layout.unit, unit_bytes,
                                    self.root_uid, self.root_gid, 0o644)
                self.command(['systemctl', 'daemon-reload'])
                self.command(['systemctl', 'start', ADAPTER_SERVICE])
                stopped = False
                self._wait_for_adapter_verification(
                    lambda: self._verify_after(reviewed, token_hash=reviewed['context_token_sha256']))
                receipt = {'schema_version': 1, 'state': 'applied', 'revision': reviewed['revision'],
                           'plan_sha256': expected_plan, 'backup': str(backup), 'files':
                           {name: row['after'] for name, row in reviewed['files'].items()},
                           'config_after_sha256': reviewed['config']['after'],
                           'unit_after_sha256': reviewed['unit']['after'],
                           'context_token_sha256': reviewed['context_token_sha256'],
                           'hermes_tools_before': reviewed['hermes_tools_before'],
                           'hermes_tools_after': sorted(set(reviewed['hermes_tools_before']) | CONTEXT_TOOL_NAMES),
                           'openclaw_tools_before': reviewed['openclaw_tools_before'],
                           'applied_at': time.time_ns()}
                self._save_private(self.layout.state / 'receipt.json', canonical(receipt))
                (self.layout.state / 'transaction.json').unlink(missing_ok=True)
                return receipt
            except BaseException as error:
                rollback_error = None
                try:
                    if not stopped:
                        self.command(['systemctl', 'stop', ADAPTER_SERVICE], check=False)
                    self._restore(backup, metadata, after_hashes, guard_after=True)
                    self.command(['systemctl', 'daemon-reload'])
                    self.command(['systemctl', 'start', ADAPTER_SERVICE])
                    self._wait_for_adapter_verification(lambda: self._verify_before(reviewed))
                except BaseException as failure:
                    rollback_error = failure
                failed = {**transaction, 'state': 'failed' if rollback_error is None else 'rollback-incomplete',
                          'failure_type': type(error).__name__,
                          'rollback_failure_type': type(rollback_error).__name__ if rollback_error else None,
                          'failed_at': time.time_ns()}
                self._save_private(self.layout.state / 'receipt.json', canonical(failed))
                # Keep the failed marker/backup for operator inspection.
                raise ValueError('assistant context activation failed; automatic rollback marker retained') from None
        finally:
            os.close(fd)

    def _verify_after(self, reviewed: dict[str, Any], *, token_hash: str) -> None:
        token, actual_hash = self._context_token()
        if not hmac.compare_digest(actual_hash, token_hash):
            raise ValueError('context credential changed during activation')
        health = self._health(token)
        if (health.get('ready') is not True
                or health.get('mode') != reviewed['context_health']['mode']):
            raise ValueError('context service is not ready')
        native = self._native_tokens()
        hermes = self._inventory(native['hermes-owner'])
        openclaw = self._inventory(native['openclaw'])
        expected_hermes = set(reviewed['hermes_tools_before']) | CONTEXT_TOOL_NAMES
        if hermes != expected_hermes or openclaw != set(reviewed['openclaw_tools_before']):
            raise ValueError('authenticated bridge inventory changed outside the reviewed owner context tools')

    def _verify_before(self, reviewed: dict[str, Any]) -> None:
        native = self._native_tokens()
        hermes = self._inventory(native['hermes-owner'])
        openclaw = self._inventory(native['openclaw'])
        if (hermes != set(reviewed['hermes_tools_before'])
                or openclaw != set(reviewed['openclaw_tools_before'])):
            raise ValueError('bridge inventory did not recover to its exact before state')

    def _archive_private_marker(self, path: Path, label: str) -> Path:
        archived = self.layout.state / (label + '-' + str(time.time_ns()) + '.json')
        os.link(path, archived, follow_symlinks=False)
        path.unlink()
        directory_fd = os.open(self.layout.state, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return archived

    def reconcile_failed(self, revision: str, expected_plan: str) -> dict[str, Any]:
        """Restore one exact failed activation to its recorded before-image."""
        self.published(revision)
        if not isinstance(expected_plan, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_plan):
            raise ValueError('reviewed failed bridge plan SHA-256 required')
        if not self.layout.state.exists():
            raise ValueError('private failed bridge transaction required')
        _safe_directory(self.layout.state, self.root_uid)
        lock_path = self.layout.state / 'lock'
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchown(fd, self.root_uid, self.root_gid)
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            transaction_path = self.layout.state / 'transaction.json'
            reconciliation_path = self.layout.state / 'reconciliation.json'
            receipt_path = self.layout.state / 'receipt.json'
            if reconciliation_path.exists():
                marker = json.loads(_safe_file(reconciliation_path, self.root_uid, 0o600))
                if (not isinstance(marker, dict) or set(marker) != {'schema_version', 'state',
                        'controller_revision', 'failed_revision', 'plan_sha256', 'transaction'}
                        or marker.get('schema_version') != 1
                        or marker.get('state') != 'reconciling'
                        or marker.get('controller_revision') != revision
                        or marker.get('plan_sha256') != expected_plan or not isinstance(marker.get('transaction'), dict)):
                    raise ValueError('failed bridge reconciliation marker differs')
                transaction = marker['transaction']
                failed_revision = marker['failed_revision']
                if transaction_path.exists():
                    active_transaction = json.loads(_safe_file(transaction_path, self.root_uid, 0o600))
                    if active_transaction != transaction:
                        raise ValueError('failed bridge transaction differs from its reconciliation marker')
            else:
                if not transaction_path.exists():
                    raise ValueError('failed bridge transaction marker required')
                transaction = json.loads(_safe_file(transaction_path, self.root_uid, 0o600))
                failed_revision = transaction.get('revision') if isinstance(transaction, dict) else None
                receipt = self._receipt()
                if (receipt is None or receipt.get('state') != 'rollback-incomplete'
                        or receipt.get('revision') != failed_revision or receipt.get('plan_sha256') != expected_plan):
                    raise ValueError('matching rollback-incomplete receipt required')
                receipt_fields = ('revision', 'plan_sha256', 'backup', 'before', 'after', 'metadata',
                                  'context_token_sha256', 'hermes_tools_before', 'openclaw_tools_before')
                if any(receipt.get(key) != transaction.get(key) for key in receipt_fields):
                    raise ValueError('failed receipt differs from its transaction record')
                marker = None

            required = {'schema_version', 'state', 'revision', 'plan_sha256', 'backup', 'before', 'after',
                        'metadata', 'context_token_sha256', 'hermes_tools_before', 'openclaw_tools_before'}
            names = set(MANAGED_FILES) | {'unit', 'config'}
            if (set(transaction) != required or transaction.get('schema_version') != 1
                    or transaction.get('state') != 'applying' or transaction.get('revision') != failed_revision
                    or transaction.get('plan_sha256') != expected_plan
                    or set(transaction.get('before', {})) != names or set(transaction.get('after', {})) != names
                    or any(transaction['before'][name] is not None and not re.fullmatch(r'[a-f0-9]{64}', transaction['before'][name])
                           for name in names)
                    or any(not isinstance(transaction['after'][name], str)
                           or not re.fullmatch(r'[a-f0-9]{64}', transaction['after'][name]) for name in names)
                    or not re.fullmatch(r'[a-f0-9]{64}', transaction.get('context_token_sha256', ''))
                    or not isinstance(transaction.get('hermes_tools_before'), list)
                    or not isinstance(transaction.get('openclaw_tools_before'), list)):
                raise ValueError('failed bridge transaction is malformed or outside the reviewed scope')
            if not isinstance(failed_revision, str) or not re.fullmatch(r'[a-f0-9]{40}', failed_revision):
                raise ValueError('failed bridge revision is not a full published commit')
            self.command(['git', '-C', str(self.layout.root), 'merge-base', '--is-ancestor',
                          failed_revision, revision])
            for name, row in MANAGED_FILES.items():
                source_blob = self.command(['git', '-C', str(self.layout.root), 'show',
                                            f'{failed_revision}:{name}'], binary=True)
                current_blob = self.command(['git', '-C', str(self.layout.root), 'show',
                                             f'{revision}:{name}'], binary=True)
                if (not isinstance(source_blob, bytes) or not isinstance(current_blob, bytes)
                        or digest(source_blob) != transaction['after'][name]
                        or digest(current_blob) != transaction['after'][name]):
                    raise ValueError('failed bridge managed source differs from its recorded after-image')
            if (digest(self._enabled_unit(self._baseline_unit()).encode()) != transaction['after']['unit']
                    or digest(self._config_bytes()) != transaction['after']['config']):
                raise ValueError('current bridge unit or configuration differs from the failed transaction scope')
            backup = Path(transaction['backup'])
            if backup.parent != self.layout.state or backup.is_symlink() or not backup.is_dir():
                raise ValueError('exact private bridge before-image required')
            metadata = self._load_metadata(backup)
            if metadata != transaction['metadata']:
                raise ValueError('private bridge before-image metadata changed')
            for name in names:
                row = metadata[name]
                recorded_before = row['sha256'] if row['exists'] else None
                if transaction['before'][name] != recorded_before:
                    raise ValueError('transaction preimage differs from its private before-image')

            token, token_hash = self._context_token()
            if not hmac.compare_digest(token_hash, transaction['context_token_sha256']):
                raise ValueError('context credential changed since the failed transaction')
            health = self._health(token)
            if health.get('ready') is not True:
                raise ValueError('assistant context is not ready for transaction reconciliation')

            paths = {name: self.layout.destination / name for name in MANAGED_FILES}
            paths.update({'unit': self.layout.unit, 'config': self.layout.context_config})
            status_rows = {}
            for name, path in paths.items():
                row = metadata[name]
                uid = row['uid'] if row['exists'] else (self.service_uid if name == 'config' else self.root_uid)
                mode = row['mode'] if row['exists'] else (0o600 if name == 'config' else 0o644)
                status_rows[name] = self._status_path(path, uid, transaction['before'][name],
                                                      transaction['after'][name], mode)

            before_hermes = set(transaction['hermes_tools_before'])
            before_openclaw = set(transaction['openclaw_tools_before'])
            after_hermes = before_hermes | CONTEXT_TOOL_NAMES
            if (len(before_hermes) != len(transaction['hermes_tools_before'])
                    or len(before_openclaw) != len(transaction['openclaw_tools_before'])
                    or before_hermes & CONTEXT_TOOL_NAMES):
                raise ValueError('failed bridge inventory preimage is malformed')
            adapter_state = self._adapter_state()
            if adapter_state == 'active':
                native = self._native_tokens()
                current_hermes = self._inventory(native['hermes-owner'])
                current_openclaw = self._inventory(native['openclaw'])
                if current_hermes not in (before_hermes, after_hermes) or current_openclaw != before_openclaw:
                    raise ValueError('current bridge inventories differ from both exact transaction states')
            elif adapter_state not in {'inactive', 'failed'}:
                raise ValueError('adapter is transitional during failed bridge reconciliation')

            if marker is None:
                marker = {'schema_version': 1, 'state': 'reconciling',
                          'controller_revision': revision, 'failed_revision': failed_revision,
                          'plan_sha256': expected_plan, 'transaction': transaction}
                self._save_private(reconciliation_path, canonical(marker))

            # Recheck exact hashes after committing the recovery fence and before
            # restoring any managed path.
            for name, path in paths.items():
                row = metadata[name]
                uid = row['uid'] if row['exists'] else (self.service_uid if name == 'config' else self.root_uid)
                mode = row['mode'] if row['exists'] else (0o600 if name == 'config' else 0o644)
                self._status_path(path, uid, transaction['before'][name], transaction['after'][name], mode)

            needs_restore = any(status_rows[name]['before'] != transaction['before'][name] for name in names)
            try:
                if needs_restore:
                    if adapter_state == 'active':
                        self.command(['systemctl', 'stop', ADAPTER_SERVICE])
                    self._restore(backup, metadata, transaction['after'], guard_after=True)
                    self.command(['systemctl', 'daemon-reload'])
                    self.command(['systemctl', 'start', ADAPTER_SERVICE])
                elif adapter_state != 'active':
                    self.command(['systemctl', 'start', ADAPTER_SERVICE])
                self._wait_for_adapter_verification(lambda: self._verify_before(transaction))
            except BaseException as error:
                failed = {**(self._receipt() or transaction), 'state': 'rollback-incomplete',
                          'reconciliation_failure_type': type(error).__name__,
                          'reconciliation_failed_at': time.time_ns()}
                self._save_private(receipt_path, canonical(failed))
                raise ValueError('failed bridge reconciliation remains fenced') from None

            old_receipt = self._receipt()
            if old_receipt is not None and old_receipt.get('state') == 'rollback-incomplete':
                self._archive_private_marker(receipt_path, 'receipt-failed')
            if transaction_path.exists():
                self._archive_private_marker(transaction_path, 'transaction-reconciled')
            result = {'schema_version': 1, 'state': 'rolled-back', 'revision': failed_revision,
                      'reconciled_by_revision': revision,
                      'plan_sha256': expected_plan, 'backup': str(backup),
                      'files': {name: transaction['after'][name] for name in MANAGED_FILES},
                      'config_after_sha256': transaction['after']['config'],
                      'unit_after_sha256': transaction['after']['unit'],
                      'context_token_sha256': transaction['context_token_sha256'],
                      'hermes_tools_before': transaction['hermes_tools_before'],
                      'hermes_tools_after': sorted(after_hermes),
                      'openclaw_tools_before': transaction['openclaw_tools_before'],
                      'reconciled_at': time.time_ns()}
            self._save_private(receipt_path, canonical(result))
            if reconciliation_path.exists():
                self._archive_private_marker(reconciliation_path, 'reconciliation-complete')
            return {'state': 'rolled-back', 'revision': failed_revision,
                    'reconciled_by_revision': revision, 'plan_sha256': expected_plan,
                    'receipt': str(receipt_path), 'before_images_retained': True}
        finally:
            os.close(fd)

    @staticmethod
    def _wait_for_adapter_verification(verify: Callable[[], None], timeout: float = 20.0) -> None:
        """Bound the systemd Type=simple listener race; only transport errors retry."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                verify()
                return
            except (ConnectionError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def apply(self, revision: str, expected_plan: str) -> dict[str, Any]:
        reviewed = self.plan(revision)
        return self._activate(reviewed, expected_plan)

    def rollback(self, revision: str) -> dict[str, Any]:
        self.published(revision)
        if not self.layout.state.exists():
            raise ValueError('private bridge apply receipt required')
        _safe_directory(self.layout.state, self.root_uid)
        if (self.layout.state / 'transaction.json').exists():
            raise ValueError('unfinished private transaction blocks rollback')
        receipt = self._receipt()
        if receipt is None:
            raise ValueError('private bridge apply receipt required')
        if receipt.get('state') == 'rolled-back':
            self._validate_receipt(receipt, 'rolled-back')
            backup = Path(receipt.get('backup', ''))
            if backup.parent != self.layout.state or not backup.is_dir() or backup.is_symlink():
                raise ValueError('private exact before-image directory required')
            metadata = self._load_metadata(backup)
            for name, row in metadata.items():
                path = (self.layout.destination / name
                        if name in MANAGED_FILES else self.layout.unit if name == 'unit' else self.layout.context_config)
                self._check_hash(path, row['sha256'] if row['exists'] else None)
            return receipt
        if receipt.get('state') != 'applied':
            raise ValueError('complete applied bridge receipt required')
        self._validate_receipt(receipt, 'applied')
        if receipt.get('revision') != revision:
            raise ValueError('rollback revision differs from applied source revision')
        for name, row in receipt.get('files', {}).items():
            path = self.layout.destination / name
            self._check_hash(path, row)
        self._check_hash(self.layout.context_config, receipt.get('config_after_sha256'))
        self._check_hash(self.layout.unit, receipt.get('unit_after_sha256'))
        token, token_hash = self._context_token()
        if not hmac.compare_digest(token_hash, receipt.get('context_token_sha256', '')):
            raise ValueError('later context credential change blocks rollback')
        self._health(token)
        native = self._native_tokens()
        current_hermes = self._inventory(native['hermes-owner'])
        current_openclaw = self._inventory(native['openclaw'])
        if (current_hermes != set(receipt.get('hermes_tools_after', []))
                or current_openclaw != set(receipt.get('openclaw_tools_before', []))):
            raise ValueError('later bridge inventory change blocks rollback')
        if self._adapter_state() != 'active':
            raise ValueError('active adapter required for guarded rollback')
        backup = Path(receipt.get('backup', ''))
        if backup.parent != self.layout.state or not backup.is_dir() or backup.is_symlink():
            raise ValueError('private exact before-image directory required')
        metadata = self._load_metadata(backup)
        paths = {name: self.layout.destination / name for name in MANAGED_FILES}
        paths.update({'unit': self.layout.unit, 'config': self.layout.context_config})
        self.command(['systemctl', 'stop', ADAPTER_SERVICE])
        after_hashes = {name: receipt['files'][name] for name in MANAGED_FILES}
        after_hashes.update({'unit': receipt['unit_after_sha256'], 'config': receipt['config_after_sha256']})
        try:
            self._restore(backup, metadata, after_hashes, guard_after=True)
            self.command(['systemctl', 'daemon-reload'])
            self.command(['systemctl', 'start', ADAPTER_SERVICE])
            self._verify_before(receipt)
        except BaseException:
            failed = {**receipt, 'state': 'rollback-incomplete', 'rollback_failed_at': time.time_ns()}
            self._save_private(self.layout.state / 'receipt.json', canonical(failed))
            raise ValueError('guarded rollback incomplete; private recovery marker retained') from None
        result = {**receipt, 'state': 'rolled-back', 'rolled_back_at': time.time_ns()}
        self._save_private(self.layout.state / 'receipt.json', canonical(result))
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='full published Git SHA')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true', help='apply the reviewed plan after hash confirmation')
    actions.add_argument('--rollback', action='store_true', help='restore the exact recorded before-image')
    actions.add_argument('--reconcile-failed', action='store_true', help='restore one exact failed activation to its before-image')
    parser.add_argument('--expected-plan-sha256', help='hash printed by the plan command')
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0 or sys.platform != 'linux' or socket.gethostname().split('.')[0] != 'home-core':
            raise ValueError('requires root on Linux home-core')
        if (args.apply or args.reconcile_failed) and (not args.expected_plan_sha256
                or not re.fullmatch(r'[a-f0-9]{64}', args.expected_plan_sha256)):
            raise ValueError('apply or failed reconciliation requires the reviewed plan SHA-256')
        if not (args.apply or args.reconcile_failed) and args.expected_plan_sha256:
            raise ValueError('--expected-plan-sha256 is valid only with --apply or --reconcile-failed')
        os.umask(0o077)
        lifecycle = BridgeLifecycle()
        if args.rollback:
            result = lifecycle.rollback(args.revision)
        elif args.reconcile_failed:
            result = lifecycle.reconcile_failed(args.revision, args.expected_plan_sha256)
        elif args.apply:
            result = lifecycle.apply(args.revision, args.expected_plan_sha256)
        else:
            result = lifecycle.plan(args.revision)
            result['plan_sha256'] = lifecycle.plan_sha256({key: value for key, value in result.items()
                                                           if key != 'plan_sha256'})
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ImportError) as error:
        print('Assistant context bridge refused (' + type(error).__name__ + '); inspect private state and reconcile the exact plan.',
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
