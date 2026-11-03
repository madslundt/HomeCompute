"""Agent boundary integration: opaque plan IDs cannot grant root approval."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/codex-worker'))
sys.path.insert(0, str(ROOT / 'scripts'))
from mcp_adapter import call_tool, RpcError, tool_inventory
from openclaw_capabilities import Maintenance

spec = importlib.util.spec_from_file_location('spark_dispatcher', ROOT / 'scripts/infrastructure-maintenance-spark.py')
spark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spark)


class IntegrationTest(unittest.TestCase):
    def test_mcp_accepts_only_opaque_ids_for_run(self):
        calls = []
        service = SimpleNamespace(request=lambda op, args: calls.append((op, args)) or {'plan': {'status': 'registered', 'approval_required': True}})
        ledger = SimpleNamespace(maintenance=service)
        result = call_tool({'name': 'homecompute_maintenance_run', 'arguments': {'plan_id': 'a' * 32}}, ledger)
        self.assertEqual(calls, [('run', {'plan_id': 'a' * 32})])
        self.assertIn('registered', json.dumps(result))
        for fields in ({'plan_id': 'a' * 32, 'approve': True}, {'plan_id': 'a' * 32, 'argv': ['/bin/sh']},
                       {'plan_id': '../root'}, {'plan_id': 'a' * 32, 'versions': ['9.0.0']}):
            with self.assertRaises(RpcError):
                call_tool({'name': 'homecompute_maintenance_run', 'arguments': fields}, ledger)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(RpcError):
            call_tool({'name': 'homecompute_maintenance_approve', 'arguments': {'plan_id': 'a' * 32}}, ledger)

    def test_discovery_requires_configured_maintenance(self):
        self.assertFalse(any('maintenance' in t['name'] for t in tool_inventory(SimpleNamespace())))
        self.assertEqual([t['name'] for t in tool_inventory(SimpleNamespace(maintenance=object())) if 'maintenance' in t['name']],
                         ['homecompute_maintenance_prepare', 'homecompute_maintenance_preparation', 'homecompute_maintenance_list', 'homecompute_maintenance_status', 'homecompute_maintenance_run'])

    def test_local_client_refuses_approval_before_opening_socket(self):
        with patch('openclaw_capabilities.socket.socket', side_effect=AssertionError('no socket call')):
            for op, arguments in [('approve', {'plan_id': 'a' * 32}), ('register', {}),
                                  ('review', {'plan_id': 'a' * 32}), ('reconcile', {'plan_id': 'a' * 32}),
                                  ('run', {'plan_id': 'a' * 32, 'command': 'apt upgrade'}), ('status', {'plan_id': '../root'})]:
                with self.assertRaises(ValueError):
                    Maintenance().request(op, arguments)

    def test_spark_key_cannot_register_or_execute_arbitrary_commands(self):
        prefix = spark.COMMAND
        self.assertEqual(spark.arguments(prefix + ' run ' + 'a' * 32), ('run', 'a' * 32))
        self.assertEqual(spark.arguments(prefix + ' approve ' + 'a' * 32 + ' ' + 'b' * 64), ('approve', 'a' * 32, 'b' * 64))
        for text in [prefix + ' approve ' + 'a' * 32, prefix + ' register /tmp/plan',
                     prefix + ' run ' + 'a' * 32 + '; id', '/bin/sh', prefix + ' run ../../root',
                     prefix + ' run ' + 'a' * 32 + '\n']:
            with self.assertRaises(ValueError): spark.arguments(text)

    def test_restart_whisper_selects_only_the_journaled_production_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cuda = state / 'cuda-candidate.json'
            runtime = state / 'runtime-candidate.json'
            cuda.write_text(json.dumps({'state': 'rolled-back'}))
            real_lstat = Path.lstat

            def root_owned(path):
                info = real_lstat(path)
                if path in {cuda, runtime}:
                    return SimpleNamespace(st_uid=0, st_mode=info.st_mode)
                return info

            with patch.object(spark.Path, 'lstat', autospec=True, side_effect=root_owned):
                self.assertEqual(spark.whisper_restart_target(state), 'gb10-whisper-primary')
                cuda.write_text(json.dumps({'state': 'promoted', 'promotion': True,
                                            'candidate_exposed': True,
                                            'candidate_image_id': spark.CPP_PROMOTED_IMAGE}))
                self.assertEqual(spark.whisper_restart_target(state), spark.CPP_WHISPER)
                for transaction in (
                    {'state': 'promoted', 'promotion': True, 'candidate_exposed': True,
                     'candidate_image_id': 'sha256:' + 'a' * 64},
                    {'state': 'candidate-running'},
                ):
                    cuda.write_text(json.dumps(transaction))
                    with self.assertRaises(ValueError):
                        spark.whisper_restart_target(state)
                cuda.write_text(json.dumps({'state': 'rolled-back'}))
                runtime.write_text(json.dumps({'state': 'candidate-running'}))
                with self.assertRaises(ValueError):
                    spark.whisper_restart_target(state)


if __name__ == '__main__':
    unittest.main()
