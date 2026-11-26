#!/usr/bin/env python3

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_KEY = "sk-fixture"


def load(script):
    spec = importlib.util.spec_from_file_location(script, ROOT / "scripts" / f"{script}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch("pwd.getpwnam", return_value=SimpleNamespace(pw_uid=101)), \
     patch("grp.getgrnam", return_value=SimpleNamespace(gr_gid=100)):
    OPENCODE = load("provision-opencode-litellm-key")

KEYS = load("setup-agent-routing-key")


class RoutingKeysTest(unittest.TestCase):
    def test_opencode_updates_only_the_known_local_model_scope_and_can_revert(self):
        owner = OPENCODE.OWNER
        token_id = hashlib.sha256(FIXTURE_KEY.encode()).hexdigest()
        legacy = {"info": {"key_alias": owner, "user_id": owner, "models": ["automation-moe"],
                           "object_permission": {"mcp_access_groups": ["all-harness-mcps"]}}}
        updated = {"info": {**legacy["info"], "models": ["automation-moe", "local-only"]}}
        for target, initial, final in (
            (OPENCODE.MODELS, legacy, updated), (OPENCODE.LEGACY_MODELS, updated, legacy),
        ):
            with self.subTest(target=target), patch.object(OPENCODE, "api", side_effect=[initial, {}, final]) as api:
                OPENCODE.validate_key(FIXTURE_KEY, None, target_models=target, apply=True)
            self.assertEqual(api.call_args_list[1].args, ("/key/update", {"key": token_id, "models": target}))

        wrong_scope = {"info": {**legacy["info"], "models": ["chatgpt-sol-latest"]}}
        with patch.object(OPENCODE, "api", return_value=wrong_scope) as api:
            with self.assertRaisesRegex(OPENCODE.Refused, "opencode-key-models-refused"):
                OPENCODE.validate_key(FIXTURE_KEY, None, target_models=OPENCODE.MODELS, apply=True)
            self.assertEqual(api.call_count, 1)

    def test_enrollment_uses_explicit_aliased_virtual_key_and_metadata_only_output(self):
        profile, instance = "hermes-assistant", "fixture"
        principal = f"agent-routing-{profile}-{instance}"
        scopes = []
        model_set = {"agent-assistant"}
        token_id = hashlib.sha256(FIXTURE_KEY.encode()).hexdigest()

        def api(path, *, body=None, query=None):
            if path == "/key/info":
                self.assertEqual(query["key"], token_id)
                return {"info": scopes[0]} if scopes else None
            self.assertEqual(path, "/key/generate")
            self.assertEqual(body["key"], FIXTURE_KEY)
            self.assertEqual(body["key_alias"], principal)
            self.assertEqual(body["models"], sorted(model_set))
            self.assertEqual(body["object_permission"], {"mcp_servers": ["no-mcp-servers"]})
            self.assertGreater(body["rpm_limit"], 0)
            self.assertGreater(body["tpm_limit"], 0)
            scopes.append({name: value for name, value in body.items() if name != "key"})

        with patch.object(KEYS.os, "geteuid", return_value=0), \
             patch.object(KEYS.socket, "gethostname", return_value="home-core"), \
             patch.object(KEYS.LITELLM, "read_key", return_value=FIXTURE_KEY), \
             patch.object(KEYS.LITELLM, "list_models", return_value=model_set), \
             patch.object(KEYS.LITELLM, "api", side_effect=api), patch("builtins.print") as report:
            KEYS.reconcile(profile, instance, True)
            KEYS.reconcile(profile, instance, False)
        self.assertEqual(len(scopes), 1)
        self.assertNotIn(FIXTURE_KEY, str(report.call_args_list))


if __name__ == "__main__":
    unittest.main()
