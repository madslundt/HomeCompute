#!/usr/bin/env python3
"""Bounded, logging-free audio normalization for the private Spark STT API."""
from __future__ import annotations

from dataclasses import dataclass, replace
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import wave


MODEL = "whisper-large-v3"
UPSTREAM = "http://whisper-primary:8000"
UPSTREAMS = {MODEL: UPSTREAM}
RECORDING_PATH = "/v1/recording-transcriptions"
RECORDING_UPSTREAM = "http://whisper-cpp-recording:8000"
RECORDING_UPSTREAM_PATH = "/v1/audio/transcriptions"
RECORDING_PROVENANCE_HEADERS = (
    "X-HomeCompute-STT-Model-SHA256",
    "X-HomeCompute-STT-Model-Snapshot",
    "X-HomeCompute-STT-Runtime",
    "X-HomeCompute-STT-Runtime-Commit",
    "X-HomeCompute-STT-HomeCompute-Revision",
    "X-HomeCompute-STT-Image-ID",
)
WHISPER_ALIASES = {b"whisper", b"whisper-large-v3", b"stt-danish", b"whisper-turbo"}
MAX_RESPONSE_BYTES = 2_000_000
FIELD_NAME = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,80}$")


class RelayError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


@dataclass(frozen=True)
class Config:
    port: int = 8007
    upstream: str = UPSTREAM
    ffmpeg: str = "ffmpeg"
    max_upload_bytes: int = 10_000_000
    max_audio_seconds: int = 60
    max_connections: int = 4
    upstream_timeout: int = 120
    transcode_timeout: int = 20
    model: str = MODEL
    recording_upstream: str = RECORDING_UPSTREAM
    recording_authorization_file: Path = Path("/run/secrets/whisper_cpp_api_key")
    recording_max_upload_bytes: int = 40_000_000
    recording_max_audio_seconds: int = 1200
    recording_upstream_timeout: int = 1800


