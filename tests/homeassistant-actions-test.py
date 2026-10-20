#!/usr/bin/env python3
"""Approval/execution authorization tests; no HA or n8n traffic."""
from __future__ import annotations
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import homeassistant_actions as ha
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_ha_actions', ROOT / 'scripts/setup-homeassistant-actions.py')
setup_ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_ha)


class FakeTransport:
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.entities = {'light.study': {'state': 'off', 'brightness': 0},
                         'light.hall': {'state': 'on', 'brightness': 100},
                         'climate.study': {'state': 'heat', 'temperature': 20,
                                           'temperature_unit': '°C', 'hvac_modes': ['heat', 'off']},
                         'cover.study': {'state': 'closed'}}
        self.failure = False

    def __call__(self, operation, request, **extra):
        self.calls.append((operation, copy.deepcopy(request), extra))
        if operation == 'snapshot':
            return {'observed_at': self.clock(), 'entities':
                    {e: copy.deepcopy(self.entities.get(e, {'state': 'unknown'})) for e in request['entity_ids']}}
        if self.failure:
            raise TimeoutError('unknown physical outcome')
        return {'executed': True, 'proposal_id': extra['proposal_id']}


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.clock = lambda: self.now
        self.transport = FakeTransport(self.clock)
        self.controller = ha.Controller(Path(self.tmp.name) / 'actions.db', self.transport, self.clock)
        self.request = {'domain': 'light', 'service': 'turn_on', 'entity_ids': ['light.study'], 'data': {'brightness': 100}}

    def tearDown(self):
        self.controller.db.close()
        self.tmp.cleanup()

    def proposal(self, request=None, actor='hermes'):
        return self.controller.propose({'actor': actor, 'action': request or self.request})['proposal_id']

    def approved(self):
        pid = self.proposal()
        review = self.controller.review(pid)
        self.controller.approve(pid, review['approval_digest'])
        return pid

    def test_proposal_is_only_intent_and_actor_scoped(self):
        pid = self.proposal()
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.controller.get(pid, 'hermes')['state'], 'proposed')
        with self.assertRaises(ValueError): self.controller.get(pid, 'openclaw')
        with self.assertRaises(ValueError): self.controller.cancel(pid, 'openclaw')
        with self.assertRaises(ValueError): self.controller.execute(pid)

    def test_review_exact_fresh_digest_and_one_shot_effect(self):
        pid = self.approved()
        self.now += 10
        self.assertEqual(self.controller.execute(pid)['state'], 'executed')
        with self.assertRaises(ValueError): self.controller.execute(pid)
        effects = [c for c in self.transport.calls if c[0] == 'execute']
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0][1], self.request)
        self.assertEqual(effects[0][2]['expected_entities']['light.study']['state'], 'off')

    def test_approval_requires_operator_review_and_exact_digest(self):
        pid = self.proposal()
        with self.assertRaises(ValueError): self.controller.approve(pid, 'a' * 64)
        review = self.controller.review(pid)
        with self.assertRaises(ValueError): self.controller.approve(pid, 'a' * 64)
        self.now += 31
        with self.assertRaises(ValueError): self.controller.approve(pid, review['approval_digest'])

    def test_review_replacement_invalidates_previous_digest(self):
        pid = self.proposal()
        old = self.controller.review(pid)
        self.now += 1
        new = self.controller.review(pid)
        with self.assertRaises(ValueError): self.controller.approve(pid, old['approval_digest'])
        self.assertEqual(self.controller.approve(pid, new['approval_digest'])['state'], 'approved')

    def test_exact_five_minute_expiry(self):
        pid = self.approved()
        self.now += 300
        self.assertEqual(self.controller.get(pid, 'hermes')['state'], 'expired')
        with self.assertRaises(ValueError): self.controller.execute(pid)
        self.assertFalse(any(c[0] == 'execute' for c in self.transport.calls))

    def test_changed_target_blocks_execution(self):
        pid = self.approved()
        self.transport.entities['light.study']['state'] = 'on'
        with self.assertRaises(ValueError): self.controller.execute(pid)
        self.assertFalse(any(c[0] == 'execute' for c in self.transport.calls))

    def test_changed_approved_arguments_block_execution(self):
        pid = self.approved()
        changed = dict(self.request, data={'brightness': 200})
        self.controller.db.execute('UPDATE proposals SET action=? WHERE id=?', (ha.canonical(changed), pid))
        with self.assertRaises(ValueError): self.controller.execute(pid)
        self.assertFalse(any(c[0] == 'execute' for c in self.transport.calls))

    def test_uncertain_effect_is_consumed_never_retried(self):
        pid = self.approved()
        self.transport.failure = True
        self.assertEqual(self.controller.execute(pid)['state'], 'uncertain')
        with self.assertRaises(ValueError): self.controller.execute(pid)
        self.assertEqual(sum(c[0] == 'execute' for c in self.transport.calls), 1)

    def test_cancel_removes_approval_and_cannot_cancel_effect(self):
        pid = self.approved()
        self.assertEqual(self.controller.cancel(pid, 'hermes')['state'], 'cancelled')
        with self.assertRaises(ValueError): self.controller.execute(pid)
        pid = self.approved()
        self.controller.execute(pid)
        with self.assertRaises(ValueError): self.controller.cancel(pid)

    def test_all_finite_services_and_boundaries(self):
        samples = [('light', 'turn_off', {}), ('climate', 'set_temperature', {'temperature': 5}),
                   ('climate', 'set_temperature', {'temperature': 35}),
                   ('climate', 'set_hvac_mode', {'hvac_mode': 'heat'}),
                   ('cover', 'open_cover', {}), ('cover', 'close_cover', {}), ('cover', 'stop_cover', {})]
        for domain, service, data in samples:
            with self.subTest(service=service, data=data):
                request = {'domain': domain, 'service': service, 'entity_ids': [domain + '.study'], 'data': data}
                pid = self.proposal(request)
                review = self.controller.review(pid)
                self.controller.approve(pid, review['approval_digest'])
                self.assertEqual(self.controller.execute(pid)['state'], 'executed')

    def test_denied_domains_urls_wildcards_and_nested_data(self):
        values = [dict(self.request, domain=d, entity_ids=[d + '.study'])
                  for d in ['lock', 'alarm_control_panel', 'switch', 'script', 'automation']]
        values += [dict(self.request, service='http://ha/api/services/light/turn_on'),
                   dict(self.request, entity_ids=['light.*']), dict(self.request, entity_ids=['climate.study']),
                   dict(self.request, entity_ids=['all']), dict(self.request, entity_ids=['light.study'] * 2),
                   dict(self.request, data={'target': {'entity_id': 'light.study'}}),
                   dict(self.request, data={'entity_id': 'light.other'}), dict(self.request, data={'brightness': True}),
                   dict(self.request, data={'brightness': 256}), dict(self.request, data={'transition': float('inf')}),
                   dict(self.request, data={'transition': 31}), dict(self.request, extra='unbounded')]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): self.proposal(value)

    def test_target_count_and_numeric_temperature_constraints(self):
        for targets in [[], ['light.e' + str(i) for i in range(9)]]:
            with self.assertRaises(ValueError): self.proposal(dict(self.request, entity_ids=targets))
        for t in [True, 4.9, 35.1, float('nan'), float('inf'), '21']:
            with self.subTest(t=t):
                with self.assertRaises(ValueError): self.proposal({'domain': 'climate', 'service': 'set_temperature',
                                                                'entity_ids': ['climate.study'], 'data': {'temperature': t}})

    def test_unavailable_target_and_unsupported_hvac_mode(self):
        pid = self.proposal(dict(self.request, entity_ids=['light.missing']))
        with self.assertRaises(ValueError): self.controller.review(pid)
        request = {'domain': 'climate', 'service': 'set_hvac_mode', 'entity_ids': ['climate.study'], 'data': {'hvac_mode': 'cool'}}
        with self.assertRaises(ValueError): self.controller.review(self.proposal(request))

    def test_snapshot_stale_extra_target_and_fahrenheit_rejected(self):
        raw = self.transport('snapshot', self.request)
        raw['observed_at'] -= 31
        with self.assertRaises(ValueError): ha.snapshot(raw, self.request, self.now)
        raw['observed_at'] = self.now
        raw['entities']['light.other'] = {'state': 'off'}
        with self.assertRaises(ValueError): ha.snapshot(raw, self.request, self.now)
        request = {'domain': 'climate', 'service': 'set_temperature', 'entity_ids': ['climate.study'], 'data': {'temperature': 21}}
        self.transport.entities['climate.study']['temperature_unit'] = '°F'
        with self.assertRaises(ValueError): self.controller.review(self.proposal(request))

    def test_operator_cli_denies_unprivileged_execution(self):
        with patch.object(sys, 'argv', ['ha', 'execute', self.proposal()]), patch.object(os, 'geteuid', return_value=12345):
            self.assertEqual(ha.main(), 2)

    def test_outstanding_dedup_and_actor_separation(self):
        request = dict(self.request, entity_ids=['light.study', 'light.hall'])
        pid = self.proposal(request)
        reverse = dict(request, entity_ids=list(reversed(request['entity_ids'])))
        self.assertEqual(self.proposal(reverse), pid)
        self.assertNotEqual(self.proposal(reverse, actor='openclaw'), pid)
        self.assertEqual(len(self.controller.list()), 2)
        self.controller.cancel(pid, 'hermes')
        self.assertNotEqual(self.proposal(request), pid)

    def test_actor_and_total_outstanding_caps(self):
        for actor in ['hermes', 'openclaw']:
            for i in range(32): self.proposal(dict(self.request, entity_ids=[f'light.e{i}']), actor=actor)
            with self.assertRaises(ValueError): self.proposal(dict(self.request, entity_ids=['light.limit']), actor=actor)
        self.assertEqual(len(self.controller.list()), 64)
        self.assertEqual(self.proposal(dict(self.request, entity_ids=['light.e0'])), self.controller.db.execute(
            "SELECT id FROM proposals WHERE actor='hermes' AND action=?", (ha.canonical(ha.action(dict(self.request, entity_ids=['light.e0']))),)).fetchone()[0])
        with patch.object(ha, 'ACTOR_LIMIT', 100):
            with self.assertRaises(ValueError): self.proposal(dict(self.request, entity_ids=['light.global_limit']))

    def test_total_record_cap_never_silently_deletes_audit(self):
        rows = [(f'{i:032x}', 'hermes', ha.canonical(self.request), 'cancelled', self.now) for i in range(10000)]
        self.controller.db.execute('BEGIN')
        self.controller.db.executemany('INSERT INTO proposals(id,actor,action,state,created) VALUES(?,?,?,?,?)', rows)
        self.controller.db.execute('COMMIT')
        with self.assertRaises(ValueError): self.proposal()
        self.assertEqual(self.controller.db.execute('SELECT count(*) FROM proposals').fetchone()[0], 10000)

    def test_expired_approval_is_no_longer_outstanding(self):
        pid = self.approved()
        self.now += 300
        self.assertNotEqual(self.proposal(), pid)

    def test_concurrent_retry_dedup_and_last_budget_slot(self):
        code = '''import sys,json,pathlib
sys.path.insert(0,sys.argv[1])
import homeassistant_actions as ha
c=ha.Controller(pathlib.Path(sys.argv[2]),lambda *_:None,lambda:1000.0)
try: print(c.propose(json.loads(sys.argv[3]))['proposal_id'])
except ValueError: print('LIMIT')
'''
        database = str(Path(self.tmp.name) / 'actions.db')
        def race(requests):
            processes = [subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'scripts'), database,
                json.dumps({'actor': 'hermes', 'action': request})], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for request in requests]
            results = []
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, error)
                results.append(output.strip())
            return results
        ids = race([self.request] * 4)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.controller.list()), 1)
        for i in range(30): self.proposal(dict(self.request, entity_ids=[f'light.e{i}']))
        results = race([dict(self.request, entity_ids=['light.slot_a']), dict(self.request, entity_ids=['light.slot_b'])])
        self.assertEqual(results.count('LIMIT'), 1)
        self.assertEqual(len(self.controller.list()), 32)

    def test_root_identity_gate_precedes_uid_drop(self):
        identity = SimpleNamespace(pw_uid=12345, pw_gid=12346)
        calls = []
        with patch.object(os, 'geteuid', return_value=0), patch.object(pwd, 'getpwnam', return_value=identity), \
                patch.object(os, 'setgroups', side_effect=lambda x: calls.append(('groups', x))), \
                patch.object(os, 'setgid', side_effect=lambda x: calls.append(('gid', x))), \
                patch.object(os, 'setuid', side_effect=lambda x: calls.append(('uid', x))):
            ha.enter_identity('list')
        self.assertEqual(calls, [('groups', []), ('gid', 12346), ('uid', 12345)])
        with patch.object(os, 'geteuid', return_value=12345), patch.object(pwd, 'getpwnam') as lookup:
            with self.assertRaises(ValueError): ha.enter_identity('list')
            lookup.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'real Linux root identity drop')
