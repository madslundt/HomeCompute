"""Native speech setup preserves conversation boundaries and local credential custody."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('speech_setup', ROOT / 'scripts/setup-openclaw-speech.py')
speech = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(speech)


class SpeechSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.config = self.root / 'openclaw.json'
        self.before = json.loads((ROOT / 'config/openclaw-nemoclaw.json').read_text())
        self.before['mcp']['servers']['homecompute'] = {
            'transport': 'streamable-http', 'url': speech.ORIGIN + '/mcp',
            'headers': {'Authorization': 'Bearer synthetic-owner-private-speech-token'}}
        self.before['channels'] = {'mattermost': {'enabled': True,
            'baseUrl': 'https://' + speech.MATTERMOST_HOST + ':9443',
            'accounts': {'default': {'enabled': True, 'botToken': 'synthetic-bot-token'}},
            'allowFrom': ['owner'], 'groupAllowFrom': ['owner']},
            'telegram': {'enabled': False, 'tts': {'auto': 'off'}}}
        self.before['bindings'] = [{'agentId': 'main', 'match': {'channel': 'mattermost'}}]
        self.before['tools']['media'] = {'image': {'enabled': False},
            'models': [{'provider': 'inference', 'model': 'vision', 'capabilities': ['image']}]}
        self.config.write_text(json.dumps(self.before))
        self.config.chmod(0o600)
        self.commands = []

    def tearDown(self):
        self.temporary.cleanup()

    def runner(self, arguments, **options):
        self.commands.append((arguments, options))
        candidate = Path(options['env']['OPENCLAW_CONFIG_PATH'])
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(candidate.read_text()), speech.candidate(self.before))
        return subprocess.CompletedProcess(arguments, 0, stdout='private-native-output', stderr='')

    def test_candidate_replies_only_to_inbound_mattermost_audio_in_final_wav(self):
        original = copy.deepcopy(self.before)
        after = speech.candidate(self.before)
        channel = after['channels']['mattermost']
        tts = after['tts']
        self.assertEqual((tts['auto'], tts['mode']), ('inbound', 'final'))
        self.assertEqual((tts['maxTextLength'], tts['timeoutMs']), (2000, 120000))
        self.assertFalse(tts['modelOverrides']['enabled'])
        provider = tts['providers']['openai']
        self.assertEqual(provider['baseUrl'], speech.ORIGIN + '/v1')
        self.assertEqual(provider['model'], 'plapre')
        self.assertEqual(provider['voice'], 'danish-default')
        self.assertEqual(provider['responseFormat'], 'wav')
        persona = tts['personas'][tts['persona']]
        self.assertEqual(persona['fallbackPolicy'], 'fail')
        self.assertEqual(persona['providers'], {'openai': provider})
        self.assertNotIn('tts', after.get('messages', {}))
        self.assertEqual(after['channels']['telegram'], original['channels']['telegram'])
        self.assertEqual(channel, original['channels']['mattermost'])
        self.assertEqual(self.before, original)

    def test_audio_models_explicitly_disable_autodetection_and_scope_to_mattermost(self):
        after = speech.candidate(self.before)
        media = after['tools']['media']
        self.assertEqual(media['audio']['scope'], {'default': 'deny', 'rules': [
            {'action': 'allow', 'match': {'channel': 'mattermost'}}]})
        self.assertFalse(media['audio']['echoTranscript'])
        self.assertNotIn('language', media['audio'])
        self.assertEqual(media['models'][0], self.before['tools']['media']['models'][0])
        model = media['models'][1]
        self.assertEqual((model['provider'], model['model'], model['capabilities']),
                         ('openai', 'whisper-turbo', ['audio']))
        self.assertEqual((model['maxBytes'], model['maxChars'], model['timeoutSeconds']),
                         (10_000_000, 4000, 120))
        self.assertEqual(model['baseUrl'], speech.ORIGIN + '/v1')
        self.assertEqual(media['image'], self.before['tools']['media']['image'])

    def test_chat_model_mcp_routing_and_security_are_unchanged(self):
        after = speech.candidate(self.before)
        for section in ('agents', 'mcp', 'bindings', 'gateway', 'memory'):
            self.assertEqual(after[section], self.before[section])
        self.assertEqual(after['models']['providers']['inference'], self.before['models']['providers']['inference'])
        self.assertEqual(after['models']['providers']['openai']['models'], [])
        for key, value in self.before['tools'].items():
            if key != 'media':
                self.assertEqual(after['tools'][key], value)
        self.assertEqual(after['plugins']['allow'].count('openai'), 1)
        self.assertTrue(after['plugins']['entries']['openai']['enabled'])
        self.assertEqual(speech.candidate(after), after)

    def test_managed_credentials_follow_current_provider_revision_without_export(self):
        expected = {'source': 'env', 'provider': 'default', 'id': speech.CREDENTIAL_ENV}
        for revision in ('', 'v123_', 's' + 'a' * 64 + '_'):
            before = copy.deepcopy(self.before)
            before['mcp']['servers']['homecompute']['headers']['Authorization'] = (
                'Bearer openshell:resolve:env:' + revision + speech.CREDENTIAL_ENV)
            after = speech.candidate(before)
            self.assertEqual(after['models']['providers']['openai']['apiKey'], expected)
            self.assertEqual(after['tts']['providers']['openai']['apiKey'], expected)
            self.assertEqual(after['mcp'], before['mcp'])

    def test_untrusted_bridge_credentials_and_conflicting_provider_are_refused(self):
        for mutate in (
            lambda cfg: cfg['mcp']['servers']['homecompute'].update(url='https://attacker.invalid/mcp'),
            lambda cfg: cfg['mcp']['servers']['homecompute'].update(url=speech.ORIGIN + '/mcp?x=y'),
            lambda cfg: cfg['mcp']['servers']['homecompute']['headers'].update(Authorization='Bearer bad\nkey'),
            lambda cfg: cfg['mcp']['servers']['homecompute']['headers'].update(
                Authorization='Bearer openshell:resolve:env:v123_UNRELATED_KEY'),
            lambda cfg: cfg['models']['providers'].update(openai={'baseUrl': 'https://api.openai.com/v1'}),
            lambda cfg: cfg['channels']['mattermost'].update(baseUrl='https://attacker.invalid'),
            lambda cfg: cfg['tools']['media']['models'].append({'provider': 'other', 'capabilities': ['audio']}),
        ):
            before = copy.deepcopy(self.before); mutate(before)
            with self.assertRaises(speech.Refused):
                speech.candidate(before)

    def test_preview_validates_private_candidate_and_preserves_config(self):
        original = self.config.read_bytes()
        with patch.dict(os.environ, {'NODE_OPTIONS': '--require /tmp/native-preload.js'}):
            result = speech.setup(self.config, runner=self.runner)
        self.assertFalse(result['applied'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [self.config])
        self.assertEqual(self.commands[0][0], ['openclaw', 'config', 'validate', '--json'])
        self.assertIn('--require /tmp/native-preload.js', self.commands[0][1]['env']['NODE_OPTIONS'])
        self.assertNotIn('synthetic-owner-private-speech-token', json.dumps(result))
        self.assertNotIn('synthetic-owner-private-speech-token', json.dumps(self.commands))

    def test_root_inbound_tts_requires_mattermost_as_only_enabled_channel(self):
        for value in ({'enabled': True}, {}, {'enabled': None}):
            before = copy.deepcopy(self.before)
            before['channels']['telegram'] = value
            with self.assertRaises(speech.Refused):
                speech.candidate(before)
        before = copy.deepcopy(self.before)
        before['channels']['defaults'] = {'groupPolicy': 'allowlist'}
        before['channels']['modelByChannel'] = {'telegram': {'*': 'inference/automation-moe'}}
        after = speech.candidate(before)
        self.assertEqual(after['channels'], before['channels'])

    def test_existing_root_agent_or_account_tts_cannot_silently_override_local_voice(self):
        for mutate in (
            lambda cfg: cfg.update(tts={'auto': 'always', 'provider': 'microsoft'}),
            lambda cfg: cfg['agents']['defaults'].update(tts={'auto': 'off'}),
            lambda cfg: cfg['agents']['entries']['main'].update(tts={'auto': 'off'}),
            lambda cfg: cfg['channels']['mattermost'].update(tts={'auto': 'off'}),
            lambda cfg: cfg['channels']['mattermost']['accounts']['default'].update(tts={'auto': 'off'}),
        ):
            before = copy.deepcopy(self.before); mutate(before)
            with self.assertRaises(speech.Refused):
                speech.candidate(before)

    def test_apply_backs_up_original_and_is_idempotent(self):
        original = self.config.read_bytes()
        result = speech.setup(self.config, apply=True, runner=self.runner)
        backup = Path(result['backup_directory']) / 'openclaw-before.json'
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.config.read_text()), speech.candidate(self.before))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        again = speech.setup(self.config, apply=True, runner=self.runner)
        self.assertFalse(again['config_changed'])
        self.assertFalse(again['restart_required'])
        self.assertNotIn('backup_directory', again)

    def test_failed_schema_and_concurrent_edits_preserve_original(self):
        original = self.config.read_bytes()
        def rejected(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, stderr='private rejected configuration')
        with self.assertRaises(speech.Refused):
            speech.setup(self.config, apply=True, runner=rejected)
        self.assertEqual(self.config.read_bytes(), original)
        def concurrent(*args, **kwargs):
            self.config.write_text('{"concurrent_operator_edit": true}')
            return subprocess.CompletedProcess(args[0], 0)
        with self.assertRaises(speech.Refused):
            speech.setup(self.config, apply=True, runner=concurrent)
        self.assertEqual(json.loads(self.config.read_text()), {'concurrent_operator_edit': True})
        self.assertFalse(list(self.root.glob('.speech-candidate-*')))
        self.assertFalse((self.root / 'speech-setup-backups').exists())


class SpeechNetworkTests(unittest.TestCase):
    def setUp(self):
        self.before = {'filesystem_policy': {'read_only': ['/usr']}, 'network_policies': {
            'other': {'retain': True}, 'mcp_bridge_homecompute': {'endpoints': [{
                'host': 'homecompute-capabilities.home.arpa', 'port': 19445, 'path': '/mcp',
                'allowed_ips': ['10.77.20.1'], 'protocol': 'mcp', 'enforcement': 'enforce',
                'credential_binding': {'provider': 'agent-openclaw-mcp-homecompute'}}]},
            'mattermost': {'binaries': [{'path': '/usr/local/bin/node'}], 'endpoints': [
                {'host': speech.MATTERMOST_HOST, 'port': 9443, 'protocol': 'rest', 'enforcement': 'enforce',
                 'rules': [{'allow': {'method': 'GET', 'path': '/api/v4/**'}}]},
                {'host': speech.MATTERMOST_HOST, 'port': 9443, 'path': '/api/v4/websocket',
                 'protocol': 'websocket', 'enforcement': 'enforce'}]}}}

    def test_only_exact_speech_posts_and_native_file_upload_are_added(self):
        original = copy.deepcopy(self.before)
        after = speech.network_candidate(self.before)
        self.assertEqual(self.before, original)
        self.assertEqual(after['filesystem_policy'], original['filesystem_policy'])
        for name in ('other', 'mcp_bridge_homecompute'):
            self.assertEqual(after['network_policies'][name], original['network_policies'][name])
        entry = after['network_policies']['homecompute_speech']
        self.assertEqual(entry['binaries'], [{'path': '/usr/local/bin/node'}])
        self.assertEqual([e['path'] for e in entry['endpoints']], list(speech.SPEECH_PATHS))
        for endpoint in entry['endpoints']:
            self.assertEqual(endpoint['allowed_ips'], ['10.77.20.1'])
            self.assertEqual(endpoint['credential_binding'], {'provider': 'agent-openclaw-mcp-homecompute'})
            self.assertEqual(endpoint['rules'], [{'allow': {'method': 'POST', 'path': endpoint['path']}}])
        expected = copy.deepcopy(original['network_policies']['mattermost'])
        expected['endpoints'][0]['rules'].append({'allow': {'method': 'POST', 'path': '/api/v4/files'}})
        self.assertEqual(after['network_policies']['mattermost'], expected)
        self.assertEqual(speech.network_candidate(after), after)

    def test_untrusted_hosts_binding_or_prior_speech_policy_drift_are_refused(self):
        after = speech.network_candidate(self.before)
        for mutate in (
            lambda cfg: cfg['network_policies']['mcp_bridge_homecompute']['endpoints'][0].update(
                allowed_ips=['0.0.0.0/0']),
            lambda cfg: cfg['network_policies']['mcp_bridge_homecompute']['endpoints'][0].update(
                credential_binding={'provider': 'unrelated'}),
            lambda cfg: cfg['network_policies']['mattermost']['endpoints'][0].update(host='attacker.invalid'),
            lambda cfg: cfg['network_policies']['mattermost'].update(binaries=[{'path': '/usr/bin/curl'}]),
            lambda cfg: cfg['network_policies']['homecompute_speech']['endpoints'][0].update(path='/**'),
        ):
            drift = copy.deepcopy(after); mutate(drift)
            with self.assertRaises(speech.Refused):
                speech.network_candidate(drift)

    def test_nemoclaw_namespaced_mattermost_policy_is_migrated_without_renaming(self):
        name = 'nemoclaw_custom__homecompute-mattermost__mattermost'
        self.before['network_policies'][name] = self.before['network_policies'].pop('mattermost')
        after = speech.network_candidate(self.before)
        self.assertNotIn('mattermost', after['network_policies'])
        rules = after['network_policies'][name]['endpoints'][0]['rules']
        self.assertIn({'allow': {'method': 'POST', 'path': '/api/v4/files'}}, rules)
        self.assertEqual(speech.network_candidate(after), after)


if __name__ == '__main__':
    unittest.main()
