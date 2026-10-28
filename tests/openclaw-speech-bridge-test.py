"""Exercise local speech validation and the authenticated native HTTP boundary."""
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_speech as speech
from openclaw_notifications import Outbox
SPEC = importlib.util.spec_from_file_location('speech_fixture', Path(__file__).with_name('openclaw-communication-test.py'))
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)
NATIVE = {'openclaw': 'synthetic-native-openclaw-' + 'x' * 40}
TTS = {'model': 'plapre', 'voice': 'danish-default', 'input': 'Hej, stemmen virker.', 'response_format': 'wav'}


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b'\x00\x01' * 2400)
    return output.getvalue()


def multipart(model='whisper-turbo', audio=None):
    data = b''
    for name, value in [('model', model.encode()), ('file', audio or wav())]:
        data += ('--speech-test\r\nContent-Disposition: form-data; name="' + name + '"' +
                 ('; filename="voice.wav"' if name == 'file' else '') + '\r\n\r\n').encode() + value + b'\r\n'
    return data + b'--speech-test--\r\n', 'multipart/form-data; boundary=speech-test'


class Response:
    status = 200
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.body[:size]


class SpeechClientTests(unittest.TestCase):
    def test_real_audio_and_fixed_local_voice_round_trip_without_forwarding_bearer(self):
        opener = Mock()
        opener.open.side_effect = [Response(b'{"text":"Hej med dig"}'), Response(wav()),
                                   Response(json.dumps({'text': TTS['input']}).encode())]
        client = speech.SpeechClient(opener)
        data, content_type = multipart()
        self.assertEqual(client.request('/v1/audio/transcriptions', data, content_type),
                         ('application/json', b'{"text": "Hej med dig"}'))
        self.assertEqual(client.request('/v1/audio/speech', json.dumps(TTS).encode(), 'application/json'),
                         ('audio/wav', wav()))
        for call in opener.open.call_args_list:
            request = call.args[0]
            self.assertTrue(request.full_url.startswith('https://ai.home.arpa/v1/audio/'))
            self.assertNotIn('Authorization', request.headers)
            self.assertIn(call.kwargs['timeout'], {110, 25, 12})

    def test_cloud_models_unapproved_voices_and_format_changes_are_rejected_before_upload(self):
        opener = Mock(); fallback = Mock(side_effect=RuntimeError('fallback offline'))
        client = speech.SpeechClient(opener, fallback)
        for body in [{**TTS, 'model': 'tts-1'}, {**TTS, 'voice': 'arbitrary'},
                     {**TTS, 'response_format': 'mp3'}, {**TTS, 'input': 'x' * 2001},
                     {**TTS, 'reference_audio': 'unapproved'}]:
            with self.assertRaises(ValueError): client.request('/v1/audio/speech', json.dumps(body).encode(), 'application/json')
        for model, audio in [('gpt-4o-transcribe', None), ('whisper-turbo', b'tiny')]:
            body, kind = multipart(model, audio)
            with self.assertRaises(ValueError): client.request('/v1/audio/transcriptions', body, kind)
        with self.assertRaises(ValueError): client.request('/v1/chat/completions', b'{}', 'application/json')
        opener.open.assert_not_called()

    def test_empty_transcripts_and_invalid_wav_fail_closed_when_cpu_is_unavailable(self):
        opener = Mock(); fallback = Mock(side_effect=RuntimeError('fallback offline'))
        client = speech.SpeechClient(opener, fallback)
        for body in [b'{}', b'{"text":""}', b'{"text":44}']:
            opener.open.return_value = Response(body)
            data, kind = multipart()
            with self.assertRaises((ValueError, RuntimeError)): client.request('/v1/audio/transcriptions', data, kind)
        for body in [b'not audio', wav()[:-2]]:
            opener.open.return_value = Response(body)
            with self.assertRaises(RuntimeError):
                client.request('/v1/audio/speech', json.dumps(TTS).encode(), 'application/json')
        self.assertEqual(opener.open.call_count, 5)

    def test_second_mismatch_uses_cpu_voice_and_never_returns_unverified_gpu_audio(self):
        opener = Mock()
        opener.open.side_effect = [Response(wav()), Response(b'{"text":"garbled unrelated words"}'),
                                   Response(wav()), Response(b'{"text":"still unrelated words"}')]
        fallback = Mock(return_value=wav())
        client = speech.SpeechClient(opener, fallback)
        self.assertEqual(client.request('/v1/audio/speech', json.dumps(TTS).encode(), 'application/json'), ('audio/wav', wav()))
        self.assertEqual(opener.open.call_count, 4)
        fallback.assert_called_once_with(TTS['input'])

    def test_timeout_falls_back_without_resynthesis_and_empty_cpu_audio_fails_closed(self):
        opener = Mock(); opener.open.side_effect = TimeoutError('GPU unavailable')
        fallback = Mock(return_value=wav()); client = speech.SpeechClient(opener, fallback)
        self.assertEqual(client.request('/v1/audio/speech', json.dumps(TTS).encode(), 'application/json'), ('audio/wav', wav()))
        self.assertEqual(opener.open.call_count, 1)
        fallback.return_value = b''
        with self.assertRaises(RuntimeError): client.request('/v1/audio/speech', json.dumps(TTS).encode(), 'application/json')

    def test_verification_rejects_changed_numbers_and_unbounded_transcripts(self):
        opener = Mock(); client = speech.SpeechClient(opener)
        for actual in ['Temperaturen er 22 grader i stuen.', 'x' * 4001]:
            opener.open.return_value = Response(json.dumps({'text': actual}).encode())
            self.assertFalse(client._verified('Temperaturen er 21 grader i stuen.', wav()))


class SpeechBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Outbox(Path(self.temp.name) / 'outbox.sqlite3', FIXTURE.SETTINGS)
        tasks = SimpleNamespace(projects={'synthetic-demo'}, tokens={'assistant': 'broker-assistant', 'snapshot': 'broker-snapshot'})
        self.client = Mock(); self.client.request.return_value = ('audio/wav', wav())
        self.turn = Mock(side_effect=AssertionError('No model turn in speech bridge'))
        self.server = FIXTURE.communication.serve(self.store, FIXTURE.TOKENS, FIXTURE.ALLOWED,
            self.turn, ('127.0.0.1', 0), tasks=tasks, mcp_tokens=NATIVE, speech_client=self.client)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store.db.close(); self.temp.cleanup()

    def post(self, token=NATIVE['openclaw'], path='/v1/audio/speech', headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('POST', path, json.dumps(TTS),
                {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read(), response.getheader('Content-Type')
        finally: connection.close()

    def test_native_bearer_gets_wav_while_transport_and_unknown_credentials_are_refused(self):
        self.assertEqual(self.post(), (200, wav(), 'audio/wav'))
        for token in [*FIXTURE.TOKENS.values(), 'retired-hermes', 'unknown']:
            self.assertEqual(self.post(token)[0], 401)
        self.assertEqual(self.client.request.call_count, 1)
        self.turn.assert_not_called()

    def test_queries_oversized_bodies_and_failures_do_not_trigger_a_model_turn(self):
        self.assertEqual(self.post(path='/v1/audio/speech?redirect=elsewhere')[0], 404)
        self.assertEqual(self.post(headers={'Content-Length': '20000'})[0], 413)
        self.client.request.side_effect = RuntimeError('private upstream detail')
        status, body, _ = self.post()
        self.assertEqual(status, 503); self.assertNotIn(b'private upstream detail', body)
        self.turn.assert_not_called()


if __name__ == '__main__': unittest.main()
