#!/usr/bin/env python3
"""Focused tests for the Wyoming-to-OpenAI STT adapter and deployment."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import unittest
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wyoming_openai_stt", ROOT / "scripts" / "wyoming-openai-stt.py")
assert SPEC and SPEC.loader
ADAPTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTER
SPEC.loader.exec_module(ADAPTER)


def config(**changes: object) -> object:
    values = {
        "bind_address": "127.0.0.1",
        "port": 10301,
        "upstream_url": "http://hviske-primary:8000/v1/audio/transcriptions",
        "api_key": b"test-key",
        "model": "stt-danish",
        "language": "da",
        "max_audio_bytes": 32_000,
        "max_audio_seconds": 2,
        "upstream_timeout_seconds": 5,
        "max_connections": 4,
    }
    values.update(changes)
    return ADAPTER.Config(**values)


def decode_event(encoded: bytes) -> tuple[dict[str, object], dict[str, object]]:
    header_bytes, remainder = encoded.split(b"\n", 1)
    header = json.loads(header_bytes)
    data_length = header.get("data_length", 0)
    return header, json.loads(remainder[:data_length]) if data_length else {}


class WyomingAdapterTest(unittest.TestCase):
    def test_describe_advertises_only_pinned_danish_asr(self) -> None:
        header, data = decode_event(ADAPTER.info_event("stt-danish", "da"))
        self.assertEqual("info", header["type"])
        self.assertEqual([], data["tts"])
        self.assertEqual(["da"], data["asr"][0]["models"][0]["languages"])
        self.assertTrue(data["asr"][0]["requires_external_vad"])

    def test_official_separate_data_and_payload_framing_is_accepted(self) -> None:
        async def exercise() -> object:
            reader = asyncio.StreamReader()
            data = json.dumps({"rate": 16000, "width": 2, "channels": 1}).encode()
            payload = b"\x00\x00" * 10
            header = json.dumps(
                {"type": "audio-chunk", "data_length": len(data), "payload_length": len(payload)}
            ).encode()
            reader.feed_data(header + b"\n" + data + payload)
            reader.feed_eof()
            return await ADAPTER.read_event(reader, 100)

        event = asyncio.run(exercise())
        self.assertEqual("audio-chunk", event.type)
        self.assertEqual(16000, event.data["rate"])
        self.assertEqual(b"\x00\x00" * 10, event.payload)

    def test_audio_is_wrapped_as_wav_and_returns_danish_transcript(self) -> None:
        received: list[bytes] = []

        async def transcribe(audio: bytes) -> str:
            received.append(audio)
            return "Tænd lyset"

        async def exercise() -> bytes:
            session = ADAPTER.WyomingSession(config(), transcribe)
            audio_format = {"rate": 16000, "width": 2, "channels": 1}
            await session.process(ADAPTER.Event("transcribe", {"name": "stt-danish", "language": "da"}))
            await session.process(ADAPTER.Event("audio-start", audio_format))
            await session.process(ADAPTER.Event("audio-chunk", audio_format, b"\x00\x00" * 800))
            return await session.process(ADAPTER.Event("audio-stop", {}))

        response = asyncio.run(exercise())
        _header, data = decode_event(response)
        self.assertEqual({"text": "Tænd lyset", "language": "da"}, data)
        with wave.open(io.BytesIO(received[0]), "rb") as stream:
            self.assertEqual((1, 2, 16000, 800), (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()))

    def test_empty_audio_returns_empty_transcript_without_upstream_call(self) -> None:
        async def forbidden(_audio: bytes) -> str:
            raise AssertionError("upstream must not be called")

        async def exercise() -> bytes:
            session = ADAPTER.WyomingSession(config(), forbidden)
            await session.process(ADAPTER.Event("transcribe", {"language": "da-DK"}))
            return await session.process(ADAPTER.Event("audio-stop", {}))

        _header, data = decode_event(asyncio.run(exercise()))
        self.assertEqual("", data["text"])

    def test_non_danish_and_unknown_models_fail_closed(self) -> None:
        async def unused(_audio: bytes) -> str:
            return ""

        for request in ({"language": "en"}, {"name": "whisper"}):
            with self.subTest(request=request):
                session = ADAPTER.WyomingSession(config(), unused)
                with self.assertRaises(ADAPTER.ProtocolError):
                    asyncio.run(session.process(ADAPTER.Event("transcribe", request)))

    def test_changed_format_and_oversized_audio_fail_closed(self) -> None:
        async def unused(_audio: bytes) -> str:
            return ""

        async def changed_format() -> None:
            session = ADAPTER.WyomingSession(config(), unused)
            await session.process(ADAPTER.Event("transcribe", {}))
            await session.process(ADAPTER.Event("audio-start", {"rate": 16000, "width": 2, "channels": 1}))
            await session.process(ADAPTER.Event("audio-chunk", {"rate": 48000, "width": 2, "channels": 1}, b"\0\0"))

        with self.assertRaises(ADAPTER.ProtocolError):
            asyncio.run(changed_format())

        async def oversized() -> None:
            session = ADAPTER.WyomingSession(config(max_audio_bytes=2), unused)
            await session.process(ADAPTER.Event("transcribe", {}))
            await session.process(ADAPTER.Event("audio-chunk", {"rate": 16000, "width": 2, "channels": 1}, b"123"))

        with self.assertRaises(ADAPTER.ProtocolError):
            asyncio.run(oversized())


class DeploymentContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = (ROOT / "deploy" / "hviske-stt" / "compose.yaml").read_text()
        cls.environment = (ROOT / "config" / "hviske-stt.env.example").read_text()
        cls.lifecycle = (ROOT / "scripts" / "setup-compute-hviske-stt.sh").read_text()

    def test_vllm_019_arm64_and_model_revision_are_pinned(self) -> None:
        self.assertIn("sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d", self.environment)
        self.assertIn("HVISKE_MODEL_REVISION=5d1a09822018702dc51d763e3a867b62d26b3501", self.environment)
        self.assertIn("HVISKE_MODEL_LICENSE_ID=cc-by-nc-4.0", self.environment)

    def test_only_wyoming_10301_is_published(self) -> None:
        primary = self.compose.split("  hviske-primary:", 1)[1].split("  hviske-wyoming:", 1)[0]
        wyoming = self.compose.split("  hviske-wyoming:", 1)[1].split("\nnetworks:\n", 1)[0]
        self.assertNotIn("ports:", primary)
        self.assertIn("${HVISKE_WYOMING_PORT:-10301}:10301", wyoming)
        self.assertIn("networks: [edge, inference]", wyoming)
        self.assertIn("internal: true", self.compose)

    def test_vllm_non_root_identity_has_a_writable_inductor_cache(self) -> None:
        primary = self.compose.split("  hviske-primary:", 1)[1].split("  hviske-wyoming:", 1)[0]
        self.assertIn("USER: gb10-ai", primary)
        self.assertIn("LOGNAME: gb10-ai", primary)
        self.assertIn("TORCHINDUCTOR_CACHE_DIR: /var/cache/vllm/torchinductor", primary)
        self.assertIn("HF_MODULES_CACHE: /var/cache/vllm/hf-modules", primary)

    def test_license_and_private_ingress_are_fail_closed_by_default(self) -> None:
        self.assertIn("HVISKE_LICENSE_DECISION=review-required", self.environment)
        self.assertIn("HVISKE_PRIVATE_INGRESS_CONFIRMED=false", self.environment)
        self.assertIn("GB10_BIND_ADDRESS=127.0.0.1", self.environment)
        start = self.lifecycle.split("start() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(start.index("license_allows_activation"), start.index("compose --profile hviske up"))
        self.assertLess(start.index("HVISKE_PRIVATE_INGRESS_CONFIRMED"), start.index("compose --profile hviske up"))


if __name__ == "__main__":
    unittest.main()
