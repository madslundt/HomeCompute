#!/usr/bin/env python3
"""Check board discovery and the read-only T3 project projection."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock


DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
reader = runpy.run_path(str(DEPLOY / "t3code/github-projects.py"))
shared = runpy.run_path(str(DEPLOY / "orca/github-projects.py"))
client = runpy.run_path(str(DEPLOY / "t3code/read-github-backlog.py"))
REPO = "madslundt/Daythread"


def node(number, login="madslundt", closed=False):
    return {"number": number, "closed": closed, "owner": {"__typename": "User", "login": login}}


def page(nodes, next_cursor=None, viewer="madslundt"):
    return {"viewer": {"login": viewer, "projectsV2": {
        "nodes": nodes, "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
    }}}


class GitHubProjectsTests(unittest.TestCase):
    def test_discovers_open_owned_boards_across_pages(self):
        graphql = Mock(side_effect=[page([node(9), node(2, closed=True), node(4, "elsewhere")], "next"),
                                    page([node(21), node(9)])])
        self.assertEqual(reader["owned_boards"](graphql, "fixture"), [9, 21])
        self.assertEqual([call.args[1] for call in graphql.call_args_list], [{"cursor": None}, {"cursor": "next"}])
        with self.assertRaisesRegex(ValueError, "different GitHub account"):
            reader["owned_boards"](Mock(return_value=page([], viewer="elsewhere")), "fixture")

    def test_projection_tracks_partial_queries_and_omits_unrelated_empty_boards(self):
        query = Mock(side_effect=[{"items": [], "more_items": False},
                                  {"items": [{"number": 8, "status": "Ready"}], "more_items": True},
                                  ValueError("temporarily unavailable")])
        result = reader["project_snapshot"](query, REPO, [1, 2, 3], "fixture")
        self.assertEqual(result, {"status": "partial", "boards": [{
            "url": "https://github.com/users/madslundt/projects/2",
            "items": [{"number": 8, "status": "Ready"}], "more_items": True,
        }]})
        self.assertTrue(all(call.args[0]["repository"] == REPO for call in query.call_args_list))

    def test_missing_credential_replaces_old_snapshot_and_removes_retired_repo(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Daythread.json").write_text('{"status":"available","boards":["old backlog"]}')
            (root / "Retired.json").write_text("{}")
            reader["refresh"](shared, [REPO], root / "missing-token", root)
            result = json.loads((root / "Daythread.json").read_text())
            self.assertEqual(result["status"], "credential unavailable")
            self.assertEqual(result["boards"], [])
            self.assertFalse((root / "Retired.json").exists())

    def test_client_rejects_stale_and_cross_repository_snapshots(self):
        now = datetime.now(timezone.utc)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            value = {"repository": REPO, "captured_at": now.isoformat(), "status": "available", "boards": []}
            path.write_text(json.dumps(value))
            self.assertEqual(client["fresh_snapshot"](path, REPO, now), value)
            with self.assertRaises(ValueError):
                client["fresh_snapshot"](path, "madslundt/ChessMate", now)
            with self.assertRaises(ValueError):
                client["fresh_snapshot"](path, REPO, now + timedelta(minutes=31))


if __name__ == "__main__":
    unittest.main()
