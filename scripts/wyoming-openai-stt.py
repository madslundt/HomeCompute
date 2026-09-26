#!/usr/bin/env python3
"""Bounded Wyoming STT adapter for an OpenAI-compatible transcription API.

The adapter intentionally uses only the Python standard library so it can run
inside the pinned vLLM image without installing mutable dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import secrets
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


HEADER_LIMIT = 65_536
WYOMING_VERSION = "1.10.2"


class ProtocolError(Exception):
    """A request violated the narrow Wyoming adapter contract."""


@dataclass(frozen=True)
class Config:
    bind_address: str
    port: int
    upstream_url: str
    api_key: bytes
    model: str
    language: str
    max_audio_bytes: int
    max_audio_seconds: int
    upstream_timeout_seconds: int
    max_connections: int


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    payload: bytes = b""


def event_bytes(event_type: str, data: dict[str, Any] | None = None) -> bytes:
    """Encode an event using the official Wyoming data_length framing."""
    header: dict[str, Any] = {"type": event_type, "version": WYOMING_VERSION}
    data_bytes = b""
    if data:
        data_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        header["data_length"] = len(data_bytes)
    return json.dumps(header, separators=(",", ":")).encode() + b"\n" + data_bytes


async def read_event(reader: asyncio.StreamReader, max_payload: int) -> Event | None:
    line = await reader.readline()
    if not line:
        return None
    if len(line) > HEADER_LIMIT or not line.endswith(b"\n"):
        raise ProtocolError("event header is too large")
    try:
        header = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("event header is not valid JSON") from exc
    if not isinstance(header, dict) or not isinstance(header.get("type"), str):
        raise ProtocolError("event type is required")
    data = header.get("data", {})
    if not isinstance(data, dict):
        raise ProtocolError("event data must be an object")
    data_length = header.get("data_length", 0)
    payload_length = header.get("payload_length", 0)
    for label, value, limit in (
        ("data_length", data_length, HEADER_LIMIT),
        ("payload_length", payload_length, max_payload),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > limit:
            raise ProtocolError(f"invalid {label}")
    if data_length:
        try:
            separate_data = json.loads(await reader.readexactly(data_length))
        except (asyncio.IncompleteReadError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("event data is invalid") from exc
        if not isinstance(separate_data, dict):
            raise ProtocolError("event data must be an object")
        data.update(separate_data)
    try:
        payload = await reader.readexactly(payload_length) if payload_length else b""
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("event payload ended early") from exc
    return Event(header["type"], data, payload)


def info_event(model: str, language: str) -> bytes:
    attribution = {"name": "SYVAI", "url": "https://huggingface.co/syvai/hviske-v5.3"}
    artifact = {
        "name": model,
        "description": "Pinned Danish Hviske speech recognition",
        "version": None,
        "attribution": attribution,
        "installed": True,
        "languages": [language],
    }
    program = {
        "name": "hviske-vllm",
        "description": "HomeCompute Wyoming to vLLM transcription adapter",
        "version": "1",
        "attribution": attribution,
        "installed": True,
        "models": [artifact],
        "supports_transcript_streaming": False,
        "requires_external_vad": True,
        "prefers_auto_gain_enabled": True,
        "prefers_noise_reduction_enabled": True,
    }
    return event_bytes("info", {"asr": [program], "tts": [], "handle": [], "intent": [], "wake": [], "mic": [], "snd": []})


def wav_bytes(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setframerate(rate)
        stream.setsampwidth(width)
        stream.setnchannels(channels)
        stream.writeframes(pcm)
    return output.getvalue()


def multipart_body(model: str, language: str, audio: bytes) -> tuple[bytes, str]:
    boundary = f"homecompute-{secrets.token_hex(16)}"
    fields = (("model", model), ("language", language), ("temperature", "0"))
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


class OpenAITranscriber:
    def __init__(self, config: Config) -> None:
        self.config = config

    def transcribe(self, audio: bytes) -> str:
        body, boundary = multipart_body(self.config.model, self.config.language, audio)
        request = urllib.request.Request(
            self.config.upstream_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key.decode()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "homecompute-wyoming-stt/1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.upstream_timeout_seconds) as response:
                response_body = response.read(1_048_577)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProtocolError("transcription service is unavailable") from exc
        if len(response_body) > 1_048_576:
            raise ProtocolError("transcription response is too large")
        try:
            document = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("transcription response is invalid") from exc
        text = document.get("text") if isinstance(document, dict) else None
        if not isinstance(text, str):
            raise ProtocolError("transcription response has no text")
        return text


class WyomingSession:
    def __init__(self, config: Config, transcribe: Callable[[bytes], Awaitable[str]]) -> None:
        self.config = config
        self.transcribe = transcribe
        self.active = False
        self.rate: int | None = None
        self.width: int | None = None
        self.channels: int | None = None
        self.audio = bytearray()

    def reset(self) -> None:
        self.active = False
        self.rate = self.width = self.channels = None
        self.audio.clear()

    async def process(self, event: Event) -> bytes | None:
        if event.type == "describe":
            return info_event(self.config.model, self.config.language)
        if event.type == "transcribe":
            requested_language = event.data.get("language")
            requested_model = event.data.get("name")
            if requested_language not in (None, self.config.language, "da-DK"):
                raise ProtocolError("only Danish transcription is supported")
            if requested_model not in (None, self.config.model, "hviske-vllm", "stt-danish"):
                raise ProtocolError("unknown transcription model")
            self.reset()
            self.active = True
            return None
        if event.type == "audio-start":
            if not self.active:
                raise ProtocolError("transcribe must precede audio")
            self._set_format(event.data)
            return None
        if event.type == "audio-chunk":
            if not self.active:
                raise ProtocolError("transcribe must precede audio")
            self._set_format(event.data)
            if len(self.audio) + len(event.payload) > self.config.max_audio_bytes:
                raise ProtocolError("audio exceeds the configured byte limit")
            self.audio.extend(event.payload)
            return None
        if event.type == "audio-stop":
            if not self.active:
                raise ProtocolError("transcribe must precede audio")
            if not self.audio:
                self.reset()
                return event_bytes("transcript", {"text": "", "language": self.config.language})
            assert self.rate is not None and self.width is not None and self.channels is not None
            seconds = len(self.audio) / (self.rate * self.width * self.channels)
            if seconds > self.config.max_audio_seconds:
                raise ProtocolError("audio exceeds the configured duration limit")
            if len(self.audio) % (self.width * self.channels):
                raise ProtocolError("audio payload does not contain complete frames")
            audio = wav_bytes(bytes(self.audio), self.rate, self.width, self.channels)
            self.reset()
            text = await self.transcribe(audio)
            return event_bytes("transcript", {"text": text, "language": self.config.language})
        return None

    def _set_format(self, data: dict[str, Any]) -> None:
        try:
            audio_format = (int(data["rate"]), int(data["width"]), int(data["channels"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("audio format is required") from exc
        rate, width, channels = audio_format
        if rate < 8_000 or rate > 48_000 or width not in (1, 2, 3, 4) or channels not in (1, 2):
            raise ProtocolError("unsupported audio format")
        if self.rate is None:
            self.rate, self.width, self.channels = audio_format
        elif audio_format != (self.rate, self.width, self.channels):
            raise ProtocolError("audio format changed within one request")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: Config, gateway: OpenAITranscriber) -> None:
    async def transcribe(audio: bytes) -> str:
        return await asyncio.to_thread(gateway.transcribe, audio)

    session = WyomingSession(config, transcribe)
    try:
        while event := await read_event(reader, config.max_audio_bytes):
            try:
                response = await session.process(event)
            except ProtocolError as exc:
                response = event_bytes("error", {"text": str(exc), "code": "invalid-request"})
                session.reset()
            if response:
                writer.write(response)
                await writer.drain()
    except (ProtocolError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


def load_config() -> Config:
    key_path = Path(os.environ["STT_API_KEY_FILE"])
    api_key = key_path.read_bytes().strip()
    if not api_key or b"\n" in api_key or b"\r" in api_key:
        raise SystemExit("STT_API_KEY_FILE must contain one non-empty line")
    try:
        api_key.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit("STT_API_KEY_FILE must contain UTF-8 text") from exc
    config = Config(
        bind_address=os.environ.get("WYOMING_BIND_ADDRESS", "0.0.0.0"),
        port=int(os.environ.get("WYOMING_PORT", "10301")),
        upstream_url=os.environ.get("STT_UPSTREAM_URL", "http://hviske-primary:8000/v1/audio/transcriptions"),
        api_key=api_key,
        model=os.environ.get("STT_MODEL", "stt-danish"),
        language=os.environ.get("STT_LANGUAGE", "da"),
        max_audio_bytes=int(os.environ.get("STT_MAX_AUDIO_BYTES", "10000000")),
        max_audio_seconds=int(os.environ.get("STT_MAX_AUDIO_SECONDS", "60")),
        upstream_timeout_seconds=int(os.environ.get("STT_UPSTREAM_TIMEOUT_SECONDS", "120")),
        max_connections=int(os.environ.get("STT_MAX_CONNECTIONS", "4")),
    )
    if config.bind_address != "0.0.0.0" or config.port != 10301:
        raise SystemExit("adapter listener must be 0.0.0.0:10301 inside its container")
    if config.upstream_url != "http://hviske-primary:8000/v1/audio/transcriptions":
        raise SystemExit("unexpected transcription upstream")
    if config.model != "stt-danish" or config.language != "da":
        raise SystemExit("adapter supports only the pinned Danish model alias")
    if (
        config.max_audio_bytes != 10_000_000
        or config.max_audio_seconds != 60
        or config.upstream_timeout_seconds != 120
        or config.max_connections != 4
    ):
        raise SystemExit("adapter request bounds differ from the reviewed tuple")
    return config


async def main_async(config: Config) -> None:
    gateway = OpenAITranscriber(config)
    semaphore = asyncio.Semaphore(config.max_connections)

    async def limited_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async with semaphore:
            await handle_client(reader, writer, config, gateway)

    server = await asyncio.start_server(
        limited_client,
        config.bind_address,
        config.port,
        limit=HEADER_LIMIT + 1,
        backlog=config.max_connections,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-ready", action="store_true")
    args = parser.parse_args()
    config = load_config()
    if args.probe_ready:
        async def probe() -> None:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", config.port), 5)
            writer.write(event_bytes("describe")); await writer.drain()
            event = await asyncio.wait_for(read_event(reader, HEADER_LIMIT), 5)
            writer.close(); await writer.wait_closed()
            if event is None or event.type != "info" or not event.data.get("asr"):
                raise SystemExit("Wyoming readiness probe failed")
        asyncio.run(probe())
        return
    asyncio.run(main_async(config))


if __name__ == "__main__":
    main()
