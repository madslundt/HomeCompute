import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('maintenance_setup', ROOT / 'scripts/setup-maintenance-monitor.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MaintenanceDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'bundle'
        self.source.mkdir(mode=0o700)
        for name in setup.FILES:
            target = self.source / name
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps({'schema_version': 1, 'hosts': {'home-core': {}, 'home-spark': {}}})
                              if name.endswith('.json') else 'VALUE = 1\n')
            target.chmod(0o644)

    def tearDown(self):
        self.temp.cleanup()

    def test_modified_source_changes_review_digest(self):
        _, before = setup.source_bundle(self.source)
        (self.source / setup.FILES[0]).write_text('VALUE = 2\n')
        _, after = setup.source_bundle(self.source)
        self.assertNotEqual(before, after)

    def test_symlink_and_shared_writable_sources_rejected(self):
        path = self.source / setup.FILES[0]
        path.unlink()
        path.symlink_to(self.source / setup.FILES[1])
        with self.assertRaises(ValueError):
            setup.source_bundle(self.source)
        path.unlink()
        path.write_text('VALUE = 1\n')
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            setup.source_bundle(self.source)

    def test_policy_must_bind_both_hosts(self):
        (self.source / setup.FILES[-1]).write_text(json.dumps({'hosts': {'home-core': {}}}))
        with self.assertRaises(ValueError):
            setup.source_bundle(self.source)

    def test_only_apt_host_keeps_narrow_uid_drop_capability(self):
        spark = setup.service('home-spark', Path('/usr/bin/python3'))
        core = setup.service('home-core', Path('/nix/store/test/bin/python3'))
        self.assertIn('\nAmbientCapabilities=CAP_SETUID\n', spark)
        self.assertIn('\nAmbientCapabilities=\n', core)
        for unit in (spark, core):
            self.assertIn('\nNoNewPrivileges=yes\n', unit)
            self.assertIn('\nRestrictSUIDSGID=yes\n', unit)
            self.assertNotIn('CAP_SYS_ADMIN', unit)
            self.assertNotIn('CAP_NET_ADMIN', unit)

    def test_dry_run_never_stages_or_activates(self):
        with patch.object(setup.os, 'geteuid', return_value=0), \
             patch.object(setup.socket, 'gethostname', return_value='home-spark'), \
             patch.object(setup, 'safe_directories'), patch.object(setup, 'verify_units'), \
             patch.object(setup, 'install') as install, patch.object(setup, 'call') as call, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = setup.main(['--host', 'home-spark', '--source', str(self.source), '--dry-run'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())['timer_activated'])
        install.assert_not_called()
        call.assert_not_called()

    def test_review_digest_required_and_mutation_refused(self):
        with patch.object(setup.os, 'geteuid', return_value=0), \
             patch.object(setup.socket, 'gethostname', return_value='home-spark'), \
             patch.object(setup, 'install') as install, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--host', 'home-spark', '--source', str(self.source), '--install']), 2)
            self.assertEqual(setup.main(['--host', 'home-spark', '--source', str(self.source), '--install',
                                         '--expected-sha256', '0'*64]), 2)
        install.assert_not_called()

    def test_install_stage_preserves_report_and_rollback_never_rewinds_it(self):
        dest, state, units = self.root / 'installed', self.root / 'state', self.root / 'units'
        state.mkdir()
        report = state / 'report.json'
        report.write_text('{"generation":1}')
        commands = []
        def call(*args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(setup, 'DEST', dest), patch.object(setup, 'STATE', state), \
             patch.object(setup, 'BACKUPS', state / 'deploy-backups'), \
             patch.object(setup, 'layout', return_value=(units, Path(sys.executable))), \
             patch.object(setup, 'safe_directories'), patch.object(setup, 'verify_units'), \
             patch.object(setup, 'active', return_value=False), patch.object(setup, 'call', side_effect=call):
            files, digest = setup.source_bundle(self.source)
            receipt = setup.install('home-spark', files, digest, False)
            self.assertFalse(receipt['timer_activated'])
            self.assertFalse(any('start' in command for command in commands))
            self.assertEqual(report.read_text(), '{"generation":1}')
            backup = Path(receipt['backup'])
            self.assertEqual((backup / 'report-before.json').read_text(), '{"generation":1}')
            report.write_text('{"generation":2}')
            real_stat = Path.stat
            def privileged_stat(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == backup:
                    fields = list(result); fields[4] = 0
                    return os.stat_result(fields)
                return result
            with patch.object(Path, 'stat', privileged_stat):
                result = setup.rollback('home-spark', backup, False)
            self.assertTrue(result['report_preserved'])
            self.assertEqual(report.read_text(), '{"generation":2}')
            self.assertFalse((dest / setup.FILES[0]).exists())

    def test_active_collector_is_never_killed_or_replaced(self):
        dest, state, units = self.root / 'installed', self.root / 'state', self.root / 'units'
        with patch.object(setup, 'DEST', dest), patch.object(setup, 'STATE', state), \
             patch.object(setup, 'BACKUPS', state / 'deploy-backups'), \
             patch.object(setup, 'layout', return_value=(units, Path(sys.executable))), \
             patch.object(setup, 'safe_directories'), patch.object(setup, 'verify_units'), \
             patch.object(setup, 'active', side_effect=lambda name: name.endswith('.service')), \
             patch.object(setup, 'call') as call:
            files, digest = setup.source_bundle(self.source)
            with self.assertRaises(ValueError):
                setup.install('home-spark', files, digest, True)
            self.assertFalse(dest.exists())
            self.assertFalse(any('stop' in command.args for command in call.call_args_list))


if __name__ == '__main__':
    unittest.main()
