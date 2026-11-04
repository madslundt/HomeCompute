"""Exercise opt-in task watches through the real store and reminder dispatcher."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_household_tasks import HouseholdTasks, FamilyTasks
from openclaw_task_followups import key
spec = importlib.util.spec_from_file_location('reminders', ROOT / 'scripts/openclaw-household-task-reminders.py')
worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        self.client = Mock(); self.client.send.return_value = 'a' * 26

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **extra):
        return self.tasks.request(self.tasks.tool_prefix + 'create', {
            'title': 'Synthetic unfinished task', 'request_id': str(uuid4()),
            'follow_up_at': (self.now + timedelta(minutes=1)).isoformat(),
            'follow_up_expires_at': (self.now + timedelta(days=1)).isoformat(), **extra})['task']

    def change(self, task, op='update', **extra):
        return self.tasks.request(self.tasks.tool_prefix + op, {'task_id': task['task_id'],
            'expected_revision': task['revision'], 'request_id': str(uuid4()), **extra})['task']

    def test_one_notification_survives_restart_and_title_edit_without_changing_reminder(self):
        task = self.create()
        self.assertEqual(task['reminder_delivery'], 'disabled')
        self.assertEqual(task['follow_up_delivery'], 'scheduled')
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['delivered'], 0)
        self.now += timedelta(minutes=2)
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['delivered'], 1)
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        current = self.change(task, title='Corrected task title')
        self.assertEqual(current['follow_up_delivery'], 'delivered')
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['delivered'], 0)
        self.assertEqual(self.client.send.call_count, 1)

    def test_quiet_hours_defer_only_watch_and_expiry_prevents_late_catchup(self):
        self.now = datetime(2026, 11, 4, 21, 0, tzinfo=timezone.utc)
        task = self.create(follow_up_expires_at='2026-11-05T05:00:00Z')
        self.tasks.request(self.tasks.tool_prefix + 'create', {'title': 'Explicit night reminder',
            'request_id': str(uuid4()), 'remind_at': '2026-11-04T21:01:00Z'})
        self.now += timedelta(minutes=2)
        result = worker.tick(self.tasks, self.client, self.now)
        self.assertTrue(result['quiet']); self.assertEqual(result['delivered'], 1)
        self.now += timedelta(hours=12)
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['delivered'], 0)
        listed = self.tasks.request(self.tasks.tool_prefix + 'list', {})['tasks']
        current = next(row for row in listed if row['task_id'] == task['task_id'])
        self.assertEqual(current['follow_up_delivery'], 'expired')
        self.assertFalse(self.tasks.reminder_transition(key(task), 'pending', 'sending', self.now))

    def test_complete_cancel_clear_and_stale_claim_fence_future_dispatch(self):
        for op in ('complete', 'cancel', 'clear'):
            task = self.create(); self.now += timedelta(minutes=2)
            self.tasks.reminders(self.now)
            self.change(task, op='update' if op == 'clear' else op, **({'follow_up_at': ''} if op == 'clear' else {}))
            self.assertFalse(self.tasks.reminder_transition(key(task), 'pending', 'sending', self.now,
                                                          expected_revision=task['revision']))
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['delivered'], 0)
        self.client.send.assert_not_called()

    def test_unknown_delivery_is_held_and_reconciled_without_replay_even_after_expiry(self):
        self.create(); self.now += timedelta(minutes=2)
        self.client.send.side_effect = OSError('lost receipt')
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['uncertain'], 1)
        self.now += timedelta(days=2); self.client.reconcile.return_value = None
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['uncertain'], 1)
        self.client.reconcile.return_value = 'b' * 26
        self.assertEqual(worker.tick(self.tasks, self.client, self.now)['reconciled'], 1)
        self.assertEqual(self.client.send.call_count, 1)

    def test_explicit_finite_watch_required_and_recurring_completion_does_not_rearm(self):
        for fields in ({'follow_up_expires_at': ''}, {'follow_up_at': '2026-11-04T12:00'},
                       {'follow_up_at': self.now.isoformat()},
                       {'follow_up_expires_at': (self.now + timedelta(days=31)).isoformat()}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): self.create(**fields)
        task = self.create(due_at='2026-11-04T13:00:00Z', recurrence='daily')
        result = self.change(task, op='complete')
        self.assertEqual(result['status'], 'open'); self.assertEqual(result['follow_up_delivery'], 'disabled')

    def test_owner_and_family_ledgers_and_destinations_remain_separate(self):
        owner = self.create()
        shared = FamilyTasks(self.root, clock=lambda: self.now)
        self.assertEqual(shared.request(shared.tool_prefix + 'list', {})['total'], 0)
        self.now += timedelta(minutes=2); self.tasks.reminders(self.now)
        row = self.tasks.reminder_rows('pending')[0]
        row['task']['delivery_target'] = 'person'
        router = worker.DeliveryRouter({}, scope='owner')
        mm = Mock(); mm.send.return_value = 'c' * 26; router._mattermost = mm
        self.assertEqual(router.send(row), 'c' * 26)
        self.assertEqual(router.terminal_state(row), 'delivered')
        self.assertEqual(row['task']['task_id'], owner['task_id'])


if __name__ == '__main__': unittest.main()
