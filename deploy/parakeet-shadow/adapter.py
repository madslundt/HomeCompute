"""Loopback-only, offline transcription discriminator; never a production route."""
from __future__ import annotations

import email.policy
from email.parser import BytesParser
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import threading
import time
import wave

# Python -I intentionally excludes the entrypoint's directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_runtime import (ParakeetRuntime, RuntimeBusyError, RuntimeGuardError,
                           RuntimeInferenceError, RuntimeNotReady)

MODEL = 'parakeet-tdt-0.6b-v3'
MAX_PCM = 180 * 16000 * 2
MAX_BODY = MAX_PCM + 65536
MAX_RESPONSE = 2 * 1024 * 1024
UPLOAD_SECONDS = 30
INFERENCE_SECONDS = 600


class InvalidRequest(ValueError):
    pass


def host_memory(path: Path = Path('/proc/meminfo')) -> tuple[int, int]:
    values = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts[0] in ('MemTotal:', 'MemAvailable:') and len(parts) == 3 and parts[2] == 'kB':
            values[parts[0][:-1]] = int(parts[1]) * 1024
    total, available = values['MemTotal'], values['MemAvailable']
    if total <= 0 or not 0 <= available <= total:
        raise ValueError('Invalid memory counters')
    return total, available


class ResourceGuard:
    """Latch low host headroom, including between cooperative decoder callbacks."""
    def __init__(self, reader=host_memory):
        self.reader = reader
        self.stopping = threading.Event()
        self.failed = threading.Event()
        self.closed = threading.Event()
        self.minimum_available = None
        self._thread = None

    def check(self):
        if self.stopping.is_set() or self.failed.is_set():
            raise RuntimeGuardError('Shadow resource guard aborted')
        try:
            total, available = self.reader()
            self.minimum_available = min(available, self.minimum_available or available)
            if available * 10 < total:
                raise ValueError('Insufficient host headroom')
        except Exception:
            self.failed.set()
            raise RuntimeGuardError('Shadow resource guard aborted') from None

    def start(self):
        self.check()

        def monitor():
            while not self.closed.wait(0.1):
                try:
                    self.check()
                except RuntimeGuardError:
                    return
        self._thread = threading.Thread(target=monitor, daemon=True)
        self._thread.start()

    def close(self):
        self.closed.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def request_guard(self, deadline):
        def check():
            self.check()
            if time.monotonic() >= deadline:
                raise RuntimeGuardError('Shadow request deadline exceeded')
        return check


