#!/usr/bin/env python3
"""Readiness follows deployed consumers and fails closed without exposing content."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('platform_health', ROOT / 'scripts/platform-health.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class PlatformHealthTest(unittest.TestCase):
    def test_nas_requires_fresh_successful_evidence_and_enabled_schedules(self):
        now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            config = state / 'nas-config.json'
            config.write_text('{}')
            def record(name, value):
                path = state / name
                path.write_text(json.dumps(value)); path.chmod(0o600)
            record('deployment.json', {'state': 'scheduled'})
            record('last-backup.json', {'time': now.isoformat(), 'snapshot': 'a' * 64})
            record('last-check.json', {'time': now.isoformat(), 'passed': True})
            permit = state / 'backup-authorized'; permit.write_text('authorized'); permit.chmod(0o600)
            def units(*, failed=False, enabled=True):
                return '\n\n'.join('\n'.join([f'Id=homecompute-nas-recovery-{operation}.{kind}',
                    'ActiveState=' + ('active' if kind == 'timer' and enabled else 'inactive'),
                    'Result=' + ('exit-code' if failed and kind == 'service' else 'success')])
                    for operation in ('backup', 'check') for kind in ('timer', 'service'))
            with patch.object(health, 'NAS_STATE', state), patch.object(health, 'NAS_CONFIG', config), \
                    patch.object(health.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, units(), '')) as call:
                self.assertEqual(health.nas_readiness(now), {'nas_backup_ready': True, 'nas_check_ready': True})
                record('last-backup.json', {'time': (now - timedelta(hours=37)).isoformat(), 'snapshot': 'a' * 64})
                self.assertFalse(health.nas_readiness(now)['nas_backup_ready'])
                self.assertTrue(health.nas_readiness(now)['nas_check_ready'])
                record('last-backup.json', {'time': (now + timedelta(minutes=3)).isoformat(), 'snapshot': 'a' * 64})
                self.assertFalse(health.nas_readiness(now)['nas_backup_ready'])
                record('last-backup.json', {'time': now.isoformat(), 'snapshot': 'a' * 64})
                call.return_value = subprocess.CompletedProcess([], 0, units(failed=True), '')
                self.assertFalse(any(health.nas_readiness(now).values()))
                call.return_value = subprocess.CompletedProcess([], 0, units(enabled=False), '')
                self.assertFalse(any(health.nas_readiness(now).values()))
                call.return_value = subprocess.CompletedProcess([], 0, units(), '')
                (state / 'last-check.json').chmod(0o644)
                self.assertFalse(any(health.nas_readiness(now).values()))

    def test_nas_optional_without_installation_but_partial_installation_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with patch.object(health, 'NAS_STATE', state), patch.object(health, 'NAS_CONFIG', state / 'config.json'):
                self.assertEqual(health.nas_readiness(), {})
                (state / 'config.json').write_text('{}')
                self.assertEqual(health.nas_readiness(), {'nas_backup_ready': False, 'nas_check_ready': False})

    def test_inventory_covers_photo_chat_cpu_speech_and_callback_failures(self):
        registry = json.loads((ROOT / 'config/system-monitoring.json').read_text())
        core = next(system for system in registry['systems'] if system['id'] == 'home-core')
        containers = {row['name'] for row in core['containers']}
        self.assertTrue({'homecompute-immich-immich-server-1', 'homecompute-mattermost-mattermost-1',
                         'home-core-embeddings', 'home-core-moss-tts', 'home-core-piper-tts'} <= containers)
        units = {row['name'] for row in core['units']}
        self.assertTrue({'ttlock-funnel.service', 'homecompute-compute-ssh-tunnel.service',
                         'homecompute-codex-vm.service', 'homecompute-service-recovery.service'} <= units)

    def test_probes_actual_upstreams_from_existing_consumer(self):
        states = {key: True for key in health.UPSTREAM_KEYS}
        with patch.object(health.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(states), '')) as run:
            self.assertEqual(health.upstream_readiness(), states)
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ['docker', 'exec', '-i', 'homecompute-control-plane-litellm-1', 'python3'])
        self.assertIn('COMPUTE_AUTOMATION_CANDIDATE_BASE_URL', run.call_args.kwargs['input'])
        self.assertNotIn('10.77.10.10', run.call_args.kwargs['input'])

    def test_unknown_fields_nonbooleans_and_content_never_become_metrics(self):
        for value in [{'text': True}, {**dict.fromkeys(health.UPSTREAM_KEYS, True), 'private': 'SECRET'},
                      dict.fromkeys(health.UPSTREAM_KEYS, 'SECRET'), []]:
            with self.subTest(value=value), patch.object(health.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, json.dumps(value), '')):
                self.assertEqual(health.upstream_readiness(), dict.fromkeys(health.UPSTREAM_KEYS, False))

    def test_unavailable_docker_times_out_closed(self):
        with patch.object(health.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 9)):
            self.assertFalse(any(health.upstream_readiness().values()))

    def test_full_platform_is_not_ready_when_one_dependency_fails(self):
        with patch.object(health, 'http_ready', return_value=True), patch.object(health, 'wyoming_ready', return_value=True), \
                patch.object(health, 'upstream_readiness', return_value={**dict.fromkeys(health.UPSTREAM_KEYS, True), 'text': False}):
            value = health.collect()
        self.assertFalse(value['platform_ready'])
        self.assertFalse(value['text_ready'])
        self.assertTrue(value['embedding_ready'])

    def test_stale_optional_vision_profile_is_not_a_required_service(self):
        with patch.object(health, 'http_ready', return_value=True), patch.object(health, 'wyoming_ready', return_value=True), \
                patch.object(health, 'upstream_readiness', return_value=dict.fromkeys(health.UPSTREAM_KEYS, True)):
            value = health.collect()
        self.assertTrue(value['platform_ready'])
        self.assertNotIn('vision_ready', value)

    def test_wyoming_requires_bounded_info_response(self):
        for response, expected in [(b'{"type":"info"}\n', True), (b'{"type":"error"}\n', False),
                                   (b'not-json\n', False), (b'x' * 65537 + b'\n', False)]:
            with self.subTest(expected=expected), socket.socket() as server:
                server.bind(('127.0.0.1', 0)); server.listen()
                def serve():
                    with server.accept()[0] as connection:
                        self.assertIn(b'describe', connection.recv(1024))
                        connection.sendall(response)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    task = pool.submit(serve)
                    self.assertEqual(health.wyoming_ready(*server.getsockname()), expected)
                    task.result()

    def test_atomic_metrics_preserve_legacy_names_and_report_failure(self):
        value = {key + '_ready': True for key in health.UPSTREAM_KEYS}
        value.update(edge_process_healthy=True, n8n_ready=True, n8n_edge_ready=True,
                     wyoming_stt_ready=True, wyoming_tts_ready=True, platform_ready=False)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            health.publish(value, directory)
            text = (directory / 'homecompute-health.prom').read_text()
            self.assertIn('homecompute_platform_ready 0\n', text)
            self.assertIn('homecompute_gx10_model_ready 1\n', text)
            self.assertEqual(len(list(directory.iterdir())), 1)

    def test_main_returns_nonzero_after_publishing_failed_readiness(self):
        value = {'platform_ready': False}
        with patch.object(health, 'collect', return_value=value), patch.object(health, 'publish') as publish:
            self.assertEqual(health.main(), 1)
            publish.assert_called_once_with(value)


if __name__ == '__main__':
    unittest.main()
