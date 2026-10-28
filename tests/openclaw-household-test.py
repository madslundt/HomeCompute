"""Household tool boundaries and durable write replay, without live mutations."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_household as household
from openclaw_household_requests import Requests
import openclaw_services as services

REQUEST = '00000000-0000-4000-8000-000000000001'
ARGUMENTS = {'request_id': REQUEST, 'name': 'Synthetic', 'amount': 1}


class HouseholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.root.chmod(0o700)
        self.path = self.root / 'requests.sqlite3'
        self.ledger = Requests(self.path)
        self.addCleanup(self.tmp.cleanup)

    def test_strict_schema_rejects_arbitrary_commands_entities_types_and_missing_preconditions(self):
        cases = [
            ('shopping_add', {**ARGUMENTS, 'entity_id': 'todo.other'}),
            ('shopping_add', {**ARGUMENTS, 'amount': True}),
            ('shopping_add', {**ARGUMENTS, 'amount': 1.5}),
            ('shopping_add', {**ARGUMENTS, 'request_id': 'new'}),
            ('shopping_add', {**ARGUMENTS, 'name': 'x\x00'}),
            ('shopping_remove', {'request_id': REQUEST, 'item_id': 'x'}),
            ('discount_watch', {'name': 'Coffee', 'request_id': REQUEST, 'max_unit_price': float('nan')}),
            ('discount_watch', {'name': 'Coffee', 'request_id': REQUEST, 'item_id': 'x'}),
            ('discount_watch', {'name': 'Coffee', 'request_id': REQUEST, 'clear_max_unit_price': True}),
            ('storage_update', {'request_id': REQUEST, 'item_id': 'a' * 32, 'expected_revision': 1, 'unit': 'kg'}),
            ('storage_add', {'request_id': REQUEST, 'location_id': 'a' * 32, 'name': 'x', 'quantity': 1, 'unit': 'kg'}),
            ('storage_add', {'request_id': REQUEST, 'location_id': 'a' * 32, 'name': 'x', 'quantity': '1',
                             'unit': 'kg', 'expiry_date': '2026-02-30'}),
            ('execute', {'command': 'anything'}),
        ]
        for operation, args in cases:
            with self.subTest(operation=operation, args=args), self.assertRaises(ValueError):
                household.validate('homecompute_' + operation, args)
        self.assertEqual(household.validate('homecompute_shopping_add', ARGUMENTS), ARGUMENTS)
        schema = {tool['name']: tool for tool in household.TOOLS}
        self.assertEqual(len(schema), 14)
        self.assertEqual(len(household.WRITES), 10)
        self.assertTrue(all(not tool['inputSchema']['additionalProperties'] for tool in schema.values()))
        self.assertFalse(schema['homecompute_shopping_add']['annotations']['readOnlyHint'])

    def test_confirmed_write_is_never_repeated_after_restart_or_id_reuse(self):
        def execute(before_write):
            self.assertEqual(self.ledger.status(REQUEST)['state'], 'prepared')
            before_write()
            self.assertEqual(self.ledger.status(REQUEST)['state'], 'sending')
            return {'outcome': 'confirmed', 'changed': True, 'item_id': 'synthetic'}
        runner = Mock(side_effect=execute)
        result = self.ledger.run('homecompute_shopping_add', ARGUMENTS, runner)
        self.assertEqual(result['outcome'], 'confirmed')
        restarted = Requests(self.path)
        self.assertTrue(restarted.run('homecompute_shopping_add', ARGUMENTS, runner)['replayed'])
        self.assertEqual(runner.call_count, 1)
        with self.assertRaises(ValueError):
            restarted.run('homecompute_shopping_add', {**ARGUMENTS, 'amount': 2}, runner)
        with closing(sqlite3.connect(self.path)) as db:
            states = [row[0] for row in db.execute('SELECT state FROM events ORDER BY sequence')]
            columns = [row[1] for row in db.execute('PRAGMA table_info(requests)')]
        self.assertEqual(states, ['prepared', 'sending', 'confirmed'])
        self.assertNotIn('arguments', columns)

    def test_post_send_timeout_and_interruption_are_uncertain_and_never_retried(self):
        def execute(before_write):
            before_write()
            raise TimeoutError('PRIVATE credentials must not appear')
        runner = Mock(side_effect=execute)
        result = self.ledger.run('homecompute_shopping_add', ARGUMENTS, runner)
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertFalse(self.ledger.status(REQUEST)['retry_allowed'])
        Requests(self.path).run('homecompute_shopping_add', ARGUMENTS, runner)
        self.assertEqual(runner.call_count, 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE requests SET state='sending',receipt=NULL")
        result = Requests(self.path).run('homecompute_shopping_add', ARGUMENTS, runner)
        self.assertEqual(result['outcome'], 'uncertain')
        self.assertEqual(runner.call_count, 1)

    def test_precondition_failure_does_not_claim_change_or_allow_new_payload_under_same_id(self):
        runner = Mock(side_effect=ValueError('PRIVATE stale item'))
        result = self.ledger.run('homecompute_shopping_add', ARGUMENTS, runner)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertIs(result['changed'], False)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertTrue(self.ledger.run('homecompute_shopping_add', ARGUMENTS, runner)['replayed'])
        self.assertEqual(runner.call_count, 1)

    def test_prepared_interrupted_before_network_can_safely_resume_same_request(self):
        def interrupted(_):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.ledger.run('homecompute_shopping_add', ARGUMENTS, interrupted)
        runner = Mock(return_value={'outcome': 'confirmed', 'changed': False})
        self.assertEqual(Requests(self.path).run('homecompute_shopping_add', ARGUMENTS, runner)['outcome'], 'confirmed')
        self.assertEqual(runner.call_count, 1)

    def test_private_paths_and_concurrent_writer_guard(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): Requests(self.path)
        self.path.chmod(0o600)
        alias = self.root / 'alias.sqlite3'; alias.symlink_to(self.path)
        with self.assertRaises(ValueError): Requests(alias)
        self.ledger.mutex.acquire()
        try:
            runner = Mock()
            with self.assertRaises(ValueError): self.ledger.run('homecompute_shopping_add', ARGUMENTS, runner)
            runner.assert_not_called()
        finally:
            self.ledger.mutex.release()

    def test_services_composition_preserves_existing_reads_and_uses_household_validation(self):
        key = self.root / 'tavily-key'; key.write_text('tvly-synthetic-key-never-live'); key.chmod(0o600)
        config = self.root / 'services.json'
        config.write_text(json.dumps({'schema_version': 1, 'tavily_token_file': str(key)})); config.chmod(0o600)
        facade = SimpleNamespace(tools=household.TOOLS, validate=household.validate,
                                 request=Mock(return_value={'outcome': 'confirmed', 'changed': False}))
        with patch.object(services, 'TOKEN_PATH', key):
            client = services.Services(config, facade)
        self.assertEqual(len(client.tools), len(services.TOOLS) + len(household.TOOLS))
        self.assertEqual(client.validate('homecompute_shopping_add', ARGUMENTS), ARGUMENTS)
        with self.assertRaises(ValueError): client.validate('homecompute_shopping_add', {**ARGUMENTS, 'amount': True})
        client.request('homecompute_shopping_add', ARGUMENTS)
        facade.request.assert_called_once_with('homecompute_shopping_add', ARGUMENTS)
        self.assertEqual(client.validate('homecompute_web_search', {'query': 'public'}), {'query': 'public'})

    def test_mcp_uses_household_typed_validator_for_write_arguments(self):
        sys.path.insert(0, str(ROOT / 'deploy/codex-worker'))
        from mcp_adapter import call_tool, RpcError
        facade = SimpleNamespace(tools=household.TOOLS, validate=household.validate,
                                 request=Mock(return_value={'outcome': 'confirmed', 'changed': False}))
        ledger = SimpleNamespace(services=facade)
        result = call_tool({'name': 'homecompute_shopping_add', 'arguments': ARGUMENTS}, ledger)
        self.assertFalse(result['isError'])
        facade.request.assert_called_once_with('homecompute_shopping_add', ARGUMENTS)
        with self.assertRaises(RpcError):
            call_tool({'name': 'homecompute_shopping_add', 'arguments': {**ARGUMENTS, 'amount': True}}, ledger)
        self.assertEqual(facade.request.call_count, 1)

    def test_private_household_configuration_never_accepts_an_arbitrary_key_path(self):
        token = self.root / 'ha-token'; token.write_text('synthetic-' + 'x' * 40); token.chmod(0o600)
        config = self.root / 'household.json'
        config.write_text(json.dumps({'schema_version': 1, 'ha_token_file': str(token)})); config.chmod(0o600)
        with patch.object(household, 'TOKEN_PATH', token):
            client = household.Household(config, self.root)
            result = client.request('homecompute_household_request_status', {'request_id': REQUEST})
            self.assertEqual(result['state'], 'not_found')
            self.assertNotIn('synthetic-', json.dumps(result))
            token.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'Private household configuration refused'):
                household.Household(config, self.root)
            token.chmod(0o600)
            config.write_text(json.dumps({'schema_version': 1, 'ha_token_file': '/tmp/key'}))
            with self.assertRaises(ValueError): household.Household(config, self.root)


if __name__ == '__main__':
    unittest.main()
