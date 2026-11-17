#!/usr/bin/env python3
"""Regression coverage for pinned archive extraction and the install lifecycle."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('offline_extension', ROOT / 'deploy/hermes/offline/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class OfflineExtensionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.archive = self.root / 'source.tar.gz'
        self.home = self.root / 'profile'
        self.home.mkdir()
        self.pin = {'kind': 'skill', 'subdir': 'skills/sample', 'commit': 'a' * 40}

    def make_archive(self, *entries):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for filename, data in entries:
                member = tarfile.TarInfo(filename)
                if data is None:
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                    archive.addfile(member)
                else:
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
        self.pin['sha256'] = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def test_selected_subtree_and_checksum(self):
        self.make_archive(('root/skills/sample/SKILL.md', b'skill'), ('root/unrelated', b'excluded'))
        destination = self.root / 'staged'
        installer.unpack(self.archive, self.pin, destination)
        self.assertEqual((destination / 'SKILL.md').read_bytes(), b'skill')
        self.assertEqual(sorted(p.name for p in destination.iterdir()), ['SKILL.md'])
        self.archive.write_bytes(b'drift')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            installer.unpack(self.archive, self.pin, destination)

    def test_rejects_escape_duplicate_and_link(self):
        base = 'root/skills/sample/'
        for entries in (((base + '../other', b'escape'),),
                        ((base + 'duplicate', b'one'), (base + 'duplicate', b'two')),
                        ((base + 'link', None),)):
            with self.subTest(entries=entries):
                self.make_archive(*entries)
                with self.assertRaises(ValueError):
                    installer.unpack(self.archive, self.pin, self.root / 'staged')
        self.assertFalse((self.root / 'other').exists())

    def test_idempotent_install_drift_and_rollback(self):
        source = self.root / 'staged'
        source.mkdir()
        (source / 'SKILL.md').write_text('original')
        self.assertEqual(installer.apply(self.home, source, 'sample', self.pin, 'fixture')['result'],
                         'installed')
        self.assertEqual(installer.apply(self.home, source, 'sample', self.pin, 'fixture')['result'],
                         'already-installed')
        target = self.home / 'skills/sample/SKILL.md'
        target.write_text('owner edit')
        with self.assertRaisesRegex(ValueError, 'differs'):
            installer.apply(self.home, source, 'sample', self.pin, 'fixture')
        with self.assertRaisesRegex(ValueError, 'unknown extension'):
            installer.rollback(self.home, 'sample', self.pin)
        target.write_text('original')
        self.assertEqual(installer.rollback(self.home, 'sample', self.pin)['result'], 'rolled-back')
        self.assertFalse(target.exists())
        retained = list(self.home.glob('.homecompute-offline-extensions/*.retained/SKILL.md'))
        self.assertEqual([p.read_text() for p in retained], ['original'])

    def test_mutation_preserves_other_configuration_and_rolls_back_exact_bytes(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML is only present in the qualified Hermes runtime')
        source = self.root / 'staged'
        source.mkdir()
        (source / 'plugin.yaml').write_text('name: sample\n')
        config = self.home / 'config.yaml'
        original = b'# owner\nmodel:\n  api_key: ${COMPATIBLE_API_KEY}\nplugins:\n  enabled: [omh]\n'
        config.write_bytes(original)
        config.chmod(0o640)
        p = {**self.pin, 'kind': 'plugin', 'memory_provider': 'sample'}
        helper = importlib.util.spec_from_file_location('omh_config', ROOT / 'deploy/hermes/omh/configure.py')
        omh = importlib.util.module_from_spec(helper)
        helper.loader.exec_module(omh)
        with patch.dict(sys.modules, {'omh_config': omh}):
            installer.apply(self.home, source, 'sample', p, 'fixture')
        active = yaml.safe_load(config.read_bytes())
        self.assertEqual(active, {**yaml.safe_load(original), 'memory': {'provider': 'sample'}})
        installer.rollback(self.home, 'sample', p)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(config.stat().st_mode & 0o777, 0o640)

    def test_caution_approval_is_exact_and_dangerous_always_fails(self):
        hit = SimpleNamespace(pattern_id='example', severity='high', file='SKILL.md', line=10)
        result = SimpleNamespace(verdict='caution', findings=[hit])
        pin = {'reviewed_caution': [['example', 'high', 'SKILL.md', 10]]}
        self.assertTrue(installer.approved_caution(result, pin))
        hit.line = 11
        self.assertFalse(installer.approved_caution(result, pin))
        hit.line = 10
        result.verdict = 'dangerous'
        self.assertFalse(installer.approved_caution(result, pin))

    def test_pins_are_immutable(self):
        pins = json.loads((ROOT / 'config/hermes-offline-extensions.json').read_bytes())
        for pin in pins['extensions'].values():
            self.assertRegex(pin['commit'], '^[a-f0-9]{40}$')
            self.assertRegex(pin['sha256'], '^[a-f0-9]{64}$')


if __name__ == '__main__':
    unittest.main()
