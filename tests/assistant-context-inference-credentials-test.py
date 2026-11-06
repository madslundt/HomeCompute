#!/usr/bin/env python3
"""Synthetic contract tests for assistant-context LiteLLM key provisioning."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "assistant_context_inference_credentials",
    ROOT / "scripts/setup-assistant-context-inference-credentials.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REVISION = "a" * 40
EMBEDDING_KEY = "sk-embedding-test-key-1234567890"
BRIEFING_KEY = "sk-briefing-test-key-1234567890"


class FakeLiteLLM:
    def __init__(self):
        self.generated: list[tuple[str, str]] = []
        self.keys: dict[str, tuple[str, str]] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[list[str], str]] = []
        self.bad_info: tuple[str, str] | None = None
        self.bad_info_call: int | None = None
        self.info_calls = 0
        self.expired = False
        self.fail_generate = False

    def __call__(self, args, **kwargs):
        source = kwargs["input"]
        self.calls.append((args, source))
        if "/key/generate" in source:
            if self.fail_generate:
                raise subprocess.TimeoutExpired(args, 45)
            if "'embedding'" in source:
                model, key = "embedding", EMBEDDING_KEY
            else:
                model, key = "automation-moe", BRIEFING_KEY
            owner = "assistant-context-" + ("embedding" if model == "embedding" else "briefing")
            self.generated.append((model, owner))
            self.keys[key] = (model, owner)
            match = re.search(r"'setup_intent_id': '([0-9a-f]{32})'", source)
            if match:
                self.metadata[key] = {"setup_intent_id": match.group(1)}
            payload = {"key": key, "models": [model], "expires": None}
        elif "/key/info" in source:
            self.info_calls += 1
            known = next((key for key in self.keys
                          if hashlib.sha256(key.encode()).hexdigest() in source), None)
            if known is None:
                # Existing test credentials are recognized from the two fixtures.
                known = next((key for key in (EMBEDDING_KEY, BRIEFING_KEY) if key in source), None)
            if known is None:
                return subprocess.CompletedProcess(args, 1, "", "")
            model, owner = self.keys.get(known, ("embedding", "assistant-context-embedding"))
            if self.bad_info and (self.bad_info_call is None or self.bad_info_call == self.info_calls):
                model, owner = self.bad_info
            payload = {"info": {"models": [model], "user_id": owner,
                                "expires": "soon" if self.expired else None,
                                "metadata": self.metadata.get(known, {})}}
        else:
            raise AssertionError("unexpected LiteLLM endpoint")
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")


class InferenceCredentialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = {name: self.root / f"assistant-context-{name}.json" for name in MODULE.KEYS}
        self.litellm = FakeLiteLLM()
        self.setup = MODULE.InferenceKeySetup(key_paths=self.paths, root_uid=os.getuid(),
                                              root_gid=os.getgid(), runner=self.litellm)

    def tearDown(self):
        self.temp.cleanup()

    def write_key(self, name: str, value: str) -> None:
        self.paths[name].write_text(value + "\n")
        self.paths[name].chmod(0o600)
        if name == "embedding":
            self.litellm.keys[value] = ("embedding", "assistant-context-embedding")
        else:
            self.litellm.keys[value] = ("automation-moe", "assistant-context-briefing")

    def write_pending(self, name: str) -> Path:
        spec = MODULE.KEYS[name]
        path = self.paths[name].with_name(self.paths[name].name + ".pending")
        raw = MODULE.canonical({"schema_version": 1, "name": name, "model": spec["model"],
                                "owner": spec["owner"], "revision": REVISION,
                                "intent_id": "1" * 32})
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def test_creates_two_independent_model_scoped_root_only_files(self):
        plan = self.setup.plan(REVISION)
        result = self.setup.apply(REVISION, plan["plan_sha256"])
        self.assertEqual(result["created"], ["embedding", "briefing"])
        self.assertEqual(self.litellm.generated, [
            ("embedding", "assistant-context-embedding"),
            ("automation-moe", "assistant-context-briefing"),
        ])
        for name, expected in (("embedding", EMBEDDING_KEY), ("briefing", BRIEFING_KEY)):
            info = self.paths[name].stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_uid, os.getuid())
            self.assertEqual(self.paths[name].read_text(), expected + "\n")
        self.assertNotIn(EMBEDDING_KEY, json.dumps(result))
        self.assertNotIn(BRIEFING_KEY, json.dumps(result))
        self.assertTrue(all(not any(secret in arg for arg in args)
                            for args, _ in self.litellm.calls
                            for secret in (EMBEDDING_KEY, BRIEFING_KEY)))
        for _args, source in self.litellm.calls:
            self.assertNotIn(EMBEDDING_KEY, source)
            self.assertNotIn(BRIEFING_KEY, source)

    def test_valid_partial_state_is_preserved_and_only_missing_key_is_created(self):
        self.write_key("embedding", EMBEDDING_KEY)
        plan = self.setup.plan(REVISION)
        result = self.setup.apply(REVISION, plan["plan_sha256"])
        self.assertEqual(result["created"], ["briefing"])
        self.assertEqual(self.paths["embedding"].read_text(), EMBEDDING_KEY + "\n")
        self.assertEqual(self.litellm.generated, [("automation-moe", "assistant-context-briefing")])

    def test_existing_key_with_wrong_owner_or_model_is_refused(self):
        self.write_key("embedding", EMBEDDING_KEY)
        self.litellm.bad_info = ("automation-moe", "other-owner")
        with self.assertRaisesRegex(MODULE.Refused, "existing-key-scope-mismatch"):
            self.setup.plan(REVISION)
        self.assertEqual(self.litellm.generated, [])

    def test_existing_expiring_key_is_refused(self):
        self.write_key("briefing", BRIEFING_KEY)
        self.litellm.expired = True
        with self.assertRaisesRegex(MODULE.Refused, "existing-key-scope-mismatch"):
            self.setup.plan(REVISION)

    def test_existing_file_with_unsafe_mode_is_refused(self):
        self.write_key("briefing", BRIEFING_KEY)
        self.paths["briefing"].chmod(0o644)
        with self.assertRaisesRegex(MODULE.Refused, "ownership-or-mode-unsafe"):
            self.setup.plan(REVISION)

    def test_stale_plan_does_not_generate_after_valid_partial_state_changes(self):
        plan = self.setup.plan(REVISION)
        self.write_key("embedding", EMBEDDING_KEY)
        with self.assertRaisesRegex(MODULE.Refused, "changed-since-reviewed-plan"):
            self.setup.apply(REVISION, plan["plan_sha256"])
        self.assertEqual(self.litellm.generated, [])

    def test_rejects_non_full_revision(self):
        with self.assertRaisesRegex(MODULE.Refused, "full-published-source-sha-required"):
            self.setup.plan("main")

    def test_api_timeout_leaves_intent_and_replay_refuses_to_mint_again(self):
        plan = self.setup.plan(REVISION)
        self.litellm.fail_generate = True
        with self.assertRaisesRegex(MODULE.Refused, "litellm-key-request-unconfirmed"):
            self.setup.apply(REVISION, plan["plan_sha256"])
        pending = self.paths["embedding"].with_name(self.paths["embedding"].name + ".pending")
        self.assertTrue(pending.is_file())
        self.assertFalse(self.paths["embedding"].exists())
        calls = len(self.litellm.calls)
        with self.assertRaisesRegex(MODULE.Refused, "pending-key-intent-requires-operator-reconciliation"):
            self.setup.plan(REVISION)
        self.assertEqual(len(self.litellm.calls), calls)

    def test_failed_readback_keeps_intent_and_does_not_install_key_file(self):
        plan = self.setup.plan(REVISION)
        self.litellm.bad_info = ("automation-moe", "wrong-owner")
        with self.assertRaisesRegex(MODULE.Refused, "existing-key-scope-mismatch"):
            self.setup.apply(REVISION, plan["plan_sha256"])
        self.assertFalse(self.paths["embedding"].exists())
        self.assertTrue(self.paths["embedding"].with_name(self.paths["embedding"].name + ".pending").exists())

    def test_pending_resolution_requires_explicit_scope_and_keeps_receipt(self):
        pending = self.write_pending("embedding")
        marker_hash = MODULE.sha256(pending.read_bytes())
        with self.assertRaisesRegex(MODULE.Refused, "verified-key-file-required"):
            self.setup._resolve_pending("embedding", marker_hash, "key-verified")
        result = self.setup._resolve_pending("embedding", marker_hash, "orphan-revoked")
        self.assertEqual(result["resolution"], "orphan-revoked")
        self.assertFalse(pending.exists())
        self.assertTrue(Path(result["receipt"]).exists())
        self.assertEqual(self.setup.plan(REVISION)["keys"]["embedding"]["state"], "absent")

    def test_pending_key_verification_requires_matching_key_scope(self):
        pending = self.write_pending("briefing")
        self.write_key("briefing", BRIEFING_KEY)
        self.litellm.metadata[BRIEFING_KEY] = {"setup_intent_id": "2" * 32}
        self.litellm.bad_info = ("embedding", "other-owner")
        with self.assertRaisesRegex(MODULE.Refused, "existing-key-scope-mismatch"):
            self.setup._resolve_pending("briefing", MODULE.sha256(pending.read_bytes()), "key-verified")
        self.assertTrue(pending.exists())

    def test_pending_key_verification_requires_matching_intent_metadata(self):
        pending = self.write_pending("embedding")
        self.write_key("embedding", EMBEDDING_KEY)
        expected_id = json.loads(pending.read_bytes())["intent_id"]
        self.litellm.metadata[EMBEDDING_KEY] = {"setup_intent_id": "3" * 32}
        with self.assertRaisesRegex(MODULE.Refused, "pending-key-intent-does-not-match-key"):
            self.setup._resolve_pending("embedding", MODULE.sha256(pending.read_bytes()), "key-verified")
        self.litellm.metadata[EMBEDDING_KEY] = {"setup_intent_id": expected_id}
        result = self.setup._resolve_pending("embedding", MODULE.sha256(pending.read_bytes()), "key-verified")
        self.assertEqual(result["resolution"], "key-verified")

    def test_postwrite_readback_failure_preserves_correlated_private_key_for_resolution(self):
        plan = self.setup.plan(REVISION)
        self.litellm.bad_info = ("automation-moe", "wrong-owner")
        self.litellm.bad_info_call = 2
        with self.assertRaisesRegex(MODULE.Refused, "existing-key-scope-mismatch"):
            self.setup.apply(REVISION, plan["plan_sha256"])
        self.assertTrue(self.paths["embedding"].is_file())
        self.assertEqual(stat.S_IMODE(self.paths["embedding"].stat().st_mode), 0o600)
        pending = self.paths["embedding"].with_name(self.paths["embedding"].name + ".pending")
        self.assertTrue(pending.is_file())
        self.litellm.bad_info = None
        resolved = self.setup._resolve_pending("embedding", MODULE.sha256(pending.read_bytes()), "key-verified")
        self.assertEqual(resolved["resolution"], "key-verified")


if __name__ == "__main__":
    unittest.main()
