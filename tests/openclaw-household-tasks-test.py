"""Durable household tasks, recurrence and delivery recovery without external writes."""
import importlib.util
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_household_tasks as task_module
from openclaw_household_tasks import FamilyTasks, HouseholdTasks, ZONE, instant

spec = importlib.util.spec_from_file_location('household_reminders', ROOT / 'scripts/openclaw-household-task-reminders.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class HouseholdTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, operation, args):
        return self.tasks.request('homecompute_household_task_' + operation, args)

    def create(self, **fields):
        return self.call('create', {'title': 'Replace filter', 'request_id': str(uuid4()), **fields})['task']

    def change(self, operation, task, **fields):
        return self.call(operation, {'task_id': task['task_id'], 'expected_revision': task['revision'],
                                    'request_id': str(uuid4()), **fields})['task']

    def test_restart_replays_atomic_write_and_refuses_request_reuse(self):
        args = {'title': 'Pay synthetic invoice', 'request_id': str(uuid4()), 'assignee': 'Owner'}
        receipt = self.call('create', args)
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        replay = self.call('create', args)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['task'], receipt['task'])
        self.assertEqual(self.call('list', {})['total'], 1)
        with self.assertRaises(ValueError): self.call('create', {**args, 'title': 'Different'})
        self.assertEqual(self.tasks.path.stat().st_mode & 0o777, 0o600)
        with self.tasks.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM household_task_events').fetchone()[0], 1)

    def test_revision_conflict_cannot_overwrite_another_update(self):
        task = self.create(notes='Keep this', due_at='2026-10-28T09:00:00+01:00')
        changed = self.change('update', task, assignee='Partner')
        self.assertEqual(changed['notes'], 'Keep this')
        self.assertEqual(changed['remind_at'], '')  # A deadline does not request delivery.
        with self.assertRaises(ValueError): self.change('complete', task)
        self.assertEqual(self.call('list', {})['tasks'][0]['assignee'], 'Partner')

    def test_invalid_schema_dates_and_unrequested_targets_are_refused(self):
        for args in ({'title': 'Task', 'due_at': '2026-10-28T09:00'},
                     {'title': 'Task', 'due_at': '2026-02-30T09:00+01:00'},
                     {'title': 'Task', 'channel_id': 'another-private-room'},
                     {'title': 'Task', 'recurrence': 'daily'},
                     {'title': 'Task', 'due_at': '2026-10-28T09:00+01:00', 'remind_at': '2026-10-28T10:00+01:00'},
                     {'title': 'Task', 'principal': 'partner'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.call('create', {**args, 'request_id': str(uuid4())})
        self.assertEqual(self.call('list', {})['total'], 0)

    def test_daily_and_weekly_recurring_tasks_preserve_copenhagen_time_across_dst(self):
        self.now = datetime(2026, 3, 28, 9, tzinfo=timezone.utc)
        task = self.create(due_at='2026-03-28T09:00:00+01:00', recurrence='daily',
                           remind_at='2026-03-28T08:30:00+01:00')
        completed = self.change('complete', task)
        due = instant(completed['due_at']).astimezone(ZONE)
        self.assertEqual((due.day, due.hour, due.utcoffset().total_seconds()), (29, 9, 7200))
        self.assertEqual((instant(completed['due_at']) - instant(completed['remind_at'])).total_seconds(), 1800)
        self.assertEqual(completed['status'], 'open')
        self.now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        task = self.create(due_at='2026-10-18T09:00:00+02:00', recurrence='weekly')
        completed = self.change('complete', task)
        self.assertGreater(instant(completed['due_at']), self.now)
        self.assertEqual(instant(completed['due_at']).astimezone(ZONE).hour, 9)

    def test_monthly_recurrence_preserves_day_31_after_february(self):
        self.now = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
        task = self.create(due_at='2026-01-31T09:00:00+01:00', recurrence='monthly')
        february = self.change('complete', task)
        self.assertEqual(instant(february['due_at']).astimezone(ZONE).day, 28)
        self.now = datetime(2026, 2, 28, 12, tzinfo=timezone.utc)
        march = self.change('complete', february)
        self.assertEqual(instant(march['due_at']).astimezone(ZONE).day, 31)

    def test_spring_clock_gap_shifts_one_occurrence_without_moving_the_series(self):
        self.now = datetime(2026, 3, 28, 9, tzinfo=timezone.utc)
        task = self.create(due_at='2026-03-28T02:30:00+01:00', recurrence='daily')
        shifted = self.change('complete', task)
        self.assertEqual(instant(shifted['due_at']).astimezone(ZONE).strftime('%m-%d %H:%M'), '03-29 03:30')
        self.now = datetime(2026, 3, 29, 9, tzinfo=timezone.utc)
        restored = self.change('complete', shifted)
        self.assertEqual(instant(restored['due_at']).astimezone(ZONE).strftime('%m-%d %H:%M'), '03-30 02:30')

    def test_delivery_receipt_must_match_exact_bot_room_and_reminder_marker(self):
        client = object.__new__(worker.Mattermost)
        client.config = {'channel_id': 'a' * 26, 'bot_id': 'b' * 26}
        post = {'id': 'c' * 26, 'channel_id': 'a' * 26, 'user_id': 'b' * 26, 'props': {worker.MARKER: 'key'}}
        self.assertEqual(client.receipt(post, 'key'), 'c' * 26)
        for changed in ({'channel_id': 'x' * 26}, {'user_id': 'x' * 26}, {'props': []},
                        {'props': {worker.MARKER: 'different'}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                client.receipt({**post, **changed}, 'key')

    def test_reminders_survive_restart_and_metadata_updates_do_not_repeat_delivery(self):
        task = self.create(due_at='2026-10-27T12:00:00+01:00', remind_at='2026-10-27T12:00:00+01:00')
        client = Mock()
        client.send.return_value = 'a' * 26
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 1)
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        self.change('update', task, title='A clearer filter title')
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 0)
        self.assertEqual(client.send.call_count, 1)
        self.assertEqual(self.call('list', {})['tasks'][0]['reminder_delivery'], 'delivered')

    def test_quiet_hours_defer_past_due_delivery_and_cancellation_suppresses_pending(self):
        self.now = datetime(2026, 10, 27, 22, tzinfo=timezone.utc)
        first = self.create(due_at='2026-10-27T22:00:00+01:00', remind_at='2026-10-27T22:00:00+01:00')
        self.create(title='Another task', due_at='2026-10-27T22:00:00+01:00', remind_at='2026-10-27T22:00:00+01:00')
        client = Mock(); client.send.return_value = 'a' * 26
        self.assertTrue(worker.tick(self.tasks, client, self.now)['quiet'])
        client.send.assert_not_called()
        self.change('cancel', first)
        self.now = datetime(2026, 10, 28, 6, tzinfo=timezone.utc)
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 1)
        self.assertEqual(client.send.call_count, 1)

    def test_interrupted_or_lost_receipt_reconciles_without_sending_twice(self):
        self.create(due_at='2026-10-27T12:00:00+01:00', remind_at='2026-10-27T12:00:00+01:00')
        client = Mock(); client.send.side_effect = OSError('private upstream detail')
        self.assertEqual(worker.tick(self.tasks, client, self.now)['uncertain'], 1)
        client.send.assert_called_once()
        client.reconcile.return_value = None
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        self.assertEqual(worker.tick(self.tasks, client, self.now)['uncertain'], 1)
        self.assertEqual(client.send.call_count, 1)
        client.reconcile.return_value = 'b' * 26
        self.assertEqual(worker.tick(self.tasks, client, self.now)['reconciled'], 1)
        self.assertEqual(client.send.call_count, 1)

    def test_explicit_rate_limit_can_retry_and_reminder_reschedule_changes_identity(self):
        task = self.create(due_at='2026-10-27T12:00:00+01:00', remind_at='2026-10-27T12:00:00+01:00')
        client = Mock(); client.send.side_effect = urllib.error.HTTPError('http://local', 429, 'limited', {}, None)
        self.assertEqual(worker.tick(self.tasks, client, self.now)['retryable'], 1)
        client.send.side_effect = None; client.send.return_value = 'b' * 26
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 1)
        self.change('update', task, due_at='2026-10-28T12:00:00+01:00')
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 0)
        self.now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 1)

    def test_deadlines_and_disabled_reminders_never_project_into_the_tasks_channel(self):
        deadline = self.create(due_at='2026-10-27T12:00:00+01:00')
        explicit = self.create(title='Requested reminder', due_at='2026-10-27T12:00:00+01:00',
                               remind_at='2026-10-27T12:00:00+01:00')
        self.change('update', explicit, remind_at='')
        client = Mock()
        self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 0)
        client.send.assert_not_called()
        self.assertEqual(deadline['reminder_delivery'], 'disabled')
        updated = self.change('update', deadline, due_at='2026-10-28T12:00:00+01:00')
        self.assertEqual(updated['remind_at'], '')

    def test_list_paginates_with_exact_revisions_without_overstating_coverage(self):
        for n in range(25): self.create(title=f'Task {n}', notes='x' * 1000)
        first = self.call('list', {})
        self.assertEqual(first['total'], 25)
        self.assertLessEqual(len(first['tasks']), 20)
        self.assertIsNotNone(first['next_offset'])
        second = self.call('list', {'offset': first['next_offset']})
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len(first['tasks']) + len(second['tasks']), 25)


class FamilyTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.owner = HouseholdTasks(self.root, clock=lambda: self.now)
        self.family = FamilyTasks(self.root, clock=lambda: self.now)

    def call(self, operation, **arguments):
        return self.family.request('homecompute_family_task_' + operation, arguments)

    def test_owner_records_never_appear_in_shared_family_and_names_cannot_switch_scope(self):
        request_id = str(uuid4())
        owner = self.owner.request('homecompute_household_task_create',
            {'title': 'Private owner record', 'request_id': request_id})['task']
        shared = self.call('create', title='Pack shared bags', request_id=request_id)
        self.assertEqual(shared['source'], 'shared-family-tasks')
        self.assertNotEqual(self.owner.path, self.family.path)
        self.assertEqual(self.family.path.name, 'family-tasks.sqlite3')
        result = self.call('list')
        self.assertEqual([t['title'] for t in result['tasks']], ['Pack shared bags'])
        self.assertNotIn('Private owner record', json.dumps(result))
        self.assertEqual(self.owner.request('homecompute_household_task_list', {})['total'], 1)
        for operation in ('homecompute_household_task_list', 'homecompute_household_task_create'):
            with self.assertRaises(ValueError): self.family.request(operation, {})
        with self.assertRaises(ValueError):
            self.call('complete', task_id=owner['task_id'], expected_revision=1, request_id=str(uuid4()))
        with self.assertRaises(ValueError):
            self.call('list', principal_scope='owner')

    def test_shared_replay_revision_conflict_and_recurrence_survive_restart(self):
        args = {'title': 'Prepare school bags', 'request_id': str(uuid4()), 'assignee': 'Mads',
                'due_at': '2026-10-27T09:00:00+01:00', 'recurrence': 'daily'}
        first = self.call('create', **args)
        self.family = FamilyTasks(self.root, clock=lambda: self.now)
        replay = self.call('create', **args)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['source'], 'shared-family-tasks')
        self.assertEqual(first['task'], replay['task'])
        task = first['task']
        changed = self.call('complete', task_id=task['task_id'], expected_revision=1, request_id=str(uuid4()))['task']
        self.assertEqual(instant(changed['due_at']).astimezone(ZONE).strftime('%Y-%m-%d %H:%M'), '2026-10-28 09:00')
        self.assertEqual(changed['reminder_delivery'], 'disabled')
        with self.assertRaises(ValueError):
            self.call('update', task_id=task['task_id'], expected_revision=1, request_id=str(uuid4()), assignee='Kristina')

    def test_shared_delivery_uses_only_family_state_and_family_marker(self):
        self.owner.request('homecompute_household_task_create', {'title': 'Private reminder',
            'request_id': str(uuid4()), 'remind_at': '2026-10-27T10:00+01:00'})
        self.call('create', title='Shared reminder', request_id=str(uuid4()), remind_at='2026-10-27T10:00+01:00')
        client = Mock(); client.send.return_value = 'a' * 26
        self.assertEqual(worker.tick(self.family, client, self.now)['delivered'], 1)
        self.assertEqual(client.send.call_args.args[0]['task']['title'], 'Shared reminder')
        self.assertEqual(self.owner.reminder_rows('delivered'), [])
        delivery = object.__new__(worker.Mattermost)
        delivery.config = {'channel_id': 'f' * 26, 'bot_id': 'b' * 26}
        delivery.scope, delivery.marker = 'family', worker.FAMILY_MARKER
        row = client.send.call_args.args[0]
        def posted(path, body):
            self.assertEqual(body['channel_id'], 'f' * 26)
            self.assertEqual(body['props'], {worker.FAMILY_MARKER: row['key']})
            self.assertIn('Family task reminder: Shared reminder', body['message'])
            return {'id': 'p' * 26, 'user_id': 'b' * 26, **body}
        delivery.request = Mock(side_effect=posted)
        self.assertEqual(delivery.send(row), 'p' * 26)
        owner_post = {'id': 'p' * 26, 'channel_id': 'f' * 26, 'user_id': 'b' * 26,
                      'props': {worker.MARKER: row['key']}}
        with self.assertRaises(ValueError): delivery.receipt(owner_post, row['key'])
        with self.assertRaises(ValueError):
            delivery.receipt({**owner_post, 'channel_id': 'o' * 26,
                              'props': {worker.FAMILY_MARKER: row['key']}}, row['key'])

    def test_owner_delivery_config_cannot_be_used_for_family(self):
        path = self.root / 'delivery.json'
        config = {'schema_version': 1, 'channel_id': 'f' * 26, 'bot_id': 'b' * 26,
                  'token_file': str(task_module.TOKEN_PATH)}
        path.write_text(json.dumps(config)); path.chmod(0o600)
        with self.assertRaises(ValueError): task_module.configuration(path, scope='family')
        config['token_file'] = str(task_module.FAMILY_TOKEN_PATH)
        path.write_text(json.dumps(config))
        self.assertEqual(task_module.configuration(path, scope='family'), config)
        with self.assertRaises(ValueError): task_module.configuration(path)
        with self.assertRaises(ValueError): task_module.configuration(path, scope='other')
        token = self.root / 'family-token'; token.write_text('a' * 26); token.chmod(0o600)
        with patch.object(worker, 'FAMILY_TOKEN_PATH', token):
            configured = worker.Mattermost({**config, 'token_file': str(token)}, scope='family')
            self.assertEqual(configured.marker, worker.FAMILY_MARKER)
            with self.assertRaises(ValueError): worker.Mattermost(config, scope='family')

    def test_family_projection_survives_reminder_failure_and_check_never_projects(self):
        publish = Mock()
        client = Mock()
        client.verify.side_effect = ValueError('recipient temporarily unavailable')
        argv = ['reminders', '--config', str(self.root / 'delivery.json'), '--state', str(self.root),
                '--scope', 'family', '--project-home-assistant']
        module = SimpleNamespace(publish=publish)
        with patch.object(sys, 'argv', argv), patch.dict(sys.modules, {'openclaw_family_task_projection': module}), \
                patch.object(worker, 'configuration', return_value={}), patch.object(worker, 'Mattermost', return_value=client), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            worker.main()
        publish.assert_called_once()
        self.assertIsInstance(publish.call_args.args[0], FamilyTasks)
        client.send.assert_not_called()
        publish.reset_mock(); client.reset_mock(); client.verify.side_effect = None
        with patch.object(sys, 'argv', argv + ['--check']), patch.dict(sys.modules, {'openclaw_family_task_projection': module}), \
                patch.object(worker, 'configuration', return_value={}), patch.object(worker, 'Mattermost', return_value=client), \
                redirect_stdout(io.StringIO()):
            worker.main()
        publish.assert_not_called()
        client.send.assert_not_called()

    def test_owner_scope_cannot_publish_to_the_shared_family_sensor(self):
        argv = ['reminders', '--config', 'unused', '--state', str(self.root), '--project-home-assistant']
        with patch.object(sys, 'argv', argv), self.assertRaises(SystemExit) as error, redirect_stdout(io.StringIO()):
            with patch.object(sys, 'stderr', io.StringIO()): worker.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
