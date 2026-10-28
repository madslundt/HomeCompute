"""Bounded local speech transport for native Mattermost audio handling."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
from difflib import SequenceMatcher
import io
import json
import re
from pathlib import Path
import ssl
import threading
import unicodedata
from typing import Any
import urllib.request
import wave

MAX_UPLOAD = 10_000_000
MAX_AUDIO = 16 * 1024 * 1024
PATHS = {'/v1/audio/transcriptions', '/v1/audio/speech'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def transcription(body: bytes, content_type: str) -> None:
    if (not 1 <= len(body) <= MAX_UPLOAD or len(content_type) > 256
            or '\r' in content_type or '\n' in content_type):
        raise ValueError('Bounded multipart audio required')
    message = BytesParser(policy=policy.default).parsebytes(
        ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii') + body)
    if message.get_content_type() != 'multipart/form-data' or not message.is_multipart() or message.defects:
        raise ValueError('Multipart audio required')
    fields, audio = {}, 0
    for index, part in enumerate(message.iter_parts()):
        name = part.get_param('name', header='content-disposition')
        if (index >= 8 or part.is_multipart() or part.defects or name in fields
                or part.get_content_disposition() != 'form-data'
                or name not in {'model', 'file', 'language', 'prompt', 'response_format', 'temperature'}):
            raise ValueError('Unsupported transcription fields')
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            raise ValueError('Invalid audio data')
        fields[name] = data
        if name == 'file':
            audio += 1
            if not 1024 <= len(data) <= MAX_UPLOAD:
                raise ValueError('Audio size exceeded')
        elif len(data) > 4096:
            raise ValueError('Transcription field exceeded')
    if (audio != 1 or fields.get('model') not in {b'stt-danish', b'whisper-turbo'}
            or fields.get('response_format', b'json') not in {b'json', b'verbose_json'}):
        raise ValueError('Local transcription model and JSON response required')


def synthesis(body: bytes, content_type: str) -> bytes:
    if content_type != 'application/json' or not 1 <= len(body) <= 16384:
        raise ValueError('Bounded speech JSON required')
    value = json.loads(body)
    if (not isinstance(value, dict) or set(value) - {'model', 'input', 'voice', 'response_format', 'speed'}
            or value.get('model') != 'plapre' or value.get('voice') != 'danish-default'
            or value.get('response_format') != 'wav' or value.get('speed', 1) != 1
            or not isinstance(value.get('input'), str) or not value['input'].strip()
            or len(value['input']) > 2000):
        raise ValueError('Fixed local voice and WAV format required')
    return json.dumps(value).encode()


class SpeechClient:
    """Never forwards the sandbox bearer or uses a proxy/cloud fallback."""
    def __init__(self, opener: Any = None, fallback: Any = None):
        certificate = Path(__file__).resolve().parents[1] / 'config/embedding-ca.crt'
        cafile = None
        if certificate.exists():
            if certificate.is_symlink() or certificate.stat().st_uid != 0 or certificate.stat().st_mode & 0o022:
                raise ValueError('Trusted local speech CA required')
            cafile = str(certificate)
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))
        self.slots = threading.BoundedSemaphore(2)
        self.fallback = fallback

    def _post(self, path: str, body: bytes, content_type: str, limit: int, timeout: float) -> bytes:
        request = urllib.request.Request('https://ai.home.arpa' + path, data=body,
                                        headers={'Content-Type': content_type}, method='POST')
        with self.opener.open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
            if response.status != 200 or not data or len(data) > limit:
                raise RuntimeError('Local speech response exceeded')
        return data

    @staticmethod
    def _wav(data: bytes) -> None:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or not 8000 <= audio.getframerate() <= 48000 or not audio.getnframes()
                    or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2):
                raise RuntimeError('Invalid local speech WAV')

    def _verified(self, text: str, audio: bytes) -> bool:
        # Known Danish output uses the Danish recognizer, independently of the
        # multilingual route used for the owner's incoming voice messages.
        boundary = 'homecompute-speech-verification'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nstt-danish\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reply.wav"\r\n'
                f'Content-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
        result = json.loads(self._post('/v1/audio/transcriptions', body,
                                      'multipart/form-data; boundary=' + boundary, 65536, 12))
        actual = result.get('text')
        if not isinstance(actual, str) or len(actual) > 4000:
            return False
        def normalized(value: str) -> str:
            return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))
        expected, actual = normalized(text), normalized(actual)
        return (bool(expected and actual) and re.findall(r'\d+', expected) == re.findall(r'\d+', actual)
                and SequenceMatcher(None, expected, actual, autojunk=False).ratio() >= 0.90)

    def _speak(self, body: bytes) -> bytes:
        text = json.loads(body)['input']
        for _ in range(2):
            try:
                audio = self._post('/v1/audio/speech', body, 'application/json', MAX_AUDIO, 25)
                self._wav(audio)
                if self._verified(text, audio):
                    return audio
            except Exception:
                # An unavailable GPU or verifier goes straight to the reliable
                # local CPU voice; only an actual ASR mismatch is resynthesized.
                break
        fallback = self.fallback
        if fallback is None:
            from wyoming_tts_client import synthesize
            fallback = synthesize
        audio = fallback(text)
        if not audio or len(audio) > MAX_AUDIO:
            raise RuntimeError('Local fallback response exceeded')
        self._wav(audio)
        return audio

    def request(self, path: str, body: bytes, content_type: str) -> tuple[str, bytes]:
        if path == '/v1/audio/transcriptions':
            transcription(body, content_type)
            limit = 65536
        elif path == '/v1/audio/speech':
            body = synthesis(body, content_type)
            limit = MAX_AUDIO
        else:
            raise ValueError('Unknown speech endpoint')
        if not self.slots.acquire(blocking=False):
            raise RuntimeError('Local speech busy')
        try:
            if path.endswith('/transcriptions'):
                data = self._post(path, body, content_type, limit, 110)
                value = json.loads(data)
                if (not isinstance(value, dict) or not isinstance(value.get('text'), str)
                        or not value['text'].strip() or len(value['text']) > 4000):
                    raise RuntimeError('Invalid local transcript')
                return 'application/json', json.dumps({'text': value['text']}).encode()
            return 'audio/wav', self._speak(body)
        finally:
            self.slots.release()
