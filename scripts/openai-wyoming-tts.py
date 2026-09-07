#!/usr/bin/env python3
"""Authenticated OpenAI speech adapter for a Wyoming TTS service."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import http.client
import io
import ipaddress
import json
import os
import socket
import sys
import threading
import uuid
import wave
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error as WyomingError
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

MAX_JSON_BYTES = 64 * 1024
MAX_PCM_BYTES = 32 * 1024 * 1024
MAX_WYOMING_EVENTS = 65_536
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
SYNTHESIS_TIMEOUT_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 65.0
MAX_CONCURRENT_REQUESTS = 4
ALLOWED_FIELDS = {"model", "input", "voice", "response_format", "speed"}


class RequestError(ValueError):
    """A safe client-facing request error."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class UpstreamError(RuntimeError):
    """A Wyoming request failed or returned an unsafe response."""


@dataclass(frozen=True)
class Config:
    bind_address: str
    port: int
    wyoming_uri: str
    api_key: bytes
    model_alias: str
    voice_alias: str
    piper_voice_id: str
    max_input_chars: int


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _integer(name: str, value: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def load_config() -> Config:
    bind_address = os.environ.get("GB10_BIND_ADDRESS", "127.0.0.1")
    address = ipaddress.ip_address(bind_address)
    if address.version != 4 or address.is_multicast:
        raise ValueError("GB10_BIND_ADDRESS must be a non-multicast IPv4 address")
    port = _integer("TTS_HOST_PORT", os.environ.get("TTS_HOST_PORT", "8004"), 1024, 65535)
    max_chars = _integer(
        "TTS_MAX_INPUT_CHARS",
        os.environ.get("TTS_MAX_INPUT_CHARS", "2000"),
        1,
        100_000,
    )
    key_path = Path(_required_env("TTS_API_KEY_FILE"))
    key_text = key_path.read_text(encoding="utf-8").strip()
    if not key_text or any(character.isspace() for character in key_text):
        raise ValueError("TTS_API_KEY_FILE must contain one non-whitespace token")
    aliases = {
        "TTS_MODEL_ALIAS": os.environ.get("TTS_MODEL_ALIAS", "tts"),
        "TTS_VOICE_ALIAS": os.environ.get("TTS_VOICE_ALIAS", "danish-default"),
        "PIPER_VOICE_ID": _required_env("PIPER_VOICE_ID"),
    }
    if any(not value or any(character.isspace() for character in value) for value in aliases.values()):
        raise ValueError("model and voice identifiers must be non-whitespace tokens")
    wyoming_uri = _required_env("WYOMING_URI")
    if not wyoming_uri.startswith(("tcp://", "unix://")):
        raise ValueError("WYOMING_URI must use tcp:// or unix://")
    return Config(
        bind_address=bind_address,
        port=port,
        wyoming_uri=wyoming_uri,
        api_key=key_text.encode("utf-8"),
        model_alias=aliases["TTS_MODEL_ALIAS"],
        voice_alias=aliases["TTS_VOICE_ALIAS"],
        piper_voice_id=aliases["PIPER_VOICE_ID"],
        max_input_chars=max_chars,
    )


def _audio_format(event: Any) -> tuple[int, int, int]:
    audio_format = (event.rate, event.width, event.channels)
    rate, width, channels = audio_format
    if not all(type(value) is int for value in audio_format):
        raise UpstreamError("invalid audio format")
    if not 1 <= rate <= 384_000 or not 1 <= width <= 4 or not 1 <= channels <= 8:
        raise UpstreamError("invalid audio format")
    return audio_format


def _make_wav(audio_format: tuple[int, int, int], pcm: bytes) -> bytes:
    rate, width, channels = audio_format
    if not pcm or len(pcm) % (width * channels):
        raise UpstreamError("invalid PCM audio")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setframerate(rate)
        wav_file.setsampwidth(width)
        wav_file.setnchannels(channels)
        wav_file.writeframes(pcm)
    return output.getvalue()


class WyomingGateway:
    def __init__(
        self,
        uri: str,
        client_factory: Callable[..., Any] = AsyncClient.from_uri,
    ) -> None:
        self.uri = uri
        self.client_factory = client_factory

    def _client(self) -> Any:
        # Wyoming 1.8, the image's declared floor, has no timeout parameters.
        # asyncio.wait_for bounds the complete operation at the adapter boundary.
        return self.client_factory(self.uri)

    async def _synthesize(self, text: str, voice_id: str) -> bytes:
        audio_format: tuple[int, int, int] | None = None
        chunks: list[bytes] = []
        pcm_bytes = 0
        saw_stop = False
        async with self._client() as client:
            await client.write_event(
                Synthesize(text=text, voice=SynthesizeVoice(name=voice_id)).event()
            )
            for _ in range(MAX_WYOMING_EVENTS):
                event = await client.read_event()
                if event is None or WyomingError.is_type(event.type):
                    raise UpstreamError("Wyoming synthesis failed")
                if AudioStart.is_type(event.type):
                    if audio_format is not None or chunks:
                        raise UpstreamError("duplicate audio start")
                    audio_format = _audio_format(AudioStart.from_event(event))
                elif AudioChunk.is_type(event.type):
                    chunk = AudioChunk.from_event(event)
                    chunk_format = _audio_format(chunk)
                    if audio_format is None or chunk_format != audio_format or not chunk.audio:
                        raise UpstreamError("inconsistent audio chunk")
                    pcm_bytes += len(chunk.audio)
                    if pcm_bytes > MAX_PCM_BYTES:
                        raise UpstreamError("Wyoming audio exceeded limit")
                    chunks.append(chunk.audio)
                elif AudioStop.is_type(event.type):
                    saw_stop = True
                    break
                else:
                    raise UpstreamError("unexpected Wyoming event")
        if not saw_stop or audio_format is None:
            raise UpstreamError("incomplete Wyoming audio")
        return _make_wav(audio_format, b"".join(chunks))

    async def _ready(self) -> bool:
        async with self._client() as client:
            await client.write_event(Describe().event())
            event = await client.read_event()
            if event is None or not Info.is_type(event.type):
                return False
            return any(program.installed for program in Info.from_event(event).tts)

    def synthesize(self, text: str, voice_id: str) -> bytes:
        try:
            return asyncio.run(
                asyncio.wait_for(
                    self._synthesize(text, voice_id),
                    timeout=SYNTHESIS_TIMEOUT_SECONDS,
                )
            )
        except (OSError, TimeoutError, asyncio.TimeoutError, ValueError, AssertionError) as error:
            raise UpstreamError("Wyoming synthesis failed") from error

    def ready(self) -> bool:
        try:
            return asyncio.run(
                asyncio.wait_for(self._ready(), timeout=CONNECT_TIMEOUT_SECONDS + READ_TIMEOUT_SECONDS)
            )
        except Exception:
            return False


class AdapterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HomeCompute-TTS"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HTTP_TIMEOUT_SECONDS)

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _request_id(self) -> str:
        request_id = getattr(self, "request_id", None)
        if request_id is None:
            request_id = uuid.uuid4().hex
            self.request_id = request_id
        return request_id

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", self._request_id())
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, document: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        body = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json", headers)

    def _error(self, status: int, code: str, message: str) -> None:
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        self._json(
            status,
            {
                "error": {"message": message, "type": "invalid_request_error", "code": code},
                "request_id": self._request_id(),
            },
            headers,
        )

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined,no-any-return]

    @property
    def gateway(self) -> WyomingGateway:
        return self.server.gateway  # type: ignore[attr-defined,no-any-return]

    def _authenticated(self) -> bool:
        values = self.headers.get_all("Authorization", failobj=[])
        if len(values) != 1:
            return False
        try:
            supplied = values[0].encode("utf-8")
        except UnicodeError:
            return False
        return hmac.compare_digest(supplied, b"Bearer " + self.config.api_key)

    def _read_document(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise RequestError(400, "unsupported_transfer_encoding", "Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1:
            raise RequestError(411, "length_required", "Exactly one Content-Length header is required")
        try:
            length = int(lengths[0])
        except ValueError as error:
            raise RequestError(400, "invalid_content_length", "Content-Length must be an integer") from error
        if length < 1 or length > MAX_JSON_BYTES:
            raise RequestError(413, "request_too_large", "JSON request body exceeds the size limit")
        if self.headers.get_content_type() != "application/json":
            raise RequestError(415, "unsupported_media_type", "Content-Type must be application/json")
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise RequestError(400, "incomplete_body", "Request body is incomplete")
            document = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RequestError(400, "invalid_json", "Request body must be valid UTF-8 JSON") from error
        if not isinstance(document, dict):
            raise RequestError(400, "invalid_schema", "Request body must be a JSON object")
        return document

    def _validate_speech(self, document: dict[str, Any]) -> str:
        unknown = set(document) - ALLOWED_FIELDS
        if unknown:
            raise RequestError(400, "unknown_field", "Request contains unsupported fields")
        if document.get("model") != self.config.model_alias:
            raise RequestError(400, "unsupported_model", "Unsupported speech model")
        if document.get("voice") != self.config.voice_alias:
            raise RequestError(400, "unsupported_voice", "Unsupported speech voice")
        if document.get("response_format") != "wav":
            raise RequestError(400, "unsupported_format", "response_format must be wav")
        speed = document.get("speed", 1.0)
        if type(speed) not in (int, float) or speed != 1.0:
            raise RequestError(400, "unsupported_speed", "speed must be 1.0")
        text = document.get("input")
        if not isinstance(text, str) or not text.strip():
            raise RequestError(400, "invalid_input", "input must be a nonempty string")
        if len(text) > self.config.max_input_chars:
            raise RequestError(400, "input_too_long", "input exceeds TTS_MAX_INPUT_CHARS")
        return text

    def do_GET(self) -> None:
        if self.path == "/health/live":
            self._json(200, {"status": "live", "request_id": self._request_id()})
            return
        if self.path == "/health/ready":
            if self.gateway.ready():
                self._json(200, {"status": "ready", "request_id": self._request_id()})
            else:
                self._error(503, "not_ready", "Wyoming TTS is not ready")
            return
        self._error(404, "not_found", "Route not found")

    def do_POST(self) -> None:
        if self.path != "/v1/audio/speech":
            self._error(404, "not_found", "Route not found")
            return
        if not self._authenticated():
            self.close_connection = True
            self._error(401, "invalid_api_key", "Invalid or missing API key")
            return
        try:
            text = self._validate_speech(self._read_document())
            wav_bytes = self.gateway.synthesize(text, self.config.piper_voice_id)
            self._send_bytes(200, wav_bytes, "audio/wav")
        except RequestError as error:
            self.close_connection = True
            self._error(error.status, error.code, error.message)
        except Exception:
            self._error(502, "tts_upstream_error", "Speech synthesis failed")

    def _method_not_allowed(self) -> None:
        self._error(405, "method_not_allowed", "Method not allowed")

    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_PUT = _method_not_allowed

    def __getattr__(self, name: str) -> Any:
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)


class AdapterHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: Config, gateway: WyomingGateway) -> None:
        self.config = config
        self.gateway = gateway
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(address, AdapterHandler)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request_id = uuid.uuid4().hex.encode("ascii")
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\nCache-Control: no-store\r\nX-Request-ID: "
                    + request_id
                    + b"\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def build_server(config: Config, gateway: WyomingGateway | None = None) -> AdapterHTTPServer:
    return AdapterHTTPServer(
        (config.bind_address, config.port),
        config,
        gateway or WyomingGateway(config.wyoming_uri),
    )


def probe_ready() -> int:
    address = os.environ.get("GB10_BIND_ADDRESS", "127.0.0.1")
    if address == "0.0.0.0":
        address = "127.0.0.1"
    port = _integer("TTS_HOST_PORT", os.environ.get("TTS_HOST_PORT", "8004"), 1024, 65535)
    connection = http.client.HTTPConnection(address, port, timeout=5)
    try:
        connection.request("GET", "/health/ready")
        response = connection.getresponse()
        response.read()
        return 0 if response.status == 200 else 1
    except OSError:
        return 1
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-ready", action="store_true")
    args = parser.parse_args()
    if args.probe_ready:
        return probe_ready()
    try:
        config = load_config()
        server = build_server(config)
    except (OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