def bounded_env(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    if not raw.isdecimal() or not 1 <= int(raw) <= maximum:
        raise ValueError(f"Invalid {name}")
    return int(raw)


def load_config() -> Config:
    upstream = os.environ.get("STT_UPSTREAM_BASE_URL", UPSTREAM)
    ffmpeg = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    model = os.environ.get("STT_MODEL", MODEL)
    if upstream != UPSTREAMS.get(model) or not ffmpeg or "\x00" in ffmpeg:
        raise ValueError("Invalid upstream or decoder configuration")
    return Config(
        port=bounded_env("STT_RELAY_PORT", 8007, 65535), upstream=upstream, ffmpeg=ffmpeg, model=model,
        max_upload_bytes=bounded_env("STT_MAX_UPLOAD_BYTES", 10_000_000, 10_000_000),
        max_audio_seconds=bounded_env("STT_MAX_AUDIO_SECONDS", 60, 60),
        max_connections=bounded_env("STT_MAX_CONNECTIONS", 4, 4),
        upstream_timeout=bounded_env("STT_UPSTREAM_TIMEOUT_SECONDS", 120, 120),
        transcode_timeout=bounded_env("STT_TRANSCODE_TIMEOUT_SECONDS", 20, 20),
        recording_max_upload_bytes=bounded_env("STT_RECORDING_MAX_UPLOAD_BYTES", 40_000_000, 40_000_000),
        recording_max_audio_seconds=bounded_env("STT_RECORDING_MAX_AUDIO_SECONDS", 1200, 1200),
        recording_upstream_timeout=bounded_env("STT_RECORDING_UPSTREAM_TIMEOUT_SECONDS", 1800, 1800),
    )


def parse_upload(body: bytes, content_type: str, config: Config) -> tuple[list[tuple[str, bytes]], bytes | memoryview]:
    if len(body) > config.max_upload_bytes:
        raise RelayError(413, "Audio upload is too large")
    if any(char in content_type for char in ("\r", "\n")) or len(content_type) > 512:
        raise RelayError(400, "Invalid multipart audio upload")
    try:
        header = BytesParser(policy=policy.default).parsebytes(
            ("MIME-Version: 1.0\r\nContent-Type: " + content_type + "\r\n\r\n").encode("ascii"))
        boundary = header.get_boundary()
    except (ValueError, UnicodeError):
        raise RelayError(400, "Invalid multipart audio upload") from None
    if header.get_content_type() != "multipart/form-data" or not boundary or len(boundary) > 70:
        raise RelayError(400, "Invalid multipart audio upload")
    marker = b"--" + boundary.encode("ascii")
    delimiter = b"\r\n" + marker
    if not body.startswith(marker + b"\r\n"):
        raise RelayError(400, "Invalid multipart audio upload")
    position = len(marker) + 2
    fields, files = [], []
    for _index in range(64):
        header_end = body.find(b"\r\n\r\n", position)
        if header_end < 0 or header_end - position > 8192:
            raise RelayError(400, "Invalid multipart audio upload")
        try:
            part = BytesParser(policy=policy.default).parsebytes(body[position:header_end] + b"\r\n\r\n")
        except (ValueError, UnicodeError):
            raise RelayError(400, "Invalid multipart audio upload") from None
        if part.defects or part.is_multipart() or part.get_content_disposition() != "form-data":
            raise RelayError(400, "Invalid multipart audio upload")
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name):
            raise RelayError(400, "Invalid multipart audio upload")
        if part.get("Content-Transfer-Encoding", "binary").lower() not in {"binary", "7bit", "8bit"}:
            raise RelayError(400, "Invalid multipart audio upload")
        data_start = header_end + 4
        data_end = body.find(delimiter, data_start)
        if data_end < 0:
            raise RelayError(400, "Invalid multipart audio upload")
        data = memoryview(body)[data_start:data_end]
        if name == "file":
            if part.get_filename() is None:
                raise RelayError(400, "Exactly one audio file is required")
            files.append(data)
        else:
            if part.get_filename() is not None or len(data) > 16_384:
                raise RelayError(400, "Invalid transcription parameter")
            fields.append((name, bytes(data)))
        position = data_end + len(delimiter)
        if body[position:position + 2] == b"--":
            position += 2
            if body[position:] not in (b"", b"\r\n"):
                raise RelayError(400, "Invalid multipart audio upload")
            break
        if body[position:position + 2] != b"\r\n":
            raise RelayError(400, "Invalid multipart audio upload")
        position += 2
    else:
        raise RelayError(400, "Invalid multipart audio upload")
    models = [value for name, value in fields if name == "model"]
    accepted = WHISPER_ALIASES if config.model == "whisper-large-v3" else {config.model.encode()}
    if len(models) != 1 or models[0] not in accepted:
        raise RelayError(400, "Unsupported transcription model")
    fields = [(name, config.model.encode() if name == "model" else value) for name, value in fields]
    if len(files) != 1 or not len(files[0]):
        raise RelayError(400, "Exactly one nonempty audio file is required")
    return fields, files[0]


def normalize_audio(audio: bytes, config: Config) -> bytes:
    if len(audio) > config.max_upload_bytes:
        raise RelayError(413, "Audio upload is too large")
    with tempfile.TemporaryDirectory(prefix="homecompute-stt-") as temporary:
        source, output = Path(temporary) / "input.audio", Path(temporary) / "normalized.wav"
        source.write_bytes(audio)
        command = [config.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-protocol_whitelist", "file,pipe",
                   "-format_whitelist", "wav,mp3,mov,ogg,matroska,webm,aac,flac,aiff,amr",
                   "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", "-t", str(config.max_audio_seconds + 1),
                   "-f", "wav", str(output)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=config.transcode_timeout)
        except subprocess.TimeoutExpired:
            raise RelayError(400, "Audio decoding timed out") from None
        except OSError:
            raise RelayError(502, "Audio decoder is unavailable") from None
        if result.returncode or not output.is_file():
            raise RelayError(400, "Invalid or unsupported audio file")
        if output.stat().st_size > (config.max_audio_seconds + 1) * 32_000 + 4096:
            raise RelayError(413, "Audio duration exceeds the limit")
        try:
            with wave.open(str(output), "rb") as decoded:
                if decoded.getnchannels() != 1 or decoded.getsampwidth() != 2 or decoded.getframerate() != 16000:
                    raise RelayError(400, "Invalid decoded audio")
                if decoded.getnframes() > config.max_audio_seconds * 16000:
                    raise RelayError(413, "Audio duration exceeds the limit")
                if decoded.getnframes() == 0:
                    raise RelayError(400, "Audio file contains no samples")
        except (wave.Error, EOFError, OSError):
            raise RelayError(400, "Invalid decoded audio") from None
        return output.read_bytes()


