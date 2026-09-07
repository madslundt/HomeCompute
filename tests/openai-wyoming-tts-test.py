#!/usr/bin/env python3
"""Focused behavioral tests for the OpenAI-to-Wyoming TTS adapter."""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import wave
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import patch


class Event:
    def __init__(self, event_type: str, data: dict[str, Any] | None = None, payload: bytes | None = None) -> None:
        self.type = event_type
        self.data = data or {}
        self.payload = payload


class AudioStart:
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "audio-start"

    @staticmethod
    def from_event(event: Event) -> Any:
        return types.SimpleNamespace(**event.data)


class AudioChunk:
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "audio-chunk"

    @staticmethod
    def from_event(event: Event) -> Any:
        return types.SimpleNamespace(audio=event.payload or b"", **event.data)


class AudioStop:
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "audio-stop"


class WyomingError:
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "error"


class Describe:
    def event(self) -> Event:
        return Event("describe")


class Info:
    @staticmethod
    def is_type(event_type: str) -> bool:
        return event_type == "info"

    @staticmethod
    def from_event(event: Event) -> Any:
        programs = [types.SimpleNamespace(installed=row.get("installed", False)) for row in event.data.get("tts", [])]
        return types.SimpleNamespace(tts=programs)


class SynthesizeVoice:
    def __init__(self, name: str) -> None:
        self.name = name


class Synthesize:
    def __init__(self, text: str, voice: SynthesizeVoice) -> None:
        self.text = text
        self.voice = voice

    def event(self) -> Event:
        return Event("synthesize", {"text": self.text, "voice": {"name": self.voice.name}})


