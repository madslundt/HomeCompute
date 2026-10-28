"""Trusted draft PR publisher: never executes repository code or shell commands."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from policy import validate_files

MAX_API_RESPONSE_BYTES = 16777216
MAX_BASE_TREE_ENTRIES = 100000
SHA = re.compile(r"[0-9a-f]{40}")


def base_modes(tree: Any, expected_sha: str, paths: list[str]) -> dict[str, str]:
    """Read modes only from GitHub's immutable base tree; fail before any writes."""
    if (not isinstance(tree, dict) or tree.get("sha") != expected_sha
            or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list)
            or len(tree["tree"]) > MAX_BASE_TREE_ENTRIES):
        raise ValueError("complete bounded base tree required")
    entries = {}
    for entry in tree["tree"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("invalid base tree entry")
        path = entry["path"]
        if (not path or path.startswith("/") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or any(ord(character) < 32 for character in path) or path in entries):
            raise ValueError("unsafe base tree path")
        entries[path] = entry
    modes = {}
    proposed = set(paths)
    for path in paths:
        parts = path.split("/")
        for length in range(1, len(parts)):
            ancestor_path = "/".join(parts[:length])
            if ancestor_path in proposed:
                raise ValueError("patch files cannot replace each other's ancestors")
            ancestor = entries.get(ancestor_path)
            if ancestor is not None and (ancestor.get("type"), ancestor.get("mode")) != ("tree", "040000"):
                raise ValueError("patch ancestor is not a directory")
        existing = entries.get(path)
        if existing is None:
            modes[path] = "100644"
        elif existing.get("type") == "blob" and existing.get("mode") in {"100644", "100755"}:
            modes[path] = existing["mode"]
        else:
            raise ValueError("only existing regular blobs can be changed")
    return modes


class Publisher:
    def __init__(self, token_file: Path):
        self.token_file = token_file

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        token = self.token_file.read_text().strip()
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "HomeCompute-task-broker"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            encoded = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(encoded) > MAX_API_RESPONSE_BYTES:
                raise ValueError("GitHub response budget exceeded")
            return json.loads(encoded)

    def publish(self, task: dict[str, Any], project: dict[str, Any]) -> dict[str, str]:
        files = validate_files(task["result"]["files"], project)
        root = "/repos/" + project["repository"]
        branch = "codex/repair-" + task["id"]
        # Stable branch name identifies partial writes for MANUAL reconciliation.
        base = self.request("GET", root + "/git/commits/" + project["base_sha"])
        if not isinstance(base, dict) or base.get("sha") != project["base_sha"]:
            raise ValueError("base commit identity changed")
        tree_identity = base.get("tree")
        base_tree = tree_identity.get("sha") if isinstance(tree_identity, dict) else None
        if not isinstance(base_tree, str) or not SHA.fullmatch(base_tree):
            raise ValueError("invalid base tree identity")
        baseline = self.request("GET", root + "/git/trees/" + base_tree + "?recursive=1")
        modes = base_modes(baseline, base_tree, [item["path"] for item in files])
        entries = []
        for item in files:
            blob = self.request("POST", root + "/git/blobs", {"content": item["content"], "encoding": "base64"})
            entries.append({"path": item["path"], "mode": modes[item["path"]], "type": "blob", "sha": blob["sha"]})
        tree = self.request("POST", root + "/git/trees", {"base_tree": base_tree, "tree": entries})
        commit = self.request("POST", root + "/git/commits", {
            "message": "Propose repair for " + task["issue_key"], "tree": tree["sha"],
            "parents": [project["base_sha"]],
        })
        self.request("POST", root + "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
        pr = self.request("POST", root + "/pulls", {
            "title": "[Draft] Investigate " + task["issue_key"], "head": branch,
            "base": project["base_branch"], "draft": True,
            "body": "Isolated Codex proposal. Task: " + task["id"]
                    + "\n\nWorker reports the fixed test command passed. This is untrusted test evidence; "
                    "independent CI and human review are required before merge or deployment.\n\n"
                    + "Base commit: " + project["base_sha"],
        })
        return {"commit": commit["sha"], "pr_url": pr["html_url"]}
