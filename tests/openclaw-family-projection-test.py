"""Shared briefing projections must never admit private owner task content."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from openclaw_family_task_projection import payload
from openclaw_household_tasks import FamilyTasks, HouseholdTasks


class FamilyProjectionTests(unittest.TestCase):
    def test_projection_contains_only_open_shared_tasks_without_private_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.chmod(0o700)
            family, owner = FamilyTasks(state), HouseholdTasks(state)
            owner.request('homecompute_household_task_create', {'title': 'Private appointment', 'request_id': str(uuid4())})
            saved = family.request('homecompute_family_task_create', {
                'title': 'Prepare school bags', 'notes': 'Long working notes', 'request_id': str(uuid4())})['task']
            now = datetime.now(timezone.utc)
            result = payload(family, now)
            self.assertEqual(result['state'], '1')
            self.assertEqual(result['attributes']['source'], 'shared-family-tasks')
            self.assertNotIn('Private appointment', json.dumps(result))
            self.assertNotIn('Long working notes', json.dumps(result))
            family.request('homecompute_family_task_complete', {
                'task_id': saved['task_id'], 'expected_revision': saved['revision'], 'request_id': str(uuid4())})
            self.assertEqual(payload(family, now)['attributes']['tasks'], [])
            with self.assertRaises(ValueError):
                payload(owner, now)

    def test_projection_pages_then_explicitly_reports_its_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.chmod(0o700)
            family = FamilyTasks(state)
            for i in range(101):
                family.request('homecompute_family_task_create', {'title': 'Task ' + str(i), 'request_id': str(uuid4())})
            result = payload(family, datetime.now(timezone.utc))['attributes']
            self.assertEqual(result['total'], 101)
            self.assertEqual(len(result['tasks']), 100)
            self.assertTrue(result['truncated'])


if __name__ == '__main__':
    unittest.main()
