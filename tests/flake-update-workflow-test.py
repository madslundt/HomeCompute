"""Exercise the updater's actual shell steps with local Git and synthetic inputs."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'automation/nix-flake-update'


def workflow_commands():
    # Read only the named run steps, preserving the shell exactly as CI runs it.
    source = (ROOT / '.github/workflows/update-flake.yml').read_text()
    for name in ('Update flake inputs', 'Create or refresh update pull request'):
        step = re.search(r'^      - name: ' + re.escape(name) +
                         r'\n(?P<body>.*?)(?=^      - name:|\Z)', source, re.M | re.S)
        run = re.search(r'^        run: (.*)\n?', step['body'], re.M)
        if run[1] == '|':
            yield textwrap.dedent(step['body'][run.end():])
        else:
            yield run[1]


class FlakeUpdateWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / 'checkout'
        self.remote = self.root / 'origin.git'
        self.calls = self.root / 'gh-calls.jsonl'
        binaries = self.root / 'bin'
        binaries.mkdir()
        home = self.root / 'home'
        home.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('GIT_') and key not in
                    ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_HOST', 'EMAIL')}
        self.env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM='1',
                        GIT_CONFIG_GLOBAL=os.devnull,
                        PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                        TEST_LOCK_VALUE='updated', TEST_OPEN_PR_COUNT='0',
                        TEST_GH_CALLS=str(self.calls))
        nix = binaries / 'nix'
        nix.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys
assert sys.argv[1:3] == ['flake', 'update'], sys.argv
lock = Path('flake.lock')
value = os.environ['TEST_LOCK_VALUE'] + '\\n'
if lock.read_text() == value:
    sys.exit(0)
lock.write_text(value)
if '--commit-lock-file' in sys.argv:
    subprocess.run(['git', 'add', 'flake.lock'], check=True)
    subprocess.run(['git', 'commit', '-m', 'Nix lock update'], check=True)
''')
        nix.chmod(0o755)
        gh = binaries / 'gh'
        gh.write_text('''#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['TEST_GH_CALLS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['pr', 'list']:
    print(os.environ['TEST_OPEN_PR_COUNT'])
    sys.exit(0)
assert sys.argv[1:3] == ['pr', 'create'], sys.argv
''')
        gh.chmod(0o755)
        self.command('git', 'init', '--bare', str(self.remote), cwd=self.root)
        self.command('git', 'init', '-b', 'master', str(self.checkout), cwd=self.root)
        self.command('git', 'config', 'user.useConfigOnly', 'true')
        (self.checkout / 'flake.lock').write_text('original\n')
        self.command('git', 'add', 'flake.lock')
        self.command('git', '-c', 'user.name=Fixture', '-c',
                     'user.email=fixture@example.invalid', 'commit', '-m', 'baseline')
        self.base = self.command('git', 'rev-parse', 'HEAD').strip()
        self.command('git', 'remote', 'add', 'origin', str(self.remote))
        self.command('git', 'push', 'origin', 'master')

    def command(self, *argv, cwd=None):
        result = subprocess.run(argv, cwd=cwd or self.checkout, env=self.env,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def run_workflow(self):
        for shell in workflow_commands():
            self.command('bash', '-e', '-c', shell)

    def gh_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] \
            if self.calls.exists() else []

    def test_changed_lock_is_committed_pushed_and_proposed_without_prior_identity(self):
        self.run_workflow()
        self.assertEqual(self.command('git', '--git-dir', str(self.remote),
                                      'show', f'{BRANCH}:flake.lock'),
                         'updated\n')
        self.assertEqual(self.command('git', 'show', '-s', '--format=%an <%ae>'),
                         'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>\n')
        self.assertEqual(self.command('git', 'rev-parse', 'HEAD^').strip(), self.base)
        self.assertEqual(self.command('git', 'status', '--porcelain'), '')
        calls = self.gh_calls()
        self.assertEqual([call[:2] for call in calls], [['pr', 'list'], ['pr', 'create']])
        self.assertIn(BRANCH, calls[1])
        self.assertIn('master', calls[1])

    def test_unchanged_lock_does_not_commit_push_or_open_pr(self):
        self.env['TEST_LOCK_VALUE'] = 'original'
        self.run_workflow()
        self.assertEqual(self.command('git', 'rev-parse', 'HEAD').strip(), self.base)
        self.assertEqual(self.command('git', 'ls-remote', 'origin', f'refs/heads/{BRANCH}'), '')
        self.assertEqual(self.gh_calls(), [])

    def test_existing_pr_branch_is_refreshed_without_duplicate_pr(self):
        self.run_workflow()
        # Each scheduled run starts from a fresh, shallow default-branch checkout.
        fresh = self.root / 'fresh-checkout'
        self.command('git', 'clone', '--branch', 'master', '--depth', '1',
                     '--single-branch', self.remote.as_uri(), str(fresh))
        self.checkout = fresh
        self.command('git', 'config', 'user.useConfigOnly', 'true')
        self.env.update(TEST_LOCK_VALUE='newer', TEST_OPEN_PR_COUNT='1')
        self.calls.unlink()
        self.run_workflow()
        self.assertEqual(self.command('git', '--git-dir', str(self.remote),
                                      'show', f'{BRANCH}:flake.lock'),
                         'newer\n')
        self.assertEqual(self.command('git', 'rev-parse', 'HEAD^').strip(), self.base)
        self.assertEqual([call[:2] for call in self.gh_calls()], [['pr', 'list']])

    def test_merged_update_pr_still_gets_a_replacement_proposal(self):
        # The previous week's pull request merged and closed, so the branch
        # exists on the remote while no open pull request references it.
        self.run_workflow()
        fresh = self.root / 'fresh-checkout'
        self.command('git', 'clone', '--branch', 'master', '--depth', '1',
                     '--single-branch', self.remote.as_uri(), str(fresh))
        self.checkout = fresh
        self.command('git', 'config', 'user.useConfigOnly', 'true')
        self.env.update(TEST_LOCK_VALUE='newer', TEST_OPEN_PR_COUNT='0')
        self.calls.unlink()
        self.run_workflow()
        self.assertEqual(self.command('git', '--git-dir', str(self.remote),
                                      'show', f'{BRANCH}:flake.lock'),
                         'newer\n')
        self.assertEqual([call[:2] for call in self.gh_calls()],
                         [['pr', 'list'], ['pr', 'create']])


if __name__ == '__main__':
    unittest.main()
