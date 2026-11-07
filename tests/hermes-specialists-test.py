#!/usr/bin/env python3
"""Exercise specialist install, recovery and removal with a synthetic sandbox."""

from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / 'scripts/setup-hermes-specialists.py'))
CLI = ['nemohermes', 'test-owner']


class SpecialistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.expected = SETUP['source_hashes']()
        self.operations = []
        self.fail_on = None

    def call(self, argv):
        self.assertEqual(argv[:2], CLI)
        if argv[2] == 'exec':
            self.assertEqual(argv[3:6], ['--', 'python3', '-c'])
            program = argv[6].replace("'/sandbox/.hermes/skills'", repr(str(self.root)))
            return subprocess.check_output([sys.executable, '-c', program, *argv[7:]])
        self.assertEqual(argv[2], 'skill')
        action, argument = argv[3:]
        name = Path(argument).name
        self.operations.append((action, name))
        if name == self.fail_on:
            raise ValueError('synthetic native failure')
        directory = self.root / name
        if action == 'install':
            directory.mkdir()
            (directory / 'SKILL.md').write_bytes((Path(argument) / 'SKILL.md').read_bytes())
        else:
            self.assertEqual(action, 'remove')
            (directory / 'SKILL.md').unlink()
            directory.rmdir()

    def reconcile(self, operation):
        return SETUP['reconcile'](self.call, CLI, self.expected, operation)

    def test_plan_apply_and_rollback_are_idempotent(self):
        absent = dict.fromkeys(self.expected, 'absent')
        self.assertEqual(self.reconcile('plan'), absent)
        self.assertEqual(self.operations, [])
        for _ in range(2):
            self.assertEqual(self.reconcile('apply'), self.expected)
        self.assertEqual(self.operations, [('install', name) for name in self.expected])
        self.operations.clear()
        for _ in range(2):
            self.assertEqual(self.reconcile('rollback'), absent)
        self.assertEqual(self.operations, [('remove', name) for name in self.expected])

    def test_resume_after_partial_install(self):
        self.fail_on = 'follow-through'
        with self.assertRaisesRegex(ValueError, 'synthetic native failure'):
            self.reconcile('apply')
        self.assertEqual(self.reconcile('plan')['day-plan'], self.expected['day-plan'])
        self.fail_on = None
        self.operations.clear()
        self.assertEqual(self.reconcile('apply'), self.expected)
        self.assertEqual(self.operations, [('install', 'follow-through'), ('install', 'home-ops')])

    def test_conflict_holds_all_changes_and_preserves_local_files(self):
        self.reconcile('apply')
        directory = self.root / 'home-ops'
        extra = directory / 'local-notes.md'
        extra.write_text('operator-owned')
        self.operations.clear()
        for operation in ('plan', 'apply', 'rollback'):
            with self.assertRaisesRegex(ValueError, 'existing home-ops differs'):
                self.reconcile(operation)
        self.assertEqual(self.operations, [])
        self.assertEqual(extra.read_text(), 'operator-owned')

    def test_symlink_is_a_conflict(self):
        (self.root / 'home-ops').symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'existing home-ops differs'):
            self.reconcile('apply')
        self.assertEqual(self.operations, [])


if __name__ == '__main__':
    unittest.main()
