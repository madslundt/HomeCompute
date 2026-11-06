#!/usr/bin/env python3
"""Focused tests for the assistant-context external credential bootstrap."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("assistant_context_credentials", ROOT / "scripts/setup-assistant-context-credentials.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
sys.path.insert(0, str(ROOT / "scripts"))
from assistant_context_api import clients as load_server_clients
from assistant_context_tools import private_file_metadata_allowed


class CredentialBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.etc = self.root / "etc/homecompute"
        self.openclaw = self.root / "var/lib/homecompute-openclaw/config"
        self.assistant = self.root / "var/lib/homecompute-assistant"
        for path, mode in ((self.etc, 0o755), (self.openclaw, 0o700)):
            path.mkdir(mode=mode, parents=True)
            os.chmod(path, mode)
        self.clients = self.etc / "assistant-context-clients.json"
        self.owner = self.openclaw / "assistant-context-token"
        self.collector = self.assistant / "context-ingest-token"
        self.installer = MODULE.CredentialBootstrap(
            clients_path=self.clients,
            owner_token_path=self.owner,
            collector_token_path=self.collector,
            openclaw_dir=self.openclaw,
            assistant_dir=self.assistant,
            etc_dir=self.etc,
            root_uid=self.uid,
            root_gid=self.gid,
            service_uid=self.uid,
            service_gid=self.gid,
        )
        self.revision = "a" * 40

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_plan_is_read_only_and_apply_creates_independent_private_credentials(self) -> None:
        reviewed = self.installer.plan(self.revision)
        self.assertEqual(reviewed["state"], "absent")
        self.assertFalse(self.clients.exists())
        result = self.installer.apply(self.revision, reviewed["plan_sha256"])
        self.assertEqual(result["status"], "created")
        self.assertEqual(set(result), {"status", "paths"})
        self.assertEqual(stat.S_IMODE(self.clients.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.owner.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.collector.stat().st_mode), 0o600)
        self.assertEqual(self.assistant.stat().st_uid, self.uid)
        self.assertEqual(stat.S_IMODE(self.assistant.stat().st_mode), 0o700)

        declaration = json.loads(self.clients.read_text())
        values = MODULE.validate_config(self.clients.read_bytes())
        self.assertEqual(len(set(values.values())), 4)
        self.assertEqual(self.owner.read_text().strip(), values["hermes-owner"])
        self.assertEqual(self.collector.read_text().strip(), values["owner-source-sync"])
        self.assertEqual([row["name"] for row in declaration["clients"]], [
            "policy-health", "hermes-owner", "owner-source-sync", "owner-briefing-workflows",
        ])
        self.assertEqual(declaration["clients"][1]["origin_id"], "owner-mattermost-context")
        rendered_result = json.dumps(result)
        for secret in values.values():
            self.assertNotIn(secret, rendered_result)

    def test_complete_install_is_idempotent_and_does_not_rotate_tokens(self) -> None:
        reviewed = self.installer.plan(self.revision)
        self.installer.apply(self.revision, reviewed["plan_sha256"])
        original = {str(path): MODULE.digest(path.read_bytes()) for path in (self.clients, self.owner, self.collector)}
        second_plan = self.installer.plan(self.revision)
        self.assertEqual(second_plan["state"], "complete")
        result = self.installer.apply(self.revision, second_plan["plan_sha256"])
        self.assertEqual(result["status"], "already_configured")
        self.assertEqual(original, {str(path): MODULE.digest(path.read_bytes()) for path in (self.clients, self.owner, self.collector)})

    def test_generated_declaration_loads_with_server_client_schema(self) -> None:
        values = {
            "policy-health": "P" * 48,
            "hermes-owner": "H" * 48,
            "owner-source-sync": "S" * 48,
            "owner-briefing-workflows": "R" * 48,
        }
        path = self.etc / "server-clients.json"
        path.write_bytes(MODULE.canonical(MODULE.expected_config(values)))
        os.chmod(path, 0o600)

        loaded = load_server_clients(path)
        by_name = {client.name: client for client in loaded}
        self.assertEqual(set(by_name), set(values))
        self.assertEqual({client.role for client in loaded}, {"operator", "assistant", "collector", "routine"})
        self.assertEqual(by_name["hermes-owner"].origin_id, "owner-mattermost-context")
        self.assertEqual(by_name["hermes-owner"].scope.principal_scope, "owner")
        self.assertEqual(by_name["hermes-owner"].scope.data_domain, "personal")
        self.assertEqual(by_name["hermes-owner"].scope.visibility, "private")
        self.assertEqual(by_name["owner-briefing-workflows"].routines,
                         frozenset({"morning-overview", "tomorrow-preparation"}))

    def test_stale_plan_and_partial_state_are_refused_without_mutation(self) -> None:
        reviewed = self.installer.plan(self.revision)
        self.owner.write_text("preserve-existing-token\n")
        with self.assertRaises(MODULE.SetupRefused):
            self.installer.apply(self.revision, reviewed["plan_sha256"])
        self.assertFalse(self.clients.exists())
        self.assertFalse(self.collector.exists())

    def test_unknown_or_unsafe_existing_declaration_is_refused(self) -> None:
        values = {name: "A" * 48 for name in ("policy-health", "hermes-owner", "owner-source-sync", "owner-briefing-workflows")}
        values["owner-source-sync"] = "B" * 48
        values["owner-briefing-workflows"] = "C" * 48
        values["policy-health"] = "D" * 48
        config = MODULE.expected_config(values)
        config["clients"][1]["unknown"] = "preserve"
        for path, owner, raw in (
            (self.clients, self.uid, MODULE.canonical(config)),
            (self.owner, self.uid, (values["hermes-owner"] + "\n").encode()),
            (self.collector, self.uid, (values["owner-source-sync"] + "\n").encode()),
        ):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw)
            os.chmod(path, 0o600)
            os.chown(path, owner, self.gid)
        with self.assertRaises(MODULE.SetupRefused):
            self.installer.plan(self.revision)

    def test_symlinked_credential_path_is_refused(self) -> None:
        self.owner.symlink_to(self.root / "elsewhere")
        with self.assertRaises(MODULE.SetupRefused):
            self.installer.plan(self.revision)


class SystemdCredentialProjectionTests(unittest.TestCase):
    def test_only_fixed_root_owned_systemd_0440_projection_is_allowed(self) -> None:
        projection = Path("/run/credentials/homecompute-assistant-context.service/clients")
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o440, st_nlink=1,
                                   st_uid=0, st_gid=0, st_size=128)
        self.assertTrue(private_file_metadata_allowed(projection, metadata, 991))
        self.assertFalse(private_file_metadata_allowed(Path("/tmp/clients"), metadata, 991))
        self.assertFalse(private_file_metadata_allowed(
            Path("/run/credentials/homecompute-assistant-context-improvements.service/clients"), metadata, 991))
        self.assertFalse(private_file_metadata_allowed(projection, SimpleNamespace(
            st_mode=metadata.st_mode, st_nlink=1, st_uid=0, st_gid=984, st_size=128), 991))
        self.assertFalse(private_file_metadata_allowed(projection, SimpleNamespace(
            st_mode=metadata.st_mode, st_nlink=2, st_uid=0, st_gid=0, st_size=128), 991))


if __name__ == "__main__":
    unittest.main()
