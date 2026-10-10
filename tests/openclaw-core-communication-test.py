#!/usr/bin/env python3
"""Core placement, least privilege and retained receiver admission boundaries."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('core_setup', ROOT / 'scripts/setup-openclaw-core-communication.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class CoreCommunicationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / 'home'
        self.config = self.home / 'config'
        self.state = self.home / 'communication'
        self.uid = os.getuid()
        self.patches = [patch.object(setup, name, value) for name, value in {
            'HOME': self.home, 'CONFIG': self.config, 'STATE': self.state,
            'DEST': self.root / 'opt/homecompute/communication',
            'UNIT_DIR': self.root / 'units', 'REPORTS': self.root / 'reports',
            'TMPFILES': self.root / 'tmpfiles/openclaw.conf',
        }.items()]
        for p in self.patches:
            p.start()
        for path in (self.home, self.config, self.state, self.state / 'adapter',
                     self.state / 'telegram', self.state / 'update-feed', self.home / '.ssh'):
            path.mkdir(mode=0o700)
        for name in ('communication.json', 'transport-tokens.json', 'telegram.json',
                     'telegram-tokens.json', 'role-tokens.json', 'projects.json'):
            self.private(self.config / name, {})
        self.private(self.config / 'codex-task-transport.json', {
            'tokens_file': str(self.config / 'role-tokens.json'),
            'project_policy': str(self.config / 'projects.json')})
        self.private(self.state / 'telegram/telegram-cursor.json', {'paused': False, 'offset': 37})
        for name in ('id_ed25519', 'known_hosts'):
            self.private(self.home / '.ssh' / name, {})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temporary.cleanup()

    def private(self, path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_fixed_core_commands_and_restart_semantics(self):
        units = {name: setup.service(name) for name in setup.NAMES}
        self.assertIn('Restart=on-failure\n', units['telegram'])
        for name in ('adapter', 'task-feed'):
            self.assertIn('Restart=always\n', units[name])
        for unit in units.values():
            for field in ('User=homecompute-openclaw', 'Group=homecompute-openclaw',
                          'NoNewPrivileges=yes', 'CapabilityBoundingSet=\n',
                          'ProtectSystem=strict', 'PrivateDevices=yes', 'MemoryMax=256M',
                          'TasksMax=64', 'StartLimitIntervalSec=0',
                          'Environment=HOMECOMPUTE_OPENCLAW_TRANSPORT=home-core'):
                self.assertIn(field, unit)
            self.assertNotIn('sudo ', unit)
            self.assertNotIn('--infrastructure-registry', unit)
            self.assertNotIn('/Users/', unit)
        update = units['update-feed']
        self.assertIn('Type=oneshot\n', update)
        self.assertNotIn('--follow', update)
        self.assertIn('--core-host-mode', update)
        self.assertIn('ExecStartPre=+/run/current-system/sw/bin/python3 ', update)
        self.assertIn('--project-core-model\n', update)
        self.assertIn(str(setup.REPORTS), update)
        self.assertIn('OnBootSec=30s', setup.timer())
        self.assertIn('OnUnitActiveSec=300s', setup.timer())
        self.assertIn('Persistent=true', setup.timer())

    def test_receiver_activation_requires_unpaused_retained_cursor_and_no_guard(self):
        setup.validate_ready(self.uid, telegram=True)
        cursor = self.state / 'telegram/telegram-cursor.json'
        self.private(cursor, {'paused': True, 'offset': 37})
        with self.assertRaises(ValueError):
            setup.validate_ready(self.uid, telegram=True)
        self.assertEqual(json.loads(cursor.read_text())['offset'], 37)
        self.private(cursor, {'paused': False, 'offset': 37})
        guard = self.state / 'telegram/telegram-admission.json'
        self.private(guard, {'update_id': 37})
        with self.assertRaises(ValueError):
            setup.validate_ready(self.uid, telegram=True)
        self.assertTrue(guard.is_file())
        guard.unlink()
        guard.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            setup.validate_ready(self.uid, telegram=True)

    def test_unsafe_credentials_and_nested_paths_are_refused(self):
        token = self.config / 'transport-tokens.json'
        token.chmod(0o640)
        with self.assertRaises(ValueError):
            setup.validate_ready(self.uid)
        token.chmod(0o600)
        with self.assertRaises(ValueError):
            setup.private_file(token, self.uid + 1)
        token.unlink()
        token.symlink_to(self.config / 'role-tokens.json')
        with self.assertRaises(OSError):
            setup.validate_ready(self.uid)
        token.unlink()
        self.private(token, {})
        self.private(self.config / 'codex-task-transport.json', {
            'tokens_file': str(self.root / 'outside.json'),
            'project_policy': str(self.config / 'projects.json')})
        with self.assertRaises(ValueError):
            setup.validate_ready(self.uid)

    def test_default_preview_has_no_side_effects(self):
        with patch.object(setup, 'call') as call, patch.object(setup, 'install') as install, \
             patch.object(setup, 'activate') as activate, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.main([]), 0)
        call.assert_not_called()
        install.assert_not_called()
        activate.assert_not_called()

    def test_install_enables_only_safe_boot_units_and_starts_nothing(self):
        def directory(path, uid, private, create=False, gid=None):
            if create:
                path.mkdir(parents=True, exist_ok=True)
        setup.TMPFILES.parent.mkdir()
        with patch.object(setup, 'protected_directory', side_effect=directory), \
             patch.object(setup, 'account', return_value=SimpleNamespace(pw_uid=self.uid, pw_gid=os.getgid())), \
             patch.object(setup, 'call', return_value=SimpleNamespace(stdout='inactive')) as call:
            setup.install({name: b'VALUE = 1\n' for name in setup.FILES})
        self.assertFalse(any(c.args[:2] == ('systemctl', 'start') for c in call.call_args_list))
        wants = setup.UNIT_DIR / 'multi-user.target.wants'
        self.assertTrue((wants / 'homecompute-openclaw-adapter.service').is_symlink())
        self.assertTrue((wants / 'homecompute-openclaw-task-feed.service').is_symlink())
        self.assertFalse((wants / 'homecompute-openclaw-telegram.service').exists())
        self.assertFalse((wants / 'homecompute-openclaw-update-feed.service').exists())
        self.assertTrue((setup.UNIT_DIR / 'timers.target.wants/homecompute-openclaw-update-feed.timer').is_symlink())
        self.assertEqual(setup.TMPFILES.read_text(), f'd {setup.REPORTS} 0755 root root -\n')
        self.assertEqual(json.loads((self.state / 'telegram/telegram-cursor.json').read_text())['offset'], 37)

    def test_bad_receiver_guard_cannot_enable_or_start_unit(self):
        self.private(self.state / 'telegram/telegram-admission.json', {'update_id': 37})
        with patch.object(setup, 'account', return_value=SimpleNamespace(pw_uid=self.uid)), \
             patch.object(setup, 'enable') as enable, patch.object(setup, 'call') as call:
            with self.assertRaises(ValueError):
                setup.activate(telegram=True)
        enable.assert_not_called()
        call.assert_not_called()

    def test_source_digest_and_symlink_boundary(self):
        source = self.root / 'source'
        source.mkdir(mode=0o700)
        for name in setup.FILES:
            path = source / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('{}' if name.endswith('.json') else 'VALUE = 1\n')
            path.chmod(0o644)
        _, before = setup.source_bundle(source)
        path = source / setup.FILES[0]
        path.write_text('VALUE = 2\n')
        _, after = setup.source_bundle(source)
        self.assertNotEqual(before, after)
        path.unlink()
        path.symlink_to(source / setup.FILES[1])
        with self.assertRaises(ValueError):
            setup.source_bundle(source)


if __name__ == '__main__':
    unittest.main()
