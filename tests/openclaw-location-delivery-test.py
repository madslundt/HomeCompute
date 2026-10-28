"""Exercise the durable event-to-delivery boundary without external requests."""
import fcntl
import hashlib
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_household_tasks import HouseholdTasks, FamilyTasks
from openclaw_location_policy import Policy, TOKEN_PATH
from openclaw_ha_websocket import HomeAssistantError

spec = importlib.util.spec_from_file_location('location_delivery_worker', ROOT / 'scripts/openclaw-household-task-reminders.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.state.chmod(0o700)
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.policy = Policy.from_value({'schema_version': 1, 'ha_token_file': str(TOKEN_PATH), 'owner_person': 'mads',
            'persons': {'mads': {'name': 'Mads', 'entity_id': 'person.madslundt', 'notify_service': 'mobile_app_mads_iphone'},
                        'kristina': {'name': 'Kristina', 'entity_id': 'person.kristinasorensen', 'notify_service': 'mobile_app_kristina_iphone'}},
            'zones': {'home': {'name': 'Home', 'entity_id': 'zone.home'}}})
        self.tasks = HouseholdTasks(self.state, clock=lambda: self.now, location_policy=self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, store=None, **extra):
        store = store or self.tasks
        return store.request(store.tool_prefix + 'create', {'title': 'Bring parcel', 'request_id': str(uuid4()),
            'reminder_trigger': 'zone_leave', 'trigger_person': 'mads', 'trigger_zone': 'home', **extra})['task']

    def accept(self, store=None):
        self.now += timedelta(seconds=1)
        return (store or self.tasks).accept_location_event({'event_id': hashlib.sha256(str(uuid4()).encode()).hexdigest(),
            'person': 'mads', 'zone': 'home', 'event': 'leave', 'occurred_at': self.now.isoformat()}, self.now)

    def test_phone_acceptance_is_durable_and_does_not_complete_task(self):
        task = self.create()
        self.accept()
        router = worker.DeliveryRouter({}, scope='owner', policy=self.policy)
        with patch('openclaw_location_transport.LocationClient') as phone, patch.object(worker, 'Mattermost') as mm:
            phone.return_value.send_person.return_value = 'ha-service-accepted'
            self.assertEqual(worker.tick(self.tasks, router, self.now)['accepted'], 1)
            self.assertEqual(worker.tick(self.tasks, router, self.now)['accepted'], 0)
            self.assertEqual(phone.return_value.send_person.call_count, 1)
            self.assertEqual(phone.return_value.send_person.call_args.args[0]['scope'], 'owner')
            mm.assert_not_called()
        listed = self.tasks.request(self.tasks.tool_prefix + 'list', {})['tasks'][0]
        self.assertEqual((listed['task_id'], listed['status'], listed['reminder_delivery']),
                         (task['task_id'], 'open', 'accepted'))

    def test_uncertain_phone_send_is_held_without_fallback_or_retry(self):
        self.create()
        self.accept()
        router = worker.DeliveryRouter({}, scope='owner', policy=self.policy)
        with patch('openclaw_location_transport.LocationClient') as phone, patch.object(worker, 'Mattermost') as mm:
            phone.return_value.send_person.side_effect = HomeAssistantError(uncertain=True)
            self.assertEqual(worker.tick(self.tasks, router, self.now)['uncertain'], 1)
            self.assertEqual(worker.tick(self.tasks, router, self.now)['uncertain'], 1)
            self.accept()
            worker.tick(self.tasks, router, self.now)
            self.assertEqual(phone.return_value.send_person.call_count, 1)
            mm.assert_not_called()

    def test_family_destination_uses_only_fixed_family_mattermost(self):
        family = FamilyTasks(self.state, clock=lambda: self.now, location_policy=self.policy)
        self.create(family, delivery_target='family')
        self.accept(family)
        router = worker.DeliveryRouter({'fixed': True}, scope='family', policy=self.policy)
        with patch.object(worker, 'Mattermost') as mm, patch('openclaw_location_transport.LocationClient') as phone:
            mm.return_value.send.return_value = 'receipt'
            self.assertEqual(worker.tick(family, router, self.now)['delivered'], 1)
            mm.assert_called_once_with({'fixed': True}, scope='family')
            mm.return_value.verify.assert_called_once()
            phone.assert_not_called()

    def test_cancellation_after_read_cannot_claim_or_send(self):
        task = self.create()
        self.accept()
        original = self.tasks.reminder_rows
        def rows(state):
            result = original(state)
            if state == 'pending' and result:
                self.tasks.request(self.tasks.tool_prefix + 'cancel', {'task_id': task['task_id'],
                    'expected_revision': task['revision'], 'request_id': str(uuid4())})
            return result
        with patch.object(self.tasks, 'reminder_rows', side_effect=rows):
            client = Mock()
            self.assertEqual(worker.tick(self.tasks, client, self.now)['delivered'], 0)
            client.send.assert_not_called()

    def test_title_change_after_read_requires_a_fresh_claim(self):
        task = self.create()
        self.accept()
        row = self.tasks.reminder_rows('pending')[0]
        self.tasks.request(self.tasks.tool_prefix + 'update', {'task_id': task['task_id'],
            'expected_revision': task['revision'], 'request_id': str(uuid4()), 'title': 'New parcel'})
        self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now,
                                                        expected_revision=row['revision']))
        fresh = self.tasks.reminder_rows('pending')[0]
        self.assertEqual(fresh['task']['title'], 'New parcel')
        self.assertTrue(self.tasks.reminder_transition(fresh['key'], 'pending', 'sending', self.now,
                                                       expected_revision=fresh['revision']))

    def test_timer_and_event_worker_share_scope_lock(self):
        path = self.state / 'household-task-reminders.lock'
        path.touch(mode=0o600)
        with path.open() as lock, patch.object(worker, 'configuration') as config:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(worker.run_scope('owner', self.state, Path('/unused')), {'busy': True})
            config.assert_not_called()


if __name__ == '__main__':
    unittest.main()
