#!/usr/bin/env python3
"""Temporary text-tunnel bridge preserves Nix ownership and other forwards."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('comfyui_access', Path(__file__).resolve().parents[1] / 'scripts/setup-comfyui-access.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

RUNNER = '''#!/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bash-5.3/bin/bash
set -Eeuo pipefail
export PATH="/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-openssh/bin:$PATH"
bind_address='172.28.200.1'
exec ssh -F /dev/null -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes \\
  -L "$bind_address:18003:127.0.0.1:8003" \\
  -L "$bind_address:18005:127.0.0.1:18300" \\
  -L "$bind_address:18006:127.0.0.1:8006" \\
  -L "$bind_address:18007:127.0.0.1:8007" \\
  homecompute-tunnel@192.168.30.126
'''
NATIVE = RUNNER.replace(':18005:127.0.0.1:18300', ':18005:127.0.0.1:18400')


class TunnelPlanTests(unittest.TestCase):
    def test_admission_service_is_separate_and_private_firewall_upgrades_old_ports(self):
        units = m.units()
        admission = units['homecompute-llm-admission']
        self.assertIn('--admission-only', admission)
        self.assertIn('CapabilityBoundingSet=\n', admission)
        self.assertNotIn('--admission-only', units['homecompute-llm-workflow-activity'])
        self.assertIn('-I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193,18194 -j HC-COMFYUI', m.FIREWALL)
        self.assertIn('-D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191,18192,18193 -j HC-COMFYUI', m.FIREWALL)
        self.assertIn('-D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18188,18189,18190,18191 -j HC-COMFYUI', m.FIREWALL)
        self.assertIn('-s 172.28.200.2/32 -j ACCEPT', m.FIREWALL)

    def test_only_requested_forward_changes(self):
        result, needed = m.admission_runner(RUNNER)
        self.assertTrue(needed)
        self.assertEqual(result, NATIVE)
        self.assertEqual(result.replace(':18005:127.0.0.1:18400', ':18005:127.0.0.1:18300'), RUNNER)

    def test_native_mapping_is_supported_noop(self):
        result, needed = m.admission_runner(NATIVE)
        self.assertFalse(needed)
        self.assertEqual(result, NATIVE)

    def test_unknown_or_multiple_mappings_fail_closed(self):
        for source in (RUNNER.replace('18300', '19000'), RUNNER + RUNNER,
                       RUNNER + NATIVE, RUNNER.replace(':18005:', ':18009:')):
            with self.assertRaises(ValueError):
                m.admission_runner(source)

    def test_runtime_reloads_current_nix_runner_instead_of_staged_copy(self):
        path = Path('/nix/store/cccccccccccccccccccccccccccccccc-homecompute-compute-ssh-tunnel/bin/homecompute-compute-ssh-tunnel')
        current = RUNNER.replace('-o BatchMode=yes', '-o BatchMode=yes -o ServerAliveInterval=17')
        with patch.object(m, 'text_runner', return_value=(path, current)), \
                patch.object(m, 'trusted_nix_file', side_effect=lambda path: path), \
                patch.object(m.os, 'execv') as execute:
            m.run_text_tunnel()
        args = execute.call_args.args
        self.assertEqual(args[1][1:], ['-c', current.replace(':18005:127.0.0.1:18300', ':18005:127.0.0.1:18400'), str(path)])
        # A later native Nix generation bypasses all cached/review artifacts.
        with patch.object(m, 'text_runner', return_value=(path, NATIVE)), \
                patch.object(m.os, 'execv', side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit):
                m.run_text_tunnel()
        execute.assert_called_once_with(str(path), [str(path)])

    def test_base_execstart_must_be_one_fixed_nix_runner(self):
        unit = Path('/nix/store/unit/homecompute-compute-ssh-tunnel.service')
        runner = '/nix/store/' + 'c' * 32 + '-homecompute-compute-ssh-tunnel/bin/homecompute-compute-ssh-tunnel'
        class Result:
            stdout = str(unit) + '\n'
        with patch.object(m.subprocess, 'run', return_value=Result()), \
                patch.object(m, 'trusted_nix_file', side_effect=lambda path: path), \
                patch.object(Path, 'read_text', side_effect=[f'[Service]\nExecStart={runner}\n', RUNNER]):
            self.assertEqual(m.text_runner(), (Path(runner), RUNNER))
        for value in ('ExecStart=/bin/bash -c dangerous\n',
                      f'ExecStart={runner}\nExecStart={runner}\n',
                      f'ExecStart={runner} --extra\n'):
            with patch.object(m.subprocess, 'run', return_value=Result()), \
                    patch.object(m, 'trusted_nix_file', side_effect=lambda path: path), \
                    patch.object(Path, 'read_text', return_value=value):
                with self.assertRaises(ValueError):
                    m.text_runner()

    def test_staging_copies_exact_runner_and_never_restarts_tunnel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dropin = root / 'unit.service.d/override.conf'
            dropin.parent.mkdir()
            original_stat = Path.stat
            def root_owned(path, *args, **kwargs):
                if path in (root, dropin.parent):
                    return SimpleNamespace(st_uid=0, st_mode=0o40755)
                return original_stat(path, *args, **kwargs)
            with patch.object(m, 'ROOT', root), patch.object(m, 'TEXT_DROPIN', dropin), \
                    patch.object(m, 'text_runner', return_value=(Path('/nix/store/runner'), RUNNER)), \
                    patch.object(Path, 'stat', root_owned), patch.object(m, 'write') as write, \
                    patch.object(m.subprocess, 'run') as command:
                self.assertIn('staged-18400', m.stage_text_tunnel())
            self.assertEqual(write.call_args_list[0].args, (root / 'compute-ssh-tunnel-admission.sh', NATIVE, 0o755))
            self.assertEqual(write.call_args_list[-1].args, (dropin, m.TEXT_OVERRIDE))
            command.assert_not_called()

    def test_native_reconcile_removes_only_our_override_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            dropin = Path(directory) / '30-homecompute-llm-admission.conf'
            dropin.write_text(m.TEXT_OVERRIDE)
            original_stat = Path.stat
            def root_owned(path, *args, **kwargs):
                if path == dropin:
                    return SimpleNamespace(st_uid=0, st_mode=0o100644)
                return original_stat(path, *args, **kwargs)
            with patch.object(m, 'TEXT_DROPIN', dropin), \
                    patch.object(m, 'text_runner', return_value=(Path('/nix/store/runner'), NATIVE)), \
                    patch.object(Path, 'stat', root_owned), \
                    patch.object(m.subprocess, 'run') as command:
                self.assertIn('native-18400', m.stage_text_tunnel())
            self.assertFalse(dropin.exists())
            command.assert_not_called()

    def test_unmanaged_override_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            dropin = Path(directory) / '30-homecompute-llm-admission.conf'
            dropin.write_text('[Service]\nEnvironment=UNRELATED=1\n')
            with patch.object(m, 'TEXT_DROPIN', dropin), \
                    patch.object(m, 'text_runner', return_value=(Path('/nix/store/runner'), NATIVE)):
                with self.assertRaises(ValueError):
                    m.stage_text_tunnel()
            self.assertIn('UNRELATED', dropin.read_text())


if __name__ == '__main__':
    unittest.main()