def encode_upload(fields: list[tuple[str, bytes]], audio: bytes) -> tuple[bytes, str]:
    boundary = "homecompute-" + secrets.token_hex(16)
    parts = []
    for name, value in fields:
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                      value, b"\r\n"])
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
                  audio, f"\r\n--{boundary}--\r\n".encode()])
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def upstream_request(config: Config, path: str, auth: str | None, body: bytes | None = None,
                     content_type: str | None = None) -> tuple[int, bytes, str]:
    headers = {"User-Agent": "homecompute-stt-relay/1"}
    if auth:
        if "\r" in auth or "\n" in auth:
            raise RelayError(400, "Invalid authorization header")
        headers["Authorization"] = auth
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(config.upstream + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=config.upstream_timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise RelayError(502, "Speech backend response is too large")
            return response.status, payload, response.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as error:
        status = error.code if error.code in {400, 401, 403, 404, 413, 422, 429} else 502
        error.close()
        raise RelayError(status, "Speech backend rejected the request") from None
    except (TimeoutError, socket.timeout):
        raise RelayError(504, "Speech backend timed out") from None
    except urllib.error.URLError as error:
        status = 504 if isinstance(error.reason, (TimeoutError, socket.timeout)) else 502
        raise RelayError(status, "Speech backend is unavailable") from None
    except OSError:
        raise RelayError(502, "Speech backend is unavailable") from None


def recording_key(config: Config) -> bytes:
    path = config.recording_authorization_file
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 10001 or
                stat.S_IMODE(info.st_mode) != 0o440 or not 16 <= info.st_size <= 512):
            raise ValueError
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            key = stream.read(513)
    except (OSError, ValueError):
        raise RelayError(503, "Recording recognition is unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not key or any(byte < 0x21 or byte > 0x7e for byte in key):
        raise RelayError(503, "Recording recognition is unavailable")
    return key


def validate_recording_pcm(audio: bytes | memoryview, config: Config) -> None:
    if len(audio) < 44 or len(audio) > config.recording_max_upload_bytes:
        raise RelayError(413 if len(audio) > config.recording_max_upload_bytes else 400,
                         "Recording audio is invalid or exceeds the limit")
    try:
        if (audio[0:4] != b"RIFF" or audio[8:12] != b"WAVE" or
                audio[12:16] != b"fmt " or audio[16:20] != b"\x10\x00\x00\x00" or
                struct.unpack_from("<HHIIHH", audio, 20) != (1, 1, 16000, 32000, 2, 16) or
                audio[36:40] != b"data"):
            raise RelayError(400, "Recording must be complete mono 16 kHz PCM16 WAV")
        riff_size = struct.unpack_from("<I", audio, 4)[0]
        data_size = struct.unpack_from("<I", audio, 40)[0]
        frames = data_size // 2
        if (riff_size != len(audio) - 8 or data_size % 2 or len(audio) != data_size + 44 or
                frames < 1 or frames > config.recording_max_audio_seconds * 16000):
            raise RelayError(400, "Recording must be complete mono 16 kHz PCM16 WAV")
    except (struct.error, TypeError, ValueError):
        raise RelayError(400, "Recording must be complete mono 16 kHz PCM16 WAV") from None


def upstream_recording_request(config: Config, auth: str, body: bytes,
                               content_type: str) -> tuple[int, bytes, str, dict[str, str]]:
    request = urllib.request.Request(
        config.recording_upstream + RECORDING_UPSTREAM_PATH,
        data=body,
        headers={"User-Agent": "homecompute-stt-relay/1", "Authorization": auth,
                 "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.recording_upstream_timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise RelayError(502, "Speech backend response is too large")
            headers = {name: response.headers[name] for name in RECORDING_PROVENANCE_HEADERS
                       if response.headers.get(name)}
            if len(headers) != len(RECORDING_PROVENANCE_HEADERS):
                raise RelayError(502, "Speech backend provenance is incomplete")
            return response.status, payload, response.headers.get("Content-Type", "application/json"), headers
    except urllib.error.HTTPError as error:
        status = error.code if error.code in {400, 401, 403, 404, 413, 422, 429} else 502
        error.close()
        raise RelayError(status, "Speech backend rejected the request") from None
    except (TimeoutError, socket.timeout):
        raise RelayError(504, "Speech backend timed out") from None
    except urllib.error.URLError as error:
        status = 504 if isinstance(error.reason, (TimeoutError, socket.timeout)) else 502
        raise RelayError(status, "Speech backend is unavailable") from None
    except OSError:
        raise RelayError(502, "Speech backend is unavailable") from None


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], config: Config):
        self.config = config
        self.inflight = threading.BoundedSemaphore(config.max_connections)
        super().__init__(address, RelayHandler)

    def handle_error(self, request, client_address):
        pass


class RelayHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self, *args):
        pass

    def log_error(self, *args):
        pass

    def send_payload(self, status: int, payload: bytes, content_type: str = "application/json",
                     extra_headers: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            if name in RECORDING_PROVENANCE_HEADERS and "\r" not in value and "\n" not in value:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def serve_request(self, transcribe: bool):
        acquired = False
        candidate_key = None
        try:
            recording = transcribe and self.path == RECORDING_PATH
            allowed = "/v1/audio/transcriptions" if transcribe and not recording else RECORDING_PATH
            if transcribe and self.path not in {"/v1/audio/transcriptions", RECORDING_PATH} or not transcribe and self.path not in {"/health", "/v1/models"}:
                raise RelayError(404, "Route not found")
            acquired = self.server.inflight.acquire(blocking=False)
            if not acquired:
                raise RelayError(429, "Speech service is busy")
            config = self.server.config
            auth_values = self.headers.get_all("Authorization", [])
            if len(auth_values) > 1:
                raise RelayError(400, "Invalid authorization header")
            auth = auth_values[0] if auth_values else None
            if transcribe:
                if recording:
                    candidate_key = recording_key(config)
                    expected_auth = b"Bearer " + candidate_key
                    try:
                        supplied_auth = auth.encode("ascii") if auth else b""
                    except UnicodeError:
                        supplied_auth = b""
                    if not secrets.compare_digest(expected_auth, supplied_auth):
                        raise RelayError(401, "Invalid authorization")
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdecimal():
                    raise RelayError(400, "A bounded Content-Length is required")
                length = int(lengths[0])
                upload_limit = config.recording_max_upload_bytes if recording else config.max_upload_bytes
                if length > upload_limit:
                    raise RelayError(413, "Audio upload is too large")
                body = self.rfile.read(length)
                if len(body) != length:
                    raise RelayError(400, "Incomplete audio upload")
                parse_config = replace(config, max_upload_bytes=upload_limit) if recording else config
                fields, audio = parse_upload(body, self.headers.get("Content-Type", ""), parse_config)
                if recording:
                    validate_recording_pcm(audio, config)
                    result = upstream_recording_request(config, "Bearer " + candidate_key.decode("ascii"),
                                                        body, self.headers.get("Content-Type", ""))
                else:
                    body, content_type = encode_upload(fields, normalize_audio(bytes(audio), config))
                    result = upstream_request(config, allowed, auth, body, content_type)
            else:
                result = upstream_request(config, self.path, auth)
            self.send_payload(*result)
        except RelayError as error:
            try:
                self.send_payload(error.status, json.dumps({"error": {"message": error.message}}).encode())
            except (BrokenPipeError, ConnectionError, OSError):
                pass
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        except Exception:
            try:
                self.send_payload(500, b'{"error":{"message":"Speech relay failed"}}')
            except OSError:
                pass
        finally:
            if acquired:
                self.server.inflight.release()

    def do_GET(self):
        self.serve_request(False)

    def do_POST(self):
        self.serve_request(True)


def main() -> int:
    try:
        config = load_config()
        if sys.argv[1:] == ["--probe-upstream"]:
            upstream_request(config, "/health", None)
            return 0
        if sys.argv[1:]:
            return 2
        with RelayServer(("0.0.0.0", config.port), config) as server:
            server.serve_forever()
    except (ValueError, RelayError, OSError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
