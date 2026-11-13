#!/usr/bin/env python3
"""Tests safe migrations for PodWave's Home AI TTS settings."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("podwave_settings", ROOT / "scripts/setup-podwave-server-settings.py")
SETTINGS = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SETTINGS)


class PodWaveServerSettingsTests(unittest.TestCase):
    def test_known_invalid_home_ai_voice_and_model_are_migrated(self) -> None:
        config = {"tts": {"prov": "openai", "base": SETTINGS.HOME_AI_BASE,
                          "model": "OmniVoice", "voiceA": SETTINGS.LEGACY_TTS_VOICE, "voiceB": ""}}
        self.assertEqual(SETTINGS.migrate_updates(config), {
            "model": "omnivoice", "voiceA": "danish-default", "voiceB": "english-default"})

    def test_custom_provider_and_custom_voice_choices_are_preserved(self) -> None:
        config = {"tts": {"prov": "openai", "base": SETTINGS.HOME_AI_BASE,
                          "model": "omnivoice", "voiceA": "danish-default", "voiceB": "my-custom-voice"}}
        self.assertEqual(SETTINGS.migrate_updates(config), {})
        config["tts"]["base"] = "https://other-provider.example/v1"
        config["tts"]["voiceA"] = SETTINGS.LEGACY_TTS_VOICE
        self.assertEqual(SETTINGS.migrate_updates(config), {})

    def test_non_openai_tts_is_untouched(self) -> None:
        config = {"tts": {"prov": "elevenlabs", "base": SETTINGS.HOME_AI_BASE,
                          "model": "OmniVoice", "voiceA": SETTINGS.LEGACY_TTS_VOICE, "voiceB": ""}}
        self.assertEqual(SETTINGS.migrate_updates(config), {})


if __name__ == "__main__":
    unittest.main()
