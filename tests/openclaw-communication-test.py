#!/usr/bin/env python3
"""Transport failure, event freshness and approval-boundary regression tests."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from openclaw_notifications import Outbox, quiet
spec = importlib.util.spec_from_file_location("communication", ROOT / "scripts/openclaw-communication.py")
communication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(communication)

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp()
KEY = "home-core:container.n8n"
ALLOWED = {KEY: {"category": "health", "ttl": 900}}
SETTINGS = {"schema_version": 1, "destination": "operator-private", "conversations": ["operator"],
            "projects": ["synthetic-demo"], "actions": {"synthetic.recover": "synthetic-core"}, "timezone": "Europe/Copenhagen",
            "quiet_start_hour": 22, "quiet_end_hour": 7, "cooldown_seconds": 1800}
TOKENS = {role: role + "-" + "x" * 40 for role in ("collector", "conversation", "delivery")}


def iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")


def report(status="degraded", now=NOW, evidence=None):
    return {"schema_version": 1, "document_type": "system_observation_report", "generated_at": iso(now),
            "mode": "observe-only", "automatic_actions": False, "physical_device_actions": False,
            "observations": [{"system_id": "home-core", "check_id": "container.n8n", "stable_key": KEY,
                              "category": "health", "status": status,
                              "severity": "warning" if status == "degraded" else "info",
                              "evidence": evidence or {"state": "unhealthy"}, "observed_at": iso(now),
                              "expires_at": iso(now + 900)}]}


class TransportTests(unittest.TestCase):
    def test_ha_ipc_is_fixed_socket_and_filters_private_controller_receipts(self):
        from unittest.mock import Mock, patch
        from openclaw_capabilities import HomeAssistant
        action = {'domain': 'light', 'service': 'turn_off', 'entity_ids': ['light.synthetic'], 'data': {}}
        receipt = {'proposal_id': 'a' * 32, 'actor': 'hermes', 'state': 'proposed', 'created_at': NOW,
                   'approval_expires_at': None, 'action': action, 'result': None, 'token': 'PRIVATE', 'snapshot': 'PRIVATE'}
        connection, stream = Mock(), Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.side_effect = [json.dumps(receipt).encode(),
            json.dumps({**receipt, 'actor': 'openclaw'}).encode()]
        with patch('openclaw_capabilities.http.client.HTTPConnection', return_value=connection), \
             patch('openclaw_capabilities.socket.socket', return_value=stream):
            client = HomeAssistant('hermes')
            result = client.request('propose', {'action': action})
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(stream.connect.call_args.args, ('/run/homecompute-ha-actions/agent.sock',))
            self.assertEqual(connection.request.call_args.args[:2], ('POST', '/propose'))
            self.assertEqual(json.loads(connection.request.call_args.args[2]), {'actor': 'hermes', 'action': action})
            with self.assertRaises(ValueError): client.request('status', {'proposal_id': 'a' * 32})
            with self.assertRaises(ValueError): client.request('approve', {'proposal_id': 'a' * 32})
            with self.assertRaises(ValueError): client.request('propose', {'action': action, 'actor': 'openclaw'})

    def test_native_ha_tools_assign_authenticated_actor_and_cannot_execute(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        native_tokens = {role: 'native-' + role + '-' * 40 for role in ('openclaw', 'hermes')}
        tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'assistant', 'snapshot': 'snapshot'})
        turn = Mock(side_effect=AssertionError('native model must not run'))
        identities = []
        def ipc(client, operation, arguments):
            identities.append((client.actor, operation, arguments))
            return {'proposal_id': 'a' * 32, 'actor': client.actor, 'state': 'proposed'}
        server = communication.serve(self.store, TOKENS, ALLOWED, turn, ('127.0.0.1', 0), lambda: NOW,
            tasks=tasks, mcp_tokens=native_tokens, ha_actions=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def call(name, arguments, actor='openclaw'):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/mcp',
                data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': arguments}}).encode(),
                headers={'Authorization': 'Bearer ' + native_tokens[actor], 'Content-Type': 'application/json',
                         'Accept': 'application/json, text/event-stream'})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.loads(response.read())
        action = {'domain': 'light', 'service': 'turn_on', 'entity_ids': ['light.synthetic'], 'data': {'brightness': 128}}
        try:
            with patch('openclaw_capabilities.HomeAssistant.request', new=ipc):
                for actor in ('openclaw',):
                    self.assertFalse(call('homecompute_ha_propose', {'action': action}, actor)['result']['isError'])
                    self.assertFalse(call('homecompute_ha_status', {'proposal_id': 'a' * 32}, actor)['result']['isError'])
                    self.assertFalse(call('homecompute_ha_cancel', {'proposal_id': 'a' * 32}, actor)['result']['isError'])
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    call('homecompute_ha_propose', {'action': action}, 'hermes')
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                self.assertEqual([entry[0] for entry in identities], ['openclaw'] * 3)
                for name in ('homecompute_ha_execute', 'homecompute_ha_approve', 'homecompute_ha_review'):
                    self.assertEqual(call(name, {})['error']['code'], -32602)
                self.assertEqual(call('homecompute_ha_propose', {'action': action, 'actor': 'hermes'})['error']['code'], -32602)
                self.assertEqual(call('homecompute_ha_propose', {'action': {**action, 'domain': 'lock'}})['error']['code'], -32602)
                self.assertEqual(call('homecompute_ha_status', {'proposal_id': '../../secrets'})['error']['code'], -32602)
                self.assertEqual(len(identities), 3)
            turn.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_native_mcp_tools_share_bounded_reads_tasks_without_model_or_approval_authority(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        native_tokens = {'openclaw': 'synthetic-native-openclaw-' + '-' * 40}
        task_id = '0aa529dc-8933-4116-852e-7871f01cbe07'
        tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'broker-assistant', 'snapshot': 'broker-snapshot'})
        reader = SimpleNamespace(registry={'systems': [{'id': 'home-core', 'enabled': True}]},
            read=Mock(return_value={'trusted': True}), compact=lambda value: {'status': 'healthy', 'fresh': value['trusted']})
        turn = Mock(side_effect=AssertionError('MCP must never call native model recursively'))
        routes = []
        def broker(origin, path, token, body=None):
            routes.append((origin, path, token, body))
            return {'id': task_id, 'project': 'synthetic-demo', 'state': 'pending', 'context': 'PRIVATE', 'policy': 'PRIVATE'}
        server = communication.serve(self.store, TOKENS, ALLOWED, turn, ('127.0.0.1', 0), lambda: NOW,
            infrastructure=reader, tasks=tasks, mcp_tokens=native_tokens)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def rpc(method, params=None, role='openclaw', path='/mcp', token=None):
            payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}' + path,
                data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + (token or native_tokens[role]),
                    'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.loads(response.read())
        try:
            with patch('openclaw_capabilities.request', side_effect=broker):
                inventory = rpc('tools/list')['result']['tools']
                self.assertEqual(len(inventory), 8)
                submit = {'project': 'synthetic-demo', 'issue_key': 'native:1', 'summary': 'Synthetic', 'context': ''}
                result = rpc('tools/call', {'name': 'homecompute_task_submit', 'arguments': submit})['result']
                self.assertFalse(result['isError'])
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(routes, [('http://127.0.0.1:18792', '/tasks', 'broker-assistant', submit)])
                result = rpc('tools/call', {'name': 'homecompute_system_read', 'arguments': {'system_id': 'home-core'}})['result']
                self.assertEqual(json.loads(result['content'][0]['text'])['report']['status'], 'healthy')
                reader.read.assert_called_once_with('home-core')
                for name in ('homecompute_task_approve', 'homecompute_task_publish', 'homecompute_action_execute', 'homecompute_action_approve'):
                    self.assertEqual(rpc('tools/call', {'name': name})['error']['code'], -32602)
                result = rpc('tools/call', {'name': 'homecompute_task_submit', 'arguments': {**submit, 'project': 'forbidden'}})['result']
                self.assertTrue(result['isError'])
                self.assertEqual(len(routes), 1)
                self.assertEqual(rpc('tools/call', {'name': 'homecompute_system_read', 'arguments': {'system_id': 'router'}})['error']['code'], -32602)
                for token in TOKENS.values():
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        rpc('tools/list', token=token)
                    self.assertEqual(denied.exception.code, 401)
                for path in ('/conversation', '/observations', '/claim', '/actions'):
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        rpc('tools/list', path=path)
                    self.assertEqual(denied.exception.code, 401)
            turn.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_native_mcp_cancel_verifies_project_before_forwarding_and_never_escalates(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from openclaw_capabilities import Capabilities
        tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'assistant', 'snapshot': 'snapshot'})
        bridge = Capabilities(tasks, actions={'synthetic.recover': 'synthetic-core'})
        with patch('openclaw_capabilities.request', return_value={'project': 'private-project'}) as request:
            with self.assertRaises(ValueError): bridge.action('known-id', 'cancel', {})
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[2], 'snapshot')
        with patch('openclaw_capabilities.request') as request:
            for operation in ('approve', 'publish', 'result', 'execute'):
                with self.assertRaises(ValueError): bridge.action('known-id', operation, {})
            with self.assertRaises(ValueError): bridge.actions.propose({})
            with self.assertRaises(ValueError): bridge.actions.propose({'action_id': 'sudo.restart', 'target': 'home-core'})
            request.assert_not_called()

    def test_native_mcp_credential_cannot_reuse_transport_or_broker_credentials(self):
        from types import SimpleNamespace
        tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'broker-key-' + 'x' * 40})
        native = {'openclaw': 'native-openclaw-' + 'x' * 40}
        for value in (*TOKENS.values(), *tasks.tokens.values(), 'openshell:resolve:env:FAKE_TOKEN_WITH_LONG_NAME'):
            with self.assertRaises(ValueError):
                communication.serve(self.store, TOKENS, ALLOWED, lambda *_: '', tasks=tasks,
                    mcp_tokens={**native, 'openclaw': value})

    def test_native_mcp_rejects_missing_or_extra_assistants(self):
        from types import SimpleNamespace
        tasks = SimpleNamespace(projects=set(), tokens={})
        for native in ({}, {'hermes': 'x' * 40}, {'openclaw': 'x' * 40, 'operator': 'y' * 40},
                       {'openclaw': 'x' * 40, 'hermes': 'x' * 40}):
            with self.assertRaises(ValueError):
                communication.serve(self.store, TOKENS, ALLOWED, lambda *_: '', tasks=tasks, mcp_tokens=native)

    def test_maintenance_commands_are_deterministic_and_never_call_model(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            store = Outbox(Path(folder) / 'transport.sqlite3', SETTINGS)
            turn = Mock(side_effect=AssertionError('model must not run'))
            maintenance = Mock()
            maintenance.respond.return_value = 'Read-only check requested; human approval required.'
            for index, text in enumerate(('/updates homepage', '/update homepage execute', '/maintenance homepage', '/approve homepage')):
                result = communication.conversation({'conversation': 'operator', 'request_id': str(index),
                    'destination': SETTINGS['destination'], 'text': text}, store, turn, NOW, maintenance=maintenance)
                self.assertIsNotNone(result)
            turn.assert_not_called()
            self.assertEqual(maintenance.respond.call_count, 3)
            store.db.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def observe(self, value):
        self.store.observations(value, ALLOWED, instant_report(value))

    def test_durable_dedup_and_fresh_envelope(self):
        self.observe(report())
        self.observe(report(now=NOW + 1200))
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.assertEqual(self.store.status(NOW)["outbox"], {"pending": 1})
        self.assertIsNotNone(self.store.claim(NOW + 1200))

    def test_unknown_and_stale_never_recover(self):
        self.observe(report())
        self.observe(report("unknown", NOW + 1))
        stale = report("healthy", NOW + 2)
        stale["observations"][0]["expires_at"] = iso(NOW + 1)
        with self.assertRaises(ValueError): self.observe(stale)
        self.assertEqual(self.store.status(NOW)["incidents"][0]["status"], "degraded")

    def test_recovery_before_delivery_is_quiet(self):
        self.observe(report())
        self.observe(report("healthy", NOW + 1))
        self.assertIsNone(self.store.claim(NOW + 1))

    def test_delivery_recovery_and_recurrence(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.observe(report("healthy", NOW + 1))
        recovery = self.store.claim(NOW + 1)
        self.assertEqual(recovery["kind"], "recovery")
        self.store.acknowledge(recovery["delivery_key"], recovery["claim"], "telegram:2", NOW + 1)
        self.observe(report(now=NOW + 1801))
        recurrence = self.store.claim(NOW + 1801)
        self.assertNotEqual(recurrence["delivery_key"], first["delivery_key"])

    def test_ambiguous_delivery_never_replays(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.assertEqual(self.store.status(NOW)["outbox"], {"uncertain": 1})
        self.assertIsNone(self.store.claim(NOW))
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        with self.assertRaises(ValueError):
            self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:2", NOW)

    def test_concurrent_claims(self):
        self.observe(report())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: self.store.claim(NOW), range(4)))
        self.assertEqual(sum(x is not None for x in values), 1)

    def test_cooldown_retains_latest_change(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "telegram:1", NOW)
        self.observe(report(now=NOW + 1, evidence={"state": "missing"}))
        self.assertIsNone(self.store.claim(NOW + 1))
        self.observe(report(now=NOW + 1800, evidence={"state": "missing"}))
        self.assertIsNotNone(self.store.claim(NOW + 1800))

    def test_quiet_hours_and_dst(self):
        for value in ("2026-10-09T21:00:00+00:00", "2026-10-10T04:00:00+00:00",
                      "2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"):
            self.assertTrue(quiet(datetime.fromisoformat(value).timestamp(), SETTINGS))
        self.assertFalse(quiet(NOW, SETTINGS))
        self.observe(report(now=NOW + 9 * 3600))
        self.assertIsNone(self.store.claim(NOW + 9 * 3600))

    def test_task_heartbeats_and_approval_boundary(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "pending", "updated": NOW, "context": "private", "error": "token"}
        self.store.tasks([task], NOW)
        self.store.tasks([task], NOW + 1)
        event = self.store.claim(NOW)
        self.assertEqual(event["kind"], "approval")
        self.assertIn("Chat replies do not approve", event["text"])
        self.assertEqual(event['text'].splitlines()[0], 'HomeCompute task needs execution approval: synthetic-demo.')
        self.assertIn('\n\n**Next step**\n\nExecution approval required: assistant-task.py approve ' + task['id'], event['text'])
        self.assertNotIn("token", event["text"])
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW)
        task["state"] = "running"
        task["updated"] += 1
        self.store.tasks([task], NOW + 1)
        self.store.tasks([task], NOW + 1)
        progress = self.store.claim(NOW)
        self.assertEqual(progress["kind"], "progress")
        self.store.acknowledge(progress["delivery_key"], progress["claim"], "test:2", NOW)
        self.assertIsNone(self.store.claim(NOW + 1))

    def test_sensitive_fields_and_unregistered_check_rejected(self):
        data = report()
        data["observations"][0]["evidence"]["household_states"] = "private"
        with self.assertRaises(ValueError): self.observe(data)
        with self.assertRaises(ValueError): self.store.observations(report(), {}, NOW)
        data = report()
        data["observations"][0]["arbitrary_instructions"] = "private"
        self.observe(data)
        self.assertNotIn("private", self.store.claim(NOW)["text"])

    def test_action_approval_request_and_obsolete_approval(self):
        action = {"id": "11111111-1111-4111-8111-111111111111", "action_id": "synthetic.recover",
                  "target": "synthetic-core", "state": "pending", "updated": NOW, "snapshot": "private"}
        self.store.actions([action], NOW)
        self.store.actions([action], NOW)
        event = self.store.claim(NOW)
        self.assertEqual(event['text'].splitlines()[0], 'HomeCompute action needs approval: synthetic.recover on synthetic-core.')
        self.assertIn('\n\n**Next step**\n\nApproval required: assistant-action.py review ' + action['id'], event['text'])
        self.assertIn("exact reviewed digest", event["text"])
        self.assertNotIn("private", event["text"])
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:action", NOW)
        action["state"] = "approved"; action["updated"] += 1
        self.store.actions([action], NOW + 1)
        self.assertIsNone(self.store.claim(NOW + 1))
        action["state"] = "pending"; action["updated"] += 1
        self.store.actions([action], NOW + 2)
        self.assertIsNotNone(self.store.claim(NOW + 2))

    def test_pending_task_approval_superseded_after_approval(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "pending", "updated": NOW}
        self.store.tasks([task], NOW)
        task["state"] = "queued"; task["updated"] += 1
        self.store.tasks([task], NOW + 1)
        self.assertEqual(self.store.claim(NOW + 1)["kind"], "progress")

    def test_stale_report_and_old_recovery(self):
        with self.assertRaises(ValueError): self.store.observations(report(), ALLOWED, NOW + 1000)
        self.observe(report())
        self.observe(report("healthy", NOW - 1))
        self.assertEqual(len(self.store.status(NOW)["incidents"]), 1)

    def test_equal_timestamp_conflicts_and_heartbeat_watermark(self):
        self.observe(report())
        with self.assertRaises(ValueError): self.observe(report("healthy"))
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo", "state": "running", "updated": NOW}
        self.store.tasks([task], NOW)
        task["updated"] = NOW + 30
        self.store.tasks([task], NOW + 30)
        task["state"] = "pending"; task["updated"] = NOW + 1
        self.store.tasks([task], NOW + 30)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events WHERE kind='approval'").fetchone())
        task["updated"] = NOW + 30
        with self.assertRaises(ValueError): self.store.tasks([task], NOW + 30)

    def test_recovery_freshness_and_reopened_incident(self):
        self.observe(report())
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "test:1", NOW)
        self.observe(report("healthy", NOW + 1))
        self.assertIsNone(self.store.claim(NOW + 902))
        self.observe(report(now=NOW + 903))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM events WHERE kind='recovery' AND state='pending'").fetchone())

    def test_registry_ttl_and_source_report_expiry(self):
        value = report()
        value["observations"][0]["expires_at"] = iso(NOW + 901)
        with self.assertRaises(ValueError): self.observe(value)
        value = report(evidence={"count": 0, "source_report_generated_at": iso(NOW - 1000)})
        with self.assertRaises(ValueError): self.observe(value)

    def test_repeated_ack_cannot_extend_cooldown(self):
        self.observe(report())
        event = self.store.claim(NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW)
        self.store.acknowledge(event["delivery_key"], event["claim"], "test:1", NOW + 900)
        row = self.store.db.execute("SELECT created FROM events WHERE key=?", (event["delivery_key"],)).fetchone()
        self.assertEqual(row[0], NOW)

    def test_explicit_reply_bypasses_proactive_quiet_hours(self):
        late = NOW + 9 * 3600
        body = {"conversation": "operator", "destination": "operator-private", "request_id": "quiet:1", "text": "/status"}
        communication.conversation(body, self.store, lambda *_: "unused", late)
        self.assertEqual(self.store.claim(late)["kind"], "reply")

    def test_stale_queue_cannot_starve_required_action(self):
        with self.store.db:
            for number in range(101):
                self.store.add("old:" + str(number), "old", "incident", {"observation": {"expires_at": iso(NOW - 1)}, "text": "old"}, NOW)
            self.store.add("required", "task", "approval", {"text": "Approval required"}, NOW)
        self.assertEqual(self.store.claim(NOW)["kind"], "approval")

    def test_newer_task_state_cannot_regress_and_status_is_bounded(self):
        import uuid
        tasks = [{"id": str(uuid.uuid4()), "project": "synthetic-demo", "state": "running", "updated": NOW} for _ in range(20)]
        self.store.tasks(tasks, NOW)
        regression = dict(tasks[0], state="pending", updated=NOW + 1)
        with self.assertRaises(ValueError): self.store.tasks([regression], NOW + 1)
        body = {"conversation": "operator", "destination": "operator-private", "request_id": "status:bounded", "text": "/status"}
        result = communication.conversation(body, self.store, lambda *_: "unused", NOW)
        status = json.loads(result["reply"])
        self.assertEqual(result["state"], "completed")
        self.assertTrue(status["tasks_truncated"])
        self.assertLess(len(result["reply"]), 4000)

    def test_normal_chat_receives_machine_context_once_without_changing_replay_identity(self):
        from types import SimpleNamespace
        reads, turns = [], []
        def context(text):
            reads.append(text)
            return "Trusted machine evidence: home-spark has 163 updates. User question: " + text
        reader = SimpleNamespace(respond=lambda *_: None, contextual_prompt=context)
        def turn(name, text):
            turns.append((name, text))
            return "home-spark: 163 available updates."
        body = {"conversation": "operator", "destination": "operator-private",
                "request_id": "machines:normal", "text": "What is the status and pending updates on my machines?"}
        first = communication.conversation(body, self.store, turn, NOW, reader)
        second = communication.conversation(body, self.store, turn, NOW + 1, reader)
        self.assertEqual(first, second)
        self.assertEqual(reads, [body["text"]])
        self.assertEqual(len(turns), 1)
        self.assertIn("Trusted machine evidence", turns[0][1])
        self.assertEqual(first["state"], "completed")

    def test_context_budget_failure_is_completed_without_dispatch_or_uncertain_pause(self):
        from types import SimpleNamespace
        def context(_):
            raise ValueError("context budget")
        def forbidden(*_):
            self.fail("unprepared context must not dispatch a native turn")
        reader = SimpleNamespace(respond=lambda *_: None, contextual_prompt=context)
        body = {"conversation": "operator", "destination": "operator-private",
                "request_id": "machines:budget", "text": "status"}
        result = communication.conversation(body, self.store, forbidden, NOW, reader)
        self.assertEqual(result["state"], "completed")
        self.assertIn("no model turn", result["reply"])

    def test_equivalent_timestamp_formats_cannot_conflict(self):
        self.observe(report())
        value = report("healthy")
        value["observations"][0]["observed_at"] = value["observations"][0]["observed_at"].replace("Z", "+00:00")
        with self.assertRaises(ValueError): self.observe(value)
        self.assertEqual(len(self.store.status(NOW)["incidents"]), 1)

    def test_model_counter_cannot_omit_source_provenance(self):
        value = report(evidence={"count": 0})
        row = value["observations"][0]
        row.update(category="updates", check_id="model-update-report.changed", stable_key="home-core:model-update-report.changed")
        allowed = {row["stable_key"]: {"category": "updates", "ttl": 900}}
        with self.assertRaises(ValueError): self.store.observations(value, allowed, NOW)
        row["evidence"]["source_report_generated_at"] = iso(NOW)
        self.store.observations(value, allowed, NOW)

    def test_unknown_observation_watermark_survives_restart(self):
        self.observe(report())
        self.observe(report("unknown", NOW + 60))
        self.store.db.close()
        self.store = Outbox(Path(self.temp.name) / "transport.sqlite3", SETTINGS)
        self.observe(report("healthy", NOW + 30))
        self.assertEqual(len(self.store.status(NOW + 60)["incidents"]), 1)
        self.observe(report("healthy", NOW + 61))
        self.assertEqual(len(self.store.status(NOW + 61)["incidents"]), 0)

    def test_native_analysis_envelope_is_scoped_and_fresh(self):
        self.observe(report())
        event = self.store.claim(NOW)
        envelope = self.store.incident(event["delivery_key"], NOW)
        self.assertEqual(envelope["issue_key"], "monitor:" + envelope["observation"]["episode_key"])
        self.assertNotIn("text", envelope)
        with self.assertRaises(ValueError): self.store.incident(event["delivery_key"], NOW + 901)
        self.observe(report("healthy", NOW + 1))
        with self.assertRaises(ValueError): self.store.incident(event["delivery_key"], NOW + 1)

    def test_http_e2e_auth_dedup_status_send_ack_and_model_outage(self):
        calls = []
        def turn(name, text):
            calls.append((name, text))
            if text == "outage": raise RuntimeError("secret diagnostics")
            return "synthetic reply"
        server = communication.serve(self.store, TOKENS, ALLOWED, turn, ("127.0.0.1", 0), lambda: NOW)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def post(path, role, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + TOKENS[role], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response: return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/claim", "conversation", {})
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            post("/observations", "collector", report())
            event = post("/claim", "delivery", {})["event"]
            self.assertEqual(event["destination"], "operator-private")
            post("/ack", "delivery", {"delivery_key": event["delivery_key"], "claim": event["claim"], "receipt": "synthetic:42"})
            self.assertIsNone(post("/claim", "delivery", {})["event"])
            analysis = post("/analysis", "collector", {"delivery_key": event["delivery_key"]})
            self.assertEqual(analysis["state"], "completed")
            self.assertIn("Read-only", calls[-1][1])
            body = {"conversation": "operator", "destination": "operator-private", "request_id": "update:1", "text": "hello"}
            self.assertEqual(post("/conversation", "conversation", body)["reply"], "synthetic reply")
            post("/conversation", "conversation", body)
            self.assertEqual(len(calls), 2)
            body["request_id"] = "update:2"; body["text"] = "/approve anything"
            self.assertIn("cannot approve", post("/conversation", "conversation", body)["reply"])
            body["request_id"] = "update:3"; body["text"] = "outage"
            self.assertEqual(post("/conversation", "conversation", body)["state"], "uncertain")
            post("/conversation", "conversation", body)
            self.assertEqual(len(calls), 3)
            body["destination"] = "family"
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/conversation", "conversation", body)
            denied.exception.close()
            self.assertFalse(post("/status", "conversation", {})["actions_enabled"])
            with self.assertRaises(urllib.error.HTTPError) as denied: post("/approve", "conversation", {})
            denied.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_machine_claim_and_ack_deliver_every_member(self):
        allowed = dict(ALLOWED, **{"home-core:container.caddy": {"category": "health", "ttl": 900}})
        server = communication.serve(self.store, TOKENS, allowed, lambda *_: "unused", ("127.0.0.1", 0), lambda: NOW)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def post(path, role, body):
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + TOKENS[role], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response: return json.load(response)
        try:
            data = report()
            data["observations"].append(dict(data["observations"][0], check_id="container.caddy", stable_key="home-core:container.caddy"))
            post("/observations", "collector", data)
            event = post("/claim", "delivery", {})["event"]
            self.assertIn("Container n8n", event["text"])
            self.assertIn("Container caddy", event["text"])
            self.assertEqual(set(event), {"delivery_key", "claim", "destination", "kind", "text"})
            post("/ack", "delivery", {"delivery_key": event["delivery_key"], "claim": event["claim"], "receipt": "synthetic:machine"})
            self.assertIsNone(post("/claim", "delivery", {})["event"])
            self.assertEqual(self.store.status(NOW)["outbox"], {"delivered": 2})
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_phone_welcome_is_deterministic_and_queued_without_model(self):
        def forbidden(*_):
            self.fail("welcome must not call the model")
        body = {"conversation": "operator", "destination": "operator-private",
                "request_id": "telegram:9876:42", "text": "/start"}
        result = communication.conversation(body, self.store, forbidden, NOW)
        self.assertEqual(result["state"], "completed")
        self.assertIn("Write a message", result["reply"])
        self.assertEqual(self.store.claim(NOW)["kind"], "reply")

    def test_fail_closed_configuration(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(SETTINGS))
        communication.load_config(path)
        with self.assertRaises(ValueError): communication.load_config(ROOT / "config/openclaw-communication.example.json")
        with self.assertRaises(ValueError): communication.serve(self.store, TOKENS, ALLOWED, lambda *_: "", ("0.0.0.0", 18793))
        tokens = dict(TOKENS); tokens["delivery"] = tokens["collector"]
        with self.assertRaises(ValueError): communication.serve(self.store, tokens, ALLOWED, lambda *_: "")
        self.assertIn("home-core:container.homecompute-automation-n8n-1", communication.registry_keys(ROOT / "config/system-monitoring.json"))
        self.assertFalse(any(x.startswith("home-assistant:") for x in communication.registry_keys(ROOT / "config/system-monitoring.json")))


    def test_task_progress_results_and_deterministic_phone_read(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo",
                "state": "running", "progress": "coding", "updated": NOW, "context": "private"}
        self.store.tasks([task], NOW)
        first = self.store.claim(NOW)
        self.store.acknowledge(first["delivery_key"], first["claim"], "test:progress", NOW)
        task["updated"] += 1
        self.store.tasks([task], NOW + 1)
        self.assertIsNone(self.store.claim(NOW + 1))
        task.update(state="review", updated=NOW + 2, result={"ok": True, "tests_passed": True,
                    "changed_files": 1, "session_id": "synthetic-session"})
        task.pop("progress")
        self.store.tasks([task], NOW + 2)
        row = self.store.status(NOW + 2)["tasks"][0]
        self.assertEqual(row["status"], "completed")
        self.assertTrue(row["approval_required"])
        self.assertEqual(row["approval_kind"], "publication")
        self.assertNotIn("context", row)
        def forbidden(*_): self.fail("status must not run a model")
        body = {"conversation": "operator", "destination": "operator-private",
                "request_id": "telegram:task:1", "text": "/task " + task["id"]}
        reply = communication.conversation(body, self.store, forbidden, NOW + 2)
        self.assertIn("changed_files", reply["reply"])

    def test_task_projection_rejects_private_result_and_hostile_link(self):
        task = {"id": "11111111-1111-4111-8111-111111111111", "project": "synthetic-demo",
                "state": "completed", "updated": NOW, "pr_url": "https://evil.invalid/pull/1"}
        with self.assertRaises(ValueError): self.store.tasks([task], NOW)
        task.pop("pr_url")
        task["result"] = {"ok": True, "changed_files": 1, "files": [{"content": "private"}]}
        with self.assertRaises(ValueError): self.store.tasks([task], NOW)
        task["result"] = {"ok": True, "changed_files": 1, "session_id": 123}
        with self.assertRaises(ValueError): self.store.tasks([task], NOW)


def instant_report(value):
    return datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00")).timestamp()


if __name__ == "__main__":
    unittest.main()