class InstalledOwnershipTest(unittest.TestCase):
    def test_root_cli_reads_controller_owned_token_and_creates_nonroot_db_wal(self):
        import pwd
        owner = pwd.getpwnam('nobody')
        with tempfile.TemporaryDirectory(prefix='hc-ha-root-cli-') as folder:
            base = Path(folder)
            base.chmod(0o711)
            state = base / 'state'
            state.mkdir(mode=0o700)
            os.chown(state, owner.pw_uid, owner.pw_gid)
            token = state / 'executor-token'
            token.write_text('a' * 48)
            token.chmod(0o600)
            os.chown(token, owner.pw_uid, owner.pw_gid)
            code = '''import sys, pathlib, pwd, os, json
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import homeassistant_actions as ha
ha.STATE=pathlib.Path(sys.argv[2])
owner=pwd.getpwnam('nobody')
ha.pwd.getpwnam=lambda name: SimpleNamespace(pw_uid=owner.pw_uid,pw_gid=owner.pw_gid)
sys.argv=['ha','list']
assert ha.main()==0
assert os.geteuid()==owner.pw_uid and os.getegid()==owner.pw_gid and os.getgroups()==[]
controller=ha.Controller(ha.STATE/'actions.sqlite3',lambda *_:None)
controller.propose({'actor':'hermes','action':{'domain':'light','service':'turn_on','entity_ids':['light.synthetic'],'data':{}}})
for name in ['actions.sqlite3','actions.sqlite3-wal','actions.sqlite3-shm']:
 assert (ha.STATE/name).stat().st_uid==owner.pw_uid,name
print('ROOT_CLI_OWNERSHIP_OK')
'''
            # The staged source directory is readable, independent from root-owned state.
            source = base / 'scripts'
            source.mkdir(mode=0o755)
            (source / 'homeassistant_actions.py').write_text((ROOT / 'scripts/homeassistant_actions.py').read_text())
            (source / 'homeassistant_actions.py').chmod(0o644)
            result = subprocess.run([sys.executable, '-c', code, str(source), str(state)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ROOT_CLI_OWNERSHIP_OK', result.stdout)


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self.host)


class SetupTest(unittest.TestCase):
    def test_only_exact_inert_reviewed_template_is_importable(self):
        template = ROOT / 'automations/agent-investigation/n8n-ha-approved-actions-workflow.json'
        self.assertFalse(setup_ha.validate_template(template)['active'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'changed.json'
            flow = json.loads(template.read_text())
            flow['nodes'][7]['parameters']['service'] = 'unlock'
            path.write_text(json.dumps(flow))
            with self.assertRaises(ValueError): setup_ha.validate_template(path)

    def test_unprivileged_setup_cannot_mutate_n8n(self):
        with patch.object(os, 'geteuid', return_value=12345), patch.object(setup_ha, 'call') as call:
            with self.assertRaises(ValueError): setup_ha.provision(Path('/nonexistent'), Path('/nonexistent'))
            call.assert_not_called()

    def test_attached_boot_link_exact_target_and_no_replacement(self):
        self.assertEqual(setup_ha.UNIT.parent, Path('/etc/systemd/system.attached'))
        with tempfile.TemporaryDirectory() as folder:
            unit = Path(folder) / 'controller.service'
            unit.write_text('[Service]\n')
            setup_ha.boot_link(unit)
            link = unit.parent / 'multi-user.target.wants' / unit.name
            self.assertEqual(os.readlink(link), str(unit))
            setup_ha.boot_link(unit)
            link.unlink()
            link.symlink_to('/different/unmanaged.service')
            with self.assertRaises(ValueError): setup_ha.boot_link(unit)


class SocketRecoveryTest(unittest.TestCase):
    def test_stale_owned_socket_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'agent.sock'
            with socket.socket(socket.AF_UNIX) as old:
                old.bind(str(path))
            ha.prepare_socket(path)
            self.assertFalse(path.exists())

    def test_active_listener_regular_file_and_symlink_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'agent.sock'
            with socket.socket(socket.AF_UNIX) as active:
                active.bind(str(path))
                active.listen(1)
                with self.assertRaises(ValueError): ha.prepare_socket(path)
                self.assertTrue(path.exists())
            path.unlink()
            path.write_text('not a socket')
            with self.assertRaises(ValueError): ha.prepare_socket(path)
            self.assertEqual(path.read_text(), 'not a socket')
            target = Path(folder) / 'other.sock'
            with socket.socket(socket.AF_UNIX) as old: old.bind(str(target))
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ValueError): ha.prepare_socket(path)
            self.assertTrue(target.exists())

    def test_wrong_owner_socket_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'agent.sock'
            with socket.socket(socket.AF_UNIX) as old: old.bind(str(path))
            with patch.object(os, 'geteuid', return_value=os.geteuid() + 10000):
                with self.assertRaises(ValueError): ha.prepare_socket(path)
            self.assertTrue(path.exists())


@unittest.skipUnless(hasattr(socket, 'SO_PEERCRED'), 'Linux controller peer-identity enforcement')
class AgentIPCTest(unittest.TestCase):
    def test_agent_cannot_approve_execute_or_impersonate_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'agent.sock')
            ready = threading.Event()
            errors = []
            def serve():
                try:
                    with socketserver.UnixStreamServer(path, ha.AgentHandler) as server:
                        server.controller = ha.Controller(Path(tmp) / 'db', lambda *_: self.fail('no HA effect permitted'))
                        server.adapter_uid = os.geteuid()
                        ready.set()
                        for _ in range(4): server.handle_request()
                        server.controller.db.close()
                except Exception as exc:
                    errors.append(exc)
                    ready.set()
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            for route in ['/approve', '/execute', '/review', '/propose']:
                conn = UnixHTTP(path, timeout=3)
                body = {'actor': 'hermes', 'action': {'domain': 'lock', 'service': 'unlock', 'entity_ids': ['lock.front'], 'data': {}}}
                conn.request('POST', route, json.dumps(body), {'Content-Type': 'application/json'})
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
                conn.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_untrusted_local_uid_cannot_even_propose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'agent.sock')
            ready = threading.Event()
            errors = []
            def serve():
                try:
                    with socketserver.UnixStreamServer(path, ha.AgentHandler) as server:
                        server.controller = ha.Controller(Path(tmp) / 'db', lambda *_: self.fail('no HA effect permitted'))
                        server.adapter_uid = os.geteuid() + 10000
                        ready.set()
                        server.handle_request()
                        self.assertEqual(server.controller.list(), [])
                        server.controller.db.close()
                except Exception as exc:
                    errors.append(exc)
                    ready.set()
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            conn = UnixHTTP(path, timeout=3)
            conn.request('POST', '/propose', json.dumps({'actor': 'hermes', 'action':
                {'domain': 'light', 'service': 'turn_on', 'entity_ids': ['light.study'], 'data': {}}}))
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
            conn.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
