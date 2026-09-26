#!/usr/bin/env python3
"""Narrow Wyoming TTS facade for Plapre's private raw-PCM HTTP API."""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import logging
import subprocess
from dataclasses import dataclass
from functools import partial
from urllib.parse import urlsplit

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop, SynthesizeStopped


LOGGER = logging.getLogger("plapre_wyoming")
SAMPLE_RATE = 24_000
SAMPLE_WIDTH = 2
CHANNELS = 1


class UpstreamError(RuntimeError):
    """The private Plapre service did not satisfy its pinned contract."""


@dataclass(frozen=True)
class Config:
    uri: str
    upstream_url: str
    voice_alias: str
    speaker_id: str
    tempo: float
    max_input_chars: int
    max_pcm_bytes: int
    timeout_seconds: int
    samples_per_chunk: int


def build_info(config: Config) -> Info:
    attribution = Attribution(name="SYV AI", url="https://huggingface.co/syvai/plapre-nano-v2")
    return Info(
        tts=[TtsProgram(
            name="plapre-nano-v2",
            attribution=attribution,
            installed=True,
            description="Pinned local Danish Plapre Nano v2 service",
            version="007e0b471e37",
            voices=[TtsVoice(
                name=config.voice_alias,
                attribution=attribution,
                installed=True,
                description="Approved Danish household voice alias",
                version="1",
                languages=["da", "da_DK"],
            )],
            supports_synthesize_streaming=True,
        )]
    )


def read_pcm_response(response: http.client.HTTPResponse, max_pcm_bytes: int) -> bytes:
    if response.status != 200:
        response.read(min(max_pcm_bytes + 1, 65_536))
        raise UpstreamError("Plapre returned a non-success response")
    expected_headers = {
        "Content-Type": "audio/pcm",
        "X-Sample-Rate": str(SAMPLE_RATE),
        "X-Channels": str(CHANNELS),
        "X-Bit-Depth": str(SAMPLE_WIDTH * 8),
    }
    for name, expected in expected_headers.items():
        actual = response.getheader(name)
        if name == "Content-Type" and actual:
            actual = actual.split(";", 1)[0].strip().lower()
        if actual != expected:
            raise UpstreamError(f"Plapre returned invalid {name}")
    pcm = response.read(max_pcm_bytes + 1)
    if len(pcm) > max_pcm_bytes:
        raise UpstreamError("Plapre audio exceeded the configured limit")
    if not pcm or len(pcm) % (SAMPLE_WIDTH * CHANNELS):
        raise UpstreamError("Plapre returned invalid PCM length")
    return pcm


def adjust_tempo(pcm: bytes, tempo: float, timeout_seconds: int, max_pcm_bytes: int) -> bytes:
    """Apply a pitch-preserving tempo change while retaining the PCM contract."""
    if tempo == 1.0:
        return pcm
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "-i", "pipe:0", "-filter:a", f"atempo={tempo:.6f}",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "pipe:1",
            ],
            input=pcm,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpstreamError("Plapre tempo adjustment failed") from error
    if result.returncode != 0 or not result.stdout:
        raise UpstreamError("Plapre tempo adjustment failed")
    if len(result.stdout) > max_pcm_bytes or len(result.stdout) % (SAMPLE_WIDTH * CHANNELS):
        raise UpstreamError("Plapre tempo adjustment returned invalid PCM")
    return result.stdout


