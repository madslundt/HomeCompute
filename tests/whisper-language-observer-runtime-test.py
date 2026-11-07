"""Runtime version/proof regressions without importing Torch or loading models."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_runtime', ROOT / 'deploy/whisper-language-observer/classify.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
spec = importlib.util.spec_from_file_location('observer_installer', ROOT / 'deploy/whisper-language-observer/installer_check.py')
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)


class EquivalentVersion(str):
    """Reproduce TorchVersion allowing equivalent .02 and .2 spellings."""
    def __eq__(self, other):
        return str(self).replace('nv26.02', 'nv26.2') == str(other).replace('nv26.02', 'nv26.2')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.torch = types.ModuleType('torch')
        self.torch.__version__ = EquivalentVersion(R.C.PIN['runtime_torch_module'])
        for name in ('set_num_threads', 'set_num_interop_threads', 'use_deterministic_algorithms'):
            setattr(self.torch, name, Mock())
        self.torch.get_num_threads = lambda: 1
        self.identity = {k: R.C.PIN[k] for k in ('runtime_torch', 'runtime_torch_module')}

    def test_configure_checks_raw_module_and_distribution_independently(self):
        self.assertEqual(self.torch.__version__, R.C.PIN['runtime_torch'])
        self.assertNotEqual(str(self.torch.__version__), R.C.PIN['runtime_torch'])
        with patch.dict(sys.modules, {'torch': self.torch}), \
             patch.object(R.importlib.metadata, 'version', return_value=R.C.PIN['runtime_torch']) as metadata:
            self.assertIs(R.configure(), self.torch)
            metadata.assert_called_once_with('torch')
            self.assertEqual(R.runtime_identity(self.torch), self.identity)
            self.torch.use_deterministic_algorithms.assert_called_once_with(True)
            self.torch.__version__ = EquivalentVersion(R.C.PIN['runtime_torch'])
            with self.assertRaises(ValueError):
                R.configure()
            self.torch.__version__ = EquivalentVersion(R.C.PIN['runtime_torch_module'])
            metadata.return_value = R.C.PIN['runtime_torch_module']
            with self.assertRaises(ValueError):
                R.configure()

    def test_verify_json_proof_is_accepted_by_classify_with_both_exact_identities(self):
        samples = [{'frames': n, 'kind': k, 'pcm_sha256': 'pcm',
                    'official_log_posteriors': [-5.0] * 107} for n, k in R.C.SYNTHETIC]
        value = {'graph_sha256': 'graph', 'source_revision': 'source', 'samples': samples,
                 'labels': [str(n) for n in range(107)], 'trace_warnings': []}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            report = directory / 'parity.json'
            with patch.object(R, 'manifest', return_value=value), patch.object(R, 'configure', return_value=self.torch), \
                 patch.object(R, 'graph', return_value=object()), patch.object(R, 'output', return_value=[-5.0] * 107), \
                 patch.object(R.importlib.metadata, 'version', return_value=R.C.PIN['runtime_torch']), \
                 patch.object(R.C, 'synthetic', return_value=b'pcm'), patch.object(R.C, 'sha', return_value='pcm'), \
                 patch.object(R.C, 'close'), patch.object(R.C, 'pcm', return_value=b'pcm'):
                proof = R.verify(directory, 'manifest', report)
                saved = json.loads(report.read_bytes())
                self.assertEqual(proof, saved)
                self.assertEqual({k: saved[k] for k in self.identity}, self.identity)
                self.assertTrue(all(type(saved[k]) is str for k in self.identity))
                actual = R.classify(directory, 'manifest', report, 'pcm', 'pcm', b'wav')
                self.assertEqual({k: actual[k] for k in self.identity}, self.identity)
                for key in self.identity:
                    changed = {**saved, key: 'wrong'}
                    report.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError):
                        R.classify(directory, 'manifest', report, 'pcm', 'pcm', b'wav')
                del saved['runtime_torch_module']
                report.write_text(json.dumps(saved))
                with self.assertRaises(ValueError):
                    R.classify(directory, 'manifest', report, 'pcm', 'pcm', b'wav')

    def test_restrictive_umask_copied_sources_become_readable_without_write_access(self):
        dockerfile = (I.ROOT / 'Dockerfile').read_text()
        self.assertIn('chmod -R a+rX,a-w /opt/language-observer /opt/observer-export', dockerfile)
        self.assertLess(dockerfile.index('chmod -R a+rX,a-w'), dockerfile.index('installer_check.py worker-export'))
        self.assertIn('installer_check.py worker-base', dockerfile)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'public'; directory.mkdir(mode=0o700)
            source = directory / 'export.py'
            previous = os.umask(0o077)
            try:
                source.write_bytes(b'public source')
            finally:
                os.umask(previous)
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            subprocess.run(['chmod', '-R', 'a-w', str(directory)], check=True)
            self.assertEqual(source.stat().st_mode & 0o777, 0o400)
            self.assertFalse(source.stat().st_mode & 0o004)  # Witnessed UID10001 denial.
            subprocess.run(['chmod', '-R', 'a+rX,a-w', str(directory)], check=True)
            self.assertEqual(source.stat().st_mode & 0o777, 0o444)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o555)
            directory.chmod(0o700)  # Permit ordinary temporary-directory cleanup.

    def test_build_worker_checks_drop_all_identity_before_exec_in_both_interpreters(self):
        for mode, interpreter, target in [('worker-export', '/opt/observer-export/bin/python', 'export'),
                                           ('worker-base', sys.executable, 'base')]:
            calls = Mock()
            with patch.object(I.sys, 'argv', ['installer_check.py', mode]), \
                 patch.object(I.os, 'setgroups', calls.groups), patch.object(I.os, 'setgid', calls.gid), \
                 patch.object(I.os, 'setuid', calls.uid), patch.object(I.os, 'execv', calls.execute):
                calls.execute.side_effect = SystemExit
                with self.assertRaises(SystemExit):
                    I.main()
            self.assertEqual([c[0] for c in calls.mock_calls], ['groups', 'gid', 'uid', 'execute'])
            calls.groups.assert_called_once_with([])
            calls.gid.assert_called_once_with(10001)
            calls.uid.assert_called_once_with(10001)
            calls.execute.assert_called_once_with(interpreter, [interpreter, '-I', '-B', str(I.ROOT / 'installer_check.py'), target])

    def test_metadata_check_reads_every_source_before_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'common.py').write_bytes(b'public')
            with patch.object(I, 'ROOT', directory), patch.object(I.sys, 'argv', ['installer_check.py', 'base']), \
                 patch.object(I.importlib.metadata, 'version', side_effect=AssertionError('Source checks first')):
                with self.assertRaises(FileNotFoundError):
                    I.main()


if __name__ == '__main__':
    unittest.main()
