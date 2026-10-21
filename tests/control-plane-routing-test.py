#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LITELLM = ROOT / "deploy/control-plane/litellm-config.yaml"
ROLLBACK = ROOT / "deploy/control-plane/litellm-config-pre-registry-rollback.yaml"
LITELLM_BACKUP = ROOT / "deploy/control-plane/litellm-config-automation-backup.yaml"
COMPUTE = ROOT / "deploy/compute-node/compose.yaml"
DEPLOYMENT = ROOT / "docs/control-plane-deployment.md"
MODULE_PATH = ROOT / "scripts/model_registry.py"
SPEC = importlib.util.spec_from_file_location("model_registry", MODULE_PATH)
assert SPEC and SPEC.loader
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)
KEY_SPEC = importlib.util.spec_from_file_location("network_inference", ROOT / "scripts/setup-network-inference.py")
assert KEY_SPEC and KEY_SPEC.loader
NETWORK = importlib.util.module_from_spec(KEY_SPEC)
KEY_SPEC.loader.exec_module(NETWORK)
CERT_SPEC = importlib.util.spec_from_file_location("gateway_certificate", ROOT / "scripts/renew-gateway-certificate.py")
assert CERT_SPEC and CERT_SPEC.loader
CERTIFICATE = importlib.util.module_from_spec(CERT_SPEC)
CERT_SPEC.loader.exec_module(CERTIFICATE)


class GatewayCertificateTests(unittest.TestCase):
    def test_published_pointer_always_selects_a_complete_private_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(CERTIFICATE.publish_pair(root, b"first certificate", b"first key"))
            old_target = (root / "current").readlink()
            self.assertFalse(CERTIFICATE.publish_pair(root, b"first certificate", b"first key"))
            self.assertTrue(CERTIFICATE.publish_pair(root, b"second certificate", b"second key"))
            self.assertEqual((root / "current/cert.pem").read_bytes(), b"second certificate")
            self.assertEqual((root / "current/key.pem").read_bytes(), b"second key")
            self.assertEqual((root / "current/key.pem").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / old_target / "key.pem").read_bytes(), b"first key")

    def test_changed_key_cannot_corrupt_an_existing_certificate_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            CERTIFICATE.publish_pair(root, b"same certificate", b"first key")
            with self.assertRaises(RuntimeError):
                CERTIFICATE.publish_pair(root, b"same certificate", b"different key")
            self.assertEqual((root / "current/key.pem").read_bytes(), b"first key")

    def test_pending_restart_is_retried_even_when_certificate_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pending-restart").touch()
            def command(args):
                if args[0] == "tailscale":
                    for flag in args:
                        if flag.startswith("--cert-file=") or flag.startswith("--key-file="):
                            Path(flag.split("=", 1)[1]).write_bytes(b"synthetic")
                return b""
            with patch.object(CERTIFICATE, "DIRECTORY", root), \
                 patch.object(CERTIFICATE, "run", side_effect=command) as commands, \
                 patch.object(CERTIFICATE, "validate_pair"), \
                 patch.object(CERTIFICATE, "publish_pair", return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()):
                CERTIFICATE.renew()
            self.assertIn(["docker", "restart", CERTIFICATE.CONTAINER], [call.args[0] for call in commands.call_args_list])
            self.assertFalse((root / "pending-restart").exists())


