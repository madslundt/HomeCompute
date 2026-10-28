"""Instruction-only upgrades preserve newer roles, policies and private identities."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('instruction_setup', ROOT / 'scripts/setup-openclaw-integration-instructions.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class InstructionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.config = self.root / 'openclaw.json'
        entries, ledger = {}, {}
        self.original = b'Private identity\n<!-- homecompute-team:start -->\nKeep mail paused and household/access policies intact.\n<!-- homecompute-team:end -->\n'
        for role in ('main', 'personal', 'researcher', 'engineer', 'operator', 'shopper', 'keeper', 'travel', 'cook', 'administrator', 'finance', 'family'):
            workspace = self.root / ('workspace' if role == 'main' else 'workspace-' + role)
            workspace.mkdir(); path = workspace / 'AGENTS.md'; path.write_bytes(self.original); path.chmod(0o600)
            skills = ['travel-adventures'] if role == 'travel' else []
            entries[role] = {'workspace': str(workspace), 'skills': skills, 'tools': {'deny': ['exec']}}
            if skills:
                path = workspace / 'skills/travel-adventures/SKILL.md'; path.parent.mkdir(parents=True)
                path.write_bytes(b'Previous managed travel skill'); path.chmod(0o600)
                ledger[str(path.relative_to(self.root))] = module.digest(path.read_bytes())
        ledger['workspace-cook/skills/cooking/SKILL.md'] = 'a' * 64
        self.config.write_text(json.dumps({'agents': {'entries': entries}, 'tools': {'private': 'retained'}})); self.config.chmod(0o600)
        self.ledger = self.root / 'team-skill-ownership.json'; self.ledger.write_text(json.dumps(ledger)); self.ledger.chmod(0o600)

    def test_fenced_upgrade_preserves_config_other_roles_identity_and_ledger(self):
        native = self.config.read_bytes(); cook = (self.root / 'workspace-cook/AGENTS.md').read_bytes()
        preview = module.setup(self.config, ROOT)
        self.assertEqual(self.config.read_bytes(), native)
        applied = module.setup(self.config, ROOT, apply=True, expected_plan_sha256=preview['plan_sha256'])
        self.assertTrue(applied['applied']); self.assertFalse(applied['native_config_changed'])
        self.assertEqual(self.config.read_bytes(), native)
        self.assertEqual((self.root / 'workspace-cook/AGENTS.md').read_bytes(), cook)
        self.assertTrue((self.root / 'workspace-travel/AGENTS.md').read_bytes().startswith(self.original))
        self.assertEqual(json.loads(self.ledger.read_bytes())['workspace-cook/skills/cooking/SKILL.md'], 'a' * 64)
        self.assertEqual(module.setup(self.config, ROOT)['changed_files'], 0)
        self.assertTrue(list((self.root / 'integration-instruction-backups').glob('*/plan.json')))

    def test_changed_destination_or_native_config_refuses_stale_review(self):
        for target in (self.root / 'workspace-travel/AGENTS.md', self.config):
            preview = module.setup(self.config, ROOT); original = target.read_bytes()
            target.write_bytes(original + b' ')
            with self.subTest(target=target), self.assertRaises(ValueError):
                module.setup(self.config, ROOT, apply=True, expected_plan_sha256=preview['plan_sha256'])
            target.write_bytes(original)
        self.assertFalse((self.root / 'integration-instruction-backups').exists())

    def test_untracked_skill_edit_noncanonical_workspace_and_symlinks_are_refused(self):
        path = self.root / 'workspace-travel/skills/travel-adventures/SKILL.md'
        old = path.read_bytes(); path.write_bytes(b'Unreviewed private edit')
        with self.assertRaises(ValueError): module.setup(self.config, ROOT)
        path.write_bytes(old)
        native = self.config.read_bytes(); cfg = json.loads(native); cfg['agents']['entries']['travel']['workspace'] = '/private/other'
        self.config.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError): module.setup(self.config, ROOT)
        self.config.write_bytes(native)
        path.unlink(); path.symlink_to(self.config)
        with self.assertRaises(ValueError): module.setup(self.config, ROOT)


if __name__ == '__main__': unittest.main()
