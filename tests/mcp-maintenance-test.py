#!/usr/bin/env python3
"""MCP version gates, immutable recipes, health checks, and one-attempt rollback."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
m = importlib.import_module('mcp_maintenance')
from infrastructure_update_policy import classify_version


def container(image='a'):
    return {'Id': 'c' * 64, 'Image': 'sha256:' + image * 64,
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {'Image': 'old-tag', 'Hostname': 'generated', 'Env': ['MODE=read-only'],
                       'Labels': {'com.docker.compose.config-hash': 'old', 'retained': 'yes'}},
            'HostConfig': {'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'Binds': ['/state:/data:rw']},
            'Mounts': [{'Type': 'bind', 'Source': '/state', 'Destination': '/data', 'RW': True}],
            'NetworkSettings': {'Networks': {'migration': {'IPAMConfig': {'IPv4Address': '172.28.201.3'},
                 'IPAddress': '172.28.201.3', 'IPPrefixLen': 24, 'Aliases': ['aula-mcp'], 'EndpointID': 'dynamic'}}}}


class PolicyTests(unittest.TestCase):
    def test_only_increasing_stable_same_major_versions_are_routine(self):
        for old, new, expected in [('1.6.2', '1.6.3', 'routine'), ('1.6.2', '1.7.0', 'routine'),
                ('1.6.2', '2.0.0', 'major'), ('0.5.3', '0.6.0', 'major'), ('0.5.3', '0.5.4', 'routine'),
                ('1.6.2', '1.6.2', 'unchanged'), ('1.6.2', '1.6.1', 'review'), ('1.6.2', '1.7.0-rc1', 'review')]:
            self.assertEqual(classify_version(old, new, 'semver')['classification'], expected)

    def test_recipe_only_replaces_the_pinned_git_commit_and_keeps_patch_checks(self):
        for component in m.COMPONENTS:
            text = (ROOT / 'deploy/automation' / m.selected(component)[1]).read_text()
            updated = m.recipe(text, 'b' * 40)
            self.assertEqual(updated.count('b' * 40), 1)
            self.assertEqual(updated, __import__('re').sub(r'(?<=git fetch --depth 1 origin )[a-f0-9]{40}', 'b' * 40, text))
            with self.assertRaises(ValueError):
                m.recipe(text.replace('--check', ''), 'b' * 40)
            with self.assertRaises(ValueError):
                m.recipe(text, 'main; touch /tmp/unsafe')

    def test_unknown_component_never_selects_an_upstream(self):
        with patch.object(m.urllib.request, 'urlopen') as request:
            for name in ('n8n', '--privileged', '../../aula-mcp', 'https://arbitrary.invalid/repo'):
                with self.assertRaises(ValueError):
                    m.upstream(name)
            request.assert_not_called()

    def test_stable_release_tag_is_resolved_once_and_version_read_from_that_commit(self):
        urls = []
        class Response:
            def __init__(self, url): self.url = url
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, size):
                return json.dumps({'sha': 'd' * 40} if '/commits/' in self.url else
                    {'tag_name': 'v1.7.0', 'draft': False, 'prerelease': False} if '/releases/' in self.url else {'version': '1.7.0'}).encode()
        def request(req, timeout):
            urls.append(req.full_url)
            return Response(req.full_url)
        with patch.object(m.urllib.request, 'urlopen', request):
            self.assertEqual(m.upstream('aula-mcp'), ('d' * 40, '1.7.0'))
        self.assertEqual(urls, ['https://api.github.com/repos/Casperjuel/aula-mcp/releases/latest',
            'https://api.github.com/repos/Casperjuel/aula-mcp/commits/v1.7.0',
            'https://raw.githubusercontent.com/Casperjuel/aula-mcp/' + 'd' * 40 + '/package.json'])

    def test_cli_is_finite_and_candidate_identity_cannot_be_a_path_or_option(self):
        for value in ('../candidate', 'A' * 32, 'a' * 31, '--help', 'a' * 32 + '/file'):
            with self.assertRaises(ValueError):
                m.identifier(value)
        with patch.object(sys, 'argv', ['helper', 'prepare', 'a' * 32]), self.assertRaises(SystemExit):
            m.main()

    def test_runtime_comparison_ignores_generated_identity_and_detects_security_or_mount_drift(self):
        old = container()
        new = copy.deepcopy(old)
        new['Config'].update(Image='new-image', Hostname='new-container-id')
        new['Config']['Labels']['com.docker.compose.config-hash'] = 'new'
        new['NetworkSettings']['Networks']['migration']['EndpointID'] = 'new'
        self.assertEqual(m.invariants(old), m.invariants(new))
        for mutate in [lambda row: row['HostConfig'].update(ReadonlyRootfs=False),
                       lambda row: row['Mounts'][0].update(Source='/wrong'),
                       lambda row: row['NetworkSettings']['Networks']['migration'].update(IPAddress='172.28.201.99'),
                       lambda row: row['Config'].update(Env=['MODE=write'])]:
            changed = copy.deepcopy(new)
            mutate(changed)
            self.assertNotEqual(m.invariants(old), m.invariants(changed))


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.state_path = self.directory / 'data'
        self.state_path.mkdir()
        self.candidate_id = 'd' * 32
        self.baseline = {'id': 'c' * 64, 'image': 'sha256:' + 'a' * 64,
                         'spec': m.invariants(container()), 'running': True, 'health': 'healthy'}
        self.value = {'component': 'aula-mcp', 'image': 'sha256:' + 'b' * 64, 'expires_at': 2000,
                      'baseline': self.baseline, 'state_path': str(self.state_path)}
        self.patches = [patch.object(m, 'trusted_path', side_effect=lambda p, *a, **k: Path(p)),
                        patch.object(m.os, 'geteuid', return_value=0),
                        patch.object(m, 'root_json', side_effect=lambda p, uid: json.loads(Path(p).read_text()))]
        for item in self.patches: item.start()
        self.helper = m.Maintenance(state=self.directory / 'candidates', clock=lambda: 1000)
        self.helper.candidate = lambda selected: (self.directory, self.value)
        self.helper.snapshot = lambda selected: copy.deepcopy(self.baseline)
        self.helper.inspect = lambda selected: container()
        self.calls = []
        self.helper.run = lambda argv, timeout=30: self.calls.append(argv) or b''

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temporary.cleanup()

    def test_failed_health_rolls_back_exact_image_once_and_retains_backup(self):
        self.helper.verify = lambda *_: (_ for _ in ()).throw(ValueError('bad candidate health'))
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            self.helper.apply(self.candidate_id)
        self.assertTrue((self.directory / 'attempt.json').exists())
        self.assertEqual(json.loads((self.directory / 'rollback.json').read_text())['image'], self.baseline['image'])
        self.assertEqual(os.stat(self.directory / 'state.tar').st_mode & 0o777, 0o600)
        updates = [argv for argv in self.calls if 'up' in argv]
        self.assertEqual(len(updates), 2)
        self.assertTrue(any('rollback.yaml' in arg for arg in updates[1]))
        self.assertTrue(all('--no-deps' in argv and '--no-build' in argv and argv[-1] == 'aula-mcp' for argv in updates))
        calls = list(self.calls)
        with self.assertRaises(FileExistsError):
            self.helper.apply(self.candidate_id)
        self.assertEqual(self.calls, calls)

    def test_attempt_marker_is_durable_before_first_service_effect(self):
        def command(argv, timeout=30):
            self.assertTrue((self.directory / 'attempt.json').exists())
            self.calls.append(argv)
            return b''
        self.helper.run = command
        self.helper.verify = lambda *_: {'verified': True}
        self.assertEqual(self.helper.apply(self.candidate_id), {'verified': True})
        self.assertTrue((self.directory / 'result.json').exists())
        self.assertFalse((self.directory / 'rollback.json').exists())

    def test_changed_baseline_or_expired_candidate_produces_no_effect_or_attempt(self):
        for cause in ('baseline', 'expired'):
            self.helper.snapshot = lambda _: dict(self.baseline, image='changed') if cause == 'baseline' else self.baseline
            self.value['expires_at'] = 999 if cause == 'expired' else 2000
            with self.assertRaisesRegex(ValueError, 'baseline'):
                self.helper.apply(self.candidate_id)
            self.assertFalse((self.directory / 'attempt.json').exists())
            self.assertEqual(self.calls, [])

    def test_verified_health_requires_n8n_protocol_probe_and_exact_runtime(self):
        self.helper.inspect = lambda _: container('b')
        self.assertTrue(self.helper.verify(self.candidate_id)['verified'])
        probe = self.calls[-1]
        self.assertEqual(probe[:5], [m.DOCKER, 'exec', 'homecompute-automation-n8n-1', 'node', '-e'])
        self.assertIn('tools/list', probe[5])
        self.assertNotIn('tools/call', probe[5])
        self.assertEqual(probe[-1], 'http://aula-mcp:7878/mcp')
        wrong = container('b')
        wrong['HostConfig']['ReadonlyRootfs'] = False
        self.helper.inspect = lambda _: wrong
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.helper.verify(self.candidate_id)

    def test_unhealthy_candidate_cannot_pass_verification(self):
        unhealthy = container('b')
        unhealthy['State']['Health']['Status'] = 'unhealthy'
        self.helper.inspect = lambda _: unhealthy
        with self.assertRaisesRegex(ValueError, 'health failed'):
            self.helper.verify(self.candidate_id, timeout=0)
        self.assertEqual(self.calls, [])

    def test_patch_or_build_error_is_not_accepted_and_output_is_bounded(self):
        for stdout, stderr, code in [(b'', b'patch failed', 1), (b'x' * (m.MAX_OUTPUT + 1), b'', 0)]:
            self.helper.runner = lambda argv, **kwargs: subprocess.CompletedProcess(argv, code, stdout, stderr)
            with self.assertRaisesRegex(ValueError, 'command failed'):
                m.Maintenance.run(self.helper, [m.DOCKER, 'build', '-f', 'reviewed.Dockerfile', '.'])

    def test_candidate_binds_environment_and_compose_files_before_effects(self):
        directory = self.helper.state / self.candidate_id
        directory.mkdir()
        compose = directory / 'production.yaml'
        compose.write_text('reviewed configuration')
        environment = self.directory / 'automation.env'
        environment.write_text('FIXED=value')
        value = {'id': self.candidate_id, 'component': 'aula-mcp', 'image': self.value['image'],
                 'env_sha256': hashlib.sha256(environment.read_bytes()).hexdigest(),
                 'files': {'production.yaml': hashlib.sha256(compose.read_bytes()).hexdigest()}}
        (directory / 'candidate.json').write_text(json.dumps(value))
        with patch.object(m, 'ENV_FILE', environment):
            self.assertEqual(m.Maintenance.candidate(self.helper, self.candidate_id)[1], value)
            environment.write_text('FIXED=changed')
            with self.assertRaisesRegex(ValueError, 'environment changed'):
                m.Maintenance.candidate(self.helper, self.candidate_id)
            environment.write_text('FIXED=value')
            compose.write_text('changed configuration')
            with self.assertRaisesRegex(ValueError, 'configuration changed'):
                m.Maintenance.candidate(self.helper, self.candidate_id)

    def test_compose_hash_preflight_has_fixed_service_and_rejects_unknown_output(self):
        self.helper.run = lambda argv, timeout=30: ('aula-mcp ' + 'e' * 64 + '\n').encode()
        self.assertEqual(self.helper.config_hash(self.directory, 'aula-mcp'), 'e' * 64)
        self.helper.run = lambda argv, timeout=30: b'n8n wrong-hash\n'
        with self.assertRaisesRegex(ValueError, 'configuration hash'):
            self.helper.config_hash(self.directory, 'aula-mcp')

    def test_manual_docker_update_drift_is_rejected_even_with_unchanged_compose_labels(self):
        service = {'cpus': 1.0, 'mem_limit': 536870912, 'pids_limit': 128, 'restart': 'unless-stopped'}
        self.helper.run = lambda argv, timeout=30: json.dumps({'services': {'aula-mcp': service}}).encode()
        baseline = container()
        baseline['HostConfig'].update(NanoCpus=1_000_000_000, Memory=536870912,
            MemorySwap=1073741824, PidsLimit=128, RestartPolicy={'Name': 'unless-stopped', 'MaximumRetryCount': 0})
        self.helper.validate_resources(self.directory, 'aula-mcp', baseline)
        for key, value in [('Memory', 1073741824), ('NanoCpus', 500000000), ('PidsLimit', 0),
                ('RestartPolicy', {'Name': 'always', 'MaximumRetryCount': 0}), ('CpuShares', 512), ('CpusetMems', '0')]:
            changed = copy.deepcopy(baseline)
            changed['HostConfig'][key] = value
            with self.assertRaisesRegex(ValueError, 'resources or restart policy'):
                self.helper.validate_resources(self.directory, 'aula-mcp', changed)


if __name__ == '__main__':
    unittest.main()
