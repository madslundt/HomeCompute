#!/usr/bin/env python3
"""Hermes native-home snapshot keeps durable state and excludes credentials."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hermes_snapshot", ROOT / "scripts/hermes-native-home-snapshot.py"
)
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)
BACKUP_SPEC = importlib.util.spec_from_file_location(
    "nas_backup", ROOT / "scripts/selected-nas-recovery-backup.py"
)
backup = importlib.util.module_from_spec(BACKUP_SPEC)
BACKUP_SPEC.loader.exec_module(backup)
EXPORTER = (ROOT / "scripts/export-hermes-state.sh").read_text()


class HermesNativeHomeSnapshotTest(unittest.TestCase):
    def test_export_downloads_transient_archive_from_sandbox_workspace(self):
        self.assertIn("readonly SANDBOX_ARCHIVE=/sandbox/.homecompute-hermes-native-home.tar.gz", EXPORTER)
        self.assertIn('HERMES_SNAPSHOT_OUTPUT="$SANDBOX_ARCHIVE" python3 -', EXPORTER)
        self.assertIn('download "$SANDBOX_ARCHIVE"', EXPORTER)
        self.assertIn('rm -f "$SANDBOX_ARCHIVE"', EXPORTER)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = root / ".hermes"
        self.source.mkdir()
        self.output = root / "hermes.tar.gz"
        snapshot.SOURCE = self.source
        snapshot.OUTPUT = self.output

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_keeps_agent_state_and_excludes_runtime_secrets_and_cache(self):
        (self.source / "MEMORY.md").write_text("synthetic memory\n")
        memories = self.source / "memories"
        memories.mkdir()
        (memories / "MEMORY.md").write_text("synthetic long-term memory\n")
        (memories / "USER.md").write_text("synthetic user profile\n")
        cron = self.source / "cron"
        cron.mkdir()
        (cron / "jobs.json").write_text('{"jobs":["synthetic"]}\n')
        relay = self.source / "bot_relay"
        relay.mkdir()
        (relay / "roster.json").write_text('{"bots":["synthetic"]}\n')
        (self.source / "config.yaml").write_text("synthetic generated config\n")
        (self.source / ".env").write_text("SYNTHETIC_TOKEN=not-a-secret\n")
        (self.source / ".env.bak-20261111").write_text("SYNTHETIC_TOKEN=not-a-secret\n")
        (self.source / "auth.json").write_text('{"api_key":"not-a-secret"}\n')
        (self.source / "backups" / "config").mkdir(parents=True)
        (self.source / "backups" / "config" / "config.yaml.good").write_text("not user data\n")
        (self.source / "lazy-packages").mkdir()
        (self.source / "lazy-packages" / "reinstallable.py").write_text("# synthetic package\n")
        (self.source / "logs").mkdir()
        (self.source / "logs" / "runtime.log").write_text("synthetic log\n")
        profile = self.source / "profiles" / "test"
        profile.mkdir(parents=True)
        (profile / ".env").write_text("SYNTHETIC_PROFILE_TOKEN=not-a-secret\n")
        (profile / "SOUL.md").write_text("synthetic profile\n")
        runtime = self.source / "runtime"
        runtime.mkdir()
        database = sqlite3.connect(runtime / "state.db")
        database.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        database.execute("INSERT INTO sessions VALUES ('synthetic-session')")
        database.commit()
        database.close()
        (self.source / "state.db").symlink_to("runtime/state.db")
        (runtime / "gateway_state.json").write_text('{"gateway":"synthetic"}\n')
        (self.source / "gateway_state.json").symlink_to("runtime/gateway_state.json")
        (runtime / "control").mkdir()
        (runtime / "control" / "gateway.sock").touch()
        (runtime / "gateway.loop-tick.123.sock").touch()
        (self.source / "state.db-wal").write_text("synthetic stale WAL sidecar\n")
        (self.source / ".homecompute-owner-sol-before.json").write_text("synthetic incident preimage")
        preimages = self.source / ".homecompute-mobile-plugin"
        preimages.mkdir()
        (preimages / "copy.txt").write_text("another synthetic preimage")

        result = snapshot.snapshot()

        with tarfile.open(result, "r:gz") as archive:
            names = set(archive.getnames())
            self.assertIn("manifest.json", names)
            self.assertIn("native-home/.hermes/MEMORY.md", names)
            self.assertIn("native-home/.hermes/memories/MEMORY.md", names)
            self.assertIn("native-home/.hermes/memories/USER.md", names)
            self.assertIn("native-home/.hermes/cron/jobs.json", names)
            self.assertIn("native-home/.hermes/bot_relay/roster.json", names)
            self.assertIn("native-home/.hermes/profiles/test/SOUL.md", names)
            self.assertIn("native-home/.hermes/state.db", names)
            self.assertIn("native-home/.hermes/runtime/state.db", names)
            self.assertIn("native-home/.hermes/gateway_state.json", names)
            self.assertNotIn("native-home/.hermes/state.db-wal", names)
            self.assertFalse(any("/.homecompute-" in name for name in names))
            self.assertFalse(any(name.endswith(".sock") for name in names))
            self.assertNotIn("native-home/.hermes/config.yaml", names)
            self.assertNotIn("native-home/.hermes/auth.json", names)
            self.assertNotIn("native-home/.hermes/.env.bak-20261111", names)
            self.assertNotIn("native-home/.hermes/backups/config/config.yaml.good", names)
            self.assertNotIn("native-home/.hermes/lazy-packages/reinstallable.py", names)
            self.assertFalse(any(name.endswith("/.env") for name in names))
            self.assertFalse(any("/logs/" in name for name in names))
            manifest = json.load(archive.extractfile("manifest.json"))
            self.assertEqual(manifest["sandbox"], "hermes-personal-v2")
            database_file = archive.extractfile("native-home/.hermes/state.db")
            extracted = Path(self.temporary.name) / "restored-state.db"
            extracted.write_bytes(database_file.read())
        with sqlite3.connect(extracted) as database:
            self.assertEqual(database.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(database.execute("SELECT id FROM sessions").fetchone(), ("synthetic-session",))
        with patch.object(backup, "STATE", Path(self.temporary.name)):
            self.assertEqual(backup.verify_hermes_archive(result)["databases"], 2)
            restored = Path(self.temporary.name) / "scratch-restore"
            restored.mkdir(mode=0o700)
            backup.extract_hermes_archive(result, restored)
        self.assertEqual(
            (restored / "hermes-native-home/.hermes/MEMORY.md").read_text(), "synthetic memory\n"
        )

    def test_snapshot_refuses_symlinks_outside_native_home(self):
        (self.source / "MEMORY.md").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "link"):
            snapshot.snapshot()


if __name__ == "__main__":
    unittest.main()
