"""Location listener admission, no-send checks, installer discovery and service isolation."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


daemon = load('location_daemon', 'openclaw-location-reminders.py')
setup = load('location_setup', 'setup-openclaw-location-reminders.py')
NOW = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
EVENT = {'event_id': 'a' * 64, 'person': 'mads', 'zone': 'home', 'event': 'leave', 'occurred_at': NOW.isoformat()}


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name).resolve()
        self.state.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_scope_uses_existing_worker_after_durable_admission(self):
        owner = Mock()
        owner.accept_location_event.return_value = {'matched': 1, 'duplicate': False}
        family = Mock()
        family.accept_location_event.return_value = {'matched': 0, 'duplicate': False}
        delivery = Mock()
        count = daemon.admit(EVENT, [('owner', owner), ('family', family)], self.state, NOW, delivery)
        self.assertEqual(count, 1)
        owner.accept_location_event.assert_called_once_with(EVENT, NOW)
        family.accept_location_event.assert_called_once_with(EVENT, NOW)
        delivery.assert_called_once_with('owner', self.state)

    def test_failed_immediate_delivery_keeps_admission_and_other_scope_delivery(self):
        stores = []
        for scope in ('owner', 'family'):
            tasks = Mock()
            tasks.accept_location_event.return_value = {'matched': 1, 'duplicate': False}
            stores.append((scope, tasks))
        delivery = Mock(side_effect=[ValueError('held'), {'busy': True}])
        self.assertEqual(daemon.admit(EVENT, stores, self.state, NOW, delivery), 2)
        self.assertEqual(delivery.call_count, 2)

    def test_subscription_check_never_opens_stores_or_sends(self):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock()
        client.subscribe.return_value = 12
        factory = Mock(return_value=client)
        result = daemon.check(object(), factory)
        self.assertEqual(result, {'ok': True, 'connected': True, 'subscription_count': 12,
                                  'messages_sent': 0, 'events_admitted': 0})
        client.next_event.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), [])

    def test_starting_inside_zone_cannot_create_a_transition(self):
        stopped = [False]
        class Client:
            def __init__(self, policy):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def subscribe(self):
                return 8
            def next_event(self, timeout_seconds):
                stopped[0] = True
                return None
        store, delivery = Mock(), Mock()
        daemon.listen(object(), self.state, [('owner', store)], Client, lambda: stopped[0],
                      clock=lambda: NOW, deliver=delivery)
        store.accept_location_event.assert_not_called()
        delivery.assert_not_called()
        status = json.loads((self.state / daemon.STATUS_NAME).read_text())
        self.assertFalse(status['connected'])
        self.assertIsNone(status['last_event_at'])
        self.assertEqual(status['last_error'], 'stopped')

    def test_reconnection_records_gap_without_replaying_current_location(self):
        stopped = [False]
        connections = [0]
        class Client:
            def __init__(self, policy):
                connections[0] += 1
                self.number = connections[0]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def subscribe(self):
                return 8
            def next_event(self, timeout_seconds):
                if self.number == 1:
                    raise ValueError('untrusted raw data must not appear in status')
                stopped[0] = True
                return None
        store = Mock()
        times = iter([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=5)])
        daemon.listen(object(), self.state, [('owner', store)], Client, lambda: stopped[0],
                      sleep=lambda seconds: None, clock=lambda: next(times))
        store.accept_location_event.assert_not_called()
        status = json.loads((self.state / daemon.STATUS_NAME).read_text())
        self.assertEqual(status['reconnects'], 1)
        self.assertEqual(status['last_gap_seconds'], 4)
        self.assertNotIn('untrusted raw data', json.dumps(status))

    def test_event_status_keeps_only_event_time_not_person_or_zone(self):
        stopped = [False]
        class Client:
            def __init__(self, policy):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def subscribe(self):
                return 8
            def next_event(self, timeout_seconds):
                stopped[0] = True
                return EVENT
        store = Mock()
        store.accept_location_event.return_value = {'matched': 0, 'duplicate': False}
        daemon.listen(object(), self.state, [('owner', store)], Client, lambda: stopped[0], clock=lambda: NOW)
        path = self.state / daemon.STATUS_NAME
        status = json.loads(path.read_text())
        self.assertEqual(status['last_event_at'], EVENT['occurred_at'])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('mads', path.read_text())
        self.assertNotIn('home', path.read_text())

    def test_failed_reconnect_attempts_preserve_entire_outage_gap(self):
        stopped = [False]
        connections = [0]
        class Client:
            def __init__(self, policy):
                connections[0] += 1
                self.number = connections[0]
            def __enter__(self):
                if self.number == 2:
                    raise ValueError('connection unavailable')
                return self
            def __exit__(self, *args):
                pass
            def subscribe(self):
                return 8
            def next_event(self, timeout_seconds):
                if self.number == 1:
                    raise ValueError('stream disconnected')
                stopped[0] = True
                return None
        times = iter([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=9)])
        daemon.listen(object(), self.state, [], Client, lambda: stopped[0],
                      sleep=lambda seconds: None, clock=lambda: next(times))
        status = json.loads((self.state / daemon.STATUS_NAME).read_text())
        self.assertEqual(status['reconnects'], 2)
        self.assertEqual(status['last_gap_seconds'], 8)

    def test_status_rejects_symlink_and_shared_state_permissions(self):
        target = self.state / 'elsewhere'
        target.write_text('unchanged')
        (self.state / daemon.STATUS_NAME).symlink_to(target)
        with self.assertRaises(ValueError):
            daemon.status_write(self.state, {'connected': True})
        self.assertEqual(target.read_text(), 'unchanged')
        self.state.chmod(0o755)
        with self.assertRaises(ValueError):
            daemon.private_state(self.state)


class InstallerTests(unittest.TestCase):
    def value(self):
        return {'schema_version': 1, 'ha_token_file': str(setup.TOKEN), 'owner_person': 'mads',
                'persons': setup.PERSONS, 'zones': {'home': {'name': 'Home', 'entity_id': 'zone.home'}}}

    def test_discovery_retains_only_zone_names_and_fixed_person_mappings(self):
        rows = [{'entity_id': person['entity_id'], 'state': 'secret-position',
                 'attributes': {'device_trackers': ['device_tracker.phone'], 'latitude': 55.123456}}
                for person in setup.PERSONS.values()]
        rows += [{'entity_id': 'zone.home', 'state': '2', 'attributes': {'friendly_name': 'Home', 'latitude': 55.123456}},
                 {'entity_id': 'sensor.private', 'state': 'private content'}]
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock()
        response.status = 200
        response.read.return_value = json.dumps(rows).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(setup, 'read_private', return_value=b't' * 64):
            result = setup.discover(os.getuid(), opener)
        self.assertEqual(result, self.value())
        for private in ('55.123456', 'secret-position', 'private content', 'device_tracker.phone'):
            self.assertNotIn(private, json.dumps(result))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://192.168.30.30/api/states')

    def test_refresh_adds_zones_without_replacing_retained_names(self):
        existing, found = self.value(), self.value()
        found['zones'] = {'home': {'name': 'Changed', 'entity_id': 'zone.home'},
                          'shop': {'name': 'Shop', 'entity_id': 'zone.shop'}}
        self.assertEqual(setup.select_policy(existing, found), existing)
        refreshed = setup.select_policy(existing, found, refresh_zones=True)
        self.assertEqual(refreshed['zones']['home']['name'], 'Home')
        self.assertEqual(refreshed['zones']['shop']['name'], 'Shop')

    def test_existing_person_identity_cannot_be_silently_changed(self):
        existing = self.value()
        existing['persons'] = json.loads(json.dumps(existing['persons']))
        existing['persons']['mads']['name'] = 'Different'
        with self.assertRaises(ValueError):
            setup.select_policy(existing, self.value(), refresh_zones=True)

    def test_installed_code_remains_service_readable_under_private_root_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'code.py'
            previous = os.umask(0o077)
            try:
                setup.atomic(path, b'pass\n', os.getuid(), os.getgid(), 0o644)
            finally:
                os.umask(previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_unit_is_unprivileged_persistent_and_has_only_adapter_state_writable(self):
        unit = setup.service()
        for field in ('User=homecompute-openclaw', 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                      'ProtectHome=yes', 'PrivateDevices=yes', 'CapabilityBoundingSet=\n',
                      'Restart=always', 'TimeoutStopSec=15s', 'MemoryMax=128M', 'TasksMax=32',
                      'WantedBy=multi-user.target', 'ReadWritePaths=' + str(setup.STATE)):
            self.assertIn(field, unit)
        self.assertEqual(unit.count('ReadWritePaths='), 1)
        for forbidden in ('docker.sock', 'sudo ', 'SSH_AUTH_SOCK=', '--check'):
            self.assertNotIn(forbidden, unit)
        self.assertIn('--config ' + str(setup.CONFIG), unit)

    def test_activation_checks_subscriptions_before_starting_only_location_service(self):
        runner = Mock()
        with patch.object(setup, 'require_host', return_value=SimpleNamespace(pw_uid=os.getuid())), \
                patch.object(setup, 'path_guard'):
            result = setup.activate(runner)
        commands = [call.args[0] for call in runner.call_args_list]
        self.assertIn('--check', commands[0])
        self.assertEqual(commands[1], ['systemctl', 'start', setup.UNIT])
        self.assertEqual(commands[2], ['systemctl', 'is-active', '--quiet', setup.UNIT])
        self.assertEqual(result['messages_sent_by_check'], 0)

    def test_staging_copies_reviewed_modules_preserves_policy_and_never_starts_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, scripts, units, state, config = (root / 'source', root / 'installed/scripts',
                                                     root / 'units', root / 'state', root / 'config/policy.json')
            for directory in (source / 'scripts', scripts, units / 'multi-user.target.wants', state, config.parent):
                directory.mkdir(parents=True, mode=0o700)
            for name in setup.FILES:
                (source / 'scripts' / name).write_text('"""Reviewed module."""\n')
            unrelated = scripts / 'openclaw_household_tasks.py'
            unrelated.write_text('original owner implementation')
            existing, found = self.value(), self.value()
            found['zones']['shop'] = {'name': 'Shop', 'entity_id': 'zone.shop'}
            config.write_text(json.dumps(existing))
            config.chmod(0o600)
            runner = Mock()
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(setup, 'require_host', return_value=account), \
                    patch.object(setup, 'path_guard'), patch.object(setup, 'discover', return_value=found), \
                    patch.object(setup.os, 'chown'), patch.object(setup, 'CONFIG', config), \
                    patch.object(setup, 'SCRIPTS', scripts), patch.object(setup, 'UNITS', units), \
                    patch.object(setup, 'STATE', state):
                result = setup.install(source, runner=runner)
            self.assertTrue(result['installed'])
            self.assertFalse(result['started'])
            self.assertEqual(json.loads(config.read_text()), existing)
            self.assertEqual(unrelated.read_text(), 'original owner implementation')
            self.assertTrue((units / 'multi-user.target.wants' / setup.UNIT).is_symlink())
            self.assertTrue((Path(result['backup']) / 'location-reminders.json').is_file())
            commands = [call.args[0] for call in runner.call_args_list]
            self.assertEqual(commands, [['systemd-analyze', 'verify', str(units / setup.UNIT)],
                                        ['systemctl', 'daemon-reload']])
            for name in setup.FILES:
                self.assertEqual((scripts / name).stat().st_mode & 0o777, 0o644)

    def test_failed_subscription_check_never_starts_daemon(self):
        import subprocess
        runner = Mock(side_effect=subprocess.CalledProcessError(1, 'check'))
        with patch.object(setup, 'require_host', return_value=SimpleNamespace(pw_uid=os.getuid())), \
                patch.object(setup, 'path_guard'), self.assertRaises(subprocess.CalledProcessError):
            setup.activate(runner)
        self.assertEqual(runner.call_count, 1)


if __name__ == '__main__':
    unittest.main()