class NetworkCredentialTests(unittest.TestCase):
    def test_whisper_migration_is_explicit_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'key'
            target.write_text('sk-synthetic-network\n')
            old = subprocess.CompletedProcess([], 0, json.dumps({
                'models': [*NETWORK.TEXT_MODELS, 'stt-danish'], 'expires': None, 'user_id': 'network-inference',
            }), '')
            updated = subprocess.CompletedProcess([], 0, json.dumps({
                'models': [*NETWORK.TEXT_MODELS, 'stt-danish', 'whisper-turbo'], 'expires': None, 'user_id': 'network-inference',
            }), '')
            with patch.object(NETWORK, 'KEY_FILE', target), \
                 patch.object(NETWORK.os, 'geteuid', return_value=0), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, 'run', return_value=old) as internal:
                with self.assertRaisesRegex(SystemExit, '--enable-whisper'):
                    NETWORK.provision()
                self.assertEqual(internal.call_count, 1)
            with patch.object(NETWORK, 'KEY_FILE', target), \
                 patch.object(NETWORK.os, 'geteuid', return_value=0), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, 'run', side_effect=[old, subprocess.CompletedProcess([], 0, '', ''), updated, updated]) as internal, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                NETWORK.provision(enable_whisper=True)
                NETWORK.provision(enable_whisper=True)
                self.assertEqual(internal.call_count, 4)
                self.assertIn('/key/update', internal.call_args_list[1].kwargs['input'])
                for call in internal.call_args_list:
                    self.assertNotIn('sk-synthetic-network', ' '.join(call.args[0]))
                self.assertNotIn('sk-synthetic-network', output.getvalue())
            self.assertEqual(target.read_text(), 'sk-synthetic-network\n')

    def test_whisper_migration_never_regrants_invalid_or_unexpected_keys(self):
        scopes = [([*NETWORK.TEXT_MODELS, 'stt-danish'], 'other-owner', None, 0),
                  ([*NETWORK.TEXT_MODELS, 'stt-danish'], 'network-inference', '2026-10-14', 0),
                  (NETWORK.TEXT_MODELS, 'network-inference', None, 0),
                  (['*'], 'network-inference', None, 0),
                  ([*NETWORK.TEXT_MODELS, 'stt-danish', 'assistant-canary'], 'network-inference', None, 0),
                  ([*NETWORK.TEXT_MODELS, 'stt-danish'], 'network-inference', None, 1)]
        for models, owner, expires, status in scopes:
            with self.subTest(models=models, owner=owner, expires=expires, status=status), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'key'
                target.write_text('sk-synthetic-network\n')
                response = subprocess.CompletedProcess([], status, json.dumps({
                    'models': models, 'expires': expires, 'user_id': owner,
                }), '')
                with patch.object(NETWORK, 'KEY_FILE', target), \
                     patch.object(NETWORK.os, 'geteuid', return_value=0), \
                     patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                     patch.object(NETWORK.subprocess, 'run', return_value=response) as internal, \
                     self.assertRaises(SystemExit):
                    NETWORK.provision(enable_whisper=True)
                self.assertEqual(internal.call_count, 1)

    def test_whisper_migration_fails_if_backend_does_not_apply_exact_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'key'
            target.write_text('sk-synthetic-network\n')
            old = subprocess.CompletedProcess([], 0, json.dumps({
                'models': [*NETWORK.TEXT_MODELS, 'stt-danish'], 'expires': None, 'user_id': 'network-inference',
            }), '')
            with patch.object(NETWORK, 'KEY_FILE', target), \
                 patch.object(NETWORK.os, 'geteuid', return_value=0), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, 'run', side_effect=[old, subprocess.CompletedProcess([], 0, '', ''), old]) as internal, \
                 self.assertRaises(SystemExit):
                NETWORK.provision(enable_whisper=True)
            self.assertEqual(internal.call_count, 3)

    def test_whisper_migration_rechecks_owner_and_expiry_after_backend_update(self):
        for owner, expires in [('other-owner', None), ('network-inference', '2026-10-15')]:
            with self.subTest(owner=owner, expires=expires), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'key'
                target.write_text('sk-synthetic-network\n')
                old = subprocess.CompletedProcess([], 0, json.dumps({
                    'models': [*NETWORK.TEXT_MODELS, 'stt-danish'], 'expires': None, 'user_id': 'network-inference',
                }), '')
                changed = subprocess.CompletedProcess([], 0, json.dumps({
                    'models': NETWORK.MODELS, 'expires': expires, 'user_id': owner,
                }), '')
                with patch.object(NETWORK, 'KEY_FILE', target), \
                     patch.object(NETWORK.os, 'geteuid', return_value=0), \
                     patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                     patch.object(NETWORK.subprocess, 'run', side_effect=[old, subprocess.CompletedProcess([], 0, '', ''), changed]) as internal, \
                     self.assertRaisesRegex(SystemExit, 'reviewed scope, owner, and expiry'):
                    NETWORK.provision(enable_whisper=True)
                self.assertEqual(internal.call_count, 3)
                self.assertEqual(target.read_text(), 'sk-synthetic-network\n')
    def test_existing_revoked_credential_fails_without_regranting_access(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "key"
            target.write_text("sk-revoked-network\n")
            response = subprocess.CompletedProcess([], 1, "", "internal authorization failure")
            with patch.object(NETWORK, "KEY_FILE", target), \
                 patch.object(NETWORK.os, "geteuid", return_value=0), \
                 patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, "run", return_value=response) as internal, \
                 self.assertRaisesRegex(SystemExit, "invalid in LiteLLM"):
                NETWORK.provision()
            self.assertEqual(target.read_text(), "sk-revoked-network\n")
            self.assertEqual(internal.call_count, 1)
            self.assertNotIn("sk-revoked-network", " ".join(internal.call_args.args[0]))

    def test_stt_migration_is_explicit_and_rechecks_exact_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'key'
            target.write_text('sk-synthetic-network\n')
            old = subprocess.CompletedProcess([], 0, json.dumps({
                'models': NETWORK.TEXT_MODELS, 'expires': None, 'user_id': 'network-inference',
            }), '')
            updated = subprocess.CompletedProcess([], 0, json.dumps({
                'models': NETWORK.STT_MODELS, 'expires': None, 'user_id': 'network-inference',
            }), '')
            with patch.object(NETWORK, 'KEY_FILE', target), \
                 patch.object(NETWORK.os, 'geteuid', return_value=0), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, 'run', return_value=old) as internal:
                with self.assertRaisesRegex(SystemExit, '--enable-stt'):
                    NETWORK.provision()
                self.assertEqual(internal.call_count, 1)
            with patch.object(NETWORK, 'KEY_FILE', target), \
                 patch.object(NETWORK.os, 'geteuid', return_value=0), \
                 patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                 patch.object(NETWORK.subprocess, 'run', side_effect=[old, subprocess.CompletedProcess([], 0, '', ''), updated]) as internal, \
                 contextlib.redirect_stdout(io.StringIO()):
                NETWORK.provision(enable_stt=True)
                self.assertIn('/key/update', internal.call_args_list[1].kwargs['input'])
                for call in internal.call_args_list:
                    self.assertNotIn('sk-synthetic-network', ' '.join(call.args[0]))
            self.assertEqual(target.read_text(), 'sk-synthetic-network\n')

    def test_stt_migration_never_widens_unexpected_owner_scope_or_expiry(self):
        for models, owner, expires in [(['*'], 'network-inference', None),
                                       (NETWORK.TEXT_MODELS, 'another-owner', None),
                                       (NETWORK.TEXT_MODELS, 'network-inference', '2026-10-14')]:
            with self.subTest(models=models, owner=owner, expires=expires), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'key'
                target.write_text('sk-synthetic-network\n')
                old = subprocess.CompletedProcess([], 0, json.dumps({
                    'models': models, 'expires': expires, 'user_id': owner,
                }), '')
                with patch.object(NETWORK, 'KEY_FILE', target), \
                     patch.object(NETWORK.os, 'geteuid', return_value=0), \
                     patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_uid=0)), \
                     patch.object(NETWORK.subprocess, 'run', return_value=old) as internal, \
                     self.assertRaises(SystemExit):
                    NETWORK.provision(enable_stt=True)
                self.assertEqual(internal.call_count, 1)

    def test_generated_credential_is_private_and_never_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "key"
            response = subprocess.CompletedProcess([], 0, json.dumps({
                "key": "sk-synthetic-network", "models": NETWORK.MODELS, "expires": None,
            }), "")
            with patch.object(NETWORK, "KEY_FILE", target), \
                 patch.object(NETWORK.os, "geteuid", return_value=0), \
                 patch.object(NETWORK.subprocess, "run", return_value=response), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                NETWORK.provision()
            self.assertEqual(target.read_text(), "sk-synthetic-network\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("sk-synthetic-network", output.getvalue())

    def test_unrestricted_or_expiring_credentials_are_not_installed(self):
        for models, expires in [(["*"], None), (NETWORK.MODELS, "2026-10-14")]:
            with self.subTest(models=models, expires=expires), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "key"
                response = subprocess.CompletedProcess([], 0, json.dumps({
                    "key": "sk-synthetic-network", "models": models, "expires": expires,
                }), "")
                with patch.object(NETWORK, "KEY_FILE", target), \
                     patch.object(NETWORK.os, "geteuid", return_value=0), \
                     patch.object(NETWORK.subprocess, "run", return_value=response), \
                     self.assertRaises(SystemExit):
                    NETWORK.provision()
                self.assertFalse(target.exists())

    def test_symlink_refused_before_key_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "key"
            target.symlink_to(Path(directory) / "missing")
            with patch.object(NETWORK, "KEY_FILE", target), \
                 patch.object(NETWORK.os, "geteuid", return_value=0), \
                 patch.object(NETWORK.subprocess, "run") as generate, \
                 self.assertRaises(SystemExit):
                NETWORK.provision()
            generate.assert_not_called()


class LocalOnlyControlPlaneTests(unittest.TestCase):
    def test_litellm_config_is_generated_from_canonical_routes(self) -> None:
        catalog = REGISTRY.load_json(ROOT / "config/model-catalog.json")
        routes = REGISTRY.load_json(ROOT / "config/capability-routes.json")
        REGISTRY.validate(catalog, routes)
        current = LITELLM.read_text(encoding="utf-8")
        self.assertEqual(current, REGISTRY.render_config(current, catalog, routes))

    def test_only_active_capabilities_and_isolated_migration_lanes_are_rendered(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        names = set(re.findall(r'^\s*- model_name:\s*"?([^"\s]+)', text, re.MULTILINE))
        self.assertEqual({"automation", "home", "automation-moe", "automation-moe-nvidia", "automation-qualification", "assistant-canary", "stt-danish", "whisper-turbo", "plapre"}, names)
        for unavailable in ("auto", "coding", "research", "meeting", "assistant"):
            self.assertNotIn(f"model_name: {unavailable}", text)

    def test_deployment_mappings_and_timeouts_are_capability_scoped(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        self.assertRegex(text, r'(?s)model_name: "automation".*?openai/automation-moe.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "automation-moe".*?openai/automation-moe.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "automation-qualification".*?openai/automation-qualification.*?timeout: 600')
        self.assertRegex(text, r'(?s)model_name: "home".*?openai/home-fast.*?timeout: 20')
        self.assertRegex(text, r'(?s)model_name: "home".*?stream_timeout: 20')
        self.assertRegex(text, r'(?s)model_name: "assistant-canary".*?openai/automation-qualification.*?timeout: 120')
        self.assertNotIn("automation-backup", text)
        self.assertIn("request_timeout: 600", text)
        self.assertIn("num_retries: 0", text)

    def test_security_settings_are_preserved_by_rendering(self) -> None:
        text = LITELLM.read_text(encoding="utf-8")
        self.assertIn("turn_off_message_logging: true", text)
        self.assertIn("redact_user_api_key_info: true", text)
        self.assertIn("disable_spend_logs: false", text)
        self.assertIn("store_prompts_in_spend_logs: false", text)
        self.assertIn("disable_error_logs: true", text)
        self.assertIn("master_key: os.environ/LITELLM_MASTER_KEY", text)
        self.assertIn("database_url: os.environ/DATABASE_URL", text)

    def test_checked_in_rollback_files_retain_known_routing_states(self) -> None:
        rollback = ROLLBACK.read_text(encoding="utf-8")
        maintenance = LITELLM_BACKUP.read_text(encoding="utf-8")
        self.assertIn("model_name: automation-moe", rollback)
        self.assertIn("model_name: assistant-canary", rollback)
        self.assertRegex(maintenance, r"(?s)model: openai/automation-backup.*?order: 1")
        self.assertRegex(maintenance, r"(?s)model: openai/automation\s+order: 2")

    def test_gateway_has_no_cloud_provider_configuration(self) -> None:
        text = "\n".join(
            [
                LITELLM.read_text(encoding="utf-8"),
                ROLLBACK.read_text(encoding="utf-8"),
                LITELLM_BACKUP.read_text(encoding="utf-8"),
                (ROOT / "deploy/control-plane/compose.yaml").read_text(encoding="utf-8"),
                (ROOT / "config/control-plane.env.example").read_text(encoding="utf-8"),
            ]
        ).lower()
        for forbidden in (
            "anthropic_api_key",
            "openai_api_key",
            "gemini_api_key",
            "api.openai.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
            "openrouter.ai",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_compute_backend_serves_only_an_internal_deployment_name(self) -> None:
        text = COMPUTE.read_text(encoding="utf-8")
        self.assertIn("--served-model-name general-spark-qwen38", text)
        self.assertIsNone(re.search(r"--served-model-name\s+(?:auto|coding|automation)(?:\s|$)", text))
        self.assertIn("COMPUTE_HOST_PORTS", (ROOT / "config/compute-node.env.example").read_text(encoding="utf-8"))

    def test_existing_client_key_scope_is_still_separate(self) -> None:
        deployment = re.sub(r"\s+", " ", DEPLOYMENT.read_text(encoding="utf-8"))
        self.assertIn("each list only `automation-moe`", deployment)
        self.assertIn("returns HTTP 403", deployment)
        self.assertIn("does not change key permissions", deployment)


if __name__ == "__main__":
    unittest.main()
