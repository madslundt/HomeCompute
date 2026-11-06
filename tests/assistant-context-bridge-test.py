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


def private_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True))
    os.chmod(path, 0o600)


class FakeCommand:
    def __init__(self, layout):
        self.layout = layout
        self.adapter = 'active'
        self.context = 'active'

    def __call__(self, args, *, check=True, timeout=30):
        args = list(args)
        if args[:3] == ['git', '-C', str(self.layout.root)]:
            if args[3:5] == ['rev-parse', 'HEAD']:
                return REVISION
            if args[3:5] == ['merge-base', '--is-ancestor']:
                return ''
            if args[3:6] == ['cat-file', '-e', f'{REVISION}:scripts/openclaw-communication.py']:
                return ''
            if args[3:6] == ['cat-file', '-e', f'{REVISION}:scripts/assistant_context_tools.py']:
                return ''
            if args[3:6] == ['cat-file', '-e', f'{REVISION}:scripts/setup-assistant-context-bridge.py']:
                return ''
            if args[3:6] == ['diff', '--exit-code', 'HEAD']:
                return ''
        if args[:2] == ['systemctl', 'is-active']:
            return self.adapter if args[2] == MODULE.ADAPTER_SERVICE else self.context
        if args[:2] == ['systemctl', 'show']:
            return 'clients:' + str(self.layout.credential_source)
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
        root = temp_root / 'checkout'
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
            root_uid=os.geteuid(), root_gid=os.getegid(), context_health=health,
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
