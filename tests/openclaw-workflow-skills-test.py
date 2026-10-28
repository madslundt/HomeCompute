"""Workflow bundle installation, role isolation and unchanged execution authority."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workflow_team_fixtures', ROOT / 'tests/openclaw-team-test.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

ASSIGNMENTS = {
    'meal-planning': {'cook'},
    'household-paperwork': {'administrator'},
    'budget-analysis': {'finance'},
    'tasks-and-reminders': {'personal', 'operator', 'family'},
}


class WorkflowSkillsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TeamSetupTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.setup = fixtures.setup
        self.team = self.fixture.team

    def test_manifest_loads_workflows_only_for_their_assigned_roles(self):
        for skill, roles in ASSIGNMENTS.items():
            with self.subTest(skill=skill):
                self.assertIn(skill, self.setup.MANAGED_SKILLS)
                self.assertEqual({role for role, entry in self.team['agents'].items()
                                  if skill in entry['skills']}, roles)
                for role in roles:
                    self.assertIn('Load ' + skill + ' before', self.team['agents'][role]['instructions'])
        # Validate every real bundle through the native installer's ownership,
        # size, frontmatter and instruction-only directory checks.
        sources = self.setup.reviewed_skill_sources(ROOT / 'deploy/openclaw/skills')
        self.assertTrue(ASSIGNMENTS.keys() <= sources.keys())

    def test_upgrade_installs_real_bundles_without_changing_authority_or_memory(self):
        previous_team = copy.deepcopy(self.team)
        for entry in previous_team['agents'].values():
            entry['skills'] = [name for name in entry['skills'] if name not in ASSIGNMENTS]
        before = fixtures.reviewed_candidate(self.fixture.before, previous_team)
        self.fixture.config.write_text(json.dumps(before))
        source = self.fixture.skill_sources(tuple(ASSIGNMENTS))
        # No pre-existing main-workspace copy may mask a missing managed install.
        for name in ASSIGNMENTS:
            directory = self.fixture.state / 'workspace/skills' / name
            (directory / 'SKILL.md').unlink()
            directory.rmdir()
        preserved = {}
        for role, entry in before['agents']['entries'].items():
            directory = Path(entry['workspace'])
            directory.mkdir(mode=0o700, exist_ok=True)
            for name in ('MEMORY.md', 'owner-notes.md'):
                path = directory / name
                path.write_text('Synthetic private notes for ' + role + '\n')
                preserved[path] = path.read_bytes()
        original = {p: p.read_bytes() for p in self.fixture.state.rglob('*') if p.is_file()}
        dry = fixtures.reviewed_setup(self.fixture.config, self.team, skills_source=source,
                                      runner=self.fixture.runner)
        self.assertEqual(original, {p: p.read_bytes() for p in self.fixture.state.rglob('*') if p.is_file()})
        self.assertEqual(set(dry['managed_skill_sha256']), set(ASSIGNMENTS))
        result = fixtures.reviewed_setup(self.fixture.config, self.team, skills_source=source,
                                         apply=True, runner=self.fixture.runner)
        self.assertTrue(result['applied'])
        after = json.loads(self.fixture.config.read_text())
        # Only the six role skill lists change in native configuration.
        normalized = copy.deepcopy(after)
        for role, entry in normalized['agents']['entries'].items():
            entry['skills'] = before['agents']['entries'][role]['skills']
        self.assertEqual(normalized, before)
        expected_ledger = {}
        for skill, roles in ASSIGNMENTS.items():
            content = (source / skill / 'SKILL.md').read_bytes()
            for role, entry in after['agents']['entries'].items():
                target = Path(entry['workspace']) / 'skills' / skill / 'SKILL.md'
                if role in roles:
                    self.assertEqual(target.read_bytes(), content)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                    expected_ledger[str(target.relative_to(self.fixture.state))] = hashlib.sha256(content).hexdigest()
                    self.assertIn('Load ' + skill + ' before', (target.parents[2] / 'AGENTS.md').read_text())
                else:
                    self.assertFalse(target.exists(), (role, skill))
        ledger = self.fixture.state / 'team-skill-ownership.json'
        self.assertEqual(json.loads(ledger.read_text()), expected_ledger)
        for path, content in preserved.items():
            self.assertEqual(path.read_bytes(), content)
        again = fixtures.reviewed_setup(self.fixture.config, self.team, skills_source=source,
                                        apply=True, runner=self.fixture.runner)
        self.assertFalse(again['config_changed'])
        self.assertEqual(again['workspace_files'], 0)
        # An unreviewed installed edit must be preserved and refused on reapply.
        edited = self.fixture.state / 'workspace-cook/skills/meal-planning/SKILL.md'
        edited.write_text(edited.read_text() + '\nLocal pending edit.\n')
        with self.assertRaisesRegex(self.setup.TeamRefused, 'unreviewed-skill-overwrite-refused'):
            fixtures.reviewed_setup(self.fixture.config, self.team, skills_source=source,
                                    apply=True, runner=self.fixture.runner)
        self.assertTrue(edited.read_text().endswith('Local pending edit.\n'))


if __name__ == '__main__':
    unittest.main()