def read_key(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            raise ValueError('Invalid shadow key file')
        data = os.read(fd, 257)
    finally:
        os.close(fd)
    if not re.fullmatch(rb'[A-Za-z0-9_-]{32,128}\n?', data):
        raise ValueError('Invalid shadow key')
    return data.decode('ascii').strip()


def await_key(path: Path, guard: ResourceGuard, timeout: float = 30) -> str:
    """Allow the launcher to create its ephemeral credential inside the tmpfs."""
    deadline = time.monotonic() + timeout
    while True:
        guard.check()
        try:
            return read_key(path)
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise ValueError('Shadow key was not provisioned') from None
            guard.stopping.wait(0.05)


def parse_audio(content_type: str, body: bytes) -> tuple[bytes, str, float]:
    if '\r' in content_type or '\n' in content_type or len(content_type) > 256:
        raise InvalidRequest('Invalid multipart content type')
    message = BytesParser(policy=email.policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    if (message.get_content_type() != 'multipart/form-data' or not message.is_multipart()
            or message.defects or (message.preamble or '').strip() or (message.epilogue or '').strip()):
        raise InvalidRequest('Expected complete multipart form')
    fields = {}
    for part in message.iter_parts():
        name = part.get_param('name', header='Content-Disposition')
        if (part.is_multipart() or part.defects or part.get_content_disposition() != 'form-data'
                or name not in ('file', 'model', 'response_format') or name in fields
                or part.get('Content-Transfer-Encoding') is not None):
            raise InvalidRequest('Unsupported or duplicate multipart field')
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            raise InvalidRequest('Invalid multipart payload')
        if name != 'file' and (len(data) > 128 or part.get_filename() is not None):
            raise InvalidRequest('Invalid transcription option')
        fields[name] = data
    if fields.get('model') != MODEL.encode() or 'file' not in fields:
        raise InvalidRequest('Expected candidate model and audio file')
    response_format = fields.get('response_format', b'verbose_json').decode('ascii')
    if response_format not in ('json', 'verbose_json', 'text'):
        raise InvalidRequest('Unsupported response format')
    data = fields['file']
    if (len(data) < 44 or len(data) > MAX_BODY - 1024 or data[:4] != b'RIFF'
            or data[8:12] != b'WAVE' or int.from_bytes(data[4:8], 'little') + 8 != len(data)):
        raise InvalidRequest('Expected complete PCM WAV')
    # wave.open reads only the first data chunk. Reject additional audio rather
    # than silently validating/transcribing a prefix of a larger RIFF file.
    offset, counts = 12, {b'fmt ': 0, b'data': 0}
    while offset < len(data):
        if offset + 8 > len(data):
            raise InvalidRequest('Incomplete WAV chunk')
        chunk_id = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], 'little')
        offset += 8 + size + size % 2
        if offset > len(data):
            raise InvalidRequest('Incomplete WAV chunk')
        if chunk_id in counts:
            counts[chunk_id] += 1
    if counts != {b'fmt ': 1, b'data': 1}:
        raise InvalidRequest('Expected one WAV format and audio chunk')
    try:
        with wave.open(io.BytesIO(data), 'rb') as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, 'NONE'):
                raise InvalidRequest('Expected mono PCM16 WAV at 16000Hz')
            frames = source.getnframes()
            if frames < 1 or frames * 2 > MAX_PCM:
                raise InvalidRequest('Audio exceeds candidate duration limit')
            pcm = source.readframes(frames + 1)
            if len(pcm) != frames * 2:
                raise InvalidRequest('Incomplete PCM WAV')
    except (wave.Error, EOFError):
        raise InvalidRequest('Invalid PCM WAV') from None
    return pcm, response_format, frames / 16000


class ShadowServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = False

    def __init__(self, address, runtime, key, revision, image_id, guard):
        if address[0] != '127.0.0.1':
            raise ValueError('Only loopback binding is supported')
        if not re.fullmatch('[0-9a-f]{40}', revision) or not re.fullmatch('sha256:[0-9a-f]{64}', image_id):
            raise ValueError('Missing immutable source/image pins')
        self.runtime, self.key = runtime, key
        self.revision, self.image_id, self.guard = revision, image_id, guard
        self.admission = threading.Lock()
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        # Base implementation prints traceback; request-derived errors stay private.
        pass


