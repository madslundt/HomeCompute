#!/usr/bin/env python3
"""Exercise guarded Hermes ChatGPT profile reconciliation."""
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / 'scripts/setup-hermes-chatgpt-profiles.py'))
DESIRED = SETUP['roster']()
CLI = ['nemohermes', 'hermes-personal-v2']


class FakeHermes:
    def __init__(self):
        self.states = {item['name']: 'absent' for item in DESIRED['profiles']}
        self.operations = []

    def __call__(self, argv, data=None):
        self.operations.append((argv, data))
        if argv[2:4] == ['exec', '--stdin'] and argv[-2:] == ['-c', SETUP['INSPECT']]:
            return json.dumps({name: {'state': state} for name, state in self.states.items()})
        if argv[2:4] == ['exec', '--'] and argv[4] == '/opt/hermes/.venv/bin/hermes':
            self.states[argv[7]] = 'created'
            return b''
        if argv[2:4] == ['exec', '--stdin'] and argv[-2:] == ['-c', SETUP['PATCH']]:
            payload = json.loads(data)
            for item in payload['profiles']:
                self.states[item['name']] = 'managed'
            return b''
        raise AssertionError(f'unexpected native command: {argv}')


class HermesChatGPTProfileTests(unittest.TestCase):
    def test_reviewed_plan_required_and_profile_plan_is_bound_to_state(self):
        fake = FakeHermes()
        before = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        digest = SETUP['plan_digest']('a' * 40, DESIRED, before)
        self.assertEqual(set(x['state'] for x in before.values()), {'absent'})
        with self.assertRaisesRegex(ValueError, 'reviewed --expected-plan-sha256 required'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', revision='a' * 40)
        with self.assertRaisesRegex(ValueError, 'state changed'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', '0' * 64, 'a' * 40)
        self.assertEqual(fake.operations[-1][0][2:4], ['exec', '--stdin'])
        self.assertEqual(digest, SETUP['plan_digest']('a' * 40, DESIRED, before))
        self.assertEqual(fake.states, {item['name']: 'absent' for item in DESIRED['profiles']})

    def test_apply_and_resume_are_idempotent(self):
        fake = FakeHermes()
        before = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        digest = SETUP['plan_digest']('b' * 40, DESIRED, before)
        result = SETUP['reconcile'](fake, CLI, DESIRED,
                                   {item['name']: 'sk-test' for item in DESIRED['profiles']},
                                   'apply', digest, 'b' * 40)
        self.assertEqual({name: row['state'] for name, row in result.items()},
                         dict.fromkeys(fake.states, 'managed'))
        creates = [row for row in fake.operations if row[0][2:4] == ['exec', '--']]
        self.assertEqual(len(creates), 3)
        after = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        result = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply',
                                   SETUP['plan_digest']('b' * 40, DESIRED, after), 'b' * 40)
        self.assertEqual({name: row['state'] for name, row in result.items()},
                         dict.fromkeys(fake.states, 'managed'))
        creates_after = [row for row in fake.operations if row[0][2:4] == ['exec', '--']]
        self.assertEqual(len(creates_after), 3)

    def test_existing_profile_conflict_blocks_every_change(self):
        fake = FakeHermes()
        fake.states['chatgpt-terra'] = 'conflict'
        with self.assertRaisesRegex(ValueError, 'differs'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', 'a' * 64, 'c' * 40)
        self.assertFalse(any(row[0][2:4] == ['exec', '--'] for row in fake.operations))


if __name__ == '__main__':
    unittest.main()
