"""Bounded local Whisper/OmniVoice command providers; no cloud or credential logging."""
import argparse
import io
import json
import os
from pathlib import Path
import ssl
import sys
import urllib.request
import wave

ROOT = Path(__file__).resolve().parent
BASE = 'https://homecompute-capabilities.home.arpa:19445/v1'
VOICES = {'da': 'danish-default', 'en': 'english-default'}


def language(text):
    """Classify only Danish/English, deterministically; very short ambiguity defaults to Danish."""
    sys.path.insert(0, str(ROOT / 'lib'))
    from langdetect.detector_factory import DetectorFactory
    from langdetect.lang_detect_exception import LangDetectException
    value = text.casefold().strip(' .,!?:;')
    if value in {'yes', 'no', 'hello', 'thanks', 'thank you', 'goodbye', 'stop'}:
        return 'en'
    if value in {'ja', 'nej', 'hej', 'tak', 'farvel'} or any(c in value for c in 'æøå'):
        return 'da'
    factory = DetectorFactory()
    factory.load_profile(str(ROOT / 'lib/langdetect/profiles'))
    factory.seed = 0
    detector = factory.create()
    detector.append(text)
    try:
        return detector.detect()
    except LangDetectException:
        return 'da'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def post(path, body, content_type):
    # The managed environment supplies an opaque OpenShell handle. Resolve it
    # afresh for every process; never persist a restart-scoped handle in config.
    from hermes_cli.config import get_env_value
    token = get_env_value('HOMECOMPUTE_HERMES_MCP_TOKEN')
    if not token or not token.startswith('openshell:'):
        raise ValueError('managed speech credential required')
    opener = urllib.request.build_opener(NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(BASE + path, data=body,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': content_type}, method='POST')
    with opener.open(request, timeout=115) as response:
        data = response.read(16 * 1024 * 1024 + 1)
        if response.status != 200 or not data or len(data) > 16 * 1024 * 1024:
            raise ValueError('bounded local speech response required')
        return data


def transcribe(path, output):
    data = path.read_bytes()
    if not 1024 <= len(data) <= 9_990_000:
        raise ValueError('bounded microphone recording required')
    boundary = 'homecompute-hermes-audio'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="microphone.webm"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    result = json.loads(post('/audio/transcriptions', body, 'multipart/form-data; boundary=' + boundary))
    if not isinstance(result.get('text'), str) or len(result['text']) > 4000:
        raise ValueError('bounded transcript required')
    output.write_text(result['text'], encoding='utf-8')


def speak(path, output, voice='auto'):
    text = path.read_text(encoding='utf-8')
    if not text.strip() or len(text) > 2000:
        raise ValueError('bounded reply sentence required')
    selected = language(text) if voice == 'auto' else next(k for k, v in VOICES.items() if v == voice)
    body = json.dumps({'input': text, 'model': 'tts-english' if selected == 'en' else 'tts-danish',
                       'voice': VOICES[selected], 'response_format': 'wav'}).encode()
    data = post('/audio/speech', body, 'application/json')
    with wave.open(io.BytesIO(data), 'rb') as audio:
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or not audio.getnframes()
                or not 8000 <= audio.getframerate() <= 48000):
            raise ValueError('valid mono speech WAV required')
    output.write_bytes(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('stt', 'tts'))
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--voice', choices=('auto', *VOICES.values()), default='auto')
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.operation == 'stt':
            transcribe(args.input, args.output)
        else:
            speak(args.input, args.output, args.voice)
    except Exception:
        # Upstream includes command stderr in its error envelope: keep it free
        # of credentials, household text and HTTP exception bodies.
        raise SystemExit('Local speech unavailable; no external fallback attempted') from None