class Handler(BaseHTTPRequestHandler):
    server: ShadowServer
    protocol_version = 'HTTP/1.1'
    server_version = 'HomeComputeShadow'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(UPLOAD_SECONDS)

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'Invalid shadow request'})

    def reply(self, code, value, *, plain=False, pins=False):
        encoded = value.encode('utf-8') if plain else json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_RESPONSE:
            code, encoded, plain, pins = 502, b'{"error":"Shadow response exceeds limit"}', False, False
        self.close_connection = True
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8' if plain else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        if pins:
            identity = self.server.runtime.identity()
            fields = {'Model-SHA256': identity['model_sha256'], 'Model-Snapshot': identity['model_snapshot'],
                      'Runtime': identity['runtime'], 'Runtime-Commit': identity['runtime_commit'],
                      'HomeCompute-Revision': self.server.revision, 'Image-ID': self.server.image_id,
                      'Device': identity['device']}
            for key, val in fields.items():
                self.send_header('X-HomeCompute-STT-' + key, val)
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self):
        values = self.headers.get_all('Authorization', [])
        return len(values) == 1 and hmac.compare_digest(values[0].encode('utf-8'), ('Bearer ' + self.server.key).encode())

    def do_GET(self):
        if self.path == '/health':
            ready = self.server.runtime.ready and not self.server.guard.failed.is_set() and not self.server.guard.stopping.is_set()
            self.reply(200 if ready else 503, {'status': 'ready' if ready else 'unavailable',
                'inference_busy': self.server.admission.locked(), 'identity': self.server.runtime.identity(),
                'homecompute_revision': self.server.revision, 'image_id': self.server.image_id,
                'minimum_host_available_bytes': self.server.guard.minimum_available})
        elif not self.authorized():
            self.reply(401, {'error': 'Unauthorized'})
        elif self.path == '/v1/models':
            self.reply(200, {'object': 'list', 'data': [{'id': MODEL, 'object': 'model', 'owned_by': 'nvidia'}]})
        else:
            self.reply(404, {'error': 'Unknown shadow endpoint'})

    def do_POST(self):
        if not self.authorized():
            self.reply(401, {'error': 'Unauthorized'})
            return
        if self.path != '/v1/audio/transcriptions':
            self.reply(404, {'error': 'Unknown shadow endpoint'})
            return
        if not self.server.admission.acquire(blocking=False):
            self.reply(409, {'error': 'Shadow inference is busy'})
            return
        try:
            self.server.guard.check()
            lengths = self.headers.get_all('Content-Length', [])
            types = self.headers.get_all('Content-Type', [])
            if (len(lengths) != 1 or not re.fullmatch('[0-9]{1,8}', lengths[0])
                    or self.headers.get_all('Transfer-Encoding') or len(types) != 1):
                raise InvalidRequest('Expected bounded content length')
            remaining = int(lengths[0])
            if not 0 < remaining <= MAX_BODY:
                self.reply(413, {'error': 'Shadow upload exceeds limit'})
                return
            chunks = []
            deadline = time.monotonic() + UPLOAD_SECONDS
            while remaining:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise socket.timeout()
                self.connection.settimeout(left)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    raise InvalidRequest('Incomplete upload')
                chunks.append(chunk)
                remaining -= len(chunk)
            pcm, response_format, duration = parse_audio(types[0], b''.join(chunks))
            chunks.clear()
            request_guard = self.server.guard.request_guard(time.monotonic() + INFERENCE_SECONDS)
            result = self.server.runtime.transcribe_pcm(pcm, sample_rate=16000, guard=request_guard)
            request_guard()
            if not isinstance(result.get('text'), str):
                raise RuntimeInferenceError('Invalid native output')
            if response_format == 'text':
                self.reply(200, result['text'], plain=True, pins=True)
            else:
                value = {'text': result['text']}
                if response_format == 'verbose_json':
                    value.update(segments=[], duration=duration, pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                                 token_ids=result['token_ids'], token_durations=result['token_durations'],
                                 token_timestamps=result['token_timestamps'],
                                 native_token_timing_status='model_predictions_not_qualified_word_alignment',
                                 encoder_frames=result['encoder_frames'],
                                 advanced_encoder_frames=result['advanced_encoder_frames'],
                                 decoder_completed=result['decoder_completed'])
                self.reply(200, value, pins=True)
        except (InvalidRequest, UnicodeError, ValueError):
            self.reply(400, {'error': 'Invalid shadow transcription input'})
        except (TimeoutError, socket.timeout):
            self.reply(408, {'error': 'Shadow upload timed out'})
        except RuntimeBusyError:
            self.reply(409, {'error': 'Shadow inference is busy'})
        except (RuntimeGuardError, RuntimeNotReady):
            self.reply(503, {'error': 'Shadow resource or deadline guard aborted'})
        except Exception:
            self.reply(502, {'error': 'Shadow inference failed'})
        finally:
            self.server.admission.release()


def main():
    guard = ResourceGuard()
    server = None

    def stop(signum, frame):
        guard.stopping.set()
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        guard.start()
        key = await_key(Path(os.environ['SHADOW_KEY_FILE']), guard)
        runtime = ParakeetRuntime(Path(os.environ.get('SHADOW_MODEL_DIR', '/models')),
                                  device=os.environ.get('SHADOW_DEVICE', 'cuda'), guard=guard.check)
        runtime.load()
        guard.check()
        server = ShadowServer(('127.0.0.1', 8080), runtime, key, os.environ['HOME_COMPUTE_REVISION'],
                              os.environ['SHADOW_IMAGE_ID'], guard)
        server.serve_forever(poll_interval=0.1)
    except Exception:
        # Fixed message only. Container logging is disabled by the owning launcher.
        print('Shadow service stopped before successful completion', file=sys.stderr)
        return 1
    finally:
        guard.stopping.set()
        if server is not None:
            server.server_close()
        guard.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
