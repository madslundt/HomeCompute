"""Offline exact-approval, service isolation, crash and rollback maintenance tests."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import homepage_maintenance as maintenance

OLD = maintenance.REPOSITORY + "@sha256:" + "a" * 64
NEW = maintenance.REPOSITORY + "@sha256:" + "b" * 64
OLD_ID = "sha256:" + "c" * 64
NEW_ID = "sha256:" + "d" * 64


class FakeRegistry:
    def platform(self, ref, target, os_name, architecture, variant=""):
        if os_name != "linux" or architecture != "amd64":
            raise maintenance.Denied("unsupported platform")
        return {"index_digest": target, "platform_digest": target, "config_digest": OLD_ID if target == OLD.split("@")[1] else NEW_ID}


class Fixture(maintenance.Maintenance):
    """Fixture filesystem trust excludes shared /tmp ancestor; production does not."""
    def trusted(self, path):
        resolved = path.resolve(strict=True)
        if resolved.stat().st_mode & 0o022 or resolved.stat().st_uid != os.getuid():
            raise maintenance.Denied("untrusted fixture file")
        return resolved


class Machine:
    def __init__(self):
        self.calls = []; self.image = OLD; self.healthy = True; self.fail = ""; self.after_pull = None
        self.container_id = "e" * 64; self.mounts = [{"Type": "bind", "Destination": "/app/config", "RW": False, "Source": "/fixed/config"}]
        self.up_count = 0; self.extra_mount = False

    def container(self):
        return {"Name": "/" + maintenance.CONTAINER, "Id": self.container_id,
                "Image": OLD_ID if self.image == OLD else NEW_ID,
                "Config": {"Image": self.image, "Hostname": self.container_id[:12], "Env": ["API_KEY=PRIVATE", "HOMEPAGE_ALLOWED_HOSTS=home-core", "NODE_VERSION=" + self.image[-1]], "Cmd": ["node", "server.js"],
                           "Labels": {"com.docker.compose.project": "homecompute-homepage", "com.docker.compose.service": "homepage",
                                      "com.docker.compose.config-hash": maintenance.digest(self.image)}},
                "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "homecompute-homepage_homepage"},
                "Mounts": self.mounts, "State": {"Running": True, "Health": {"Status": "healthy" if self.healthy else "unhealthy"}}}

    @staticmethod
    def image_metadata(image):
        return {"Id": OLD_ID if image in {OLD, OLD_ID} else NEW_ID, "RepoDigests": [OLD if image in {OLD, OLD_ID} else NEW],
                "Os": "linux", "Architecture": "amd64"}

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[:2] == ["docker", "inspect"]:
            value = [self.container()]
        elif argv[:3] == ["docker", "image", "inspect"]:
            value = [self.image_metadata(argv[-1])]
        elif argv[:2] == ["docker", "compose"]:
            image = OLD
            for index, arg in enumerate(argv):
                if arg == "-f" and argv[index + 1].endswith(".json"):
                    image = json.loads(Path(argv[index + 1]).read_text())["services"]["homepage"]["image"]
            if argv[-3:] == ["config", "--format", "json"]:
                value = {"services": {"homepage": {"image": image, "read_only": True, "user": "1000:1000"}}}
            elif argv[-3:] == ["config", "--hash", "homepage"]:
                return subprocess.CompletedProcess(argv, 0, "homepage " + maintenance.digest(image), "")
            elif argv[-2:] == ["pull", "homepage"]:
                if self.after_pull:
                    self.after_pull()
                if self.fail == "pull":
                    return subprocess.CompletedProcess(argv, 1, "", "PRIVATE")
                value = {}
            elif "up" in argv:
                self.up_count += 1
                self.image = image; self.container_id = str(self.up_count) * 64
                if self.fail == "ambiguous" and self.up_count == 1:
                    self.mounts.append({"Type": "bind", "Destination": "/unsafe", "RW": True, "Source": "/changed"})
                self.healthy = not (self.fail in {"up", "ambiguous", "rollback"} and (self.up_count == 1 or self.fail == "rollback"))
                if not self.healthy:
                    return subprocess.CompletedProcess(argv, 1, "", "PRIVATE")
                value = {}
            else:
                raise AssertionError(argv)
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); directory = Path(self.temp.name)
        release = directory / "release"; homepage = release / "deploy/homepage"
        (homepage / "config").mkdir(parents=True)
        (homepage / "compose.yaml").write_text("reviewed Compose")
        (homepage / "config/services.yaml").write_text("private config token")
        environment = directory / "homepage.env"; environment.write_text("HOMEPAGE_IMAGE=" + OLD)
        candidate = directory / "candidate.json"
        self.manifest = {"schema_version": 1, "candidate_image": NEW, "release_notes": "v2.5 public release notes reviewed",
                         "compatibility_review": "stateless homepage; no config/schema changes", "rollback_supported": True}
        candidate.write_text(json.dumps(self.manifest))
        self.paths = maintenance.Paths(release, environment, candidate, directory / "state", directory / "deploy.lock")
        self.machine = Machine(); self.now = 1000
        self.machine.mounts[0]["Source"] = str((homepage / "config").resolve())
        self.executor = Fixture(self.paths, self.machine, FakeRegistry(), lambda: self.machine.healthy, lambda: self.now, uid=os.getuid())

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self, approve=True):
        job = self.executor.prepare()
        if approve:
            self.executor.approve(job["id"], job["proposal_hash"])
        return job

    def effects(self):
        return [argv for argv, _ in self.machine.calls if "up" in argv or "pull" in argv]

    def test_prepare_is_read_only_idempotent_and_redacts_secrets(self):
        job = self.prepared(False)
        self.assertEqual(self.executor.prepare()["id"], job["id"])
        self.assertEqual(self.effects(), [])
        public = json.dumps(job)
        self.assertNotIn("PRIVATE", public); self.assertNotIn("private config token", public)
        self.assertEqual(job["proposal"]["baseline"]["image"], OLD)
        self.assertEqual(job["proposal"]["candidate_image"], NEW)

    def test_approval_and_hash_are_mandatory(self):
        job = self.prepared(False)
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])
        with self.assertRaises(maintenance.Denied): self.executor.approve(job["id"], "0" * 64)
        self.assertEqual(self.effects(), [])

    def test_service_only_replacement_exact_image_and_backup(self):
        job = self.prepared()
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "succeeded"); self.assertEqual(self.machine.image, NEW)
        effects = self.effects(); self.assertEqual(len(effects), 2)
        self.assertEqual(effects[0][-2:], ["pull", "homepage"])
        self.assertEqual(effects[1][-1], "homepage")
        for flag in ("--no-deps", "--no-build", "never", "--wait"):
            self.assertIn(flag, effects[1])
        self.assertEqual(json.loads((self.paths.state / "active-image.json").read_text())["services"]["homepage"]["image"], NEW)
        backup = json.loads((self.paths.state / (job["id"] + "-backup/manifest.json")).read_text())
        self.assertEqual(backup["previous_image"], OLD); self.assertEqual(len(backup["source_files"]), 3)
        for argv, kwargs in self.machine.calls:
            self.assertEqual(kwargs["env"], maintenance.ENV); self.assertNotIn("shell", kwargs)
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])

    def test_changed_configuration_container_or_candidate_blocks_every_effect(self):
        for kind in ("config", "container", "candidate", "unhealthy"):
            with self.subTest(kind=kind):
                job = self.prepared()
                if kind == "config":
                    self.paths.environment.write_text("changed")
                elif kind == "container":
                    self.machine.container_id = "f" * 64
                elif kind == "candidate":
                    self.paths.candidate.write_text(json.dumps({**self.manifest, "release_notes": "changed"}))
                else:
                    self.machine.healthy = False
                with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])
                self.assertEqual(self.effects(), [])
                self.tearDown(); self.setUp()

    def test_expired_approval_and_proposal_rejected(self):
        job = self.prepared(); self.now += 301
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])
        self.now += 3600
        with self.assertRaises(maintenance.Denied): self.executor.approve(job["id"], job["proposal_hash"])
        self.assertEqual(self.effects(), [])

    def test_changed_baseline_after_pull_prevents_replacement(self):
        job = self.prepared()
        self.machine.after_pull = lambda: self.paths.environment.write_text("changed during pull")
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "failed_before_recreate")
        self.assertEqual(self.machine.up_count, 0)
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])

    def test_approval_expiring_during_pull_prevents_replacement(self):
        job = self.prepared()
        def expire(): self.now += 301
        self.machine.after_pull = expire
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "failed_before_recreate")
        self.assertEqual(self.machine.up_count, 0)

    def test_failure_rolls_back_only_previous_local_digest(self):
        job = self.prepared(); self.machine.fail = "up"
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "rolled_back"); self.assertEqual(self.machine.image, OLD)
        self.assertEqual(self.machine.up_count, 2)
        self.assertEqual(sum("pull" in argv for argv in self.effects()), 1)
        self.assertNotIn("active-image.json", [path.name for path in self.paths.state.iterdir()])
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])

    def test_ambiguous_runtime_never_rolls_back_and_blocks_new_proposals(self):
        job = self.prepared(); self.machine.fail = "ambiguous"
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "reconciliation_required"); self.assertEqual(self.machine.up_count, 1)
        with self.assertRaises(maintenance.Denied): self.executor.prepare()

    def test_failed_rollback_requires_reconciliation_without_retry(self):
        job = self.prepared(); self.machine.fail = "rollback"
        result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "reconciliation_required"); self.assertEqual(self.machine.up_count, 2)
        with self.assertRaises(maintenance.Denied): self.executor.prepare()

    def test_crash_claim_persists_and_blocks_new_runs(self):
        job = self.prepared(); stored = self.executor.review(job["id"])
        self.executor.record(stored, "attempt_started")
        with self.assertRaises(maintenance.Denied): self.executor.execute(job["id"], job["proposal_hash"])
        with self.assertRaises(maintenance.Denied): self.executor.prepare()
        self.assertEqual(self.effects(), [])

    def test_finite_images_paths_manifest_shape_and_root_cli(self):
        for image in ("ghcr.io/gethomepage/homepage:latest", "ghcr.io/evil/image@sha256:" + "b" * 64, NEW + "\n"):
            with self.assertRaises(ValueError): self.executor.pin(image)
        for proposal_id in ("../candidate", "/etc/passwd", "-x"):
            with self.assertRaises(maintenance.Denied): self.executor.review(proposal_id)
        self.paths.candidate.write_text(json.dumps({**self.manifest, "command": "docker restart all"}))
        with self.assertRaises(maintenance.Denied): self.executor.prepare()
        with patch.object(sys, "argv", ["homepage_maintenance", "prepare"]), patch.object(maintenance.os, "geteuid", return_value=1000):
            with self.assertRaises(SystemExit): maintenance.main()

    def test_configuration_symlinks_and_writable_candidate_rejected(self):
        self.paths.candidate.chmod(0o666)
        with self.assertRaises(maintenance.Denied): self.executor.prepare()
        self.paths.candidate.chmod(0o600)
        (self.paths.release / "deploy/homepage/config/link").symlink_to(self.paths.environment)
        with self.assertRaises(maintenance.Denied): self.executor.prepare()

    def test_persistent_writable_mounts_and_wrong_configuration_source_are_denied(self):
        self.machine.mounts.append({"Type": "bind", "Destination": "/app/config/logs", "RW": True, "Source": "/persistent/logs"})
        with self.assertRaises(maintenance.Denied): self.executor.prepare()
        self.machine.mounts.pop(); self.machine.mounts[0]["Source"] = "/another/release/config"
        with self.assertRaises(maintenance.Denied): self.executor.prepare()

    def test_pulled_digest_provenance_mismatch_blocks_replacement(self):
        job = self.prepared()
        original = self.machine.image_metadata
        def bad_metadata(image):
            value = original(image)
            if image == NEW:
                value["RepoDigests"] = [OLD]
            return value
        with patch.object(self.machine, "image_metadata", side_effect=bad_metadata):
            result = self.executor.execute(job["id"], job["proposal_hash"])
        self.assertEqual(result["status"], "failed_before_recreate")
        self.assertEqual(self.machine.up_count, 0)

    def test_shared_deployment_lock_rejects_concurrency(self):
        self.paths.deployment_lock.touch()
        with self.paths.deployment_lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(maintenance.Denied): self.executor.prepare()
        self.assertEqual(self.effects(), [])

    def test_http_health_requires_api_and_html_without_redirects(self):
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            def read(self, maximum): return self.body[:maximum]
        with patch.object(maintenance.http.client, "HTTPConnection") as connection:
            instance = connection.return_value
            instance.getresponse.side_effect = [Response(b'up'), Response(b"<!doctype html><html></html>")]
            self.assertTrue(maintenance.Maintenance.http_health())
            self.assertEqual(instance.request.call_args_list[0].args[:2], ("GET", "/api/healthcheck"))
            instance.getresponse.side_effect = [Response(b"down")]
            self.assertFalse(maintenance.Maintenance.http_health())


if __name__ == "__main__":
    unittest.main()
