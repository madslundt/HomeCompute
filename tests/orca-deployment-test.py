#!/usr/bin/env python3
"""Exercise Orca's resolved Compose boundary and read-only backlog adapter."""

import json
import os
from pathlib import Path
import runpy
import subprocess
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


if __name__ == "__main__":
    unittest.main()
