#!/usr/bin/env python3
"""Exercise the private HTTPS speech boundary with synthetic content only."""
import http.client
import io
import json
from pathlib import Path
import socket
import ssl
import time
import wave


def request(path, body, content_type):
    key = Path('/srv/state/control-plane/caddy-data/network-inference-key').read_text().strip()
    context = ssl.create_default_context(cafile='/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt')
    connection = http.client.HTTPSConnection('ai.home.arpa', timeout=320, context=context)
    connection.sock = context.wrap_socket(socket.create_connection(('192.168.30.122', 443), timeout=320), server_hostname='ai.home.arpa')
    try:
        connection.request('POST', path, body, {'Authorization': 'Bearer ' + key, 'Content-Type': content_type})
        result = connection.getresponse()
        payload = result.read(32 * 1024 * 1024 + 1)
        if result.status != 200 or len(payload) > 32 * 1024 * 1024:
            raise RuntimeError('Speech probe failed, HTTP ' + str(result.status))
        return payload
    finally:
        connection.close()


def main():
    started = time.monotonic()
    audio = request('/v1/audio/speech', json.dumps({'model': 'tts-danish',
        'input': 'Lyset er nu slukket.', 'voice': 'danish-default', 'response_format': 'wav'}).encode(), 'application/json')
    with wave.open(io.BytesIO(audio)) as wav:
        if wav.getnframes() < 1 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError('Invalid synthesized WAV')
        seconds = wav.getnframes() / wav.getframerate()
    tts_seconds = time.monotonic() - started
    english = request('/v1/audio/speech', json.dumps({'model': 'tts-english',
        'input': 'The light is now off.', 'voice': 'english-default', 'response_format': 'wav'}).encode(), 'application/json')
    with wave.open(io.BytesIO(english)) as wav:
        if wav.getnframes() < 1 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise RuntimeError('Invalid English WAV')
    boundary = 'homecompute-synthetic-speech-probe'
    body = b''
    for field, value in [('model', 'whisper'), ('language', 'da'), ('response_format', 'json')]:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n').encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    started = time.monotonic()
    result = json.loads(request('/v1/audio/transcriptions', body, 'multipart/form-data; boundary=' + boundary))
    if not isinstance(result.get('text'), str) or not result['text'].strip():
        raise RuntimeError('Synthetic transcription was empty')
    print(json.dumps({'https_tts_danish': True, 'https_tts_english': True, 'https_stt': True, 'audio_seconds': round(seconds, 3),
        'tts_seconds': round(tts_seconds, 3), 'stt_seconds': round(time.monotonic() - started, 3),
        'content_retained': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Speech API smoke failed: ' + type(error).__name__) from None
