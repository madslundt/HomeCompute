#!/usr/bin/env python3
"""Finite service tools stay behind authenticated native OpenClaw transport."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from openclaw_notifications import Outbox
from openclaw_household_tasks import FamilyTasks, HouseholdTasks
import openclaw_services
spec = importlib.util.spec_from_file_location('communication', ROOT / 'scripts/openclaw-communication.py')
communication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(communication)


class ServiceWiringTests(unittest.TestCase):
    def test_family_task_tools_dispatch_to_isolated_store_and_cannot_impersonate_owner(self):
        from uuid import uuid4
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            token = root / 'token'; token.write_text('tvly-synthetic-not-live'); token.chmod(0o600)
            config = root / 'services.json'
            config.write_text(json.dumps({'schema_version': 1, 'tavily_token_file': str(token)})); config.chmod(0o600)
            owner = HouseholdTasks(root)
            owner.request('homecompute_household_task_create', {'title': 'Private task', 'request_id': str(uuid4())})
            family = FamilyTasks(root)
            with patch.object(openclaw_services, 'TOKEN_PATH', token):
                services = openclaw_services.Services(config, family_tasks=family)
            names = {tool['name'] for tool in services.tools}
            self.assertIn('homecompute_family_task_create', names)
            self.assertNotIn('homecompute_household_task_list', names)
            args = {'title': 'Shared task', 'request_id': str(uuid4())}
            self.assertEqual(services.validate('homecompute_family_task_create', args), args)
            self.assertEqual(services.request('homecompute_family_task_create', args)['source'], 'shared-family-tasks')
            result = services.request('homecompute_family_task_list', {})
            self.assertEqual([t['title'] for t in result['tasks']], ['Shared task'])
            with self.assertRaises(ValueError): services.request('homecompute_household_task_list', {})
            with self.assertRaises(ValueError): services.validate('homecompute_family_task_list', {'scope': 'owner'})

    def test_calendar_composes_with_existing_services_and_routes_typed_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / 'token'
            token.write_text('tvly-synthetic-not-live')
            token.chmod(0o600)
            config = root / 'services.json'
            config.write_text(json.dumps({'schema_version': 1, 'tavily_token_file': str(token)}))
            config.chmod(0o600)
            name = 'homecompute_calendar_create'
            args = {'calendar': 'family', 'request_id': 'synthetic'}
            calendar = SimpleNamespace(tools=[{'name': name}],
                validate=Mock(return_value=args), request=Mock(return_value={'outcome': 'confirmed'}))
            household = SimpleNamespace(tools=[{'name': 'homecompute_shopping_list'}],
                validate=Mock(return_value={}), request=Mock(return_value={'items': []}))
            with patch.object(openclaw_services, 'TOKEN_PATH', token):
                services = openclaw_services.Services(config, household, calendar)
            self.assertEqual(len(services.tools), len(openclaw_services.TOOLS) + 2)
            self.assertEqual(services.validate(name, args), args)
            self.assertEqual(services.request(name, args), {'outcome': 'confirmed'})
            calendar.validate.assert_called_once_with(name, args)
            calendar.request.assert_called_once_with(name, args)
            household.request.assert_not_called()
            self.assertEqual(services.request('homecompute_shopping_list', {}), {'items': []})
            self.assertEqual(services.validate('homecompute_web_search', {'query': 'public'}), {'query': 'public'})
            with self.assertRaises(ValueError):
                services.validate('homecompute_calendar_delete', args)

    def test_native_authentication_inventory_validation_and_failure_redaction(self):
        settings = {'schema_version': 1, 'destination': 'operator-private', 'conversations': ['operator'],
                    'projects': [], 'actions': {}, 'timezone': 'Europe/Copenhagen',
                    'quiet_start_hour': 22, 'quiet_end_hour': 7, 'cooldown_seconds': 1800}
        tokens = {name: name + 'x' * 40 for name in ('collector', 'conversation', 'delivery')}
        native = {name: name + '-' * 40 for name in ('openclaw', 'hermes')}
        name = 'homecompute_web_search'
        def validate(tool, arguments):
            if tool != name or arguments != {'query': 'synthetic'}:
                raise ValueError('PRIVATE diagnostic')
            return arguments
        services = SimpleNamespace(tools=[{'name': name, 'inputSchema': {'type': 'object'}}],
            validate=validate, request=Mock(return_value={'source': 'synthetic', 'untrusted_content': True}))
        tasks = SimpleNamespace(tokens={'assistant': 'broker-assistant'}, projects=set())
        turn = Mock(side_effect=AssertionError('model recursion forbidden'))
        with tempfile.TemporaryDirectory() as directory:
            store = Outbox(Path(directory) / 'transport.sqlite3', settings)
            server = communication.serve(store, tokens, {}, turn, ('127.0.0.1', 0),
                tasks=tasks, mcp_tokens=native, services=services)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            def rpc(method, params=None, actor='openclaw'):
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_address[1]}/mcp',
                    data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode(),
                    headers={'Authorization': 'Bearer ' + native[actor], 'Content-Type': 'application/json',
                             'Accept': 'application/json, text/event-stream'})
                with urllib.request.urlopen(request, timeout=3) as response:
                    return json.loads(response.read())
            try:
                self.assertIn(name, [tool['name'] for tool in rpc('tools/list')['result']['tools']])
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    rpc('tools/list', actor='hermes')
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                arguments = {'query': 'synthetic'}
                result = rpc('tools/call', {'name': name, 'arguments': arguments})
                self.assertFalse(result['result']['isError'])
                services.request.assert_called_once_with(name, arguments)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    rpc('tools/call', {'name': name, 'arguments': arguments}, 'hermes')
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                for tool, args, actor in ((name, {**arguments, 'url': 'http://private'}, 'openclaw'),
                    ('homecompute_school_send_message', {}, 'openclaw')):
                    result = rpc('tools/call', {'name': tool, 'arguments': args}, actor)
                    self.assertEqual(result['error']['code'], -32602)
                    self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(services.request.call_count, 1)
                services.request.side_effect = ValueError('PRIVATE upstream secret')
                result = rpc('tools/call', {'name': name, 'arguments': arguments})
                self.assertTrue(result['result']['isError'])
                self.assertNotIn('PRIVATE', json.dumps(result))
                turn.assert_not_called()
            finally:
                server.shutdown(); server.server_close(); worker.join(); store.db.close()


if __name__ == '__main__':
    unittest.main()
