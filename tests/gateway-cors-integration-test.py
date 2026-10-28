#!/usr/bin/env python3
"""Live gateway CORS checks; run explicitly, outside offline repository checks."""
import os
import json
import ssl
import unittest
import urllib.error
import urllib.request


BASE = os.environ.get('HOMECOMPUTE_GATEWAY_URL', 'https://ai.home.arpa').rstrip('/')
CA = os.environ.get('HOMECOMPUTE_CA_CERT', os.path.expanduser('~/.config/homecompute/home-core-root.crt'))


def tls_context():
    return ssl.create_default_context(cafile=CA or None)


class GatewayCorsTests(unittest.TestCase):
    def preflight(self, path, origin, method=None, private_network=False):
        headers = {'Origin': origin}
        if method:
            headers['Access-Control-Request-Method'] = method
            headers['Access-Control-Request-Headers'] = 'authorization,content-type,x-client-test'
        if private_network:
            headers['Access-Control-Request-Private-Network'] = 'true'
        request = urllib.request.Request(BASE + path, method='OPTIONS', headers=headers)
        try:
            response = urllib.request.urlopen(request, context=tls_context(), timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertIn(response.status, (200, 204), f'OPTIONS {path}')
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
            if method:
                self.assertIn(method, response.headers.get('Access-Control-Allow-Methods', ''))
                self.assertIn('authorization', response.headers.get('Access-Control-Allow-Headers', '').lower())
            if private_network:
                self.assertEqual(response.headers.get('Access-Control-Allow-Private-Network'), 'true')

    def test_base_and_bare_preflight_without_key(self):
        for path in ('/v1', '/v1/', '/v1/models', '/v1/chat/completions', '/v1/responses', '/v1/audio/transcriptions', '/v1/embeddings', '/v1/videos'):
            with self.subTest(path=path):
                self.preflight(path, 'https://example.org')

    def test_standard_and_trailing_slash_preflight_without_key(self):
        for path, method in (('/v1/models', 'GET'), ('/v1/chat/completions', 'POST'), ('/v1/responses', 'POST'), ('/v1/audio/transcriptions', 'POST'), ('/v1/embeddings', 'POST'), ('/v1/videos', 'POST')):
            for suffix in ('', '/'):
                with self.subTest(path=path + suffix):
                    self.preflight(path + suffix, 'http://localhost:3000', method)

    def test_private_network_preflight(self):
        self.preflight('/v1/chat/completions', 'https://example.org', 'POST', private_network=True)

    def test_video_status_and_content_preflight_without_generation(self):
        for path in ('/v1/videos/not-a-real-video', '/v1/videos/not-a-real-video/content'):
            for suffix in ('', '/'):
                with self.subTest(path=path, suffix=suffix):
                    self.preflight(path + suffix, 'http://localhost:8080', 'GET', private_network=True)

    def test_invalid_video_status_has_cors_and_never_submits_a_job(self):
        for credentials in ({}, {'Authorization': 'Bearer local'}):
            with self.subTest(placeholder=bool(credentials)):
                request = urllib.request.Request(BASE + '/v1/videos/not-a-real-video', headers={
                    'Origin': 'http://localhost:8080', **credentials,
                })
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, context=tls_context(), timeout=15)
                with caught.exception as response:
                    self.assertIn(response.code, (400, 404, 422))
                    self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
                    self.assertIn('error', json.load(response))

    def test_keyless_embeddings_support_placeholders_and_trailing_slashes(self):
        import math
        for suffix, authorization in (('', None), ('/', 'Bearer local')):
            with self.subTest(suffix=suffix):
                headers = {'Content-Type': 'application/json', 'Origin': 'http://localhost:8080'}
                if authorization:
                    headers['Authorization'] = authorization
                request = urllib.request.Request(BASE + '/v1/embeddings' + suffix,
                    headers=headers, data=json.dumps({
                        'model': 'embedding', 'input': ['Dette er en prøve.', 'This is a test.'],
                        'encoding_format': 'float',
                    }).encode())
                with urllib.request.urlopen(request, context=tls_context(), timeout=60) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
                    body = json.load(response)
                    self.assertEqual(body['model'], 'embedding')
                    self.assertEqual([row['index'] for row in body['data']], [0, 1])
                    for row in body['data']:
                        self.assertEqual(len(row['embedding']), 1024)
                        self.assertTrue(all(math.isfinite(value) for value in row['embedding']))

    def test_unrecognized_credentials_fall_back_to_network_access(self):
        examples = [
            {'Authorization': 'Bearer '},
            {'Authorization': 'Bearer undefined'},
            {'Authorization': 'Bearer sk-unrecognized-placeholder'},
            {'X-Litellm-Api-Key': 'unrecognized-placeholder'},
        ]
        for credentials in examples:
            with self.subTest(credentials=credentials):
                request = urllib.request.Request(BASE + '/v1/models', headers={
                    'Origin': 'http://localhost:8080', **credentials,
                })
                with urllib.request.urlopen(request, context=tls_context(), timeout=15) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual({item['id'] for item in json.load(response)['data']},
                                     {'automation', 'home', 'automation-moe', 'stt-danish', 'whisper-turbo', 'plapre', 'embedding', 'video'})
        request = urllib.request.Request(BASE + '/v1/chat/completions', headers={
            'Origin': 'http://localhost:8080', 'Content-Type': 'application/json', 'Authorization': 'Bearer ',
        }, data=json.dumps({
            'model': 'automation-moe', 'messages': [{'role': 'user', 'content': 'Reply with exactly READY.'}],
            'max_tokens': 32, 'temperature': 0,
        }).encode())
        with urllib.request.urlopen(request, context=tls_context(), timeout=30) as response:
            self.assertEqual(json.load(response)['choices'][0]['message']['content'].strip(), 'READY')

    def test_valid_explicit_key_retains_its_scope(self):
        key = os.environ.get('HOMECOMPUTE_SCRIPT_API_KEY')
        if not key:
            self.skipTest('Optional scoped scripts key not configured.')
        request = urllib.request.Request(BASE + '/v1/models', headers={'Authorization': 'Bearer ' + key})
        with urllib.request.urlopen(request, context=tls_context(), timeout=15) as response:
            self.assertEqual([item['id'] for item in json.load(response)['data']], ['automation-moe'])
        request = urllib.request.Request(BASE + '/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            data=json.dumps({'model': 'automation-qualification', 'messages': [{'role': 'user', 'content': 'Hi'}],
                             'max_tokens': 1}).encode())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, context=tls_context(), timeout=15)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_trailing_slash_models_and_keyless_chat(self):
        headers = {'Origin': 'http://localhost:8080', 'Content-Type': 'application/json'}
        request = urllib.request.Request(BASE + '/v1/models/', headers=headers)
        with urllib.request.urlopen(request, context=tls_context(), timeout=15) as response:
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
            self.assertIn('automation-moe', [item['id'] for item in json.load(response)['data']])
        request = urllib.request.Request(BASE + '/v1/chat/completions/', headers=headers, data=json.dumps({
            'model': 'automation-moe', 'messages': [{'role': 'user', 'content': 'Reply with exactly READY.'}],
            'max_tokens': 32, 'temperature': 0,
        }).encode())
        with urllib.request.urlopen(request, context=tls_context(), timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
            self.assertEqual(json.load(response)['choices'][0]['message']['content'].strip(), 'READY')

    def test_transcription_has_cors_and_preserves_multipart_body(self):
        # Synthetic silence exercises real audio decoding without private speech.
        import io
        import wave
        handle = io.BytesIO()
        with wave.open(handle, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b'\0' * 32000)
        boundary = 'homecompute-stt-integration'
        for model in ('stt-danish', 'whisper-turbo'):
            # Omit language and response_format: verify automatic language and
            # the OpenAI-compatible JSON default with only model and file.
            fields = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n').encode()
            fields += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="silence.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
            body = fields + handle.getvalue() + f'\r\n--{boundary}--\r\n'.encode()
            for suffix, key in [('', None), ('/', 'Bearer undefined')]:
                with self.subTest(model=model, suffix=suffix, placeholder=bool(key)):
                    headers = {'Origin': 'http://localhost:8080', 'Content-Type': 'multipart/form-data; boundary=' + boundary}
                    if key:
                        headers['Authorization'] = key
                    request = urllib.request.Request(BASE + '/v1/audio/transcriptions' + suffix, data=body, headers=headers)
                    with urllib.request.urlopen(request, context=tls_context(), timeout=150) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')
                        self.assertIsInstance(json.load(response)['text'], str)

    def test_administrative_preflight_remains_unavailable(self):
        request = urllib.request.Request(BASE + '/key/generate', method='OPTIONS', headers={
            'Origin': 'http://localhost:8080', 'Access-Control-Request-Method': 'POST',
        })
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, context=tls_context(), timeout=15)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()
