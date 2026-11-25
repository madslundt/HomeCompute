#!/usr/bin/env python3
"""Test the Paseo catalog, voice defaults and project team projection."""

import json
from pathlib import Path
import re
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / "scripts/setup-paseo.py"))
MODELS = set(re.findall(
    r'^  - model_name: "([^"]+)"$',
    (ROOT / "deploy/control-plane/litellm-config.yaml").read_text(), flags=re.MULTILINE,
))


class PaseoConfigurationTest(unittest.TestCase):
    def test_models_and_speech_are_split_by_capability(self):
        settings, opencode, codex = SETUP["render_settings"](MODELS)
        text, special = SETUP["model_groups"](MODELS)
        self.assertEqual({row["slug"] for row in codex["models"]}, text)
        self.assertEqual(set(opencode["providers"]["homecompute"]["models"]),
                         {model for model in text if not model.startswith("chatgpt-")})
        self.assertTrue({"embedding", "stt-danish", "omnivoice", "video"} <= special)
        self.assertEqual(settings["features"]["dictation"]["stt"]["model"], "stt-danish")
        voice = settings["features"]["voiceMode"]
        self.assertEqual(voice["llm"]["model"], "homecompute/local-only")
        self.assertEqual(voice["tts"]["voice"], "danish-default")
        self.assertFalse(settings["daemon"]["relay"]["enabled"])

    def test_each_checkout_has_a_local_product_owner(self):
        settings, _, _ = SETUP["render_settings"](MODELS)
        profiles = {profile["name"]: profile for profile in settings["daemon"]["agentProfiles"]}
        projects = json.loads((ROOT / "deploy/paseo/projects.json").read_text())
        for project in projects:
            profile = profiles[project["repository"].split("/")[1] + " PO"]
            self.assertEqual(profile["model"], "homecompute/local-only")
        self.assertEqual(profiles["Developer"]["provider"], "codex")
        self.assertEqual(profiles["Hermes Code Review"]["provider"], "hermes")
        self.assertEqual(profiles["QA"]["modeId"], "plan")


if __name__ == "__main__":
    unittest.main()
