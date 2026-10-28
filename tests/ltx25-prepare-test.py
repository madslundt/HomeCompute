#!/usr/bin/env python3
"""Check preparation integrity and preservation without a GPU or model downloads."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ltx25_prepare', ROOT / 'scripts/prepare-ltx25-spark.py')
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.payload = b'fixture checkpoint\n' * 32
        self.asset = m.Asset('diffusion_models/fixture.safetensors', len(self.payload),
                             hashlib.sha256(self.payload).hexdigest())
        self.models = self.root / 'models/ltx-2.5'
        self.destination = self.models / self.asset.name

    def checkpoint(self, payload):
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(payload)

    def test_default_plan_has_pins_and_does_not_create_installation_root(self):
        target = self.root / 'does-not-exist'
        result = subprocess.run([sys.executable, str(SPEC.origin), '--root', str(target)],
                                capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['source']['revision'], m.SOURCE_REVISION)
        self.assertEqual(plan['model_revision'], m.MODEL_REVISION)
        self.assertEqual(len(plan['assets']), 5)
        self.assertFalse(plan['services_changed'])
        self.assertFalse(target.exists())

    def test_truncation_and_same_size_corruption_are_detected_and_preserved(self):
        for body, failure in [(self.payload[:17], 'size'), (b'x' * len(self.payload), 'SHA256')]:
            with self.subTest(failure=failure):
                self.checkpoint(body)
                with self.assertRaisesRegex(RuntimeError, failure):
                    m.verify_asset(self.models, self.asset)
                self.assertEqual(self.destination.read_bytes(), body)

    def test_existing_corrupt_checkpoint_never_reaches_downloader_or_keeps_ready_marker(self):
        self.checkpoint(b'x' * len(self.payload))
        (self.root / m.MANIFEST).write_text('{"state":"verified"}')
        with patch.object(m, 'ASSETS', (self.asset,)), patch.object(m, 'command') as runner:
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                m.download(self.root)
        runner.assert_not_called()
        self.assertFalse((self.root / m.MANIFEST).exists())
        self.assertEqual(self.destination.read_bytes(), b'x' * len(self.payload))

    def test_gate_failure_keeps_partials_and_retry_produces_manifest_only_after_verification(self):
        python = self.root / 'LTX-2/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('fixture')
        partial = self.models / '.cache/huggingface/download/fixture.incomplete'
        partial.parent.mkdir(parents=True)
        partial.write_bytes(self.payload[:17])
        with patch.object(m, 'ASSETS', (self.asset,)), \
                patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), \
                patch.object(m, 'command', side_effect=RuntimeError('gated repository')):
            with self.assertRaisesRegex(RuntimeError, 'gated repository'):
                m.download(self.root)
        self.assertEqual(partial.read_bytes(), self.payload[:17])
        self.assertFalse((self.root / m.MANIFEST).exists())
        with patch.object(m, 'ASSETS', (self.asset,)), \
                patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), \
                patch.object(m, 'command', side_effect=lambda *_args, **_kwargs: self.checkpoint(self.payload)):
            m.download(self.root)
        manifest = json.loads((self.root / m.MANIFEST).read_text())
        self.assertEqual(manifest['state'], 'verified')
        self.assertEqual(manifest['assets'][0]['sha256'], self.asset.sha256)
        self.assertEqual(partial.read_bytes(), self.payload[:17])

    def test_existing_source_edits_are_refused_before_network_or_write(self):
        source = self.root / 'LTX-2'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        path = source / m.SETUP_FILE
        path.parent.mkdir(parents=True)
        path.write_text('original kernel\n')
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'fixture'], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        path.write_text('preserve my local kernel edit\n')
        with patch.object(m, 'SOURCE_REVISION', revision), patch.object(m, 'pinned_input') as network:
            with self.assertRaisesRegex(RuntimeError, 'modifications'):
                m.install_runtime(self.root)
        network.assert_not_called()
        self.assertEqual(path.read_text(), 'preserve my local kernel edit\n')
        self.assertFalse((source / 'uv.lock').exists())

    def test_symlinked_checkpoint_does_not_follow_or_change_external_target(self):
        outside = self.root / 'preserve'
        outside.write_bytes(self.payload)
        self.destination.parent.mkdir(parents=True)
        self.destination.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
            m.verify_asset(self.models, self.asset)
        self.assertEqual(outside.read_bytes(), self.payload)

    def test_mutation_action_rejects_wrong_host_before_creating_any_directory(self):
        target = self.root / 'not-created'
        with patch.object(m.platform, 'system', return_value='Linux'), \
                patch.object(m.platform, 'machine', return_value='aarch64'), \
                patch.object(m.platform, 'node', return_value='different-host'):
            self.assertEqual(m.main(['download', '--root', str(target)]), 1)
        self.assertFalse(target.exists())

    def test_repeated_download_under_group_writable_umask_keeps_private_metadata_and_readiness(self):
        self.checkpoint(self.payload)
        previous_umask = os.umask(0o002)
        try:
            with patch.object(m, 'ASSETS', (self.asset,)), patch.object(m, 'mutation_guard'):
                for _ in range(2):
                    self.assertEqual(m.main(['download', '--root', str(self.root)]), 0)
                    self.assertEqual(os.umask(0o002), 0o002)
                    for name in ('.prepare.lock', m.MANIFEST):
                        self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
                    self.assertEqual(json.loads((self.root / m.MANIFEST).read_text())['state'], 'verified')
        finally:
            os.umask(previous_umask)
        self.assertEqual(self.destination.read_bytes(), self.payload)


if __name__ == '__main__':
    unittest.main()
