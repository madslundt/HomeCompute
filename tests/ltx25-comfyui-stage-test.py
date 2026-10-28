#!/usr/bin/env python3
"""Exercise artifact publication and sharing without Docker or GPU access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ltx25_stage', ROOT / 'scripts/stage-ltx25-comfyui.py')
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source, self.destination, self.workflows = [self.root / name for name in ('source', 'destination', 'workflows')]
        for directory in (self.source, self.destination, self.workflows):
            directory.mkdir()
        self.payload = b'fixture NVFP4 weights\n' * 32
        self.asset = {'name': 'diffusion_models/fixture.safetensors', 'size': len(self.payload),
                      'sha256': hashlib.sha256(self.payload).hexdigest()}
        self.incoming = self.source / self.asset['name']
        self.incoming.parent.mkdir()
        self.incoming.write_bytes(self.payload)
        self.final = self.destination / self.asset['name']
        self.workflow = '{"nodes": [], "version": 0.4}\n'

    def publish(self, assets=None):
        m.publish_bundle(self.source, self.destination, self.workflows, assets or [self.asset],
                         self.workflow, os.geteuid(), os.getegid())

    def test_verified_publish_and_repeated_dedup_keep_both_runtimes_on_one_inode(self):
        old = self.destination / 'unrelated-image-model'
        old.write_bytes(b'preserve old image weights')
        self.publish()
        self.assertEqual(self.final.read_bytes(), self.payload)
        self.assertEqual(self.final.stat().st_mode & 0o777, 0o644)
        m.deduplicate(self.source, self.destination, [self.asset])
        self.assertTrue(os.path.samefile(self.incoming, self.final))
        self.publish()
        m.deduplicate(self.source, self.destination, [self.asset])
        self.assertTrue(os.path.samefile(self.incoming, self.final))
        self.assertEqual(old.read_bytes(), b'preserve old image weights')
        self.assertEqual((self.workflows / m.WORKFLOW_NAME).read_text(), self.workflow)

    def test_corrupt_or_truncated_source_is_never_published(self):
        for body in (self.payload[:17], b'x' * len(self.payload)):
            with self.subTest(length=len(body)):
                self.incoming.write_bytes(body)
                with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                    self.publish()
                self.assertFalse(self.final.exists())
                self.assertEqual(self.incoming.read_bytes(), body)

    def test_differing_final_or_workflow_is_preserved_and_blocks_all_publication(self):
        self.final.parent.mkdir()
        self.final.write_bytes(b'preserve existing model')
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            self.publish()
        self.assertEqual(self.final.read_bytes(), b'preserve existing model')
        self.final.unlink()
        workflow = self.workflows / m.WORKFLOW_NAME
        workflow.write_text('preserve existing workflow')
        with self.assertRaisesRegex(RuntimeError, 'preserved'):
            self.publish()
        self.assertFalse(self.final.exists())
        self.assertEqual(workflow.read_text(), 'preserve existing workflow')

    def test_symlinked_component_directory_cannot_write_outside_destination(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.final.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symlinked'):
            self.publish()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.incoming.read_bytes(), self.payload)

    def test_bad_shared_destination_preserves_source_before_any_replacement(self):
        self.final.parent.mkdir()
        self.final.write_bytes(b'x' * len(self.payload))
        inode = self.incoming.stat().st_ino
        with self.assertRaisesRegex(RuntimeError, 'SHA256'):
            m.deduplicate(self.source, self.destination, [self.asset])
        self.assertEqual(self.incoming.stat().st_ino, inode)
        self.assertEqual(self.incoming.read_bytes(), self.payload)
        self.assertFalse(self.incoming.with_name(self.incoming.name + '.shared').exists())


if __name__ == '__main__':
    unittest.main()
