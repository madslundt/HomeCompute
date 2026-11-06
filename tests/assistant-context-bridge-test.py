#!/usr/bin/env python3
"""Synthetic tests for the narrowly scoped owner context bridge lifecycle."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from dataclasses import replace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('setup_assistant_context_bridge',
                                              ROOT / 'scripts/setup-assistant-context-bridge.py')
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
import sys
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BASE_TOOLS = {'homecompute_system_read', 'homecompute_task_list'}
CONTEXT_TOOLS = set(MODULE.CONTEXT_TOOL_NAMES)
TOKEN = 'context-assistant-token-value-for-synthetic-tests-only-2026'
MCP_HERMES = 'hermes-owner-token-value-for-synthetic-tests-only-2026'
MCP_OPENCLAW = 'openclaw-token-value-for-synthetic-tests-only-2026'
REVISION = 'a' * 40
UNIT_TEXT = '''[Unit]
Description=Existing OpenClaw adapter
[Service]
ExecStart=/run/current-system/sw/bin/python3 /opt/homecompute/openclaw-communication/scripts/openclaw-communication.py --config /var/lib/homecompute-openclaw/config/communication.json --tokens /var/lib/homecompute-openclaw/config/transport-tokens.json --state /var/lib/homecompute-openclaw/communication/adapter --task-transport /var/lib/homecompute-openclaw/config/codex-task-transport.json --registry /var/lib/homecompute-openclaw/config/system-monitoring.json --infrastructure-registry /var/lib/homecompute-openclaw/config/system-monitoring.json --mcp-tokens /var/lib/homecompute-openclaw/config/native-mcp-tokens.json
User=homecompute-openclaw
UMask=0077
'''
CONTEXT_UNIT_TEXT = '''# /etc/systemd/system/homecompute-assistant-context.service -> /nix/store/xisbr0b8qbqb9lz5fjnps76pdif00m85-unit-homecompute-assistant-context.service/homecompute-assistant-context.service
[Service]
LoadCredential=clients:/etc/homecompute/assistant-context-clients.json
'''


def private_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True))
    os.chmod(path, 0o600)


class FakeCommand:
    def __init__(self, layout):
        self.layout = layout
        self.source_blobs = {
            rel: (layout.root / rel).read_bytes()
            for rel in ('scripts/openclaw-communication.py', 'scripts/assistant_context_tools.py',
                        'scripts/setup-assistant-context-bridge.py')
        }
        self.adapter = 'active'
        self.context = 'active'
        self.dropins = ''
        self.fragment = MODULE.CONTEXT_SERVICE_FRAGMENT
        self.context_unit = CONTEXT_UNIT_TEXT

    def __call__(self, args, *, check=True, timeout=30, binary=False):
        args = list(args)
        if args[:3] == ['git', '-C', str(self.layout.root)]:
            if args[3:5] == ['rev-parse', 'HEAD']:
                return REVISION
            if args[3:5] == ['merge-base', '--is-ancestor']:
                return ''
            if len(args) == 5 and args[3] == 'show' and args[4].startswith(REVISION + ':'):
                rel = args[4].split(':', 1)[1]
                return self.source_blobs[rel] if binary else self.source_blobs[rel].decode()
        if args[:2] == ['systemctl', 'is-active']:
            return self.adapter if args[2] == MODULE.ADAPTER_SERVICE else self.context
        if args[:2] == ['systemctl', 'show']:
            if args[2] == '--property=LoadCredential':
                return '[unprintable]'
            if args[2] == '--property=FragmentPath':
                return self.fragment
            if args[2] == '--property=DropInPaths':
                return self.dropins
        if args[:2] == ['systemctl', 'cat']:
            return self.context_unit.replace(MODULE.CONTEXT_CLIENTS_SOURCE,
                                             str(self.layout.credential_source))
        if args[:2] == ['systemctl', 'stop']:
            self.adapter = 'inactive'
            return ''
        if args[:2] == ['systemctl', 'start']:
            self.adapter = 'active'
            return ''
        if args[:2] == ['systemctl', 'daemon-reload']:
            return ''
        if not check:
            return ''
        raise AssertionError('unexpected command: ' + ' '.join(args))


class ContextBridgeLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp.name).resolve()
        releases = temp_root / 'releases'
        releases.mkdir()
        root = releases / REVISION
        home = temp_root / 'service-home'
        config = home / 'config'
        dest = temp_root / 'opt' / 'openclaw-communication'
        unit = temp_root / 'units' / 'homecompute-openclaw-adapter.service'
        state = temp_root / 'state'
        credentials = temp_root / 'etc' / 'assistant-context-clients.json'
        loaded = temp_root / 'run' / 'credentials' / 'clients'
        renderer = temp_root / 'renderer.py'
        for path in (root / 'scripts', home, config, dest / 'scripts', unit.parent,
                     credentials.parent, loaded.parent, renderer.parent):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        os.chmod(dest, 0o755)
        os.chmod(dest / 'scripts', 0o755)
        (root / 'scripts/openclaw-communication.py').write_bytes(
            (ROOT / 'scripts/openclaw-communication.py').read_bytes())
        (root / 'scripts/assistant_context_tools.py').write_bytes(
            (ROOT / 'scripts/assistant_context_tools.py').read_bytes())
        (root / 'scripts/setup-assistant-context-bridge.py').write_bytes(
            (ROOT / 'scripts/setup-assistant-context-bridge.py').read_bytes())
        (dest / 'scripts/openclaw-communication.py').write_bytes(b'known published baseline')
        os.chmod(dest / 'scripts/openclaw-communication.py', 0o644)
        unit.write_text(UNIT_TEXT)
        os.chmod(unit, 0o644)
        private_json(config / 'native-mcp-tokens.json', {'openclaw': MCP_OPENCLAW, 'hermes-owner': MCP_HERMES})
        context_clients = {'schema_version': 1, 'clients': [{
            'name': 'hermes-owner', 'token': TOKEN, 'role': 'assistant', 'principal_scope': 'owner',
            'data_domain': 'personal', 'visibility': 'private',
            'sources': ['home-assistant', 'daythread', 'aula', 'calendar'],
        }]}
        private_json(credentials, context_clients)
        private_json(loaded, context_clients)
        (config / 'assistant-context-token').write_text(TOKEN + '\n')
        os.chmod(config / 'assistant-context-token', 0o600)
        # Present transport/task credentials prove the context token is independent.
        private_json(config / 'transport-tokens.json', {'collector': 'collector-token-1234567890',
                                                         'conversation': 'conversation-token-1234567890',
                                                         'delivery': 'delivery-token-1234567890'})
        private_json(config / 'codex-task-transport.json', {'tokens_file': str(config / 'task-tokens.json')})
        private_json(config / 'task-tokens.json', {'task': 'task-token-12345678901234567890'})

        self.layout = MODULE.Layout(root=root, home=home, config=config, destination=dest,
                                    unit=unit, state=state, renderer=renderer,
                                    context_token=config / 'assistant-context-token',
                                    context_config=config / 'assistant-context.json',
                                    native_tokens=config / 'native-mcp-tokens.json',
                                    credential_source=credentials, systemd_credential=loaded)
        self.command = FakeCommand(self.layout)
        self.health_calls: list[str] = []
        self.health_failure_call: int | None = None
        self.openclaw_extra_context = False

        def health(token: str) -> dict:
            self.health_calls.append(token)
            if self.health_failure_call == len(self.health_calls):
                raise ValueError('synthetic health failure')
            if token != TOKEN:
                raise AssertionError('context probe used wrong principal')
            return {'ready': True, 'mode': 'shadow'}

        def inventory(token: str) -> set[str]:
            enabled = (self.layout.context_config.exists()
                       and MODULE.CONTEXT_ARG in self.layout.unit.read_text())
            if token == MCP_HERMES:
                return BASE_TOOLS | (CONTEXT_TOOLS if enabled else set())
            if token == MCP_OPENCLAW:
                return BASE_TOOLS | (CONTEXT_TOOLS if self.openclaw_extra_context else set())
            raise AssertionError('bridge probe used wrong principal')

        self.bridge = MODULE.BridgeLifecycle(
            layout=self.layout, command=self.command, service_uid=os.geteuid(), service_gid=os.getegid(),
            root_uid=os.geteuid(), root_gid=os.getegid(), releases_dir=releases,
            context_health=health,
            tool_inventory=inventory, renderer=lambda: UNIT_TEXT,
        )
        self.baseline_hash = MODULE.digest(b'known published baseline')
        MODULE.MANAGED_FILES = {
            'scripts/openclaw-communication.py': {
                'source': root / 'scripts/openclaw-communication.py',
                'target': dest / 'scripts/openclaw-communication.py', 'before': self.baseline_hash},
            'scripts/assistant_context_tools.py': {
                'source': root / 'scripts/assistant_context_tools.py',
                'target': dest / 'scripts/assistant_context_tools.py', 'before': None},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()
        MODULE.MANAGED_FILES = {
            'scripts/openclaw-communication.py': {
                'source': MODULE.ROOT / 'scripts/openclaw-communication.py',
                'target': MODULE.DEST / 'scripts/openclaw-communication.py',
                'before': MODULE.BASELINE_COMMUNICATION_SHA256},
            'scripts/assistant_context_tools.py': {
                'source': MODULE.ROOT / 'scripts/assistant_context_tools.py',
                'target': MODULE.DEST / 'scripts/assistant_context_tools.py', 'before': None},
        }

    def test_plan_apply_idempotent_and_rollback_preserves_only_bridge_files(self) -> None:
        reviewed = self.bridge.plan(REVISION)
        plan_hash = self.bridge.plan_sha256(reviewed)
        self.assertEqual(reviewed['action'], 'enable-owner-context')
        self.assertEqual(reviewed['hermes_tools_before'], sorted(BASE_TOOLS))
        self.assertEqual(reviewed['openclaw_tools_before'], sorted(BASE_TOOLS))
        self.assertNotIn(TOKEN, json.dumps(reviewed))

        with self.assertRaisesRegex(ValueError, 'plan hash differs'):
            self.bridge.apply(REVISION, '0' * 64)
        self.assertEqual((self.layout.destination / 'scripts/openclaw-communication.py').read_bytes(),
                         b'known published baseline')

        receipt = self.bridge.apply(REVISION, plan_hash)
        self.assertEqual(receipt['state'], 'applied')
        self.assertEqual(set(receipt['hermes_tools_after']), BASE_TOOLS | CONTEXT_TOOLS)
        self.assertEqual(set(receipt['openclaw_tools_before']), BASE_TOOLS)
        self.assertIn(MODULE.CONTEXT_ARG, self.layout.unit.read_text())
        self.assertTrue(self.layout.context_config.exists())
        self.assertEqual(stat.S_IMODE(self.layout.context_config.stat().st_mode), 0o600)
        receipt_bytes = (self.layout.state / 'receipt.json').read_bytes()
        self.assertNotIn(TOKEN.encode(), receipt_bytes)
        self.assertNotIn(TOKEN.encode(), b''.join(path.read_bytes() for path in Path(receipt['backup']).iterdir()
                                                 if path.name.isdigit()))

        already = self.bridge.plan(REVISION)
        self.assertEqual(already['action'], 'already-active')
        rerun = self.bridge.apply(REVISION, self.bridge.plan_sha256(already))
        self.assertEqual(rerun['state'], 'applied')
        rolled = self.bridge.rollback(REVISION)
        self.assertEqual(rolled['state'], 'rolled-back')
        self.assertNotIn(MODULE.CONTEXT_ARG, self.layout.unit.read_text())
        self.assertFalse(self.layout.context_config.exists())
        self.assertFalse((self.layout.destination / 'scripts/assistant_context_tools.py').exists())
        self.assertEqual((self.layout.destination / 'scripts/openclaw-communication.py').read_bytes(),
                         b'known published baseline')
        self.assertEqual(self.bridge.rollback(REVISION)['state'], 'rolled-back')

    def test_rejects_unknown_installed_source_and_unit_drift(self) -> None:
        target = self.layout.destination / 'scripts/openclaw-communication.py'
        target.write_bytes(b'unknown local edit')
        with self.assertRaisesRegex(ValueError, 'preimage'):
            self.bridge.plan(REVISION)
        target.write_bytes(b'known published baseline')
        self.layout.unit.write_text(UNIT_TEXT + '# unreviewed unit edit\n')
        with self.assertRaisesRegex(ValueError, 'renderer output'):
            self.bridge.plan(REVISION)

    def test_refuses_idle_adapter_and_openclaw_context_exposure(self) -> None:
        self.command.adapter = 'inactive'
        with self.assertRaisesRegex(ValueError, 'must be active'):
            self.bridge.plan(REVISION)
        self.command.adapter = 'active'
        self.openclaw_extra_context = True
        with self.assertRaisesRegex(ValueError, 'OpenClaw already exposes'):
            self.bridge.plan(REVISION)

    def test_accepts_bound_origin_without_changing_owner_scope(self) -> None:
        value = json.loads(self.layout.credential_source.read_text())
        value['clients'][0]['origin_id'] = 'owner-mattermost-context'
        private_json(self.layout.credential_source, value)
        private_json(self.layout.systemd_credential, value)
        self.bridge._context_token()
        value['clients'][0]['origin_id'] = {'untrusted': 'scope'}
        private_json(self.layout.credential_source, value)
        private_json(self.layout.systemd_credential, value)
        with self.assertRaises(ValueError):
            self.bridge._context_token()

    def test_opaque_systemctl_show_uses_exact_unit_cat_credential(self) -> None:
        opaque = self.command(['systemctl', 'show', '--property=LoadCredential', '--value',
                               MODULE.CONTEXT_SERVICE])
        self.assertEqual(opaque, '[unprintable]')
        self.assertEqual(self.bridge._loaded_client_bytes(), self.layout.credential_source.read_bytes())

    def test_published_source_requires_exact_root_owned_release_and_blob_bytes(self) -> None:
        self.bridge.published(REVISION)
        root = self.layout.root
        root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'root-owned immutable'):
            self.bridge.published(REVISION)
        root.chmod(0o700)
        owner_uid = self.bridge.root_uid
        self.bridge.root_uid = owner_uid + 1
        with self.assertRaisesRegex(ValueError, 'root-owned immutable'):
            self.bridge.published(REVISION)
        self.bridge.root_uid = owner_uid

        source = root / 'scripts/assistant_context_tools.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\n# changed after publish\n')
        with self.assertRaisesRegex(ValueError, 'source differs from published bytes'):
            self.bridge.published(REVISION)
        source.write_bytes(original)

        other_root = root.parent / 'other'
        other_root.mkdir(mode=0o700)
        alternate = replace(self.layout, root=other_root)
        self.bridge.layout = alternate
        with self.assertRaisesRegex(ValueError, 'root-owned immutable'):
            self.bridge.published(REVISION)
        self.bridge.layout = self.layout

    def test_git_blob_reader_preserves_all_source_bytes(self) -> None:
        raw = MODULE.run([sys.executable, '-c',
                          "import sys; sys.stdout.buffer.write(b'\\nsource bytes\\n')"], binary=True)
        self.assertEqual(raw, b'\nsource bytes\n')

    def test_rejects_duplicate_reset_and_unexpected_credential_sources(self) -> None:
        for addition in (
                'LoadCredential=clients:/etc/homecompute/assistant-context-clients.json\n',
                'LoadCredential=clients:/etc/homecompute/assistant-context-clients.json\n'
                'LoadCredential=\n',
                'LoadCredential=clients:/etc/other-clients.json\n',
                'LoadCredentialEncrypted=clients:/etc/homecompute/assistant-context-clients.json\n',
                'LoadCredential=unknown:/etc/unknown-secret\n'):
            self.command.context_unit = CONTEXT_UNIT_TEXT + addition
            with self.subTest(addition=addition), self.assertRaisesRegex(
                    ValueError, 'credential directives are malformed|clients and only declared'):
                self.bridge._loaded_client_bytes()

    def test_accepts_only_fixed_optional_local_inference_credentials(self) -> None:
        self.command.context_unit = CONTEXT_UNIT_TEXT + (
            'LoadCredential=inference-ca:/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt\n'
            'LoadCredential=embedding:/etc/homecompute/assistant-context-embedding.json\n'
            'LoadCredential=briefing:/etc/homecompute/assistant-context-briefing.json\n')
        self.assertEqual(self.bridge._loaded_client_bytes(), self.layout.credential_source.read_bytes())
        self.command.context_unit += 'LoadCredential=embedding:/etc/other-key.json\n'
        with self.assertRaisesRegex(ValueError, 'clients and only declared'):
            self.bridge._loaded_client_bytes()

    def test_rejects_systemd_dropins_or_wrong_fragment_even_with_valid_cat(self) -> None:
        self.command.dropins = '/etc/systemd/system/homecompute-assistant-context.service.d/override.conf'
        with self.assertRaisesRegex(ValueError, 'fragment or drop-ins'):
            self.bridge._loaded_client_bytes()
        self.command.dropins = ''
        self.command.fragment = '/run/systemd/generator/homecompute-assistant-context.service'
        with self.assertRaisesRegex(ValueError, 'fragment or drop-ins'):
            self.bridge._loaded_client_bytes()

    def test_refuses_mismatched_external_context_token(self) -> None:
        changed = {'schema_version': 1, 'clients': [{
            'name': 'hermes-owner', 'token': 'different-assistant-token-value-for-tests-only-2026',
            'role': 'assistant', 'principal_scope': 'owner', 'data_domain': 'personal',
            'visibility': 'private', 'sources': ['home-assistant', 'daythread', 'aula', 'calendar'],
        }]}
        private_json(self.layout.systemd_credential, changed)
        with self.assertRaisesRegex(ValueError, 'loaded context credentials differ'):
            self.bridge.plan(REVISION)

    def test_health_failure_auto_restores_files_and_leaves_failed_marker(self) -> None:
        reviewed = self.bridge.plan(REVISION)
        self.health_failure_call = len(self.health_calls) + 3  # initial plan, re-plan, post-start probe
        with self.assertRaisesRegex(ValueError, 'automatic rollback marker retained'):
            self.bridge.apply(REVISION, self.bridge.plan_sha256(reviewed))
        marker = json.loads((self.layout.state / 'receipt.json').read_text())
        self.assertEqual(marker['state'], 'failed')
        self.assertTrue((self.layout.state / 'transaction.json').exists())
        self.assertFalse(self.layout.context_config.exists())
        self.assertEqual((self.layout.destination / 'scripts/openclaw-communication.py').read_bytes(),
                         b'known published baseline')
        self.assertEqual(self.layout.unit.read_text(), UNIT_TEXT)

    def test_stale_rollback_and_incomplete_receipt_are_refused(self) -> None:
        reviewed = self.bridge.plan(REVISION)
        receipt = self.bridge.apply(REVISION, self.bridge.plan_sha256(reviewed))
        self.layout.unit.write_text(self.layout.unit.read_text() + '# later operator change\n')
        with self.assertRaisesRegex(ValueError, 'change detected'):
            self.bridge.rollback(REVISION)
        # Restore the recorded after image only for this isolated receipt case.
        self.layout.unit.write_text(self.bridge._enabled_unit(UNIT_TEXT))
        # A malformed/incomplete receipt must not create a guessed rollback.
        (self.layout.state / 'receipt.json').write_text('{"schema_version":1}')
        os.chmod(self.layout.state / 'receipt.json', 0o600)
        with self.assertRaises((ValueError, KeyError, TypeError)):
            self.bridge.rollback(REVISION)


if __name__ == '__main__':
    unittest.main()
