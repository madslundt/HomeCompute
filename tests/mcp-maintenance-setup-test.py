import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mcp_setup', ROOT / 'scripts/setup-mcp-maintenance.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Controller:
    def __init__(self, classification='routine', status='succeeded', override=False):
        self.classification, self.result_status, self.override = classification, status, override
        self.registered, self.runs = [], []

    def register(self, path):
        self.registered.append(path)
        return {'plan_id': 'a' * 32, 'requires_approval': self.override,
                'classification': {'classification': self.classification,
                                   'approval_required': self.classification != 'routine'}}

    def run(self, plan_id):
        self.runs.append(plan_id)
        return {'status': self.result_status}


def prepared(controller, component, output):
    return {'component': component, 'plan_file': str(output),
            'installed': '1.0.0', 'candidate': '1.1.0', 'candidate_id': 'b' * 32}


class McpMaintenanceSetupTest(unittest.TestCase):
    def setUp(self):
        # Source trust checks deliberately reject writable /tmp ancestors.
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o700)
        for name in setup.FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}\n' if name.endswith('.json') else 'VALUE = 1\n')
            path.chmod(0o644)
        self.state, self.public = self.root / 'state', self.root / 'public'
        self.state.mkdir(mode=0o700); self.public.mkdir(mode=0o755)

    def tearDown(self):
        self.temp.cleanup()

    def weekly(self, controller, prepare=prepared):
        with patch.object(setup, 'STATE', self.state), patch.object(setup, 'PUBLIC', self.public), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)):
            return setup.run_weekly(controller, prepare)

    def test_hash_binds_every_recipe_and_installer(self):
        _, before = setup.bundle(self.source)
        for name in setup.FILES:
            path = self.source / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            self.assertNotEqual(setup.bundle(self.source)[1], before)
            path.write_bytes(original)

    def test_sources_reject_links_writable_files_and_hardlinks(self):
        path = self.source / setup.FILES[0]
        path.unlink(); path.symlink_to(self.source / setup.FILES[1])
        with self.assertRaises(ValueError):
            setup.bundle(self.source)
        path.unlink(); path.write_text('VALUE = 1\n')
        os.link(path, self.source / 'extra-link')
        with self.assertRaises(ValueError):
            setup.bundle(self.source)
        (self.source / 'extra-link').unlink()
        path.chmod(0o666)
        try:
            with self.assertRaises(ValueError):
                setup.bundle(self.source)
        finally:
            path.chmod(0o644)

    def test_dry_run_has_no_root_requirement_or_mutation(self):
        with patch.object(setup, 'install') as install, patch.object(setup, 'weekly') as weekly, \
             patch.object(setup.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(setup.main(['--source', str(self.source)]), 0)
        self.assertFalse(json.loads(out.getvalue())['installed'])
        install.assert_not_called(); weekly.assert_not_called(); run.assert_not_called()

    def test_install_requires_hash_root_and_reviewed_host(self):
        for uid, host, digest in ((1000, 'home-core', '0' * 64), (0, 'other', '0' * 64),
                                  (0, 'home-core', '0' * 64)):
            with patch.object(setup.os, 'geteuid', return_value=uid), \
                 patch.object(setup.socket, 'gethostname', return_value=host), \
                 patch.object(setup, 'directory') as directory:
                with self.assertRaises(ValueError):
                    setup.install(self.source, digest)
                directory.assert_not_called()

    def test_weekly_fixed_schedule_is_bounded_without_retry(self):
        service, timer = setup.service(), setup.timer()
        self.assertIn('User=root\n', service)
        self.assertIn('Type=oneshot\n', service)
        self.assertIn('TimeoutStartSec=3600\n', service)
        self.assertIn('Restart=no\n', service)
        self.assertIn(' --weekly\n', service)
        self.assertIn('OnCalendar=Mon *-*-* 09:20:00 Europe/Copenhagen\n', timer)
        self.assertIn('Persistent=true\n', timer)
        self.assertNotIn('OnUnitActiveSec', timer)

    def test_only_routine_unheld_plans_execute(self):
        for classification, override, expected in (('routine', False, 2), ('major', False, 0),
                                                    ('review', False, 0), ('routine', True, 0)):
            with self.subTest(classification=classification, override=override):
                (self.state / 'weekly-attempts.json').unlink(missing_ok=True)
                controller = Controller(classification, override=override)
                result = self.weekly(controller)
                self.assertEqual(len(controller.runs), expected)
                self.assertEqual(len(controller.registered), 2)
                self.assertEqual([r['component'] for r in result['components']], list(setup.COMPONENTS))
                self.assertTrue(all(r['status'] == ('succeeded' if expected else 'approval_required')
                                    for r in result['components']))

    def test_no_change_does_not_register_and_errors_are_finite(self):
        controller = Controller()
        def current(*args):
            raise ValueError('no increasing stable MCP release')
        result = self.weekly(controller, current)
        self.assertTrue(all(r['status'] == 'current' for r in result['components']))
        self.assertEqual(controller.registered, [])
        def unsafe(*args):
            raise ValueError('token=DO-NOT-LEAK')
        result = self.weekly(controller, unsafe)
        self.assertTrue(all(r['status'] == 'failed' for r in result['components']))
        self.assertNotIn('DO-NOT-LEAK', json.dumps(result))

    def test_failed_uncertain_and_interrupted_attempts_never_retry(self):
        for status in ('precheck_failed', 'uncertain'):
            with self.subTest(status=status):
                (self.state / 'weekly-attempts.json').unlink(missing_ok=True)
                controller = Controller(status=status)
                self.weekly(controller)
                self.assertEqual(len(controller.runs), 2)
                result = self.weekly(controller)
                self.assertEqual(len(controller.runs), 2)
                self.assertTrue(all(r['status'] == 'blocked' for r in result['components']))
        (self.state / 'weekly-attempts.json').write_text(json.dumps({
            c: {'status': 'executing'} for c in setup.COMPONENTS}))
        controller = Controller()
        result = self.weekly(controller)
        self.assertEqual(controller.registered, [])
        self.assertTrue(all(r['status'] == 'uncertain' for r in result['components']))

    def test_installed_paths_reject_writable_ancestors_and_symlinks(self):
        parent = self.root / 'unsafe'
        parent.mkdir(mode=0o777); parent.chmod(0o777)
        path = parent / 'code.py'
        path.write_text('VALUE = 1\n')
        real_lstat = Path.lstat
        def privileged_lstat(item):
            info = list(real_lstat(item))
            info[4] = 0
            if item != parent:
                info[0] &= ~0o022
            return os.stat_result(info)
        with patch.object(Path, 'lstat', privileged_lstat):
            with self.assertRaises(ValueError):
                setup.safe(path)
        parent.chmod(0o755)
        path.unlink(); path.symlink_to(self.source / setup.FILES[0])
        with self.assertRaises(ValueError):
            setup.safe(path, root=False)

    def test_active_service_is_not_replaced(self):
        with patch.object(setup, 'require_host'), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(setup, 'directory') as directory:
            with self.assertRaises(ValueError):
                setup.install(self.source, setup.bundle(self.source)[1])
            directory.assert_not_called()

    def test_install_preserves_private_state_and_backs_up_recipes(self):
        dest, units, libexec = self.root / 'dest', self.root / 'units', self.root / 'libexec/helper'
        old = dest / 'automation/compose.yaml'
        old.parent.mkdir(parents=True)
        old.write_text('previous reviewed recipe')
        state = self.state / 'weekly-attempts.json'
        state.write_text('{"aula-mcp":{"status":"uncertain"}}')
        old_report = self.public / 'report.json'
        old_report.write_text('{"generation":42}')
        commands = []
        def command(argv, **kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 3 if 'is-active' in argv else 0, b'', b'')
        with patch.object(setup, 'DEST', dest), patch.object(setup, 'STATE', self.state), \
             patch.object(setup, 'PUBLIC', self.public), patch.object(setup, 'UNITS', units), \
             patch.object(setup, 'LIBEXEC', libexec), patch.object(setup, 'require_host'), \
             patch.object(setup, 'safe', side_effect=lambda p, **kwargs: Path(p)), \
             patch.object(setup.subprocess, 'run', side_effect=command):
            result = setup.install(self.source, setup.bundle(self.source)[1])
        self.assertEqual(state.read_text(), '{"aula-mcp":{"status":"uncertain"}}')
        self.assertEqual(old_report.read_text(), '{"generation":42}')
        self.assertEqual((Path(result['recipe_backup']) / 'automation/compose.yaml').read_text(),
                         'previous reviewed recipe')
        self.assertEqual(old.read_text(), (self.source / 'deploy/automation/compose.yaml').read_text())
        self.assertTrue(any(c[-1] == setup.NAME + '.timer' and c[1] == 'restart' for c in commands))
        self.assertFalse(any('start' in c or ('restart' in c and c[-1].endswith('.service')) for c in commands))
        self.assertEqual(result['updates_run'], 0)


if __name__ == '__main__':
    unittest.main()
