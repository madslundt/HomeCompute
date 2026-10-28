"""Location event admission and reminder durability without HA or external writes."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_household_tasks import FamilyTasks, HouseholdTasks
from openclaw_location_policy import Policy, TOKEN_PATH


def policy():
    return Policy.from_value({'schema_version': 1, 'ha_token_file': str(TOKEN_PATH), 'owner_person': 'mads',
        'persons': {'mads': {'name': 'Mads', 'entity_id': 'person.madslundt', 'notify_service': 'mobile_app_mads_iphone'},
                    'kristina': {'name': 'Kristina', 'entity_id': 'person.kristinasorensen',
                                 'notify_service': 'mobile_app_kristina_iphone'}},
        'zones': {'home': {'name': 'Home', 'entity_id': 'zone.home'},
                  'shop': {'name': 'Shop', 'entity_id': 'zone.shop'}}})


class LocationTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        self.policy = policy()
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now, location_policy=self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, operation, args, store=None):
        store = store or self.tasks
        return store.request(store.tool_prefix + operation, args)

    def create(self, store=None, **fields):
        return self.call('create', {'title': 'Bring the parcel', 'request_id': str(uuid4()),
            'reminder_trigger': 'zone_leave', 'trigger_person': 'mads', 'trigger_zone': 'home', **fields}, store)['task']

    def change(self, task, operation='update', store=None, **fields):
        return self.call(operation, {'task_id': task['task_id'], 'expected_revision': task['revision'],
                                    'request_id': str(uuid4()), **fields}, store)['task']

    def event(self, *, seconds=1, **fields):
        self.now += timedelta(seconds=seconds)
        return {'event_id': hashlib.sha256(str(uuid4()).encode()).hexdigest(), 'person': 'mads', 'zone': 'home',
                'event': 'leave', 'occurred_at': self.now.isoformat(), **fields}

    def accept(self, event, store=None):
        return (store or self.tasks).accept_location_event(event, self.now)

    def test_location_defaults_and_scoped_options_do_not_change_legacy_timers(self):
        task = self.create()
        self.assertEqual((task['delivery_target'], task['trigger_repeat'], task['reminder_delivery']),
                         ('person', 'once', 'waiting_for_event'))
        owner = self.call('list', {})['reminder_options']
        self.assertEqual([p['alias'] for p in owner['persons']], ['mads'])
        self.assertEqual(owner['delivery_targets'], ['person'])
        family = FamilyTasks(self.root, clock=lambda: self.now, location_policy=self.policy)
        self.assertEqual(self.call('list', {}, family)['reminder_options']['delivery_targets'], ['person', 'family'])
        legacy = self.call('create', {'title': 'Timer', 'request_id': str(uuid4()), 'remind_at': self.now.isoformat()})['task']
        self.assertEqual((legacy['reminder_trigger'], legacy['delivery_target']), ('time', 'mattermost'))
        self.tasks.reminders(self.now)
        self.assertEqual([r['task_id'] for r in self.tasks.reminder_rows('pending')], [legacy['task_id']])

    def test_location_requests_require_policy_explicit_aliases_and_single_trigger(self):
        original = self.tasks
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        with self.assertRaises(ValueError): self.create()
        self.tasks = original
        for fields in ({'trigger_person': ''}, {'trigger_zone': ''}, {'trigger_person': 'kristina'},
                       {'trigger_zone': 'unknown'}, {'delivery_target': 'family'}, {'delivery_target': 'mattermost'},
                       {'remind_at': self.now.isoformat()},
                       {'due_at': self.now.isoformat(), 'recurrence': 'daily'},
                       {'trigger_expires_at': '2026-10-27T13:00:00'},
                       {'trigger_expires_at': self.now.isoformat()}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): self.create(**fields)
        self.assertEqual(self.call('list', {})['total'], 0)

    def test_events_are_deduplicated_atomically_and_do_not_complete_the_task(self):
        task = self.create()
        event = self.event()
        self.assertEqual(self.accept(event), {'matched': 1, 'duplicate': False})
        self.assertEqual(self.accept(event), {'matched': 0, 'duplicate': True})
        row = self.tasks.reminder_rows('pending')[0]
        self.assertEqual(row['location_context'], {**event, 'generation': task['trigger_generation']})
        self.assertTrue(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now))
        self.assertTrue(self.tasks.reminder_transition(row['key'], 'sending', 'accepted', self.now))
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now, location_policy=self.policy)
        self.assertEqual(self.accept(self.event()), {'matched': 0, 'duplicate': False})
        listed = self.call('list', {})['tasks'][0]
        self.assertEqual((listed['status'], listed['reminder_delivery']), ('open', 'accepted'))
        self.tasks.reminders(self.now)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        with self.assertRaises(ValueError): self.accept({**event, 'zone': 'shop'})

    def test_untrusted_stale_future_and_prearming_events_cannot_enqueue(self):
        self.create()
        event = self.event(seconds=0)
        self.assertEqual(self.accept(event)['matched'], 0)
        event = self.event()
        invalid = [{**event, 'person': 'stranger'}, {**event, 'zone': 'unknown'}, {**event, 'event': 'state_changed'},
                   {**event, 'event_id': 'not-a-hash'}, {**event, 'event': []}, {**event, 'coordinates': [1, 2]},
                   {**event, 'occurred_at': (self.now - timedelta(seconds=121)).isoformat()},
                   {**event, 'occurred_at': (self.now + timedelta(seconds=6)).isoformat()}]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError): self.accept(candidate)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])

    def test_trusted_partner_event_passes_owner_and_matches_only_family_store(self):
        self.create()
        family = FamilyTasks(self.root, clock=lambda: self.now, location_policy=self.policy)
        shared = self.create(family, trigger_person='kristina', delivery_target='family')
        event = self.event(person='kristina')
        self.assertEqual(self.accept(event)['matched'], 0)
        self.assertEqual(self.accept(event, family)['matched'], 1)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertEqual(family.reminder_rows('pending')[0]['task_id'], shared['task_id'])
        self.assertNotEqual(self.tasks.path, family.path)

    def test_metadata_changes_keep_occurrence_and_sent_receipts(self):
        task = self.create()
        self.accept(self.event())
        original = self.tasks.reminder_rows('pending')[0]
        changed = self.change(task, title='Bring the blue parcel', assignee='Someone')
        row = self.tasks.reminder_rows('pending')[0]
        self.assertEqual((row['key'], row['revision']), (original['key'], changed['revision']))
        self.assertEqual(changed['trigger_generation'], task['trigger_generation'])
        self.tasks.reminder_transition(row['key'], 'pending', 'delivered', self.now, 'receipt')
        changed = self.change(changed, notes='At the door')
        self.assertEqual(changed['reminder_delivery'], 'delivered')
        self.assertEqual(self.accept(self.event())['matched'], 0)
        self.assertEqual(self.tasks.reminder_rows('delivered')[0]['post_id'], 'receipt')

    def test_trigger_edit_rearms_and_obsolete_pending_claim_fails(self):
        task = self.create()
        self.accept(self.event())
        original = self.tasks.reminder_rows('pending')[0]
        changed = self.change(task, trigger_zone='shop')
        self.assertEqual(changed['trigger_generation'], task['trigger_generation'] + 1)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertFalse(self.tasks.reminder_transition(original['key'], 'pending', 'sending', self.now))
        self.assertEqual(self.accept(self.event())['matched'], 0)
        self.assertEqual(self.accept(self.event(zone='shop'))['matched'], 1)
        self.assertNotEqual(self.tasks.reminder_rows('pending')[0]['key'], original['key'])

    def test_claim_checks_revision_and_cancelling_or_disabling_works_without_policy(self):
        task = self.create()
        self.accept(self.event())
        row = self.tasks.reminder_rows('pending')[0]
        changed = self.change(task, title='A new title')
        self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now,
                                                       expected_revision=row['revision']))
        self.assertTrue(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now,
                                                      expected_revision=changed['revision']))
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        self.assertTrue(self.tasks.reminder_transition(row['key'], 'sending', 'pending', self.now))
        self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now))
        self.assertEqual(self.change(changed, 'cancel')['status'], 'cancelled')
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now, location_policy=self.policy)
        task = self.create()
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now)
        self.assertEqual(self.change(task, reminder_trigger='none')['reminder_delivery'], 'disabled')

    def test_every_time_uses_distinct_events_and_five_minute_cooldown(self):
        self.create(trigger_repeat='every_time')
        self.accept(self.event())
        first = self.tasks.reminder_rows('pending')[0]
        self.tasks.reminder_transition(first['key'], 'pending', 'accepted', self.now)
        self.assertEqual(self.accept(self.event(seconds=299))['matched'], 0)
        self.assertEqual(self.accept(self.event(seconds=1))['matched'], 1)
        second = self.tasks.reminder_rows('pending')[0]
        self.assertNotEqual(first['key'], second['key'])
        self.assertEqual(len(self.tasks.reminder_rows('accepted')), 1)

    def test_completion_cancellation_and_expiry_prevent_pending_delivery(self):
        for operation in ('complete', 'cancel'):
            with self.subTest(operation=operation):
                task = self.create()
                self.accept(self.event())
                row = self.tasks.reminder_rows('pending')[0]
                self.change(task, operation)
                self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now))
                self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.create(trigger_expires_at=(self.now + timedelta(seconds=5)).isoformat())
        self.accept(self.event())
        row = self.tasks.reminder_rows('pending')[0]
        self.now += timedelta(seconds=5)
        self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now))
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertEqual(self.accept(self.event())['matched'], 0)

    def test_stale_pending_event_is_suppressed_and_a_fresh_event_can_replace_it(self):
        self.create()
        self.accept(self.event())
        original = self.tasks.reminder_rows('pending')[0]
        self.now += timedelta(seconds=301)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertEqual(self.call('list', {})['tasks'][0]['reminder_delivery'], 'waiting_for_event')
        self.assertEqual(self.accept(self.event())['matched'], 1)
        replacement = self.tasks.reminder_rows('pending')[0]
        self.assertEqual(original['key'], replacement['key'])
        self.assertNotEqual(original['location_context']['event_id'], replacement['location_context']['event_id'])

    def test_sending_uncertain_and_accepted_one_shot_receipts_are_never_replayed(self):
        for state in ('sending', 'uncertain', 'accepted', 'delivered'):
            with self.subTest(state=state):
                task = self.create()
                self.accept(self.event())
                row = next(r for r in self.tasks.reminder_rows('pending') if r['task_id'] == task['task_id'])
                self.tasks.reminder_transition(row['key'], 'pending', state, self.now)
                self.now += timedelta(seconds=301)
                self.accept(self.event())
                self.assertFalse(any(r['task_id'] == task['task_id'] for r in self.tasks.reminder_rows('pending')))

    def test_timed_snooze_preserves_phone_and_disable_prevents_event_recreation(self):
        task = self.create(trigger_repeat='every_time')
        self.accept(self.event())
        snoozed = self.change(task, remind_at=(self.now + timedelta(minutes=10)).isoformat())
        self.assertEqual((snoozed['reminder_trigger'], snoozed['delivery_target'], snoozed['trigger_person'],
                          snoozed['trigger_zone'], snoozed['trigger_repeat']), ('time', 'person', 'mads', '', 'once'))
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertEqual(self.accept(self.event())['matched'], 0)
        self.now += timedelta(minutes=10)
        self.tasks.reminders(self.now)
        row = self.tasks.reminder_rows('pending')[0]
        self.assertIsNone(row['location_context'])
        disabled = self.change(snoozed, reminder_trigger='none')
        self.assertEqual(disabled['reminder_delivery'], 'disabled')
        self.assertFalse(self.tasks.reminder_transition(row['key'], 'pending', 'sending', self.now))
        self.tasks.reminders(self.now)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])

    def test_old_database_migrates_without_losing_a_delivered_receipt(self):
        task = self.call('create', {'title': 'Old timer', 'request_id': str(uuid4()),
                                   'remind_at': self.now.isoformat()})['task']
        self.tasks.reminders(self.now)
        row = self.tasks.reminder_rows('pending')[0]
        self.tasks.reminder_transition(row['key'], 'pending', 'delivered', self.now, 'old-receipt')
        with self.tasks.connect() as db:
            body = json.loads(db.execute('SELECT body FROM household_tasks WHERE id=?', (task['task_id'],)).fetchone()[0])
            for key in ('reminder_trigger', 'trigger_person', 'trigger_zone', 'trigger_repeat', 'trigger_expires_at',
                        'delivery_target', 'trigger_generation', 'trigger_armed_at'):
                body.pop(key, None)
            db.execute('UPDATE household_tasks SET body=? WHERE id=?', (json.dumps(body), task['task_id']))
            db.execute('ALTER TABLE household_task_reminders DROP COLUMN location_context')
            db.execute('DROP TABLE household_location_events')
        self.tasks = HouseholdTasks(self.root, clock=lambda: self.now, location_policy=self.policy)
        changed = self.change(task, title='Updated old timer')
        self.assertEqual(changed['reminder_delivery'], 'delivered')
        self.tasks.reminders(self.now)
        self.assertEqual(self.tasks.reminder_rows('pending'), [])
        self.assertEqual(self.tasks.reminder_rows('delivered')[0]['post_id'], 'old-receipt')


if __name__ == '__main__':
    unittest.main()
