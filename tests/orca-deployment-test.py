#!/usr/bin/env python3
"""Exercise Orca's resolved Compose boundary and read-only backlog adapter."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy/orca"


def compose(*overlays):
    args = ["docker", "compose", "-f", str(DEPLOY / "compose.yaml")]
    for overlay in overlays:
        args.extend(("-f", str(DEPLOY / overlay)))
    return json.loads(subprocess.check_output(
        [*args, "config", "--format", "json"],
        env={**os.environ, "ORCA_RELEASE": "validation"},
    ))


class OrcaDeploymentTest(unittest.TestCase):
    def test_runtime_mounts_routes_and_identity(self):
        deployment = compose()
        self.assertEqual(set(deployment["services"]), {"orca", "orca-egress", "orca-gateway"})
        orca = deployment["services"]["orca"]
        self.assertEqual(orca["user"], "12074:12074")
        self.assertEqual(orca["cap_drop"], ["ALL"])
        self.assertTrue(orca["read_only"])
        self.assertEqual(orca["logging"]["driver"], "none")
        self.assertNotIn("ports", orca)
        self.assertEqual(set(orca["networks"]), {"runtime"})
        self.assertEqual([(row["source"], row["target"]) for row in orca["volumes"]],
                         [("/srv/state/orca", "/data")])
        self.assertTrue(deployment["networks"]["runtime"]["internal"])
        self.assertEqual(deployment["networks"]["runtime"]["driver_opts"],
                         {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"})
        self.assertEqual(set(deployment["services"]["orca-gateway"]["networks"]),
                         {"runtime", "ingress"})
        self.assertEqual(set(deployment["services"]["orca-egress"]["networks"]),
                         {"runtime", "uplink"})
        self.assertEqual(json.loads((DEPLOY / "egress-hosts.json").read_bytes()),
                         ["api.github.com", "github.com"])

    def test_opt_in_codex_policy_changes_only_proxy_host_allowlist(self):
        base = compose()
        opt_in = compose("codex.compose.yaml")
        volumes = opt_in["services"]["orca-egress"]["volumes"]
        destination = "/etc/homecompute-orca/egress-hosts.json"
        projected = [row for row in volumes if row["target"] == destination]
        self.assertEqual(len(projected), 1)
        self.assertEqual(Path(projected[0]["source"]).name, "egress-codex-hosts.json")
        projected[0]["source"] = str(DEPLOY / "egress-hosts.json")
        self.assertEqual(opt_in, base)

    def test_public_metadata_skips_duplicate_pr_issue_rows_and_never_writes(self):
        client = runpy.run_path(str(DEPLOY / "project-status.py"))
        sha = "a" * 40
        common = {"number": 1, "title": "Synthetic review", "html_url": "https://github.com/test",
                  "updated_at": "2026-11-21T12:00:00Z"}
        issue = {**common, "number": 2}
        responses = [
            [issue, {**common, "pull_request": {}}],
            [{**common, "head": {"sha": sha}, "draft": True}],
            {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]},
        ]
        review = client["review"]
        with patch.dict(review.__globals__, {"get": Mock(side_effect=responses)}):
            result = review("acme/example")
        self.assertEqual(result["issues"], [issue])
        self.assertTrue(result["pull_requests"][0]["draft"])
        self.assertEqual(result["check_runs_for_first_five_pull_requests"][1][0]["conclusion"], "success")


    def test_status_snapshot_replaces_prior_data_and_reports_unavailable_projects(self):
        save_review = runpy.run_path(str(DEPLOY / "project-status.py"))["save_review"]
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            old = directory / "old.json"
            old.write_text("private old data")
            (directory / "project-status.json").symlink_to(old)
            with patch.dict(save_review.__globals__, {"review": Mock(side_effect=TimeoutError)}):
                save_review("acme/example", directory)
            snapshot = json.loads((directory / "project-status.json").read_bytes())
            self.assertEqual(snapshot["repository"], "acme/example")
            self.assertEqual(snapshot["repository_status"], "unavailable")
            self.assertIn("repository metadata only", snapshot["github_projects_status"])
            self.assertEqual(old.read_text(), "private old data")

    def test_legacy_schedule_keeps_id_history_and_existing_enabled_state(self):
        reconcile = runpy.run_path(str(DEPLOY / "schedules.py"))["reconcile"]
        project = {
            "id": "example", "name": "Example", "po": "Example PO",
            "repository": "acme/example", "review_cron": "15 9 * * 1",
        }
        target = {"botId": "bot-example"}
        edit_calls = []

        def command(*args):
            if args == ("bot", "list"):
                return {"bots": [{"name": project["po"], "id": target["botId"]}]}
            if args == ("automations", "list"):
                return {"automations": [{
                    "id": "original-id", "name": "Example PO weekday GitHub review",
                    "bot": target, "enabled": True,
                }]}
            edit_calls.append(args)
            updates = dict(zip(args[3::2], args[4::2]))
            return {"automation": {
                "id": "original-id", "bot": target, "enabled": True,
                "name": updates["--name"], "prompt": updates["--prompt"],
                "timezone": updates["--timezone"],
                "precheck": {"command": updates["--precheck"]},
            }}

        teams = Mock()
        teams.read_bytes.return_value = json.dumps({"projects": [project]}).encode()
        with patch.dict(reconcile.__globals__, {"call": command, "TEAMS": teams}):
            added, = reconcile(False)
        self.assertEqual(added["id"], "original-id")
        edit, = edit_calls
        self.assertEqual(edit[:3], ("automations", "edit", "original-id"))
        self.assertIn("15 9 * * 1", edit)
        self.assertIn("/usr/local/bin/orca-project-status example --save-review", edit)

    def test_open_code_has_explicit_queued_deep_lane(self):
        config = json.loads((DEPLOY / "opencode.json").read_bytes())
        default, deep = (config["providers"][provider] for provider in ("homecompute", "homecompute-deep"))
        self.assertEqual(config["model"], "homecompute/automation-moe")
        self.assertEqual(default["models"]["automation-moe"]["body"]["reasoning_effort"], "medium")
        self.assertEqual(deep["models"]["automation-moe"]["body"]["reasoning_effort"], "xhigh")
        self.assertEqual(deep["settings"]["baseURL"], "http://orca-gateway:18081/v1")
        self.assertIn("import automation_inference 1", (DEPLOY / "gateway.Caddyfile").read_text())


if __name__ == "__main__":
    unittest.main()
