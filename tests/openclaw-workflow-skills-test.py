"""Scoped native workflow installation, preservation, fencing and access-policy regression."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_team_policy as policy
from openclaw_workflow_skills import WORKFLOWS, SKILLS

spec = importlib.util.spec_from_file_location('workflow_setup', ROOT / 'scripts/setup-openclaw-workflow-skills.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.config = self.root / 'openclaw.json'
        self.entries = {}
        self.original = b'Private identity\n<!-- homecompute-team:start -->\nRetain native policies.\n<!-- homecompute-team:end -->\n'
        for role in (*policy.ROSTER, 'cook', 'administrator', 'finance', 'family'):
            workspace = self.root / ('workspace' if role == 'main' else 'workspace-' + role)
            workspace.mkdir()
            (workspace / 'AGENTS.md').write_bytes(self.original)
            (workspace / 'MEMORY.md').write_text('Private memory for ' + role)
            self.entries[role] = {'workspace': str(workspace), 'skills': ['existing-skill'],
                                  'tools': {'allow': ['read', 'session_status'], 'deny': ['exec']}}
        self.before = {'agents': {'entries': self.entries, 'defaults': {'model': 'retained'}},
                       'tools': {'deny': ['exec']}, 'plugins': {'private': 'retained'}, 'channels': {'private': 'retained'}}
        self.config.write_text(json.dumps(self.before))
        self.config.chmod(0o600)
        self.ledger = self.root / 'team-skill-ownership.json'
        self.ledger.write_text(json.dumps({'workspace/skills/retained/SKILL.md': 'a' * 64}))
        self.ledger.chmod(0o600)
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        if '--dry-run' not in argv:
            current = json.loads(self.config.read_text())
            for change in json.loads(argv[argv.index('--batch-json') + 1]):
                target = current
                keys = change['path'].split('.')
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = change['value']
            self.config.write_text(json.dumps(current))
        return subprocess.CompletedProcess(argv, 0)

    def test_real_bundles_install_only_in_assigned_roles_without_widening_authority(self):
        self.assertEqual(SKILLS, {'meal-planning', 'household-paperwork', 'budget-analysis', 'tasks-and-reminders'})
        self.assertTrue(SKILLS <= policy.MANAGED_SKILLS)
        initial = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        dry = module.setup(self.config, ROOT, runner=self.runner)
        self.assertEqual(initial, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        applied = module.setup(self.config, ROOT, apply=True, expected_plan_sha256=dry['plan_sha256'], runner=self.runner)
        self.assertEqual(applied['role_count'], 12)
        after = json.loads(self.config.read_text())
        normalized = copy.deepcopy(after)
        for role, entry in normalized['agents']['entries'].items():
            entry['skills'] = self.before['agents']['entries'][role]['skills']
        self.assertEqual(normalized, self.before)
        for role, entry in after['agents']['entries'].items():
            workspace = Path(entry['workspace'])
            self.assertEqual((workspace / 'MEMORY.md').read_bytes(), initial[workspace / 'MEMORY.md'])
            for skill in SKILLS:
                path = workspace / 'skills' / skill / 'SKILL.md'
                expected = role in WORKFLOWS and WORKFLOWS[role][0] == skill
                self.assertEqual(path.exists(), expected, (role, skill))
                if expected:
                    self.assertEqual(path.read_bytes(), (ROOT / 'deploy/openclaw/skills' / skill / 'SKILL.md').read_bytes())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    identity = (workspace / 'AGENTS.md').read_bytes()
                    self.assertTrue(identity.startswith(self.original))
                    self.assertIn(('Load ' + skill + ' before').encode(), identity)
            if role not in WORKFLOWS:
                self.assertEqual((workspace / 'AGENTS.md').read_bytes(), self.original)
        ledger = json.loads(self.ledger.read_text())
        self.assertEqual(ledger['workspace/skills/retained/SKILL.md'], 'a' * 64)
        self.assertEqual(len(ledger), 7)
        self.assertTrue(Path(applied['backup_directory'], 'openclaw-before.json').exists())
        again = module.setup(self.config, ROOT, runner=self.runner)
        self.assertEqual(again['changed_files'], 0)
        self.assertFalse(again['config_changed'])

    def test_stale_review_and_changes_during_native_validation_write_nothing(self):
        dry = module.setup(self.config, ROOT, runner=self.runner)
        path = self.root / 'workspace-cook/AGENTS.md'
        path.write_bytes(self.original + b'Pending edit\n')
        with self.assertRaisesRegex(ValueError, 'plan changed'):
            module.setup(self.config, ROOT, apply=True, expected_plan_sha256=dry['plan_sha256'], runner=self.runner)
        self.assertFalse((self.root / 'workflow-skill-backups').exists())
        dry = module.setup(self.config, ROOT, runner=self.runner)
        def changing_runner(argv, **kwargs):
            path.write_bytes(path.read_bytes() + b'Concurrent edit\n')
            return subprocess.CompletedProcess(argv, 0)
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            module.setup(self.config, ROOT, apply=True, expected_plan_sha256=dry['plan_sha256'], runner=changing_runner)
        self.assertFalse((self.root / 'workflow-skill-backups').exists())

    def test_unreviewed_skill_edits_and_symlinks_are_preserved_and_refused(self):
        dry = module.setup(self.config, ROOT, runner=self.runner)
        module.setup(self.config, ROOT, apply=True, expected_plan_sha256=dry['plan_sha256'], runner=self.runner)
        path = self.root / 'workspace-cook/skills/meal-planning/SKILL.md'
        path.write_text('Local edit\n')
        with self.assertRaisesRegex(ValueError, 'overwrite refused'):
            module.setup(self.config, ROOT, runner=self.runner)
        self.assertEqual(path.read_text(), 'Local edit\n')
        path.unlink()
        path.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, 'Symlink refused'):
            module.setup(self.config, ROOT, runner=self.runner)

    def test_missing_roles_reader_and_noncanonical_workspaces_are_refused(self):
        for mutation in ('role', 'reader', 'workspace'):
            value = copy.deepcopy(self.before)
            if mutation == 'role':
                del value['agents']['entries']['family']
            elif mutation == 'reader':
                value['agents']['entries']['family']['tools']['allow'] = []
            else:
                value['agents']['entries']['family']['workspace'] = str(self.root / 'workspace-personal')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.candidate(value, self.root)

    def test_access_wrapper_passes_the_closed_native_roster_without_restoring_revocations(self):
        before = copy.deepcopy(self.before)
        before['tools'] = {'allow': ['read'], 'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False},
                           'subagents': {'tools': {'allow': ['read'], 'deny': ['exec']}}}
        with self.assertRaisesRegex(policy.TeamRefused, 'reviewed-access-upgrade-required'):
            policy.access_candidate(before)
        granted = policy.access_candidate(before, enable_access_upgrade=True)
        for role, entry in granted['agents']['entries'].items():
            self.assertEqual(set(entry['tools']['allow']) & policy.ACCESS_TOOLS,
                             policy.ACCESS_TOOLS if role in {'travel', 'shopper'} else set())
        self.assertEqual(policy.access_candidate(granted), granted)
        revoked = copy.deepcopy(granted)
        revoked['agents']['entries']['travel']['tools']['allow'].remove(sorted(policy.ACCESS_TOOLS)[0])
        with self.assertRaisesRegex(policy.TeamRefused, 'partial-or-revoked-access-grant'):
            policy.access_candidate(revoked, enable_access_upgrade=True)
        unknown = copy.deepcopy(before)
        unknown['agents']['entries']['unreviewed-role'] = copy.deepcopy(unknown['agents']['entries']['family'])
        with self.assertRaisesRegex(policy.TeamRefused, 'managed-access-contract-refused'):
            policy.access_candidate(unknown, enable_access_upgrade=True)

    def test_retained_backup_restores_only_workflow_changes_and_refuses_later_edits(self):
        dry = module.setup(self.config, ROOT, runner=self.runner)
        applied = module.setup(self.config, ROOT, apply=True, expected_plan_sha256=dry['plan_sha256'], runner=self.runner)
        backup = Path(applied['backup_directory'])
        path = self.root / 'workspace-cook/AGENTS.md'
        installed = path.read_bytes()
        path.write_bytes(installed + b'Later private edit\n')
        with self.assertRaisesRegex(ValueError, 'Later file changes'):
            module.rollback(self.config, backup, runner=self.runner)
        path.write_bytes(installed)
        preview = module.rollback(self.config, backup, runner=self.runner)
        module.rollback(self.config, backup, apply=True, expected_plan_sha256=preview['plan_sha256'], runner=self.runner)
        self.assertEqual(json.loads(self.config.read_text()), self.before)
        self.assertEqual(path.read_bytes(), self.original)
        self.assertFalse((self.root / 'workspace-cook/skills/meal-planning/SKILL.md').exists())
        self.assertEqual(json.loads(self.ledger.read_text()), {'workspace/skills/retained/SKILL.md': 'a' * 64})


if __name__ == '__main__':
    unittest.main()
