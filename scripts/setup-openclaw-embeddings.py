#!/usr/bin/env python3
"""Plan or apply native, isolated Harrier memory and rebuild agent indexes.

Run inside the managed sandbox as its configuration owner during a quiet
gateway window; use its supported native restart after success. Default only validates a private
candidate. Existing MCP origin and bearer are reused; no new egress or cloud
credential is accepted. An interrupted reindex is retried on the next --apply.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openclaw_team_policy import ROSTER

ORIGIN = 'https://homecompute-capabilities.home.arpa:19445'
MCP_CREDENTIAL_ENV = 'HOMECOMPUTE_OPENCLAW_MCP_TOKEN'


class Refused(Exception):
    """Fixed diagnostics exclude native configuration and secrets."""


def checked(path: Path, *, directory: bool = False, managed_group: bool = False) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise Refused('symlink-path-refused')
    info = path.stat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o002
            or info.st_mode & 0o020 and not (managed_group and info.st_gid == os.getegid())
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or not directory and not managed_group and stat.S_IMODE(info.st_mode) != 0o600):
        raise Refused('owner-private-path-required')


def write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def candidate(before: dict[str, Any], state_root: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        server = before['mcp']['servers']['homecompute']
        endpoint = urlsplit(server['url'])
        bearer = server['headers']['Authorization']
        if (server.get('transport') != 'streamable-http' or endpoint.scheme != 'https'
                or endpoint.netloc != 'homecompute-capabilities.home.arpa:19445'
                or endpoint.path != '/mcp' or endpoint.query or endpoint.fragment
                or not isinstance(bearer, str) or not bearer.startswith('Bearer ')
                or not 16 <= len(bearer[7:]) <= 4096 or re.search(r'\s', bearer[7:])):
            raise Refused('trusted-memory-bridge-required')
        entries = before['agents']['entries']
        if not entries or not set(entries) <= set(ROSTER) | {'assistant'}:
            raise Refused('reviewed-agent-roster-required')
        defaults = before['agents']['defaults']
        after = copy.deepcopy(before)
        api_key: str | dict[str, str] = bearer[7:]
        if api_key.startswith('openshell:resolve:'):
            if not re.fullmatch(r'openshell:resolve:env:(?:(?:v[0-9]{1,20}|s[a-f0-9]{64})_)?'
                                + MCP_CREDENTIAL_ENV, api_key):
                raise Refused('trusted-memory-credential-required')
            # OpenShell changes revision-scoped placeholders when provider state
            # refreshes. Resolve the current managed environment at activation
            # instead of freezing the MCP adapter's previous revision in memory.
            api_key = {'source': 'env', 'provider': 'default', 'id': MCP_CREDENTIAL_ENV}
        search = copy.deepcopy(before.get('memory', {}).get('search', {}))
        search.update({'enabled': True, 'provider': 'openai-compatible', 'model': 'embedding',
                       'fallback': 'none', 'sources': ['memory'], 'extraPaths': [],
                       'experimental': {'sessionMemory': False}, 'queryInputType': 'query',
                       'documentInputType': 'document', 'outputDimensionality': 1024,
                       'remote': {'baseUrl': ORIGIN + '/v1', 'apiKey': api_key,
                                  'batch': {'enabled': False}}})
        search.setdefault('store', {}).setdefault('vector', {})['enabled'] = True
        after.setdefault('memory', {})['search'] = search
        for agent, entry in entries.items():
            workspace = Path(entry.get('workspace', defaults['workspace']))
            expected = state_root / ('workspace' if agent in {'main', 'assistant'} else 'workspace-' + agent)
            expected_agent_dir = state_root / 'agents' / agent / 'agent'
            if (workspace != expected or workspace.is_symlink()
                    or Path(entry.get('agentDir', expected_agent_dir)) != expected_agent_dir):
                raise Refused('isolated-agent-workspace-required')
            # An explicit copy prevents stale per-agent none/vector=false settings
            # from overriding the migrated root provider. Sources stay local.
            agent_search = copy.deepcopy(entry.get('memory', {}).get('search', {}))
            for key, value in search.items():
                if key not in {'store', 'query', 'cache'}:
                    agent_search[key] = copy.deepcopy(value)
            for key in ('store', 'query', 'cache'):
                if key in search:
                    section = copy.deepcopy(search[key])
                    section.update(agent_search.get(key, {}))
                    agent_search[key] = section
            agent_search.setdefault('store', {}).setdefault('vector', {})['enabled'] = True
            after['agents']['entries'][agent].setdefault('memory', {})['search'] = agent_search
        return after, sorted(entries)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise Refused('managed-memory-contract-required') from None


def native(arguments: list[str], config: Path, runner: Callable[..., Any]) -> None:
    environment = dict(os.environ, OPENCLAW_CONFIG_PATH=str(config))
    environment['NODE_OPTIONS'] = (environment.get('NODE_OPTIONS', '') + ' --max-old-space-size=384').strip()
    try:
        result = runner(['openclaw', *arguments], capture_output=True, text=True,
                        timeout=600, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('native-memory-command-unconfirmed') from None
    if result.returncode:
        raise Refused('native-memory-command-refused')


def setup(config: Path, *, apply: bool = False,
          runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    checked(config)
    checked(config.parent, directory=True)
    raw = config.read_bytes()
    before = json.loads(raw)
    after, agents = candidate(before, config.parent)
    encoded = (json.dumps(after, indent=2) + '\n').encode()
    identity = hashlib.sha256(json.dumps(after['memory']['search'], sort_keys=True).encode()).hexdigest()
    marker = config.parent / '.homecompute-memory-embedding-identity'
    if marker.exists():
        checked(marker)
    completed = marker.exists() and marker.read_text().strip() == identity
    temporary = config.parent / ('.embedding-candidate-' + str(uuid.uuid4()) + '.json')
    write(temporary, encoded)
    try:
        native(['config', 'validate', '--json'], temporary, runner)
        changed = before != after
        summary = {'agents': agents, 'config_changed': changed, 'schema_validated': True,
                   'applied': False, 'reindexed': False, 'restart_required': changed or not completed,
                   'reindex_required': changed or not completed}
        if not apply:
            return summary
        if config.read_bytes() != raw:
            raise Refused('native-config-changed-since-validation')
        if changed or not completed:
            backup_root = config.parent / 'embedding-setup-backups'
            if not backup_root.exists():
                backup_root.mkdir(mode=0o700)
            checked(backup_root, directory=True)
            backup = backup_root / str(uuid.uuid4())
            backup.mkdir(mode=0o700)
            write(backup / 'openclaw-before.json', raw)
            # Preserve the canonical conversation database and each agent's own
            # memory store. Older releases also used memory/*.sqlite.
            canonical = config.parent / 'state' / 'openclaw.sqlite'
            if canonical.exists():
                checked(canonical, managed_group=True)
                source = sqlite3.connect('file:' + str(canonical) + '?mode=ro', uri=True)
                target = backup / 'openclaw-state-before.sqlite'
                write(target, b'')
                destination = sqlite3.connect(target)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
            for agent in agents:
                database = config.parent / 'agents' / agent / 'agent' / 'openclaw-agent.sqlite'
                if database.exists():
                    checked(database, managed_group=True)
                    source = sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)
                    target = backup / ('agent-' + agent + '-before.sqlite')
                    write(target, b'')
                    destination = sqlite3.connect(target)
                    try:
                        source.backup(destination)
                    finally:
                        destination.close()
                        source.close()
            memory = config.parent / 'memory'
            if memory.exists():
                checked(memory, directory=True, managed_group=True)
                for database in memory.glob('*.sqlite'):
                    checked(database, managed_group=True)
                    source = sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)
                    target = backup / database.name
                    write(target, b'')
                    destination = sqlite3.connect(target)
                    try:
                        source.backup(destination)
                    finally:
                        destination.close()
                        source.close()
            # Invalidate a previous marker before any mutation. Failures leave a
            # resumable pending migration, never a falsely completed index.
            if marker.exists():
                marker.unlink()
            if changed:
                os.replace(temporary, config)
            for agent in agents:
                native(['memory', 'index', '--force', '--agent', agent], config, runner)
            write(marker, (identity + '\n').encode())
            summary.update({'reindexed': True, 'backup_directory': str(backup)})
        summary['applied'] = True
        return summary
    finally:
        temporary.unlink(missing_ok=True)


def network_candidate(policy: dict[str, Any]) -> dict[str, Any]:
    """Add only the pinned Node embedding route beside the existing MCP route."""
    try:
        network = policy['network_policies']
        endpoints = network['mcp_bridge_homecompute']['endpoints']
        bridge = next(endpoint for endpoint in endpoints
                      if endpoint.get('host') == 'homecompute-capabilities.home.arpa'
                      and endpoint.get('port') == 19445 and endpoint.get('path') == '/mcp')
        if (bridge.get('allowed_ips') != ['10.77.20.1'] or bridge.get('protocol') != 'mcp'
                or bridge.get('enforcement') != 'enforce'
                or bridge.get('credential_binding') != {'provider': 'agent-openclaw-mcp-homecompute'}):
            raise Refused('trusted-mcp-policy-required')
        entry = {'name': 'homecompute_embeddings', 'binaries': [{'path': '/usr/local/bin/node'}],
                 'endpoints': [{'host': bridge['host'], 'port': bridge['port'],
                                'path': '/v1/embeddings', 'allowed_ips': ['10.77.20.1'],
                                'protocol': 'rest', 'enforcement': 'enforce',
                                'credential_binding': copy.deepcopy(bridge['credential_binding']),
                                'rules': [{'allow': {'method': 'POST', 'path': '/v1/embeddings'}}]}]}
        if 'homecompute_embeddings' in network and network['homecompute_embeddings'] != entry:
            raise Refused('existing-embedding-policy-drift')
        after = copy.deepcopy(policy)
        after['network_policies']['homecompute_embeddings'] = entry
        return after
    except (KeyError, TypeError, StopIteration):
        raise Refused('trusted-mcp-policy-required') from None


def network_setup(*, apply: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Run on the agents VM operator account; retain all other sandbox policy."""
    executable = str(Path.home() / '.local/bin/openshell')
    common = ['agent-openclaw', '-g', 'nemoclaw-9123']
    try:
        result = runner([executable, 'policy', 'get', *common, '--base', '-o', 'json'],
                        capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise Refused('native-policy-read-refused')
        metadata = json.loads(result.stdout)
        before = metadata['policy']
        after = network_candidate(before)
        changed = after != before
        summary = {'policy_changed': changed, 'applied': False, 'prior_version': metadata['version']}
        if not apply:
            return summary
        if changed:
            directory = Path.home() / '.local/state/homecompute/embedding-policy-backups'
            if any(path.is_symlink() for path in (directory, *directory.parents)):
                raise Refused('symlink-policy-backup-refused')
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            checked(directory, directory=True)
            backup = directory / str(uuid.uuid4())
            backup.mkdir(mode=0o700)
            write(backup / 'policy-before.json', json.dumps(metadata, indent=2).encode())
            candidate_path = backup / 'policy-after.json'
            write(candidate_path, json.dumps(after, indent=2).encode())
            current = runner([executable, 'policy', 'get', *common, '--base', '-o', 'json'],
                             capture_output=True, text=True, timeout=45)
            if current.returncode or json.loads(current.stdout)['policy'] != before:
                raise Refused('native-policy-changed-since-validation')
            update = runner([executable, 'policy', 'set', *common, '--policy', str(candidate_path),
                             '--wait', '--timeout', '45'], capture_output=True, text=True, timeout=55)
            if update.returncode:
                raise Refused('native-policy-update-refused')
            summary['backup_directory'] = str(backup)
        summary['applied'] = True
        return summary
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        raise Refused('native-policy-command-unconfirmed') from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/sandbox/.openclaw/openclaw.json'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--network-policy', action='store_true', help='on the agents VM, admit only the Node embedding endpoint')
    args = parser.parse_args()
    try:
        result = network_setup(apply=args.apply) if args.network_policy else setup(args.config, apply=args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, OSError, ValueError, sqlite3.Error):
        print('OpenClaw embedding setup refused; check owner paths, native schema, bridge availability and rebuild status.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
