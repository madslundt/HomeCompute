#!/usr/bin/env python3
"""Tests safe migrations for PodWave's Home AI TTS settings."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("podwave_settings", ROOT / "scripts/setup-podwave-server-settings.py")
SETTINGS = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SETTINGS)


class PodWaveServerSettingsTests(unittest.TestCase):
    def test_docker_exec_does_not_consume_the_deployment_script_stdin(self) -> None:
        with patch.object(SETTINGS.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ok"
            self.assertEqual(SETTINGS.docker_exec("abcdef123456", "print('ok')"), "ok")
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["docker", "exec", "-i", "abcdef123456"])
            self.assertEqual(argv[-1], "-")
            self.assertEqual(run.call_args.kwargs["input"], "print('ok')")

    def test_known_invalid_home_ai_voice_and_model_are_migrated(self) -> None:
        config = {"tts": {"prov": "openai", "base": SETTINGS.HOME_AI_BASE,
                          "model": "OmniVoice", "voiceA": SETTINGS.LEGACY_TTS_VOICE, "voiceB": ""}}
        self.assertEqual(SETTINGS.migrate_updates(config), {"tts": {
            "model": "omnivoice", "voiceA": "danish-default", "voiceB": "english-default"}})

    def test_profile_schema_migrates_only_matching_profile_and_preserves_defaults(self) -> None:
        profiles = [
            {"id": "home-ai", "name": "Home AI", "prov": "openai", "base": SETTINGS.HOME_AI_BASE,
             "model": "OmniVoice", "voiceA": SETTINGS.LEGACY_TTS_VOICE, "voiceB": ""},
            {"id": "other", "name": "Other", "prov": "openai", "base": "https://other.example/v1",
             "model": "custom", "voiceA": "custom-a", "voiceB": "custom-b"},
        ]
        config = {"ttss": profiles, "defaultTts": "other"}
        self.assertEqual(SETTINGS.migrate_updates(config), {"ttss": [
            {**profiles[0], "model": "omnivoice", "voiceA": "danish-default", "voiceB": "english-default"},
            profiles[1],
        ], "defaultTts": "other"})

    def test_custom_provider_and_custom_voice_choices_are_preserved(self) -> None:
        config = {"tts": {"prov": "openai", "base": SETTINGS.HOME_AI_BASE,
                          "model": "omnivoice", "voiceA": "danish-default", "voiceB": "my-custom-voice"}}
        self.assertEqual(SETTINGS.migrate_updates(config), {})
        config["tts"]["base"] = "https://other-provider.example/v1"
        config["tts"]["voiceA"] = SETTINGS.LEGACY_TTS_VOICE
        self.assertEqual(SETTINGS.migrate_updates(config), {})

    def test_model_endpoint_migration_clears_only_home_ai_keys(self) -> None:
        managed = {"id": "local", "base": "https://ai.home.arpa:8443/v1",
                   "model": "automation-moe", "keySet": True}
        external = {"id": "external", "base": "https://provider.invalid/v1", "keySet": True}
        self.assertEqual(SETTINGS.migrate_updates({"llms": [managed, external], "defaultLlm": "local"}), {
            "llms": [{**managed, "base": SETTINGS.HOME_AI_BASE, "key": ""}, external],
            "defaultLlm": "local",
        })

    def test_migration_probes_https_with_the_container_ca_before_saving(self) -> None:
        original = {"llms": [{"id": "local", "base": "https://ai.home.arpa:8443/v1", "keySet": False}]}
        final = {"llms": [{"id": "local", "base": SETTINGS.HOME_AI_BASE, "keySet": False}]}
        calls = []

        def runner(container, code):
            calls.append(code)
            if len(calls) == 1:
                return json.dumps(original)
            if len(calls) == 4:
                return json.dumps(final)
            return ""

        self.assertEqual(SETTINGS.reconcile("fixture", runner), {"changed": True, "fields": ["base"]})
        self.assertIn('ssl.create_default_context(cafile="/run/homecompute/home-core-root.crt")', calls[1])
        self.assertIn("context=context", calls[1])
        self.assertIn('method="PUT"', calls[2])

    def test_non_openai_tts_is_untouched(self) -> None:
        config = {"tts": {"prov": "elevenlabs", "base": SETTINGS.HOME_AI_BASE,
                          "model": "OmniVoice", "voiceA": SETTINGS.LEGACY_TTS_VOICE, "voiceB": ""}}
        self.assertEqual(SETTINGS.migrate_updates(config), {})


if __name__ == "__main__":
    unittest.main()
