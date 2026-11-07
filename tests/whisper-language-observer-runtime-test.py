"""Runtime version/proof regressions without importing Torch or loading models."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_runtime', ROOT / 'deploy/whisper-language-observer/classify.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


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


if __name__ == '__main__':
    unittest.main()
