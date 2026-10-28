"""Synthetic policy, HA zone subscriptions and phone transport; no live requests."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import openclaw_ha_websocket as ha
import openclaw_location_policy as policy_module
from openclaw_location_policy import Policy
import openclaw_location_transport as transport

spec = importlib.util.spec_from_file_location('ha_wire_fixture', Path(__file__).with_name('openclaw-ha-websocket-test.py'))
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
TOKEN = wire.TOKEN


def configuration():
    return {'schema_version': 1, 'ha_token_file': str(policy_module.TOKEN_PATH), 'owner_person': 'mads',
            'persons': {'mads': {'name': 'Mads', 'entity_id': 'person.madslundt', 'notify_service': 'mobile_app_mads_iphone'},
                        'kristina': {'name': 'Kristina', 'entity_id': 'person.kristinasorensen', 'notify_service': 'mobile_app_kristina_iphone'}},
            'zones': {'home': {'name': 'Home', 'entity_id': 'zone.home'}}}


def task(person='mads', **changes):
    return {'title': 'Husk nøgler @all', 'status': 'open', 'remind_at': '', 'reminder_trigger': 'zone_leave',
            'trigger_person': person, 'trigger_zone': 'home', 'delivery_target': 'person',
            'trigger_repeat': 'once', 'trigger_expires_at': '', **changes}


def event(identifier=1, person='mads', kind='enter', stamp=None):
    entity = configuration()['persons'][person]['entity_id']
    state = {'entity_id': entity, 'state': 'home', 'last_updated': stamp or datetime.now(timezone.utc).isoformat(),
             'attributes': {'latitude': 55.12345, 'longitude': 12.98765, 'secret': TOKEN}}
    return {'id': identifier, 'type': 'event', 'event': {'context': {'id': '01K00000000000000000000001'},
            'variables': {'trigger': {'entity_id': entity, 'from_state': copy.deepcopy(state), 'to_state': state,
                                     'zone': {'entity_id': 'zone.home', 'attributes': {'latitude': 55.1}}, 'event': kind}}}}


class Response:
    status = 200
    def __init__(self, body=b'[]'):
        self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.body[:size]


class PolicyTests(unittest.TestCase):
    def test_options_are_scoped_safe_copies_and_configuration_is_finite(self):
        policy = Policy.from_value(configuration())
        owner = policy.options('owner')
        self.assertEqual(owner['persons'], [{'alias': 'mads', 'name': 'Mads'}])
        self.assertEqual(len(policy.options('family')['persons']), 2)
        self.assertEqual(owner['zones'], [{'alias': 'home', 'name': 'Home'}])
        self.assertEqual(owner['delivery_targets'], ['person'])
        self.assertEqual(len(policy.rules()), 4)
        for forbidden in ('token', 'entity_id', 'notify_service', 'latitude'):
            self.assertNotIn(forbidden, json.dumps(owner))
        value = policy.value
        value['persons']['mads']['notify_service'] = 'other'
        self.assertEqual(policy.person('mads')['notify_service'], 'mobile_app_mads_iphone')
        with self.assertRaises(ValueError): policy.options('guessed-scope')
        bad = []
        for key, value in [('schema_version', True), ('ha_token_file', '/tmp/token'), ('owner_person', 'kristina'),
                           ('zones', {}), ('extra', 'http://evil.invalid')]:
            candidate = configuration(); candidate[key] = value; bad.append(candidate)
        candidate = configuration(); candidate['persons']['mads']['notify_service'] = 'send_anywhere'; bad.append(candidate)
        candidate = configuration(); candidate['persons']['other'] = candidate['persons']['mads']; bad.append(candidate)
        candidate = configuration(); candidate['zones']['home']['entity_id'] = 'zone.foreign'; bad.append(candidate)
        for name in ('@all', 'a\nb', '<script>', 'a\u202eb', 'x' * 81):
            candidate = configuration(); candidate['zones']['home']['name'] = name; bad.append(candidate)
        candidate = configuration(); candidate['zones'] = {f'z{i}': {'name': 'Zone', 'entity_id': f'zone.z{i}'} for i in range(33)}; bad.append(candidate)
        for candidate in bad:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError): Policy.from_value(candidate)

    def test_policy_file_private_mode_symlink_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            path.write_text(json.dumps(configuration())); path.chmod(0o600)
            self.assertEqual(Policy(path).value, configuration())
            path.chmod(0o644)
            with self.assertRaises(ValueError): Policy(path)
            path.chmod(0o600)
            link = path.with_name('link'); link.symlink_to(path)
            with self.assertRaises(ValueError): Policy(link)
            path.write_text('x' * 16385)
            with self.assertRaises(ValueError): Policy(path)

    def test_location_scope_and_legacy_timed_phone_snooze(self):
        policy = Policy.from_value(configuration())
        policy.validate_task(task(), 'owner')
        policy.validate_task(task('kristina'), 'family')
        policy.validate_task({'remind_at': '2026-10-27T12:00:00+01:00'}, 'owner')
        policy.validate_task(task(reminder_trigger='time', trigger_zone='', remind_at='2026-10-27T12:00:00+01:00'), 'owner')
        policy.validate_task(task(delivery_target='family'), 'family')
        for candidate, scope in [(task('kristina'), 'owner'), (task(delivery_target='family'), 'owner'),
                                 (task(delivery_target='mattermost'), 'family'), (task(trigger_zone='missing'), 'owner'),
                                 (task(trigger_person='person.madslundt'), 'owner'), (task(remind_at='tomorrow'), 'owner'),
                                 (task(trigger_expires_at='2026-10-27T12:00'), 'owner'),
                                 (task(trigger_zone=[]), 'owner'), (task(reminder_trigger={}), 'owner'),
                                 (task(trigger_expires_at=[]), 'owner'),
                                 (task(reminder_trigger='time', trigger_person='other'), 'family')]:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError): policy.validate_task(candidate, scope)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(transport, 'private', return_value=TOKEN.encode())
        self.token.start(); self.addCleanup(self.token.stop)
        self.policy = Policy.from_value(configuration())

    def client(self): return transport.LocationClient(self.policy)

    def connection(self, sock): return patch.object(ha.socket, 'create_connection', return_value=sock)

    @staticmethod
    def handler(command):
        if command['type'] == 'ping': return wire.frame({'id': command['id'], 'type': 'pong'})
        return wire.frame({'id': command['id'], 'type': 'result', 'success': True, 'result': None})

    def test_exact_native_subscriptions_no_initial_event_and_healthy_idle_heartbeat(self):
        sock = wire.Socket(self.handler)
        with self.connection(sock), self.client() as client:
            self.assertEqual(client.subscribe(), 4)
            with patch.object(transport.select, 'select', return_value=([], [], [])):
                self.assertIsNone(client.next_event(0))
            self.assertFalse(sock.closed)
            commands = [json.loads(wire.client_frame(raw)[1]) for raw in sock.sent[1:] if raw[0] & 15 == 1][1:]
            subscriptions = [command for command in commands if command['type'] == 'subscribe_trigger']
            self.assertEqual(len(subscriptions), 4)
            self.assertEqual(subscriptions[0], {'id': 1, 'type': 'subscribe_trigger', 'trigger': {
                'trigger': 'zone', 'entity_id': 'person.madslundt', 'zone': 'zone.home', 'event': 'enter'}})
            self.assertEqual({command['type'] for command in commands}, {'subscribe_trigger', 'ping'})
            with self.assertRaises(ha.HomeAssistantError): client.call({'type': 'get_states'})

    def test_maximum_policy_stays_within_128_exact_subscriptions(self):
        value = configuration()
        value['zones'] = {f'z{i}': {'name': f'Zone {i}', 'entity_id': f'zone.z{i}'} for i in range(32)}
        sock = wire.Socket(self.handler)
        with self.connection(sock), transport.LocationClient(Policy.from_value(value)) as client:
            self.assertEqual(client.subscribe(), 128)
            with self.assertRaises(ha.HomeAssistantError): client.subscribe()
        self.assertEqual(sum(b'subscribe_trigger' in wire.client_frame(raw)[1] for raw in sock.sent[1:] if raw[0] & 15 == 1), 128)

    def test_closed_event_projection_stable_identity_and_fallback(self):
        sock = wire.Socket(self.handler)
        with self.connection(sock), self.client() as client:
            client.subscribe()
            raw = event()
            sock.incoming += wire.frame(raw)
            with patch.object(transport.select, 'select', return_value=([sock], [], [])):
                projected = client.next_event(0)
            self.assertEqual(set(projected), {'event_id', 'person', 'zone', 'event', 'occurred_at'})
            self.assertEqual(projected['person'], 'mads'); self.assertEqual(projected['zone'], 'home')
            self.assertEqual(projected, client._project(raw))
            self.assertRegex(projected['event_id'], r'^[0-9a-f]{64}$')
            serialized = json.dumps(projected)
            for secret in (TOKEN, 'latitude', 'longitude', '55.12345', 'person.madslundt'):
                self.assertNotIn(secret, serialized)
            raw['event']['context'] = None
            self.assertEqual(client._project(raw), client._project(copy.deepcopy(raw)))

    def test_bad_subscription_state_timestamp_and_raw_variables_refused(self):
        client = self.client(); client._rules[1] = ('mads', 'home', 'enter')
        invalid = [event(identifier=99), event(stamp=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()),
                   event(stamp=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat())]
        for key, value in [('entity_id', 'person.other'), ('event', 'leave'), ('from_state', None)]:
            candidate = event(); candidate['event']['variables']['trigger'][key] = value; invalid.append(candidate)
        for state in ('unknown', 'unavailable'):
            candidate = event(); candidate['event']['variables']['trigger']['from_state']['state'] = state; invalid.append(candidate)
        candidate = event(); candidate['event']['variables'] = []; invalid.append(candidate)
        candidate = event(); candidate['event']['variables']['trigger']['zone']['entity_id'] = 'zone.other'; invalid.append(candidate)
        candidate = event(); candidate['event']['context']['id'] = TOKEN + '\n'; invalid.append(candidate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises((ha.HomeAssistantError, ValueError)) as caught:
                client._project(candidate)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_event_before_subscription_ack_preserved_and_partial_frame_closes(self):
        def handler(command):
            ack = self.handler(command)
            return wire.frame(event()) + ack if command['id'] == 1 else ack
        sock = wire.Socket(handler)
        with self.connection(sock), self.client() as client:
            client.subscribe()
            self.assertEqual(client.next_event(0)['event'], 'enter')
            sock.incoming += b'\x81\x10abc'
            with patch.object(transport.select, 'select', return_value=([sock], [], [])), self.assertRaises(ha.HomeAssistantError):
                client.next_event(0)
            self.assertTrue(sock.closed); self.assertIsNone(client.sock)

    def test_unknown_live_subscription_and_denied_subscription_close(self):
        sock = wire.Socket(self.handler)
        with self.connection(sock), self.client() as client:
            client.subscribe(); sock.incoming += wire.frame(event(identifier=999))
            with patch.object(transport.select, 'select', return_value=([sock], [], [])), self.assertRaises(ha.HomeAssistantError): client.next_event(0)
            self.assertTrue(sock.closed)
        denied = wire.Socket(lambda command: wire.frame({'id': command['id'], 'type': 'result', 'success': False, 'error': {'message': TOKEN}}))
        with self.connection(denied), self.client() as client:
            with self.assertRaises(ha.HomeAssistantError) as caught: client.subscribe()
            self.assertNotIn(TOKEN, str(caught.exception)); self.assertTrue(denied.closed)

    def test_fixed_person_http_without_websocket_safe_text_and_accepted_receipt(self):
        client = self.client()
        row = {'key': 'a' * 64, 'scope': 'family', 'task': task('kristina'), 'location_context': {
            'person': 'kristina', 'zone': 'home', 'event': 'leave', 'occurred_at': datetime.now(timezone.utc).isoformat()}}
        with patch.object(client.opener, 'open', return_value=Response()) as send:
            self.assertEqual(client.send_person(row), 'ha-service-accepted')
        self.assertIsNone(client.sock)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'http://192.168.30.30:80/api/services/notify/mobile_app_kristina_iphone')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        payload = json.loads(request.data)
        self.assertIn('@\u200ball', payload['message']); self.assertIn('Forlod Home', payload['message'])
        self.assertEqual(payload['data']['tag'], 'homecompute-reminder-' + row['key'])
        self.assertNotIn(TOKEN, json.dumps(payload)); self.assertNotIn('latitude', json.dumps(payload))
        row['task'] = task(reminder_trigger='time', trigger_zone='', remind_at='2026-10-27T12:00:00+01:00')
        row.pop('location_context')
        with patch.object(client.opener, 'open', return_value=Response()): self.assertEqual(client.send_person(row), 'ha-service-accepted')

    def test_phone_invalid_recipient_fails_before_send_and_ambiguous_post_not_retried(self):
        client = self.client()
        for candidate in (task('kristina'), task('kristina', scope='family'), task(delivery_target='family'), task(status='done'),
                          task(trigger_person='http://evil.invalid'), task(trigger_expires_at='2000-01-01T00:00:00Z')):
            with patch.object(client.opener, 'open') as send, self.assertRaises(ha.HomeAssistantError) as caught:
                client.send_person({'key': 'b'*64, 'scope': 'owner', 'task': candidate})
            send.assert_not_called(); self.assertFalse(caught.exception.uncertain)
        row = {'key': 'b'*64, 'scope': 'owner', 'task': task(reminder_trigger='time', trigger_zone='', remind_at='2026-10-27T12:00:00Z')}
        with patch.object(client.opener, 'open', side_effect=OSError(TOKEN)) as send, self.assertRaises(ha.HomeAssistantError) as caught:
            client.send_person(row)
        self.assertEqual(send.call_count, 1); self.assertTrue(caught.exception.uncertain)
        self.assertNotIn(TOKEN, str(caught.exception))
        for body in (b'{}', b'not-json', b'x'*65537):
            with patch.object(client.opener, 'open', return_value=Response(body)), self.assertRaises(ha.HomeAssistantError) as caught:
                client.send_person(row)
            self.assertTrue(caught.exception.uncertain)

    def test_queued_event_uses_ledger_five_minute_delivery_window(self):
        client = self.client()
        row = {'key': 'c' * 64, 'scope': 'owner', 'task': task(), 'location_context': {
            'person': 'mads', 'zone': 'home', 'event': 'leave',
            'occurred_at': (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()}}
        with patch.object(client.opener, 'open', return_value=Response()):
            self.assertEqual(client.send_person(row), 'ha-service-accepted')
        row['location_context']['occurred_at'] = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        with patch.object(client.opener, 'open') as send, self.assertRaises(ha.HomeAssistantError) as caught:
            client.send_person(row)
        send.assert_not_called(); self.assertFalse(caught.exception.uncertain)


if __name__ == '__main__': unittest.main()
