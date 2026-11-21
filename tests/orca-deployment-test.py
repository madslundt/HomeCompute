#!/usr/bin/env python3
"""Exercise Orca's resolved Compose boundary and read-only backlog adapter."""

from datetime import datetime, timedelta, timezone
import io
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
        self.assertEqual(set(deployment["services"]), {"orca", "orca-egress", "orca-gateway", "orca-projects"})
        orca = deployment["services"]["orca"]
        self.assertEqual(orca["user"], "12074:12074")
        self.assertEqual(orca["cap_drop"], ["ALL"])
        self.assertTrue(orca["read_only"])
        self.assertEqual(orca["logging"]["driver"], "none")
        self.assertNotIn("ports", orca)
        self.assertEqual(set(orca["networks"]), {"runtime"})
        self.assertEqual([(row["source"], row["target"], row.get("read_only", False))
                          for row in orca["volumes"]],
                         [("/srv/state/orca", "/data", False),
                          ("/var/lib/homecompute/orca/github-projects/snapshots",
                           "/etc/homecompute-orca/github-projects", True)])
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

    def test_claude_egress_composes_with_codex(self):
        base = compose("codex.compose.yaml")
        combined = compose("codex.compose.yaml", "claude.compose.yaml")
        volumes = combined["services"]["orca-egress"]["volumes"]
        claude = next(row for row in volumes if row["target"].endswith("egress-claude-hosts.json"))
        self.assertTrue(claude["read_only"])
        self.assertEqual(set(json.loads(Path(claude["source"]).read_bytes())),
                         {"api.anthropic.com", "claude.ai", "claude.com", "platform.claude.com"})
        volumes.remove(claude)
        self.assertEqual(combined, base)

    def test_projects_reader_owns_credential_and_has_only_internal_network(self):
        services = compose()["services"]
        reader = services["orca-projects"]
        self.assertTrue(reader["read_only"])
        self.assertEqual(reader["user"], "10001:10001")
        self.assertEqual(set(reader["networks"]), {"runtime"})
        self.assertNotIn("ports", reader)
        bindings = {row["target"]: row for row in reader["volumes"]}
        secret = bindings["/run/orca-projects/credentials"]
        self.assertTrue(secret["read_only"])
        self.assertTrue(secret["source"].endswith("/github-projects/credentials"))
        self.assertFalse(bindings["/snapshots"].get("read_only", False))
        for service, config in services.items():
            if service != "orca-projects":
                self.assertNotIn(secret["source"], [row["source"] for row in config["volumes"]])

    def test_board_reader_refuses_write_scope_and_limits_output_to_configured_public_repo(self):
        reader = runpy.run_path(str(DEPLOY / "github-projects.py"))["project_items"]
        entry = {"repository": "acme/example",
                 "github_project": {"kind": "user", "owner": "acme"}}
        content = {"__typename": "Issue", "number": 11, "state": "OPEN",
                   "repository": {"nameWithOwner": "acme/Example", "isPrivate": False}}
        current = {"content": content, "status": {"name": "Ready"}, "priority": {"name": "High"}}
        private = {**current, "content": {**content, "repository": {
            "nameWithOwner": "acme/example", "isPrivate": True}}}
        unrelated = {**current, "content": {**content, "repository": {
            "nameWithOwner": "acme/other", "isPrivate": False}}}
        payload = json.dumps({"data": {"user": {"projectV2": {"closed": False, "items": {
            "nodes": [current, private, unrelated], "pageInfo": {"hasNextPage": True},
        }}}}}).encode()

        class Response(io.BytesIO):
            headers = {"X-OAuth-Scopes": "project"}

        opener = Mock()
        opener.open.side_effect = lambda *_args, **_kwargs: Response(payload)
        with patch.dict(reader.__globals__, {"build_opener": Mock(return_value=opener)}):
            with self.assertRaisesRegex(ValueError, "read:project scope"):
                reader(entry, 7, "ghp_synthetic")
            Response.headers = {"X-OAuth-Scopes": "read:project"}
            projected = reader(entry, 7, "ghp_synthetic")
        self.assertEqual(projected, {"more_items": True, "items": [
            {"number": 11, "url": "https://github.com/acme/example/issues/11",
             "status": "Ready", "priority": "High"}
        ]})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/graphql")
        query = json.loads(request.data)
        self.assertTrue(query["query"].lstrip().startswith("query OrcaProject"))
        self.assertEqual(query["variables"], {"owner": "acme", "number": 7,
                                              "filter": "repo:acme/example is:open"})

    def test_board_snapshot_refuses_stale_or_different_board(self):
        board_status = runpy.run_path(str(DEPLOY / "project-status.py"))["board_status"]
        board = {"kind": "user", "owner": "acme"}
        project = {"id": "example", "repository": "acme/example", "github_project": board}
        now = datetime.now(timezone.utc)
        snapshot = {"repository": project["repository"], "status": "available",
                    "captured_at": now.isoformat(), "board": board, "items": []}
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            file = directory / "example.json"
            with patch.dict(board_status.__globals__, {"BOARDS": directory}):
                file.write_text(json.dumps(snapshot))
                self.assertEqual(board_status(project)["github_projects"], snapshot)
                for update in ({"captured_at": (now - timedelta(hours=1)).isoformat()},
                               {"board": {**board, "owner": "different"}}):
                    file.write_text(json.dumps({**snapshot, **update}))
                    self.assertNotIn("github_projects", board_status(project))

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
                save_review({"repository": "acme/example"}, directory)
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
        prompt = edit[edit.index("--prompt") + 1]
        self.assertIn("/data/profile/bots/example-po/project-status.json", prompt)

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
