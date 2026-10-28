#!/usr/bin/env python3
"""Plan or apply local Mattermost voice messages in the managed OpenClaw sandbox.

The default validates a private candidate. --network-policy runs on the agents
VM; ordinary setup runs as the sandbox configuration owner. The existing MCP
credential authenticates only the trusted speech bridge. No cloud key is used.
The installed Mattermost plugin lacks channel-scoped TTS configuration, so
root inbound TTS requires Mattermost to be the only enabled channel. Existing
per-agent or account speech overrides require explicit reconciliation first.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Callable
import uuid

ORIGIN = 'https://homecompute-capabilities.home.arpa:19445'
MATTERMOST_HOST = 'home-core.tail479ad.ts.net'
CREDENTIAL_ENV = 'HOMECOMPUTE_OPENCLAW_MCP_TOKEN'
SPEECH_PATHS = ('/v1/audio/transcriptions', '/v1/audio/speech')


class Refused(Exception):
    """Fixed diagnostics exclude native configuration and secrets."""


def checked(path: Path, *, directory: bool = False) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise Refused('symlink-path-refused')
    info = path.stat()
    if (info.st_uid != os.geteuid() or info.st_mode & 0o022
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or not directory and stat.S_IMODE(info.st_mode) != 0o600):
        raise Refused('owner-private-path-required')


def write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def credential(before: dict[str, Any]) -> str | dict[str, str]:
    try:
        server = before['mcp']['servers']['homecompute']
        bearer = server['headers']['Authorization']
        if (server.get('transport') != 'streamable-http' or server['url'] != ORIGIN + '/mcp'
                or not isinstance(bearer, str) or not bearer.startswith('Bearer ')
                or not 16 <= len(bearer[7:]) <= 4096 or re.search(r'\s', bearer[7:])):
            raise Refused('trusted-speech-bridge-required')
        key = bearer[7:]
        if key.startswith('openshell:resolve:'):
            if not re.fullmatch(r'openshell:resolve:env:(?:(?:v[0-9]{1,20}|s[a-f0-9]{64})_)?'
                                + CREDENTIAL_ENV, key):
                raise Refused('trusted-speech-credential-required')
            return {'source': 'env', 'provider': 'default', 'id': CREDENTIAL_ENV}
        return key
    except (KeyError, TypeError, AttributeError):
        raise Refused('trusted-speech-bridge-required') from None


def candidate(before: dict[str, Any]) -> dict[str, Any]:
    """Preserve native conversation routing while adding local-only speech."""
    key = credential(before)
    try:
        channel = before['channels']['mattermost']
        if channel.get('enabled') is not True or channel.get('baseUrl') != 'https://' + MATTERMOST_HOST + ':9443':
            raise Refused('managed-mattermost-channel-required')
        if any(name not in {'mattermost', 'defaults', 'modelByChannel'}
               and (not isinstance(config, dict) or config.get('enabled') is not False)
               for name, config in before['channels'].items()):
            raise Refused('mattermost-must-be-the-only-enabled-channel')
        scoped = [before['agents'].get('defaults', {}), channel]
        scoped.extend(before['agents'].get('entries', {}).values())
        scoped.extend(channel.get('accounts', {}).values())
        if any('tts' in section for section in scoped):
            raise Refused('existing-scoped-tts-requires-reconciliation')
        after = copy.deepcopy(before)
        provider = {'baseUrl': ORIGIN + '/v1', 'apiKey': key, 'models': []}
        providers = after.setdefault('models', {}).setdefault('providers', {})
        if 'openai' in providers and providers['openai'] != provider:
            raise Refused('existing-openai-provider-requires-reconciliation')
        providers['openai'] = provider
        plugins = after.setdefault('plugins', {})
        allow = plugins.setdefault('allow', [])
        if 'openai' not in allow:
            allow.append('openai')
        plugins.setdefault('entries', {}).setdefault('openai', {})['enabled'] = True
        media = after.setdefault('tools', {}).setdefault('media', {})
        model = {'provider': 'openai', 'model': 'whisper-turbo', 'capabilities': ['audio'],
                 'baseUrl': ORIGIN + '/v1', 'maxBytes': 10_000_000, 'maxChars': 4000,
                 'timeoutSeconds': 120}
        retained = []
        for current in media.get('models', []):
            if current == model:
                continue
            capabilities = current.get('capabilities')
            if not capabilities or 'audio' in capabilities:
                raise Refused('existing-audio-model-requires-reconciliation')
            retained.append(current)
        media['models'] = [*retained, model]
        media['audio'] = {'enabled': True, 'maxBytes': 10_000_000, 'maxChars': 4000,
                          'timeoutSeconds': 120, 'echoTranscript': False,
                          'scope': {'default': 'deny', 'rules': [
                              {'action': 'allow', 'match': {'channel': 'mattermost'}}]}}
        speech_provider = {'apiKey': copy.deepcopy(key), 'baseUrl': ORIGIN + '/v1',
                           'model': 'plapre', 'voice': 'danish-default', 'responseFormat': 'wav'}
        tts = {
            'auto': 'inbound', 'mode': 'final', 'provider': 'openai',
            'maxTextLength': 2000, 'timeoutMs': 120000,
            'modelOverrides': {'enabled': False}, 'persona': 'local-danish',
            'personas': {'local-danish': {'label': 'Local Danish', 'provider': 'openai',
                                        'fallbackPolicy': 'fail',
                                        'providers': {'openai': copy.deepcopy(speech_provider)}}},
            'providers': {'openai': speech_provider}}
        if 'tts' in before and before['tts'] != tts:
            raise Refused('existing-root-tts-requires-reconciliation')
        after['tts'] = tts
        return after
    except (KeyError, TypeError, AttributeError):
        raise Refused('managed-speech-contract-required') from None


def setup(config: Path, *, apply: bool = False,
          runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    checked(config)
    checked(config.parent, directory=True)
    raw = config.read_bytes()
    before = json.loads(raw)
    after = candidate(before)
    temporary = config.parent / ('.speech-candidate-' + uuid.uuid4().hex + '.json')
    write(temporary, (json.dumps(after, indent=2) + '\n').encode())
    try:
        environment = dict(os.environ, OPENCLAW_CONFIG_PATH=str(temporary))
        environment['NODE_OPTIONS'] = (environment.get('NODE_OPTIONS', '') + ' --max-old-space-size=384').strip()
        result = runner(['openclaw', 'config', 'validate', '--json'], capture_output=True,
                        text=True, timeout=600, env=environment)
        if result.returncode:
            raise Refused('native-speech-schema-refused')
        changed = before != after
        summary = {'schema_validated': True, 'config_changed': changed, 'applied': False,
                   'restart_required': changed, 'channel': 'mattermost',
                   'stt_model': 'whisper-turbo', 'tts_model': 'plapre', 'tts_auto': 'inbound'}
        if not apply:
            return summary
        if config.read_bytes() != raw:
            raise Refused('native-config-changed-since-validation')
        if changed:
            backup_root = config.parent / 'speech-setup-backups'
            backup_root.mkdir(mode=0o700, exist_ok=True)
            checked(backup_root, directory=True)
            backup = backup_root / uuid.uuid4().hex
            backup.mkdir(mode=0o700)
            write(backup / 'openclaw-before.json', raw)
            os.replace(temporary, config)
            summary['backup_directory'] = str(backup)
        summary['applied'] = True
        return summary
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('native-speech-command-unconfirmed') from None
    finally:
        temporary.unlink(missing_ok=True)


def network_candidate(policy: dict[str, Any]) -> dict[str, Any]:
    """Admit only speech POSTs and native Mattermost attachment uploads."""
    try:
        after = copy.deepcopy(policy)
        network = after['network_policies']
        bridges = network['mcp_bridge_homecompute']['endpoints']
        bridge = next(endpoint for endpoint in bridges
                      if endpoint.get('host') == 'homecompute-capabilities.home.arpa'
                      and endpoint.get('port') == 19445 and endpoint.get('path') == '/mcp')
        binding = {'provider': 'agent-openclaw-mcp-homecompute'}
        if (bridge.get('allowed_ips') != ['10.77.20.1'] or bridge.get('protocol') != 'mcp'
                or bridge.get('enforcement') != 'enforce' or bridge.get('credential_binding') != binding):
            raise Refused('trusted-mcp-policy-required')
        entry = {'name': 'homecompute_speech', 'binaries': [{'path': '/usr/local/bin/node'}],
                 'endpoints': [{'host': bridge['host'], 'port': bridge['port'], 'path': path,
                                'allowed_ips': ['10.77.20.1'], 'protocol': 'rest', 'enforcement': 'enforce',
                                'credential_binding': copy.deepcopy(binding),
                                'rules': [{'allow': {'method': 'POST', 'path': path}}]}
                               for path in SPEECH_PATHS]}
        if 'homecompute_speech' in network and network['homecompute_speech'] != entry:
            raise Refused('existing-speech-policy-drift')
        candidates = [entry for name, entry in network.items()
                      if name == 'mattermost' or name == 'nemoclaw_custom__homecompute-mattermost__mattermost']
        if len(candidates) != 1:
            raise Refused('unique-managed-mattermost-policy-required')
        mattermost = candidates[0]
        if mattermost.get('binaries') != [{'path': '/usr/local/bin/node'}]:
            raise Refused('trusted-mattermost-policy-required')
        rest = next(endpoint for endpoint in mattermost['endpoints']
                    if endpoint.get('host') == MATTERMOST_HOST and endpoint.get('port') == 9443
                    and endpoint.get('protocol') == 'rest' and endpoint.get('enforcement') == 'enforce'
                    and endpoint.get('path') in (None, '/'))
        upload = {'allow': {'method': 'POST', 'path': '/api/v4/files'}}
        if upload not in rest['rules']:
            rest['rules'].append(upload)
        network['homecompute_speech'] = entry
        return after
    except (KeyError, TypeError, StopIteration):
        raise Refused('trusted-speech-network-policy-required') from None


def network_setup(*, apply: bool = False,
                  runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
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
            directory = Path.home() / '.local/state/homecompute/speech-policy-backups'
            if any(path.is_symlink() for path in (directory, *directory.parents)):
                raise Refused('symlink-policy-backup-refused')
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            checked(directory, directory=True)
            backup = directory / uuid.uuid4().hex
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
    parser.add_argument('--network-policy', action='store_true')
    args = parser.parse_args()
    try:
        result = network_setup(apply=args.apply) if args.network_policy else setup(args.config, apply=args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, OSError, ValueError):
        print('OpenClaw speech setup refused; reconcile private paths, native schema and trusted bridge.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
