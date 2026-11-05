#!/usr/bin/env python3
"""Exercise subnet migration, refusal and recovery without a live Docker daemon."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('whisper_network_lifecycle', ROOT / 'scripts/setup-whisper-stt.py')
LIFECYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIFECYCLE)
NAME = 'whisper-cpp-gb10-cuda_candidate'
MODEL = 'c' * 64
REVISION = 'd' * 40


class NetworkMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment = {**os.environ, 'WHISPER_CPP_HOME_COMPUTE_REVISION': REVISION,
                       'WHISPER_CPP_IMAGE_ID': LIFECYCLE.CPP_QUALIFIED_IMAGE_ID,
                       'WHISPER_CPP_CUDA_IMAGE': LIFECYCLE.CPP_QUALIFIED_IMAGE_NAME,
                       'WHISPER_CPP_CUDA_PATCH_VERSION': LIFECYCLE.CPP_QUALIFIED_PATCH_VERSION,
                       'WHISPER_CPP_CUDA_PATCHER_SHA256': LIFECYCLE.CPP_QUALIFIED_PATCHER_SHA256,
                       'WHISPER_CPP_MODEL_FILE': '/synthetic/model',
                       'WHISPER_CPP_API_KEY_FILE': '/synthetic/key'}
        result = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'deploy/whisper-cpp-stt/compose-cuda.yaml'),
                                 '--profile', 'cuda-candidate', 'config', '--format', 'json'],
                                env=environment, capture_output=True, text=True, check=True)
        cls.rendered = json.loads(result.stdout)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        self.original = {'Name': NAME, 'Id': 'a' * 64, 'Driver': 'bridge', 'Internal': True,
                         'EnableIPv6': False, 'Attachable': False, 'Options': {},
                         'IPAM': {'Driver': 'default', 'Config': [{'Subnet': '192.168.0.0/20', 'Gateway': '192.168.0.1'}]},
                         'Labels': {'com.docker.compose.project': 'whisper-cpp-gb10-cuda',
                                    'com.docker.compose.network': 'candidate', 'com.docker.compose.config-hash': 'old'},
                         'Containers': {MODEL: {'Name': 'gb10-whisper-cpp-primary'}}}
        self.network = copy.deepcopy(self.original)
        self.inference = {'NetworkID': 'e' * 64, 'EndpointID': 'f' * 64, 'IPAddress': '172.30.0.2',
                          'Aliases': ['whisper-primary', 'whisper-cpp-recording']}
        self.endpoint = {'NetworkID': self.network['Id'], 'Aliases': ['whisper-cpp-cuda', 'gb10-whisper-cpp-primary']}
        self.tx = {'state': 'promoted', 'promotion': True, 'candidate_image_id': LIFECYCLE.CPP_QUALIFIED_IMAGE_ID,
                   'relay_candidate_id': 'relay', 'relay_candidate_image_id': 'relay-image'}
        (self.state / 'cuda-candidate.json').write_text(json.dumps(self.tx))
        self.routes = [{'dst': '192.168.10.0/24', 'dev': 'lan'}]
        self.commands = []
        self.other_networks = []
        self.fail_connect = False
        self.fail_auth = False
        self.image = LIFECYCLE.CPP_QUALIFIED_IMAGE_ID
        for name, value in [('STATE', self.state), ('protected', lambda *a: None), ('guards', lambda: None),
                            ('inspect', self.inspect), ('run', self.run_command),
                            ('compose_cuda', lambda *a, **kw: SimpleNamespace(stdout=json.dumps(self.rendered)))]:
            mock = patch.object(LIFECYCLE, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def inspect(self, name):
        if name == 'gb10-whisper-cpp-primary':
            networks = {'whisper-large-v3-stt_inference': copy.deepcopy(self.inference)}
            if self.endpoint is not None:
                networks[NAME] = copy.deepcopy(self.endpoint)
            return {'Id': MODEL, 'Image': self.image, 'Config': {'Labels': {
                'com.docker.compose.project': 'whisper-cpp-gb10-cuda',
                'com.docker.compose.service': 'whisper-cpp-cuda'}},
                'State': {'Running': True, 'Health': {'Status': 'healthy'}},
                'NetworkSettings': {'Networks': networks}}
        return {'Id': 'relay', 'Image': 'relay-image', 'State': {'Running': True, 'Health': {'Status': 'healthy'}}}

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        output = ''
        if command[:3] == ['docker', 'network', 'ls']:
            listed = ([self.network] if self.network else [])
            if '--filter' not in command:
                listed += self.other_networks
            output = '\n'.join(network['Id'] for network in listed)
        elif command[:3] == ['docker', 'network', 'inspect']:
            output = json.dumps([next(network for network in ([self.network] if self.network else []) + self.other_networks
                                      if command[3] in {network['Id'], network['Name']})])
        elif command[0] == 'ip':
            output = json.dumps(self.routes)
        elif command[:3] == ['docker', 'network', 'disconnect']:
            self.network['Containers'] = {}
            self.endpoint = None
        elif command[:3] == ['docker', 'network', 'rm']:
            assert not self.network['Containers']
            self.network = None
        elif command[:3] == ['docker', 'network', 'create']:
            self.network = copy.deepcopy(self.original)
            self.network.update(Id='b' * 64, Containers={})
            self.network['IPAM']['Config'] = [{'Subnet': command[command.index('--subnet') + 1],
                                              'Gateway': command[command.index('--gateway') + 1]}]
            self.network['Labels'] = dict(value.split('=', 1) for index, value in enumerate(command)
                                          if index and command[index - 1] == '--label')
        elif command[:3] == ['docker', 'network', 'connect']:
            if self.fail_connect:
                self.fail_connect = False
                raise RuntimeError('simulated connect failure')
            self.network['Containers'] = {MODEL: {'Name': 'gb10-whisper-cpp-primary'}}
            self.endpoint = {'NetworkID': self.network['Id'], 'Aliases': [value for index, value in enumerate(command)
                                                                 if index and command[index - 1] == '--alias']}
        elif command[:2] == ['docker', 'exec'] and self.fail_auth:
            raise RuntimeError('simulated authentication failure')
        return SimpleNamespace(stdout=output, returncode=0, stderr='')

    def migrate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return LIFECYCLE.reconcile_cuda_network(REVISION)

    def test_migration_uses_declared_subnet_and_preserves_model_and_inference(self):
        before = copy.deepcopy(self.inference)
        self.migrate()
        self.assertEqual(self.network['IPAM']['Config'], [{'Subnet': '10.253.73.0/28', 'Gateway': '10.253.73.1'}])
        self.assertEqual(self.inference, before)
        self.assertEqual(set(self.network['Containers']), {MODEL})
        self.assertNotIn('com.docker.compose.config-hash', self.network['Labels'])
        self.assertTrue(all(command[1] == 'network' or command[1] == 'exec'
                            for command in self.commands if command[0] == 'docker'))
        self.assertEqual(json.loads((self.state / 'cuda-candidate.json').read_text())['network_reconciliation']['state'], 'completed')

    def test_repeat_does_not_recreate_network(self):
        self.migrate()
        self.commands.clear()
        self.migrate()
        self.assertFalse(any(command[:3] in (['docker', 'network', 'disconnect'], ['docker', 'network', 'create'])
                             for command in self.commands))

    def test_repeat_auth_failure_does_not_restore_the_colliding_route(self):
        self.migrate()
        self.fail_auth = True
        self.commands.clear()
        with self.assertRaises(RuntimeError):
            self.migrate()
        self.assertEqual(self.network['IPAM']['Config'][0]['Subnet'], '10.253.73.0/28')
        self.assertFalse(any(command[:3] == ['docker', 'network', 'disconnect'] for command in self.commands))

    def test_changed_internal_aliases_refuse_before_mutation(self):
        self.endpoint['Aliases'] = ['unrelated']
        with self.assertRaisesRegex(ValueError, 'endpoint ownership'):
            self.migrate()
        self.assertEqual(self.network, self.original)

    def test_host_route_overlap_refuses_before_mutation(self):
        self.routes.append({'dst': '10.253.73.0/24', 'dev': 'lan'})
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.migrate()
        self.assertEqual(self.network, self.original)

    def test_unexpected_container_refuses_before_mutation(self):
        self.network['Containers']['other'] = {'Name': 'unrelated'}
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertIsNotNone(self.endpoint)

    def test_unqualified_image_refuses_before_mutation(self):
        self.image = 'unqualified'
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertEqual(self.network, self.original)

    def test_another_docker_network_overlap_refuses_before_mutation(self):
        other = copy.deepcopy(self.original)
        other.update(Name='unrelated', Id='9' * 64)
        other['IPAM']['Config'] = [{'Subnet': '10.253.73.0/24', 'Gateway': '10.253.73.1'}]
        self.other_networks.append(other)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.migrate()
        self.assertEqual(self.network, self.original)

    def test_unexpected_production_aliases_refuse_before_mutation(self):
        self.inference['Aliases'] = ['unexpected']
        with self.assertRaisesRegex(ValueError, 'inference endpoint'):
            self.migrate()
        self.assertEqual(self.network, self.original)

    def test_network_ownership_drift_refuses_before_mutation(self):
        self.network['Labels']['com.docker.compose.project'] = 'unrelated'
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.migrate()
        self.assertIsNotNone(self.endpoint)

    def test_reconnect_failure_restores_original_subnet_and_aliases(self):
        self.fail_connect = True
        with self.assertRaises(RuntimeError):
            self.migrate()
        self.assertEqual(self.network['IPAM'], self.original['IPAM'])
        self.assertEqual(set(self.network['Containers']), {MODEL})
        self.assertEqual(self.endpoint['Aliases'], ['whisper-cpp-cuda', 'gb10-whisper-cpp-primary'])

    def test_interrupted_migration_resumes_after_network_removal(self):
        self.migrate()
        tx = json.loads((self.state / 'cuda-candidate.json').read_text())
        tx['network_reconciliation']['state'] = 'pending'
        (self.state / 'cuda-candidate.json').write_text(json.dumps(tx))
        self.network = self.endpoint = None
        self.migrate()
        self.assertEqual(self.network['IPAM']['Config'][0]['Subnet'], '10.253.73.0/28')

    def test_interrupted_migration_resumes_after_endpoint_disconnect(self):
        self.migrate()
        tx = json.loads((self.state / 'cuda-candidate.json').read_text())
        tx['network_reconciliation']['state'] = 'pending'
        (self.state / 'cuda-candidate.json').write_text(json.dumps(tx))
        self.network = copy.deepcopy(self.original)
        self.network['Containers'] = {}
        self.endpoint = None
        self.migrate()
        self.assertEqual(self.network['IPAM']['Config'][0]['Subnet'], '10.253.73.0/28')


if __name__ == '__main__':
    unittest.main()
