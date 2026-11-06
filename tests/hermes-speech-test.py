"""Protect source settings, exact speech grants, bilingual requests and boot recovery."""
import copy
import http.server
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


setup = load('speech_setup', 'scripts/setup-hermes-speech.py')
payload = load('speech_config', 'deploy/hermes/speech/configure.py')
client = load('speech_client', 'deploy/hermes/speech/client.py')
recovery = load('speech_recovery', 'scripts/recover-hermes-dashboard.py')
bridge = load('speech_bridge', 'scripts/setup-hermes-speech-bridge.py')
incident = load('speech_incident', 'scripts/recover-hermes-webapp-port.py')
native = load('speech_native', 'scripts/recover-hermes-native-state.py')


class NativeRecoveryTest(unittest.TestCase):
    @staticmethod
    def encode_varint(value):
        out = bytearray()
        while value > 127:
            out.append((value & 127) | 128); value >>= 7
        return bytes(out) + bytes([value])

    def item(self, field, value):
        if isinstance(value, int):
            return self.encode_varint(field << 3) + self.encode_varint(value)
        return self.encode_varint((field << 3) | 2) + self.encode_varint(len(value)) + value

    def payload(self, phase=3, reason=b'MainProcessExited', container=None):
        metadata = self.item(1, native.ID.encode()) + self.item(2, native.NAME.encode())
        condition = b''.join(self.item(n, value) for n, value in enumerate([
            b'Ready', b'False', reason, b'Canonical main process exited'], 1))
        runtime = b''.join([
            self.item(1, ('openshell-default--' + native.NAME + '-' + native.ID).encode()),
            self.item(2, container or native.CONTAINER.encode()), self.item(5, condition),
            self.item(6, phase), self.item(7, 8), self.item(8, b'unchanged-generation')])
        return self.item(1, metadata) + self.item(2, b'opaque-policy-and-credentials') + self.item(3, runtime)

    def test_changes_exactly_one_phase_byte_and_preserves_identity_policy_and_condition(self):
        before = self.payload(); after = native.candidate(before)
        self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)
        self.assertEqual(after, self.payload(phase=7))
        self.assertEqual(native.candidate(after), after)

    def test_refuses_ready_foreign_container_different_failure_and_duplicate_runtime(self):
        for raw in [self.payload(phase=1), self.payload(reason=b'PolicyRejected'),
                    self.payload(container=b'foreign'), self.payload() + self.item(3, b'')]:
            with self.subTest(raw=raw[-20:]):
                with self.assertRaises(ValueError): native.candidate(raw)


