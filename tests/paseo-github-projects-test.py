#!/usr/bin/env python3
"""Check board discovery, the Paseo project projection, and scoped writes."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError


DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
reader = runpy.run_path(str(DEPLOY / "paseo/github-projects.py"))
shared = runpy.run_path(str(DEPLOY / "orca/github-projects.py"))
client = runpy.run_path(str(DEPLOY / "paseo/read-github-backlog.py"))
writer = runpy.run_path(str(DEPLOY / "paseo/github_project_writes.py"))
REPO = "madslundt/Daythread"
ADD_ITEM = {"action": "add", "board": 1, "number": 2, "repository": REPO}
SET_STATUS = {**ADD_ITEM, "action": "set", "field": "Status", "value": "Done"}


def node(number, login="madslundt", closed=False):
    return {"number": number, "closed": closed, "owner": {"__typename": "User", "login": login}}


def page(nodes, next_cursor=None, viewer="madslundt"):
    return {"viewer": {"login": viewer, "projectsV2": {
        "nodes": nodes, "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
    }}}


def target(*, items=(), closed=False, private=False, state="OPEN", next_cursor=None):
    return {
        "viewer": {"login": "madslundt"},
        "user": {"projectV2": {
            "id": "board", "closed": closed, "viewerCanUpdate": True,
            "owner": {"__typename": "User", "login": "madslundt"},
            "selection": {"id": "status", "options": [{"name": "Done", "id": "done"}]},
        }},
        "repository": {"nameWithOwner": REPO, "isPrivate": private, "issueOrPullRequest": {
            "id": "content", "state": state, "projectItems": {
                "nodes": items, "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
            },
        }},
    }


class GitHubProjectsTests(unittest.TestCase):
    def test_github_authentication_failure_reports_a_safe_reason(self):
        graphql = shared["github_graphql"]
        opener = Mock()
        failure = HTTPError(
            "https://api.github.com/graphql", 401, "Untrusted upstream detail", {}, None,
        )
        self.addCleanup(failure.close)
        opener.open.side_effect = failure
        with patch.dict(graphql.__globals__, {"build_opener": Mock(return_value=opener)}):
            with self.assertRaisesRegex(ValueError, r"GitHub rejected the token \(HTTP 401\)") as error:
                graphql("query { viewer { login } }", {}, "ghp_synthetic")
            self.assertNotIn("Untrusted upstream detail", str(error.exception))

    def test_github_scope_check_keeps_exact_read_only_permission(self):
        graphql = shared["github_graphql"]
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"data":{"viewer":{"login":"madslundt"}}}'
        with patch.dict(graphql.__globals__, {"build_opener": Mock(return_value=opener)}):
            response.headers = {"X-OAuth-Scopes": "read:project, repo"}
            with self.assertRaisesRegex(ValueError, "additional permissions"):
                graphql("query { viewer { login } }", {}, "ghp_synthetic")
            response.headers = {"X-OAuth-Scopes": "read:project"}
            self.assertEqual(graphql("query { viewer { login } }", {}, "ghp_synthetic"),
                             {"viewer": {"login": "madslundt"}})

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

    def test_writer_requires_exact_project_permission_and_reader_accepts_either_scope(self):
        graphql = shared["github_graphql"]
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"data":{"viewer":{"login":"madslundt"}}}'
        with patch.dict(graphql.__globals__, {"build_opener": Mock(return_value=opener)}):
            response.headers = {"X-OAuth-Scopes": "read:project"}
            with self.assertRaisesRegex(ValueError, "required scope"):
                graphql("query { viewer { login } }", {}, "ghp_synthetic", allowed_scopes=("project",))
            response.headers = {"X-OAuth-Scopes": "project, repo"}
            with self.assertRaisesRegex(ValueError, "additional permissions"):
                graphql("query { viewer { login } }", {}, "ghp_synthetic", allowed_scopes=("project",))
            response.headers = {"X-OAuth-Scopes": "project"}
            with self.assertRaisesRegex(ValueError, "reports write access"):
                graphql("query { viewer { login } }", {}, "ghp_synthetic")
            for scope in ("project", "read:project"):
                response.headers = {"X-OAuth-Scopes": scope}
                self.assertEqual(graphql("query { viewer { login } }", {}, "ghp_synthetic",
                                         allowed_scopes=("project", "read:project")),
                                 {"viewer": {"login": "madslundt"}})

    def test_writer_rejects_out_of_scope_targets_without_mutating(self):
        for invalid in (target(closed=True), target(private=True), target(state="CLOSED"),
                        target(items=[{"id": "item", "project": {"id": "board"}, "isArchived": True}])):
            graphql = Mock(return_value=invalid)
            with self.assertRaises(writer["WriteError"]):
                writer["write_project"](graphql, "madslundt", ADD_ITEM)
            graphql.assert_called_once()
        graphql = Mock(return_value=target())
        with self.assertRaisesRegex(writer["WriteError"], "Add the item"):
            writer["write_project"](graphql, "madslundt", SET_STATUS)
        graphql.assert_called_once()

    def test_writer_sets_one_named_option_after_resolving_paginated_membership(self):
        existing = {"id": "item", "project": {"id": "board"}, "isArchived": False}
        graphql = Mock(side_effect=[
            target(next_cursor="next"), target(items=[existing]),
            {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}}},
        ])
        self.assertIn("Updated Status", writer["write_project"](graphql, "madslundt", SET_STATUS))
        self.assertEqual(graphql.call_args_list[1].args[1]["cursor"], "next")
        graphql.assert_called_with(writer["SET"], {
            "project": "board", "item": "item", "field": "status", "option": "done",
        })

        graphql = Mock(return_value=target(items=[existing]))
        self.assertIn("already on the board", writer["write_project"](graphql, "madslundt", ADD_ITEM))
        graphql.assert_called_once()
        graphql.reset_mock()
        with self.assertRaisesRegex(writer["WriteError"], "exact option name"):
            writer["write_project"](graphql, "madslundt", {**SET_STATUS, "value": "Fabricated"})
        graphql.assert_called_once()

    def test_client_to_service_socket_scopes_the_github_write(self):
        graphql = Mock(side_effect=[target(), {"addProjectV2ItemById": {"item": {"id": "new"}}}])
        with TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            credential = root / "token"
            updated = Event()
            with writer["serve_writes"](root / "write.sock", graphql, shared["GitHubProjectsError"],
                                        credential, "madslundt", [REPO], updated) as server:
                thread = Thread(target=server.serve_forever)
                thread.start()
                try:
                    send_write = client["send_write"]
                    with patch.dict(send_write.__globals__, {"SNAPSHOTS": root}):
                        self.assertFalse(send_write(ADD_ITEM)["ok"])
                        graphql.assert_not_called()
                        credential.write_text("ghp_fixture")
                        self.assertFalse(send_write({**ADD_ITEM, "repository": "other/repo"})["ok"])
                        graphql.assert_not_called()
                        self.assertTrue(send_write(ADD_ITEM)["ok"])
                        graphql.assert_called_with(writer["ADD"], {"project": "board", "content": "content"},
                                                   "ghp_fixture", allowed_scopes=("project",))
                        self.assertTrue(updated.is_set())
                finally:
                    server.shutdown()
                    thread.join()

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
