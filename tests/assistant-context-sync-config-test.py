#!/usr/bin/env python3
"""Focused contract tests for private source-selection planning."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "assistant_context_sync_setup", ROOT / "scripts/setup-assistant-context-sync.py")
setup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(setup)
SYNC_SPEC = importlib.util.spec_from_file_location(
    "assistant_context_sync", ROOT / "scripts/assistant-context-sync.py")
sync = importlib.util.module_from_spec(SYNC_SPEC)
assert SYNC_SPEC.loader is not None
SYNC_SPEC.loader.exec_module(sync)


class SyncConfigSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = self.root / "var" / "lib"
        self.parent.mkdir(parents=True)
        self.target = self.parent / "homecompute-assistant" / "context-sync.json"
        self.uid, self.gid = os.geteuid(), os.getegid()
        self.example = ROOT / "config/assistant-context-sync.example.json"
        self.bootstrap = setup.SyncConfigBootstrap(
            target=self.target, example=self.example, service_uid=self.uid,
            service_gid=self.gid, root_uid=self.uid, root_gid=self.gid)
        self.revision = "a" * 40

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fixed_private_selection_defaults_aula_off_and_is_schema_valid(self) -> None:
        encoded, _ = setup.build_config([])
        config_path = self.root / "config.json"
        config_path.write_bytes(encoded)
        os.chmod(config_path, 0o600)
        config = sync.load_config(config_path)
        self.assertFalse(config["aula"]["enabled"])
        self.assertEqual(config["aula"]["profile_ids"], [])
        self.assertEqual(config["calendar"], {
            "enabled": True, "aliases": ["mads", "family"],
            "rolling": {"past_days": 1, "future_days": 7}})
        self.assertEqual(config["daythread"], {"enabled": True, "host": "daythread.home.arpa"})

    def test_explicit_profile_ids_are_canonical_and_not_in_plan(self) -> None:
        first, _ = setup.build_config([93, 14])
        second, _ = setup.build_config([14, 93])
        self.assertEqual(first, second)
        plan = self.bootstrap.plan(self.revision, [14, 93])
        self.assertEqual(plan["aula_profile_count"], 2)
        self.assertNotIn("profile_ids", plan)

    def test_selection_input_is_bounded_unique_and_exact_schema(self) -> None:
        self.assertEqual(setup.read_selection(io.StringIO('{"aula_profile_ids":[7,2]}')), [2, 7])
        for raw in (
            '{"aula_profile_ids":[2,2]}',
            '{"aula_profile_ids":[0]}',
            '{"aula_profile_ids":[true]}',
            '{"aula_profile_ids":[],"other":true}',
            '{"aula_profile_ids":[],"aula_profile_ids":[]}',
        ):
            with self.subTest(raw=raw), self.assertRaises(setup.SetupRefused):
                setup.read_selection(io.StringIO(raw))

    def test_plan_binds_revision_and_apply_creates_private_config_idempotently(self) -> None:
        plan = self.bootstrap.plan(self.revision, [])
        self.assertEqual(plan["state"], "absent")
        self.assertNotEqual(plan["plan_sha256"], self.bootstrap.plan("b" * 40, [])["plan_sha256"])
        self.assertNotIn("revision", self.bootstrap.inspect([]))
        with self.assertRaises(setup.SetupRefused):
            self.bootstrap.apply(self.revision, [], "0" * 64)
        created = self.bootstrap.apply(self.revision, [], plan["plan_sha256"])
        self.assertEqual(created["status"], "created")
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.target.parent.stat().st_mode & 0o777, 0o700)
        installed = self.target.read_bytes()
        second_plan = self.bootstrap.plan(self.revision, [])
        self.assertEqual(second_plan["state"], "matching")
        already = self.bootstrap.apply(self.revision, [], second_plan["plan_sha256"])
        self.assertEqual(already["status"], "already_configured")
        self.assertEqual(self.target.read_bytes(), installed)

    def test_apply_refuses_target_drift_and_unsafe_symlink(self) -> None:
        self.target.parent.mkdir(mode=0o700)
        os.chown(self.target.parent, self.uid, self.gid)
        self.target.write_text("{}\n")
        os.chmod(self.target, 0o600)
        with self.assertRaises(setup.SetupRefused):
            self.bootstrap.apply(self.revision, [], self.bootstrap.plan(self.revision, [])[
                "plan_sha256"])
        self.target.unlink()
        self.target.symlink_to(self.example)
        with self.assertRaises(setup.SetupRefused):
            self.bootstrap.plan(self.revision, [])

    def test_reviewed_profile_selection_update_retains_private_before_image(self) -> None:
        initial = self.bootstrap.plan(self.revision, [])
        self.bootstrap.apply(self.revision, [], initial["plan_sha256"])
        before = self.target.read_bytes()
        selected = self.bootstrap.plan(self.revision, [14, 93])
        self.assertEqual(selected["state"], "selection_change")
        self.assertEqual(selected["preimage_sha256"], setup.digest(before))
        result = self.bootstrap.apply(self.revision, [14, 93], selected["plan_sha256"])
        self.assertEqual(result["status"], "updated")
        config = sync.load_config(self.target)
        self.assertEqual(config["aula"]["profile_ids"], [14, 93])
        self.assertTrue(config["aula"]["enabled"])
        backup = self.target.parent / "selection-history" / (setup.digest(before) + ".json")
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(backup.stat().st_mode & 0o7777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o7777, 0o700)
        fresh = self.bootstrap.plan(self.revision, [14, 93])
        self.assertEqual(self.bootstrap.apply(self.revision, [14, 93], fresh["plan_sha256"])["status"],
                         "already_configured")

    def test_selection_update_rejects_stale_plan_and_unrelated_config_changes(self) -> None:
        initial = self.bootstrap.plan(self.revision, [])
        self.bootstrap.apply(self.revision, [], initial["plan_sha256"])
        old_plan = self.bootstrap.plan(self.revision, [14, 93])
        other_plan = self.bootstrap.plan(self.revision, [14])
        self.bootstrap.apply(self.revision, [14], other_plan["plan_sha256"])
        current = self.target.read_bytes()
        with self.assertRaises(setup.SetupRefused):
            self.bootstrap.apply(self.revision, [14, 93], old_plan["plan_sha256"])
        self.assertEqual(self.target.read_bytes(), current)
        unrelated = json.loads(current)
        unrelated['calendar']['aliases'] = ['family']
        self.target.write_bytes(setup.canonical(unrelated))
        plan = self.bootstrap.plan(self.revision, [14, 93])
        self.assertEqual(plan['state'], 'drift')
        with self.assertRaises(setup.SetupRefused):
            self.bootstrap.apply(self.revision, [14, 93], plan['plan_sha256'])
        self.assertEqual(self.target.read_bytes(), setup.canonical(unrelated))

    def test_published_guard_checks_exact_rooted_release_and_tracked_bytes(self) -> None:
        releases = self.root / "releases"
        releases.mkdir()
        staging = self.root / "staging"
        staging.mkdir()
        for relative in ("scripts/setup-assistant-context-sync.py",
                         "config/assistant-context-sync.example.json"):
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(staging), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git("init", "--initial-branch=main")
        git("config", "user.name", "Sync config test")
        git("config", "user.email", "sync-config-test@example.invalid")
        git("add", "scripts/setup-assistant-context-sync.py",
            "config/assistant-context-sync.example.json")
        git("commit", "-m", "test source release")
        revision = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/master", revision)
        release_root = releases / revision
        staging.rename(release_root)
        setup.verify_published_source(release_root, revision, releases_dir=releases,
                                      root_uid=self.uid)

        for relative in ("scripts/setup-assistant-context-sync.py",
                         "config/assistant-context-sync.example.json"):
            source = release_root / relative
            original = source.read_bytes()
            source.write_bytes(original + b"untracked edit\n")
            with self.subTest(relative=relative), self.assertRaises(setup.SetupRefused):
                setup.verify_published_source(release_root, revision, releases_dir=releases,
                                              root_uid=self.uid)
            source.write_bytes(original)

        release_root.chmod(0o775)
        with self.assertRaises(setup.SetupRefused):
            setup.verify_published_source(release_root, revision, releases_dir=releases,
                                          root_uid=self.uid)
        release_root.chmod(0o755)

        with self.assertRaises(setup.SetupRefused):
            setup.verify_published_source(self.root, revision, releases_dir=releases,
                                          root_uid=self.uid)


if __name__ == "__main__":
    unittest.main()
