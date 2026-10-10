#!/usr/bin/env python3
"""Backup gates, immutable ownership, and durable mutation-failure fences."""
from contextlib import closing
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recovery", ROOT / "scripts/restore-openclaw-nemoclaw.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def config():
    return {"gateway": {"auth": {"token": "old-synthetic"}, "reload": {"mode": "off"}},
            "models": {"providers": {"inference": {"baseUrl": "https://inference.local/v1", "apiKey": "unused"}}},
            "proxy": {"enabled": True},
            "agents": {"entries": {"main": {"tools": {"allow": sorted(MOD.TOOLS), "deny": sorted(MOD.DENIES)}}}},
            "tools": {"allow": sorted(MOD.TOOLS), "deny": sorted(MOD.DENIES | MOD.BLOCKED_TOOLS)},
            "hooks": {"enabled": False}, "cron": {"enabled": False}, "browser": {"enabled": False},
            "plugins": {"allow": ["nemoclaw", "memory-core", "homecompute-broker"],
                        "entries": {"nemoclaw": {"enabled": True}, "memory-core": {"enabled": True},
                                    "homecompute-broker": {"enabled": False}}}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.context = Mock(state=self.directory, uuid="old-id", generation="old-generation",
                            container="exact-old-container", cli="/fixed/nemoclaw")
        self.context.observe.return_value = "Error"

    def backup(self):
        path = self.directory / "backup/.openclaw"
        (path / "workspace").mkdir(parents=True)
        (path / "agents/main").mkdir(parents=True)
        (path / "openclaw.json").write_text(json.dumps(config()))
        (path / "workspace/memory.md").write_text("synthetic retained memory")
        with closing(sqlite3.connect(path / "agents/main/sessions.db")) as db, db:
            db.execute("CREATE TABLE sessions (id TEXT)")
            db.execute("INSERT INTO sessions VALUES (?)", ("retained-session",))
        return path

    def test_merge_preserves_new_auth_model_proxy_native_install(self):
        old = config()
        native = copy.deepcopy(old)
        native["gateway"]["auth"]["token"] = "new-synthetic"
        native["plugins"]["installs"] = {"nemoclaw": {"source": "native-new"}}
        merged = MOD.merge_managed_config(old, native)
        for key in ("models", "proxy"):
            self.assertEqual(merged[key], native[key])
        self.assertEqual(merged["gateway"]["auth"], native["gateway"]["auth"])
        self.assertEqual(merged["plugins"]["installs"], native["plugins"]["installs"])
        self.assertFalse(merged["plugins"]["entries"]["homecompute-broker"]["enabled"])

    def test_grants_extra_agents_and_enabled_channels_refused(self):
        for mutate in (lambda x: x["tools"]["deny"].remove("write"),
                       lambda x: x["tools"]["deny"].append("session_status"),
                       lambda x: x["tools"]["allow"].append("exec"),
                       lambda x: x["agents"]["entries"].update(other={}),
                       lambda x: x["hooks"].update(enabled=True),
                       lambda x: x["plugins"]["entries"]["homecompute-broker"].update(enabled=True)):
            value = config()
            mutate(value)
            with self.assertRaises(MOD.RecoveryRefused):
                MOD.validate_safe_config(value)

    def test_backup_verifies_conversation_and_workspace_hashes(self):
        hashes = MOD.verify_backup(self.backup())
        self.assertIn("agents/main/sessions.db", hashes)
        self.assertIn("workspace/memory.md", hashes)

    def test_logical_conversations_allow_boot_metadata_but_refuse_transcript_drift(self):
        path = self.backup()
        native = path / "agents/main/agent/openclaw-agent.sqlite"
        native.parent.mkdir()
        with closing(sqlite3.connect(native)) as db, db:
            db.execute("CREATE TABLE session_nodes (id TEXT PRIMARY KEY, title TEXT)")
            db.execute("CREATE TABLE session_windows (id TEXT PRIMARY KEY, metadata TEXT)")
            db.execute("CREATE TABLE transcript_events (id TEXT PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO session_nodes VALUES ('stable-id', 'original')")
            db.execute("INSERT INTO session_windows VALUES ('stable-window', 'old-lease')")
            db.execute("INSERT INTO transcript_events VALUES ('stable-event', 'retained-text')")
        original = MOD.logical_conversations(path)
        with closing(sqlite3.connect(native)) as db, db:
            db.execute("UPDATE session_windows SET metadata='new-lease'")
        self.assertEqual(MOD.logical_conversations(path), original)
        with closing(sqlite3.connect(native)) as db, db:
            db.execute("UPDATE transcript_events SET payload='changed-text'")
        self.assertNotEqual(MOD.logical_conversations(path), original)

    def test_corrupt_machine_database_refuses_backup_before_delete(self):
        path = self.backup()
        (path / "state").mkdir()
        (path / "state/openclaw.sqlite").write_bytes(b"SQLite format 3\x00" + b"corrupt")
        with self.assertRaises(MOD.RecoveryRefused):
            MOD.verify_backup(path)

    def test_backup_symlink_and_missing_state_refused(self):
        path = self.backup()
        (path / "agents/main/link").symlink_to(path / "workspace/memory.md")
        with self.assertRaises(MOD.RecoveryRefused):
            MOD.verify_backup(path)
        with self.assertRaises(MOD.RecoveryRefused):
            MOD.verify_backup(self.directory / "missing")

    def test_only_exact_native_broker_sdk_link_is_accepted_before_delete(self):
        path = self.backup()
        broker = path / "extensions/homecompute-broker"
        (broker / "node_modules").mkdir(parents=True)
        link = broker / "node_modules/openclaw"
        link.symlink_to("/usr/local/lib/nemoclaw/openclaw-runtime/node_modules/openclaw")
        MOD.verify_backup(path)
        link.unlink()
        link.symlink_to("/etc/shadow")
        with self.assertRaises(MOD.RecoveryRefused):
            MOD.verify_backup(path)

    def test_resume_requires_exact_failed_journal_and_backup_containment(self):
        transaction = MOD.Transaction(self.context)
        MOD.atomic_json(transaction.path, {"status": "complete", "newId": "id"})
        with self.assertRaises(MOD.RecoveryRefused):
            transaction.resume()
        MOD.atomic_json(transaction.path, {"status": "failed", "newId": "id", "backup": "/untrusted"})
        with self.assertRaises(MOD.RecoveryRefused):
            transaction.resume()
        self.context.run.assert_not_called()

    def test_previous_mutation_failure_or_active_transaction_blocks(self):
        transaction = MOD.Transaction(self.context)
        for status in ("failed", "active"):
            MOD.atomic_json(transaction.path, {"status": status})
            with self.assertRaises(MOD.RecoveryRefused):
                transaction.preflight()
        self.context.run.assert_not_called()

    def test_hourly_budget_is_durable_and_pre_mutation_refusal_does_not_count(self):
        transaction = MOD.Transaction(self.context)
        MOD.atomic_json(transaction.path, {"status": "complete", "startedAt": time.time()})
        with self.assertRaises(MOD.RecoveryRefused):
            transaction.preflight()
        MOD.atomic_json(transaction.path, {"status": "refused", "startedAt": time.time()})
        transaction.inspect = Mock()
        transaction.preflight()

    def test_container_identity_requires_exact_image_memory_swap_gpu_and_volume(self):
        transaction = MOD.Transaction(self.context)
        data = {"image": MOD.IMAGE, "memory": MOD.MEMORY, "swap": MOD.MEMORY,
                "gpu": None, "status": "exited", "mounts": [{"Destination": "/sandbox/.openclaw",
                "Type": "volume", "Source": MOD.VOLUME}]}
        transaction.command = Mock(return_value=json.dumps(data).encode())
        transaction.inspect(old=True)
        for key, value in (("memory", 16 * 1024 ** 3), ("swap", 2 * MOD.MEMORY),
                           ("gpu", [{"Count": 1}]), ("image", "unknown"), ("status", "running")):
            modified = dict(data, **{key: value})
            transaction.command.return_value = json.dumps(modified).encode()
            with self.assertRaises(MOD.RecoveryRefused):
                transaction.inspect(old=True)

    def test_missing_backup_never_reaches_native_destroy(self):
        transaction = MOD.Transaction(self.context)
        transaction.preflight = Mock()
        transaction.command = Mock(return_value=b"")
        with self.assertRaises(MOD.RecoveryRefused):
            transaction.execute()
        self.assertTrue(all("destroy" not in call.args[0] for call in transaction.command.call_args_list))

    def test_failure_after_native_delete_is_durable_and_preserves_backup(self):
        backup = self.backup()
        transaction = MOD.Transaction(self.context)
        transaction.preflight = Mock()
        transaction.inspect = Mock()
        calls = []
        def command(argv, label, **options):
            calls.append(argv)
            if label == "native-destroy":
                raise MOD.RecoveryRefused("simulated-partial-delete")
            return b""
        transaction.command = command
        with patch.object(MOD, "verify_backup", return_value={"agents/main/sessions.db": "hash"}), \
                patch.object(MOD.json, "loads", wraps=MOD.json.loads), \
                patch.object(MOD.Path, "read_bytes", return_value=json.dumps(config()).encode()):
            with self.assertRaises(MOD.RecoveryRefused):
                transaction.execute()
        record = json.loads(transaction.path.read_text())
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["stage"], "failed-after-mutation")
        self.assertEqual(self.context.uuid, "old-id")
        self.assertTrue(backup.is_dir())
        self.assertEqual(calls[-1][2:], ["destroy", "--yes", "--no-cleanup-gateway"])

    def test_atomic_journal_is_private_and_does_not_include_auth(self):
        path = self.directory / "journal.json"
        MOD.atomic_json(path, {"status": "complete", "newId": "public-id"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("token", path.read_text())


if __name__ == "__main__":
    unittest.main()
