#!/usr/bin/env python3
"""Household evidence boundaries, provenance and ordinary core-host chat coverage."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import homeassistant_observations as HA
from openclaw_infrastructure import Reader
from openclaw_notifications import Outbox

SPEC = importlib.util.spec_from_file_location('household_feed', ROOT / 'scripts/openclaw-observation-feed.py')
FEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FEED)
SPEC = importlib.util.spec_from_file_location('household_communication', ROOT / 'scripts/openclaw-communication.py')
COMM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMM)

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (NOW + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def fixture():
    return {'schema_version': 2, 'host': 'home-assistant', 'generated_at': stamp(),
            'installed_version': '2026.10.1', 'update_available_count': 1, 'unavailable_count': 0,
            'checks': [{'check_id': check, 'status': 'degraded' if check == 'ha.update.core' else 'healthy',
                        'evidence': {'available': True, 'installed_version': '2026.10.1', 'latest_version': '2026.10.2'}
                        if check == 'ha.update.core' else {'available': True}, 'observed_at': stamp()}
                       for check in HA.CHECKS],
            'energy': {'entities': {alias: {'status': 'available', 'value': 52 if spec[1] == '%' else 1.5,
                                          'unit': spec[1], 'source': spec[2], 'reported_at': stamp(-86400),
                                          'sampled_at': stamp(-10)} for alias, spec in HA.NUMERIC.items()},
                       'price_slots': [{'start': stamp(i * 3600), 'end': stamp((i + 1) * 3600),
                                        'price_dkk_per_kwh': -0.25 if i == 0 else 1.5} for i in range(6)],
                       'plan': {'status': 'available', 'created_at': stamp(-10), 'expected_cost_dkk': 5,
                                'baseline_cost_dkk': 7, 'expected_savings_dkk': 2,
                                'blocks': [{'start': stamp(i * 1800), 'end': stamp((i + 1) * 1800), 'action': 'charge',
                                            'energy_kwh': 0.5, 'expected_load_kwh': 0.2, 'expected_grid_import_kwh': 0.7,
                                            'expected_cost_dkk': 1.2, 'expected_savings_dkk': 0.3} for i in range(10)]},
                       'solar': {'status': 'unknown', 'reason': 'not_provisioned'}, 'tomorrow_prices': 'on'}}


class HouseholdProjectionTests(unittest.TestCase):
    def reject(self, mutate):
        raw = fixture()
        mutate(raw)
        with self.assertRaises(ValueError):
            HA.validate(raw)

    def test_verified_units_sources_and_old_unchanged_state_keep_actual_sample_time(self):
        raw = HA.validate(fixture())
        self.assertEqual(raw['energy']['entities']['battery_soc']['sampled_at'], stamp(-10))
        self.assertEqual(raw['energy']['entities']['battery_soc']['reported_at'], stamp(-86400))
        self.assertEqual(raw['energy']['entities']['grid_import_energy']['unit'], 'Wh')
        self.assertEqual(raw['energy']['entities']['estimated_savings_today']['source'], 'estimate')
        self.assertEqual(raw['energy']['solar'], {'status': 'unknown', 'reason': 'not_provisioned'})

    def test_schema_entity_selection_and_personal_payloads_are_rejected(self):
        for mutation in (lambda r: r.update(persons={'private': 'home'}),
                         lambda r: r.update(schema_version=True),
                         lambda r: r['energy'].update(attributes={'token': 'SECRET'}),
                         lambda r: r['energy']['entities'].update(private={'value': 1}),
                         lambda r: r['energy']['entities'].pop('battery_soc'),
                         lambda r: r['energy']['entities']['battery_soc'].update(friendly_name='PRIVATE'),
                         lambda r: r['checks'][0]['evidence'].update(raw_state='PRIVATE'),
                         lambda r: r['checks'].pop()):
            with self.subTest(mutation=mutation):
                self.reject(mutation)

    def test_missing_and_stale_values_are_explicit_unknown_without_a_numeric_value(self):
        for reason in ('missing', 'stale', 'unsupported_schema', 'health_not_reported'):
            raw = fixture()
            row = raw['energy']['entities']['battery_soc']
            row.pop('value')
            row.update(status='unknown', reason=reason, sampled_at=None)
            self.assertEqual(HA.validate(raw)['energy']['entities']['battery_soc']['reason'], reason)
            row['value'] = 0
            with self.assertRaises(ValueError):
                HA.validate(raw)

    def test_wrong_units_source_and_nonfinite_or_boolean_values_fail_closed(self):
        for key, bad in [('unit', 'kWh'), ('source', 'invented_meter'), ('value', float('nan')),
                         ('value', float('inf')), ('value', True), ('value', 1e13)]:
            with self.subTest(key=key, bad=bad):
                self.reject(lambda r: r['energy']['entities']['battery_soc'].update({key: bad}))

    def test_available_values_require_current_sample_and_valid_percentage(self):
        for mutate in (lambda r: r['energy']['entities']['battery_soc'].update(sampled_at=stamp(-901)),
                       lambda r: r['energy']['entities']['battery_soc'].update(sampled_at=None),
                       lambda r: r['energy']['entities']['battery_soc'].update(value=101),
                       lambda r: r['energy']['entities']['battery_soc'].update(value=-1),
                       lambda r: r['energy']['entities']['battery_soc'].update(sampled_at=stamp(1))):
            self.reject(mutate)

    def test_update_versions_and_check_timestamps_are_bounded(self):
        self.assertEqual(HA.validate(fixture())['checks'][6]['evidence']['latest_version'], '2026.10.2')
        for mutation in (lambda r: r['checks'][0].update(observed_at=stamp(-901)),
                         lambda r: r['checks'][0].update(observed_at=stamp(31)),
                         lambda r: r['checks'][0]['evidence'].update(latest_version='PRIVATE instruction'),
                         lambda r: r.update(update_available_count=True),
                         lambda r: r.update(unavailable_count=101)):
            self.reject(mutation)

    def test_prices_and_modeled_blocks_reject_oversize_overlap_and_invalid_values(self):
        for mutation in (lambda r: r['energy'].update(price_slots=r['energy']['price_slots'] * 34),
                         lambda r: r['energy']['price_slots'][1].update(start=stamp(1)),
                         lambda r: r['energy']['price_slots'][0].update(price_dkk_per_kwh=float('nan')),
                         lambda r: r['energy']['price_slots'][0].update(end=stamp()),
                         lambda r: r['energy']['plan'].update(created_at=stamp(-901)),
                         lambda r: r['energy']['plan'].update(blocks=r['energy']['plan']['blocks'] * 4),
                         lambda r: r['energy']['plan']['blocks'][0].update(action='unlock'),
                         lambda r: r['energy']['plan']['blocks'][0].update(energy_kwh=float('inf')),
                         lambda r: r['energy']['plan']['blocks'][1].update(start=stamp(1)),
                         lambda r: r['energy']['plan'].update(private_note='PRIVATE')):
            self.reject(mutation)

    def test_chat_summary_is_small_and_reusable_source_keeps_all_slots_and_blocks(self):
        raw = fixture()
        compact = HA.compact(raw['energy'], raw['generated_at'])
        self.assertEqual(compact['price_slots']['count'], 6)
        self.assertEqual(compact['price_slots']['min'], -0.25)
        self.assertEqual(len(compact['price_slots']['next']), 4)
        self.assertEqual(len(compact['plan']['blocks']), 4)
        self.assertEqual(len(raw['energy']['plan']['blocks']), 10)
        self.assertEqual(len(raw['energy']['price_slots']), 6)
        self.assertNotIn('reported_at', compact['entity_columns'])
        self.assertEqual(compact['entities']['battery_soc'][compact['entity_columns'].index('sampled_at')], stamp(-10))

    def test_local_reader_uses_only_fixed_authenticated_get_with_bounded_response(self):
        with patch.object(HA.urllib.request, 'build_opener') as factory:
            response = factory.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps(fixture()).encode()
            self.assertEqual(HA.local_read('synthetic-private-token')['schema_version'], 2)
            args, kwargs = factory.return_value.open.call_args
            request = args[0]
            self.assertEqual(request.full_url, 'http://127.0.0.1:15678/webhook/homecompute-openclaw-ha-metadata')
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-private-token')
            self.assertEqual(kwargs['timeout'], 4)
            response.read.assert_called_once_with(HA.MAX_BYTES + 1)
            handlers = factory.call_args.args
            self.assertTrue(any(isinstance(handler, HA.NoRedirect) for handler in handlers))
            self.assertTrue(any(isinstance(handler, HA.urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers))

    def test_local_reader_rejects_non_success_oversize_and_unreviewed_response(self):
        for status, payload in [(503, b'{}'), (200, b'x' * (HA.MAX_BYTES + 1)),
                                (200, b'{"personal_states": "PRIVATE"}')]:
            with self.subTest(status=status, size=len(payload)), patch.object(HA.urllib.request, 'build_opener') as factory:
                response = factory.return_value.open.return_value.__enter__.return_value
                response.status, response.read.return_value = status, payload
                with self.assertRaises(ValueError):
                    HA.local_read('synthetic-private-token')


class CoreHouseholdReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        registry = json.loads((ROOT / 'config/system-monitoring.json').read_text())
        registry['systems'] = [s for s in registry['systems'] if s['id'] == 'home-assistant']
        registry['systems'][0]['enabled'] = True
        self.registry = directory / 'registry.json'
        self.registry.write_text(json.dumps(registry))
        token = directory / 'ha-token'
        token.write_text('x' * 48)
        token.chmod(0o600)
        transport = directory / 'transport.json'
        transport.write_text(json.dumps({'schema_version': 1, 'token_file': str(token)}))
        transport.chmod(0o600)
        self.reader = Reader(self.registry, ha_transport=transport, core_host_mode=True, clock=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def test_core_reader_uses_bounded_local_route_and_keeps_energy_outside_incident_checks(self):
        with patch.object(HA, 'local_read', return_value=copy.deepcopy(fixture())) as read:
            report = self.reader.read('home-assistant')
        read.assert_called_once_with('x' * 48)
        self.assertEqual(report['observations'][0]['status'], 'healthy')
        self.assertEqual(report['host_metrics']['home-assistant']['status'], 'not_exposed')
        self.assertTrue(any(row['check_id'] == 'ha.battery_optimizer' for row in report['observations']))
        self.assertTrue(any(row['check_id'] == 'ha.update.core' and row['status'] == 'degraded' for row in report['observations']))
        self.assertNotIn('battery_soc', json.dumps(report['observations']))
        self.assertIn('battery_soc', json.dumps(Reader.compact(report)))
        self.assertFalse(report['automatic_actions'])
        self.assertFalse(report['physical_device_actions'])

    def test_ordinary_chat_gets_household_evidence_without_commands_or_secret_token(self):
        with patch.object(HA, 'local_read', return_value=copy.deepcopy(fixture())):
            prompt = self.reader.contextual_prompt('What is the battery charge and electricity price?')
        self.assertIn('battery_soc', prompt)
        self.assertIn('electricity_price', prompt)
        self.assertIn('estimated_savings_today', prompt)
        self.assertNotIn('x' * 48, prompt)
        self.assertLessEqual(len(prompt.encode()), 16384)

    def test_energy_question_selects_ha_while_cross_system_question_keeps_all_coverage(self):
        report = {'generated_at': stamp(), 'observations': [], 'home_assistant_energy': {'status': 'unknown'}}
        with patch.object(self.reader, 'read', return_value=report) as read:
            self.reader.contextual_prompt('What is the battery SOC and electricity tariff?')
            read.assert_called_once_with('home-assistant')
            read.reset_mock()
            self.reader.contextual_prompt('Compare home-core health with Home Assistant battery health')
            read.assert_called_once_with('all')

    def test_route_failure_is_explicit_availability_incident_with_no_cached_energy(self):
        with patch.object(HA, 'local_read', side_effect=ValueError('PRIVATE credential error')):
            report = self.reader.read('home-assistant')
        collection = next(row for row in report['observations'] if row['check_id'] == 'collection')
        self.assertEqual(collection['status'], 'degraded')
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertNotIn('battery_soc', json.dumps(Reader.compact(report)))


class HouseholdFeedAndOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.observer = FEED.load_observer()
        self.registry = json.loads((ROOT / 'config/system-monitoring.json').read_text())
        self.registry['systems'] = [s for s in self.registry['systems'] if s['id'] == 'home-assistant']
        self.registry['systems'][0]['enabled'] = True
        self.registry_path = self.directory / 'registry.json'
        self.registry_path.write_text(json.dumps(self.registry))
        self.tokens = self.directory / 'tokens.json'
        self.tokens.write_text(json.dumps({'collector': 'synthetic-private-token'}))
        self.tokens.chmod(0o600)
        self.ha_transport = self.directory / 'ha-transport.json'
        self.ha_transport.write_text(json.dumps({'schema_version': 1, 'token_file': str(self.directory / 'ha-token')}))
        self.ha_transport.chmod(0o600)
        settings = {'destination': 'operator-private', 'timezone': 'Europe/Copenhagen',
                    'quiet_start_hour': 22, 'quiet_end_hour': 7, 'cooldown_seconds': 1800}
        self.store = Outbox(self.directory / 'outbox.sqlite3', settings)
        self.allowed = COMM.registry_keys(self.registry_path)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def report(self, raw, seconds=0):
        raw = copy.deepcopy(raw)
        generated = stamp(seconds)
        raw['generated_at'] = generated
        for row in raw['checks']:
            row['observed_at'] = generated
        return self.observer.make_report(self.registry, {'home-assistant': raw}, NOW + timedelta(seconds=seconds))

    def test_scheduled_core_feed_admits_ha_health_and_updates_but_no_energy_values(self):
        destination = self.directory / 'latest.json'
        with patch.object(FEED, 'load_observer', return_value=self.observer), \
             patch.object(self.observer, 'collect', return_value=fixture()) as collect, \
             patch.object(FEED, 'core_snapshot', side_effect=AssertionError('HA must use its local route')), \
             patch.object(FEED, 'datetime') as clock, \
             patch.object(FEED, 'request', return_value={'accepted': True, 'actions_enabled': False}) as admit:
            clock.now.return_value = NOW
            result = FEED.feed(self.registry_path, self.tokens, destination,
                               core_host_mode=True, ha_transport=self.ha_transport)
        collect.assert_called_once_with(self.registry['systems'][0],
                                        ha_transport={'schema_version': 1, 'token_file': str(self.directory / 'ha-token')},
                                        core_host_mode=True)
        report = admit.call_args.args[-1]
        self.assertTrue(result['accepted'])
        self.assertEqual(admit.call_args.args[:3], (FEED.ADAPTER, '/observations', 'synthetic-private-token'))
        self.assertEqual({row['category'] for row in report['observations']}, {'health', 'updates'})
        self.assertEqual({row['check_id'] for row in report['observations']}, set(HA.CHECKS) | {'collection', 'installed-version'})
        self.assertNotIn('battery_soc', json.dumps(report))
        self.assertNotIn('price_slots', json.dumps(report))
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['changes'], [])
        self.assertEqual(json.loads(destination.read_text()), report)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_grouped_incidents_repeat_quietly_unknown_cannot_recover_and_fresh_recovery_shares_receipt(self):
        raw = fixture()
        optimizer = next(row for row in raw['checks'] if row['check_id'] == 'ha.battery_optimizer')
        optimizer.update(status='degraded', evidence={'failed': True})
        self.store.observations(self.report(raw), self.allowed, NOW.timestamp())
        event = self.store.claim(NOW.timestamp())
        self.assertIn('home-assistant', event['text'])
        self.assertIn('Battery optimizer', event['text'])
        self.assertIn('HA update: core', event['text'])
        self.assertNotIn('battery_soc', event['text'])
        self.assertEqual(event['text'].count('operator approval'), 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events WHERE claim=?', (event['claim'],)).fetchone()[0], 2)
        self.store.acknowledge(event['delivery_key'], event['claim'], 'synthetic:1', NOW.timestamp())
        raw['energy']['entities']['battery_soc']['value'] = 53
        self.store.observations(self.report(raw, 60), self.allowed, NOW.timestamp() + 60)
        self.assertIsNone(self.store.claim(NOW.timestamp() + 60))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], 2)
        for row in raw['checks']:
            if row['check_id'] in {'ha.battery_optimizer', 'ha.update.core'}:
                row.update(status='unknown', evidence={'reason': 'health_not_reported'})
        self.store.observations(self.report(raw, 120), self.allowed, NOW.timestamp() + 120)
        self.assertEqual(len(self.store.status(NOW.timestamp() + 120)['incidents']), 2)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='recovery'").fetchone()[0], 0)
        self.assertIsNone(self.store.claim(NOW.timestamp() + 120))
        for row in raw['checks']:
            if row['check_id'] in {'ha.battery_optimizer', 'ha.update.core'}:
                row.update(status='healthy', evidence={'available': True})
        self.store.observations(self.report(raw, 180), self.allowed, NOW.timestamp() + 180)
        recovered = self.store.claim(NOW.timestamp() + 180)
        self.assertEqual(recovered['text'].count('Recovered:'), 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events WHERE claim=?', (recovered['claim'],)).fetchone()[0], 2)
        self.store.acknowledge(recovered['delivery_key'], recovered['claim'], 'synthetic:2', NOW.timestamp() + 180)
        self.assertEqual(self.store.status(NOW.timestamp() + 180)['incidents'], [])

    def test_undelivered_incident_recovery_stays_quiet(self):
        raw = fixture()
        self.store.observations(self.report(raw), self.allowed, NOW.timestamp())
        for row in raw['checks']:
            row.update(status='healthy', evidence={'available': True})
        self.store.observations(self.report(raw, 60), self.allowed, NOW.timestamp() + 60)
        self.assertIsNone(self.store.claim(NOW.timestamp() + 60))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='recovery'").fetchone()[0], 0)


class HAProvisioningBoundaryTests(unittest.TestCase):
    def test_provisioning_rejects_control_send_retention_and_projection_bypasses(self):
        spec = importlib.util.spec_from_file_location('ha_setup', ROOT / 'scripts/setup-openclaw-home-assistant.py')
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        flow = json.loads((ROOT / 'automations/agent-investigation/n8n-ha-metadata-workflow.json').read_text())
        setup.validate_template(flow)
        mutations = [lambda f: f['nodes'][2]['parameters'].update(resource='service', operation='call'),
                     lambda f: f['nodes'][0]['parameters'].update(httpMethod='POST'),
                     lambda f: f['nodes'][2]['credentials']['homeAssistantApi'].update(id='different-credential'),
                     lambda f: f['nodes'][4].update(type='n8n-nodes-base.telegram'),
                     lambda f: f['settings'].update(saveDataSuccessExecution='all'),
                     lambda f: f['settings'].update(availableInMCP=True),
                     lambda f: f['connections']['Read HA state inventory']['main'][0][0].update(node='Return finite metadata')]
        for mutate in mutations:
            corrupted = copy.deepcopy(flow)
            mutate(corrupted)
            with self.assertRaises(ValueError):
                setup.validate_template(corrupted)


if __name__ == '__main__':
    unittest.main()