class AsyncClient:
    @staticmethod
    def from_uri(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("tests must inject a Wyoming client")


def install_fake_wyoming() -> None:
    package = types.ModuleType("wyoming")
    modules = {
        "wyoming": package,
        "wyoming.audio": types.ModuleType("wyoming.audio"),
        "wyoming.client": types.ModuleType("wyoming.client"),
        "wyoming.error": types.ModuleType("wyoming.error"),
        "wyoming.info": types.ModuleType("wyoming.info"),
        "wyoming.tts": types.ModuleType("wyoming.tts"),
    }
    modules["wyoming.audio"].AudioChunk = AudioChunk
    modules["wyoming.audio"].AudioStart = AudioStart
    modules["wyoming.audio"].AudioStop = AudioStop
    modules["wyoming.client"].AsyncClient = AsyncClient
    modules["wyoming.error"].Error = WyomingError
    modules["wyoming.info"].Describe = Describe
    modules["wyoming.info"].Info = Info
    modules["wyoming.tts"].Synthesize = Synthesize
    modules["wyoming.tts"].SynthesizeVoice = SynthesizeVoice
    sys.modules.update(modules)


install_fake_wyoming()
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openai_wyoming_tts", ROOT / "scripts" / "openai-wyoming-tts.py")
assert SPEC and SPEC.loader
ADAPTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTER
SPEC.loader.exec_module(ADAPTER)


class FakeClient:
    def __init__(self, events: list[Event]) -> None:
        self.events = deque(events)
        self.writes: list[Event] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def write_event(self, event: Event) -> None:
        self.writes.append(event)

    async def read_event(self) -> Event | None:
        return self.events.popleft() if self.events else None


class FakeClientFactory:
    def __init__(self, clients: list[FakeClient]) -> None:
        self.clients = deque(clients)
        self.calls = 0

    def __call__(self, *_args: Any, **_kwargs: Any) -> FakeClient:
        self.calls += 1
        if not self.clients:
            raise AssertionError("unexpected Wyoming connection")
        return self.clients.popleft()


class RunningAdapter:
    def __init__(self, gateway: Any) -> None:
        self.config = ADAPTER.Config(
            bind_address="127.0.0.1",
            port=0,
            wyoming_uri="tcp://wyoming:10200",
            api_key=b"correct-test-key",
            model_alias="tts",
            voice_alias="danish-default",
            piper_voice_id="da_DK-talesyntese-medium",
            max_input_chars=20,
        )
        self.server = ADAPTER.build_server(self.config, gateway)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RunningAdapter":
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        document: dict[str, Any] | None = None,
        key: str | None = "correct-test-key",
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = {}
        body: bytes | None = None
        if document is not None:
            body = json.dumps(document).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


def valid_request(**changes: Any) -> dict[str, Any]:
    request = {
        "model": "tts",
        "input": "Hej verden",
        "voice": "danish-default",
        "response_format": "wav",
        "speed": 1.0,
    }
    request.update(changes)
    return request


class OpenAIWyomingTtsTest(unittest.TestCase):
    def test_config_allows_bridge_wildcard_for_exact_host_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "api-key"
            key_path.write_text("test-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "GB10_BIND_ADDRESS": "0.0.0.0",
                    "TTS_API_KEY_FILE": str(key_path),
                    "PIPER_VOICE_ID": "da_DK-talesyntese-medium",
                    "WYOMING_URI": "tcp://wyoming-tts:10200",
                },
                clear=True,
            ):
                config = ADAPTER.load_config()
        self.assertEqual(config.bind_address, "0.0.0.0")
        self.assertEqual(config.port, 8004)

    def test_auth_denial_is_constant_contract_and_does_not_reach_wyoming(self) -> None:
        factory = FakeClientFactory([])
        gateway = ADAPTER.WyomingGateway("tcp://wyoming:10200", factory)
        with RunningAdapter(gateway) as adapter:
            for key in (None, "wrong-secret"):
                with self.subTest(key=key):
                    status, headers, body = adapter.request(
                        "POST",
                        "/v1/audio/speech",
                        valid_request(input="PRIVATE-CONTENT"),
                        key=key,
                    )
                    self.assertEqual(status, 401)
                    self.assertEqual(headers["WWW-Authenticate"], "Bearer")
                    self.assertIn("X-Request-ID", headers)
                    self.assertNotIn(b"PRIVATE-CONTENT", body)
                    self.assertNotIn(b"wrong-secret", body)
        self.assertEqual(factory.calls, 0)

    def test_schema_allow_list_denies_unsupported_requests_before_wyoming(self) -> None:
        cases = (
            (valid_request(debug="PRIVATE-FIELD"), "unknown_field"),
            (valid_request(model="other"), "unsupported_model"),
            (valid_request(voice="other"), "unsupported_voice"),
            (valid_request(response_format="mp3"), "unsupported_format"),
            (valid_request(speed=1.1), "unsupported_speed"),
            (valid_request(input=""), "invalid_input"),
            (valid_request(input="x" * 21), "input_too_long"),
        )
        factory = FakeClientFactory([])
        gateway = ADAPTER.WyomingGateway("tcp://wyoming:10200", factory)
        with RunningAdapter(gateway) as adapter:
            for document, code in cases:
                with self.subTest(code=code):
                    status, _headers, body = adapter.request("POST", "/v1/audio/speech", document)
                    self.assertEqual(status, 400)
                    self.assertEqual(json.loads(body)["error"]["code"], code)
                    self.assertNotIn(b"PRIVATE-FIELD", body)
            status, _headers, _body = adapter.request("GET", "/v1/audio/speech")
            self.assertEqual(status, 404)
            status, _headers, _body = adapter.request("TRACE", "/health/live")
            self.assertEqual(status, 405)
        self.assertEqual(factory.calls, 0)

    def test_valid_request_maps_voice_and_returns_real_wav(self) -> None:
        pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        client = FakeClient(
            [
                Event("audio-start", {"rate": 22_050, "width": 2, "channels": 1}),
                Event("audio-chunk", {"rate": 22_050, "width": 2, "channels": 1}, pcm[:4]),
                Event("audio-chunk", {"rate": 22_050, "width": 2, "channels": 1}, pcm[4:]),
                Event("audio-stop"),
            ]
        )
        factory = FakeClientFactory([client])
        gateway = ADAPTER.WyomingGateway("tcp://wyoming:10200", factory)
        with RunningAdapter(gateway) as adapter:
            status, headers, body = adapter.request("POST", "/v1/audio/speech", valid_request())
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/wav")
        self.assertTrue(body.startswith(b"RIFF"))
        with wave.open(io.BytesIO(body), "rb") as wav_file:
            self.assertEqual((wav_file.getframerate(), wav_file.getsampwidth(), wav_file.getnchannels()), (22_050, 2, 1))
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), pcm)
        self.assertEqual(client.writes[0].type, "synthesize")
        self.assertEqual(client.writes[0].data["voice"]["name"], "da_DK-talesyntese-medium")
        self.assertEqual(client.writes[0].data["text"], "Hej verden")

    def test_inconsistent_wyoming_pcm_fails_with_generic_error(self) -> None:
        client = FakeClient(
            [
                Event("audio-start", {"rate": 22_050, "width": 2, "channels": 1}),
                Event("audio-chunk", {"rate": 16_000, "width": 2, "channels": 1}, b"\x00\x00"),
                Event("audio-stop"),
            ]
        )
        gateway = ADAPTER.WyomingGateway(
            "tcp://wyoming:10200", FakeClientFactory([client])
        )
        with RunningAdapter(gateway) as adapter:
            status, headers, body = adapter.request(
                "POST", "/v1/audio/speech", valid_request()
            )
        self.assertEqual(status, 502)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body)["error"]["code"], "tts_upstream_error")
        self.assertNotIn(b"inconsistent", body)

    def test_readiness_is_unauthenticated_and_requires_installed_tts_program(self) -> None:
        ready_client = FakeClient([Event("info", {"tts": [{"installed": True}]})])
        probe_client = FakeClient([Event("info", {"tts": [{"installed": True}]})])
        not_ready_client = FakeClient([Event("info", {"tts": []})])
        factory = FakeClientFactory([ready_client, probe_client, not_ready_client])
        gateway = ADAPTER.WyomingGateway("tcp://wyoming:10200", factory)
        with RunningAdapter(gateway) as adapter:
            status, _headers, body = adapter.request("GET", "/health/live", key=None)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], "live")
            status, _headers, body = adapter.request("GET", "/health/ready", key=None)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], "ready")
            with patch.dict(
                os.environ,
                {
                    "GB10_BIND_ADDRESS": "127.0.0.1",
                    "TTS_HOST_PORT": str(adapter.server.server_port),
                },
            ):
                self.assertEqual(ADAPTER.probe_ready(), 0)
            status, _headers, body = adapter.request("GET", "/health/ready", key=None)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "not_ready")
        self.assertEqual(ready_client.writes[0].type, "describe")
        self.assertEqual(factory.calls, 3)


if __name__ == "__main__":
    unittest.main()