class PlapreHttpClient:
    def __init__(self, base_url: str, timeout_seconds: int, max_pcm_bytes: int, tempo: float) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("PLAPRE_UPSTREAM_URL must be an unauthenticated http URL")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("PLAPRE_UPSTREAM_URL must not include a path, query, or fragment")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.timeout_seconds = timeout_seconds
        self.max_pcm_bytes = max_pcm_bytes
        self.tempo = tempo

    def _synthesize(self, text: str, speaker: str) -> bytes:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_seconds)
        body = json.dumps({"text": text, "speaker": speaker}, ensure_ascii=False).encode("utf-8")
        try:
            connection.request(
                "POST",
                "/v1/audio/speech",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            pcm = read_pcm_response(connection.getresponse(), self.max_pcm_bytes)
            return adjust_tempo(pcm, self.tempo, self.timeout_seconds, self.max_pcm_bytes)
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise UpstreamError("Plapre request failed") from error
        finally:
            connection.close()

    async def synthesize(self, text: str, speaker: str) -> bytes:
        return await asyncio.to_thread(self._synthesize, text, speaker)


class PlapreEventHandler(AsyncEventHandler):
    def __init__(self, client: PlapreHttpClient, config: Config, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.client = client
        self.config = config
        self.streaming = False
        self.stream_text: list[str] = []
        self.stream_voice: str | None = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(build_info(self.config).event())
            return True
        try:
            if SynthesizeStart.is_type(event.type):
                request = SynthesizeStart.from_event(event)
                self.streaming = True
                self.stream_text = []
                self.stream_voice = getattr(getattr(request, "voice", None), "name", None)
                return True
            if SynthesizeChunk.is_type(event.type):
                if self.streaming:
                    self.stream_text.append(SynthesizeChunk.from_event(event).text)
                return True
            if SynthesizeStop.is_type(event.type):
                text = "".join(self.stream_text)
                voice = self.stream_voice
                self.streaming, self.stream_text, self.stream_voice = False, [], None
                await self.synthesize(text, voice)
                await self.write_event(SynthesizeStopped().event())
                return True
            if Synthesize.is_type(event.type) and not self.streaming:
                request = Synthesize.from_event(event)
                await self.synthesize(request.text, getattr(getattr(request, "voice", None), "name", None))
            return True
        except ValueError as error:
            LOGGER.warning("Rejected Wyoming synthesis request: %s", error.args[0])
            await self.write_event(Error(text="The requested voice or text is not allowed", code=error.args[0]).event())
            return True
        except Exception:
            # Upstream exception text may contain an HTTP body. Keep both logs and
            # Wyoming responses independent of household text and upstream detail.
            LOGGER.error("Plapre synthesis failed")
            await self.write_event(Error(text="The local TTS service is unavailable", code="tts_upstream_error").event())
            return True

    async def synthesize(self, text: str, voice: str | None) -> None:
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("invalid_input")
        if len(normalized) > self.config.max_input_chars:
            raise ValueError("input_too_long")
        if voice not in (None, "", self.config.voice_alias):
            raise ValueError("unsupported_voice")
        pcm = await self.client.synthesize(normalized, self.config.speaker_id)
        bytes_per_chunk = self.config.samples_per_chunk * SAMPLE_WIDTH * CHANNELS
        await self.write_event(AudioStart(rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=CHANNELS).event())
        for offset in range(0, len(pcm), bytes_per_chunk):
            await self.write_event(AudioChunk(
                rate=SAMPLE_RATE,
                width=SAMPLE_WIDTH,
                channels=CHANNELS,
                audio=pcm[offset:offset + bytes_per_chunk],
            ).event())
        await self.write_event(AudioStop().event())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="tcp://0.0.0.0:10201")
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--voice-alias", required=True)
    parser.add_argument("--speaker-id", required=True)
    parser.add_argument("--tempo", type=float, default=1.0)
    parser.add_argument("--max-input-chars", type=int, default=2_000)
    parser.add_argument("--max-pcm-bytes", type=int, default=48_000_000)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--samples-per-chunk", type=int, default=4_096)
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    config = Config(
        uri=args.uri,
        upstream_url=args.upstream_url,
        voice_alias=args.voice_alias,
        speaker_id=args.speaker_id,
        tempo=args.tempo,
        max_input_chars=args.max_input_chars,
        max_pcm_bytes=args.max_pcm_bytes,
        timeout_seconds=args.timeout_seconds,
        samples_per_chunk=args.samples_per_chunk,
    )
    if min(config.max_input_chars, config.max_pcm_bytes, config.timeout_seconds, config.samples_per_chunk) <= 0:
        raise ValueError("numeric limits must be positive")
    if not 0.5 <= config.tempo <= 2.0:
        raise ValueError("tempo must be between 0.5 and 2.0")
    client = PlapreHttpClient(
        config.upstream_url, config.timeout_seconds, config.max_pcm_bytes, config.tempo
    )
    await AsyncServer.from_uri(config.uri).run(partial(PlapreEventHandler, client, config))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    main()