class PolicyTest(unittest.TestCase):
    def test_bridge_plan_accepts_selected_source_and_refuses_unknown_installed_code(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'adapter.py'
            target.write_bytes((ROOT / 'scripts/openclaw-communication.py').read_bytes())
            with patch.object(bridge, 'TARGET', target):
                selected = bridge.plan('a' * 40)
                self.assertEqual(selected['before'], selected['after'])
                target.write_text('unknown private workaround')
                with self.assertRaises(ValueError): bridge.plan('a' * 40)

    def setUp(self):
        self.before = {'network_policies': {'other': {'preserve': True},
            'mcp_bridge_homecompute': {'endpoints': [{'host': 'homecompute-capabilities.home.arpa',
                'port': 19445, 'allowed_ips': ['10.77.20.1'],
                'credential_binding': {'provider': 'hermes-personal-v23-mcp-homecompute'}}]}}}

    def test_two_exact_audio_paths_reuse_only_the_owner_credential(self):
        original = copy.deepcopy(self.before)
        after = setup.policy_candidate(self.before, 'hermes-personal-v23')
        self.assertEqual(self.before, original)
        self.assertEqual(after['network_policies']['other'], original['network_policies']['other'])
        self.assertEqual(setup.policy_candidate(after, 'hermes-personal-v23'), after)
        for key, path in [('homecompute_speech', '/v1/audio/speech'),
                          ('homecompute_transcription', '/v1/audio/transcriptions')]:
            grant = after['network_policies'][key]
            self.assertEqual(grant['binaries'], [{'path': '/opt/hermes/python-runtime/bin/python3.14'}])
            endpoint = grant['endpoints'][0]
            self.assertEqual(endpoint['rules'], [{'allow': {'method': 'POST', 'path': path}}])
            self.assertEqual(endpoint['credential_binding'], {'provider': 'hermes-personal-v23-mcp-homecompute'})

    def test_foreign_endpoint_or_existing_broad_grant_is_preserved_and_refused(self):
        self.before['network_policies']['mcp_bridge_homecompute']['endpoints'][0]['allowed_ips'] = ['1.2.3.4']
        with self.assertRaises(ValueError): setup.policy_candidate(self.before, 'hermes-personal-v23')
        self.setUp()
        self.before['network_policies']['homecompute_speech'] = {'arbitrary': 'grant'}
        with self.assertRaises(ValueError): setup.policy_candidate(self.before, 'hermes-personal-v23')


class ConfigTest(unittest.TestCase):
    def test_privacy_migration_accepts_only_verified_source_or_no_thanks_choice(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML is exercised on the qualified Linux guest')
        original = payload.encode({'model': {'default': 'automation-moe'},
                                   'dashboard': {'basic_auth': {'username': 'synthetic'}}})
        before = payload.candidate(yaml.safe_load(original))
        before.pop('telemetry')
        record = {'config_before': payload.sha(original), 'config_after': payload.sha(payload.encode(before))}
        selected = payload.candidate(before)
        for current in (before, selected):
            self.assertEqual(yaml.safe_load(payload.privacy_upgrade(payload.encode(current), record, original)), selected)
        selected['model']['default'] = 'unknown'
        with self.assertRaises(ValueError): payload.privacy_upgrade(payload.encode(selected), record, original)
        record['config_after'] = 'unknown'
        with self.assertRaises(ValueError): payload.privacy_upgrade(payload.encode(before), record, original)

    def test_incident_candidate_changes_only_reserved_port_and_stale_mcp_handle(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML is exercised on the qualified Linux guest')
        before = payload.candidate({'model': {'default': 'automation-moe'},
            'dashboard': {'basic_auth': {'username': 'synthetic'}},
            'mcp_servers': {'homecompute': {'headers': {'Authorization': 'Bearer ${HOMECOMPUTE_HERMES_MCP_TOKEN}'}}}})
        before['webapp']['port'] = 8646
        # The historical incident predates the source-owned metrics opt-out.
        before.pop('telemetry')
        before['mcp_servers']['homecompute']['headers']['Authorization'] = 'Bearer openshell:resolve:env:HOMECOMPUTE_HERMES_MCP_TOKEN:old'
        after = yaml.safe_load(incident.candidate(yaml.safe_dump(before).encode()))
        expected = payload.candidate(before); expected.pop('telemetry')
        self.assertEqual(after, expected)
        before['tts']['provider'] = 'unknown'
        with self.assertRaises(ValueError): incident.candidate(yaml.safe_dump(before).encode())

    def test_client_endpoint_and_voices_match_the_canonical_pin(self):
        pin = json.loads((ROOT / 'config/hermes-speech.json').read_bytes())
        self.assertEqual(client.BASE, pin['base_url'])
        self.assertEqual(client.VOICES, pin['voices'])
        self.assertEqual(pin['transcription_model'], 'whisper')

    def test_speech_preserves_auth_inference_memory_plugins_and_household_tools(self):
        before = {'model': {'api_key': '${COMPATIBLE_API_KEY}'}, 'memory': {'enabled': True},
                  'plugins': {'enabled': ['omh', 'nemoclaw']}, 'dashboard': {'private': 'synthetic'},
                  'mcp_servers': {'homecompute': {'url': 'synthetic'}}, 'voice': {'record_key': 'ctrl+b'}}
        after = payload.candidate(before)
        for key in ('model', 'memory', 'plugins', 'dashboard', 'mcp_servers'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['voice']['record_key'], 'ctrl+b')
        self.assertTrue(after['voice']['auto_tts'])
        self.assertFalse(after['voice']['client_direct'])
        self.assertEqual(after['voice']['voice_chat_mode'], 'chained')
        self.assertEqual(after['tts']['providers']['homecompute-omnivoice']['output_format'], 'wav')
        self.assertEqual(payload.candidate(after), after)
        self.assertEqual(after['webapp']['port'], 18796)
        self.assertNotEqual(after['webapp']['port'], 8646)

    def test_mcp_credential_is_resolved_afresh_after_restart(self):
        before = {'mcp_servers': {'homecompute': {'headers': {
            'Authorization': 'Bearer openshell:resolve:env:HOMECOMPUTE_HERMES_MCP_TOKEN:old-scope'}}}}
        after = payload.candidate(before)
        self.assertEqual(after['mcp_servers']['homecompute']['headers']['Authorization'],
                         'Bearer ${HOMECOMPUTE_HERMES_MCP_TOKEN}')


class ClientTest(unittest.TestCase):
    def test_both_languages_use_matching_alias_voice_and_wav(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(b'\0\1' * 2400)
        with tempfile.TemporaryDirectory() as directory:
            text = Path(directory) / 'input.txt'; output = Path(directory) / 'reply.wav'
            for language, phrase, voice, model in [('da', 'Hej, dette er en prøve.', 'danish-default', 'tts-danish'),
                                                   ('en', 'Hello, this is a test.', 'english-default', 'tts-english')]:
                text.write_text(phrase)
                with patch.object(client, 'language', return_value=language), patch.object(client, 'post', return_value=buffer.getvalue()) as post:
                    client.speak(text, output)
                    path, body, kind = post.call_args.args
                    self.assertEqual(path, '/audio/speech')
                    self.assertEqual(json.loads(body), {'input': phrase, 'model': model, 'voice': voice, 'response_format': 'wav'})
                    self.assertEqual(kind, 'application/json')
                    self.assertEqual(output.read_bytes(), buffer.getvalue())

    def test_invalid_or_oversized_requests_never_reach_the_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(client, 'post') as post:
            text = Path(directory) / 'input.txt'; output = Path(directory) / 'reply.wav'
            text.write_text('x' * 2001)
            with self.assertRaises(ValueError): client.speak(text, output)
            text.write_bytes(b'\0' * 100)
            with self.assertRaises(ValueError): client.transcribe(text, output)
            post.assert_not_called()


class RecoveryTest(unittest.TestCase):
    def test_closed_forward_recovers_natively_but_auth_failure_is_not_success(self):
        run = Mock(return_value=SimpleNamespace(returncode=0))
        probe = Mock(side_effect=[False, True])
        launch = Mock(return_value=Mock(poll=Mock(return_value=None)))
        self.assertTrue(recovery.ensure_forward(probe, run, launch=launch))
        self.assertEqual(run.call_args.args[0], ['nemohermes', 'hermes-personal-v23', 'recover'])
        run.reset_mock()
        self.assertFalse(recovery.ensure_forward(lambda: True, run)); run.assert_not_called()
        with self.assertRaises(RuntimeError):
            recovery.ensure_forward(lambda: False, run, launch=launch, wait=lambda _: None)

    def test_real_socket_requires_401_rather_than_login_or_200(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            status = 401
            def do_GET(self):
                self.send_response(self.status); self.end_headers()
            def log_message(self, *_): pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            self.assertTrue(recovery.protected_api(server.server_port))
            for status in (200, 302, 502):
                Handler.status = status
                self.assertFalse(recovery.protected_api(server.server_port))
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.assertFalse(recovery.protected_api(server.server_port))


if __name__ == '__main__': unittest.main()
