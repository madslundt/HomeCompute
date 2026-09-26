#!/usr/bin/env python3
"""Behavioral tests for the direct Plapre-to-Wyoming adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any


class Event:
    def __init__(self, event_type: str, data: dict[str, Any] | None = None, payload: bytes | None = None) -> None:
        self.type = event_type
        self.data = data or {}
        self.payload = payload


class EventValue:
    event_type = ""

    @classmethod
    def is_type(cls, event_type: str) -> bool:
        return event_type == cls.event_type

    def event(self) -> Event:
        return Event(self.event_type, vars(self))


class AudioStart(EventValue):
    event_type = "audio-start"

    def __init__(self, rate: int, width: int, channels: int) -> None:
        self.rate, self.width, self.channels = rate, width, channels


class AudioChunk(AudioStart):
    event_type = "audio-chunk"

    def __init__(self, rate: int, width: int, channels: int, audio: bytes) -> None:
        super().__init__(rate, width, channels)
        self.audio = audio

    def event(self) -> Event:
        return Event(self.event_type, {"rate": self.rate, "width": self.width, "channels": self.channels}, self.audio)


class AudioStop(EventValue):
    event_type = "audio-stop"


class Error(EventValue):
    event_type = "error"

    def __init__(self, text: str, code: str) -> None:
        self.text, self.code = text, code


class Describe(EventValue):
    event_type = "describe"


class Info:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)

    def event(self) -> Event:
        return Event("info", {"tts": [program.__dict__ for program in self.tts]})


class Struct:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class Synthesize(EventValue):
    event_type = "synthesize"

    @classmethod
    def from_event(cls, event: Event) -> Any:
        voice = event.data.get("voice")
        return Struct(text=event.data["text"], voice=Struct(name=voice["name"]) if voice else None)


class SynthesizeStart(Synthesize):
    event_type = "synthesize-start"


class SynthesizeChunk(Synthesize):
    event_type = "synthesize-chunk"


class SynthesizeStop(EventValue):
    event_type = "synthesize-stop"


class SynthesizeStopped(EventValue):
    event_type = "synthesize-stopped"


class AsyncEventHandler:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.writes: list[Event] = []

    async def write_event(self, event: Event) -> None:
        self.writes.append(event)


class AsyncServer:
    @staticmethod
    def from_uri(_uri: str) -> Any:
        return Struct(run=lambda _handler: None)


def install_fake_wyoming() -> None:
    modules = {name: types.ModuleType(name) for name in (
        "wyoming", "wyoming.audio", "wyoming.error", "wyoming.event", "wyoming.info", "wyoming.server", "wyoming.tts"
    )}
    modules["wyoming.audio"].AudioChunk = AudioChunk
    modules["wyoming.audio"].AudioStart = AudioStart
    modules["wyoming.audio"].AudioStop = AudioStop
    modules["wyoming.error"].Error = Error
    modules["wyoming.event"].Event = Event
    modules["wyoming.info"].Attribution = Struct
    modules["wyoming.info"].Describe = Describe
    modules["wyoming.info"].Info = Info
    modules["wyoming.info"].TtsProgram = Struct
    modules["wyoming.info"].TtsVoice = Struct
    modules["wyoming.server"].AsyncEventHandler = AsyncEventHandler
    modules["wyoming.server"].AsyncServer = AsyncServer
    modules["wyoming.tts"].Synthesize = Synthesize
    modules["wyoming.tts"].SynthesizeChunk = SynthesizeChunk
    modules["wyoming.tts"].SynthesizeStart = SynthesizeStart
    modules["wyoming.tts"].SynthesizeStop = SynthesizeStop
    modules["wyoming.tts"].SynthesizeStopped = SynthesizeStopped
    sys.modules.update(modules)


install_fake_wyoming()
ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "deploy" / "compute-node" / "plapre" / "wyoming_adapter.py"
SPEC = importlib.util.spec_from_file_location("plapre_wyoming", ADAPTER_PATH)
assert SPEC and SPEC.loader
ADAPTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTER
SPEC.loader.exec_module(ADAPTER)


class FakeClient:
    def __init__(self, result: bytes = b"\x01\x02\x03\x04", error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.calls: list[tuple[str, str]] = []

    async def synthesize(self, text: str, speaker: str) -> bytes:
        self.calls.append((text, speaker))
        if self.error:
            raise self.error
        return self.result


class PlapreWyomingTest(unittest.TestCase):
    def config(self) -> Any:
        return ADAPTER.Config(
            uri="tcp://0.0.0.0:10201",
            upstream_url="http://plapre-primary:8004",
            voice_alias="danish-default",
            speaker_id="tor",
            tempo=1.25,
            max_input_chars=40,
            max_pcm_bytes=1024,
            timeout_seconds=30,
            samples_per_chunk=2,
        )

    def test_info_exposes_only_approved_alias_not_internal_speaker(self) -> None:
        info = ADAPTER.build_info(self.config())
        self.assertEqual([voice.name for voice in info.tts[0].voices], ["danish-default"])
        self.assertNotIn("tor", json.dumps(info.tts[0].__dict__, default=lambda value: value.__dict__))

    def test_synthesize_maps_alias_to_internal_speaker_and_emits_pcm(self) -> None:
        client = FakeClient()
        handler = ADAPTER.PlapreEventHandler(client, self.config())
        event = Event("synthesize", {"text": "  Hej\nverden  ", "voice": {"name": "danish-default"}})
        self.assertTrue(asyncio.run(handler.handle_event(event)))
        self.assertEqual(client.calls, [("Hej verden", "tor")])
        self.assertEqual([item.type for item in handler.writes], ["audio-start", "audio-chunk", "audio-stop"])
        self.assertEqual(handler.writes[1].payload, b"\x01\x02\x03\x04")

    def test_unknown_voice_and_upstream_failure_are_generic(self) -> None:
        client = FakeClient(error=ADAPTER.UpstreamError("PRIVATE upstream body"))
        handler = ADAPTER.PlapreEventHandler(client, self.config())
        unknown = Event("synthesize", {"text": "hemmeligt", "voice": {"name": "other"}})
        asyncio.run(handler.handle_event(unknown))
        upstream = Event("synthesize", {"text": "hemmeligt", "voice": {"name": "danish-default"}})
        asyncio.run(handler.handle_event(upstream))
        errors = [item for item in handler.writes if item.type == "error"]
        self.assertEqual([item.data["code"] for item in errors], ["unsupported_voice", "tts_upstream_error"])
        self.assertNotIn("PRIVATE", json.dumps([item.data for item in errors]))
        self.assertNotIn("hemmeligt", json.dumps([item.data for item in errors]))

    def test_http_contract_requires_exact_raw_pcm_metadata(self) -> None:
        response = Struct(
            status=200,
            getheader=lambda name: {
                "Content-Type": "audio/pcm",
                "X-Sample-Rate": "24000",
                "X-Channels": "1",
                "X-Bit-Depth": "16",
            }.get(name),
            read=lambda limit: b"\x00\x01" if limit >= 2 else b"",
        )
        self.assertEqual(ADAPTER.read_pcm_response(response, 32), b"\x00\x01")
        response.getheader = lambda name: "22050" if name == "X-Sample-Rate" else {
            "Content-Type": "audio/pcm", "X-Channels": "1", "X-Bit-Depth": "16"
        }.get(name)
        with self.assertRaises(ADAPTER.UpstreamError):
            ADAPTER.read_pcm_response(response, 32)

    def test_tempo_adjustment_is_pitch_preserving_and_keeps_pcm_contract(self) -> None:
        completed = types.SimpleNamespace(returncode=0, stdout=b"\x01\x02", stderr=b"")
        with patch.object(ADAPTER.subprocess, "run", return_value=completed) as run:
            self.assertEqual(ADAPTER.adjust_tempo(b"\x00\x00\x00\x00", 1.25, 30, 32), b"\x01\x02")
        command = run.call_args.args[0]
        self.assertIn("atempo=1.250000", command)
        self.assertEqual(run.call_args.kwargs["input"], b"\x00\x00\x00\x00")

    def test_tempo_adjustment_rejects_failed_or_invalid_output(self) -> None:
        failed = types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"private detail")
        with patch.object(ADAPTER.subprocess, "run", return_value=failed):
            with self.assertRaises(ADAPTER.UpstreamError):
                ADAPTER.adjust_tempo(b"\x00\x00", 1.25, 30, 32)


if __name__ == "__main__":
    unittest.main()
