"""Immutable operator policy; agent supplied text never selects commands or URLs."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


def load_projects(path: Path) -> dict[str, dict[str, Any]]:
    value = json.loads(path.read_text())
    if set(value) != {"schema_version", "projects"} or value["schema_version"] != 1:
        raise ValueError("unsupported project policy")
    for name, project in value["projects"].items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
            raise ValueError("invalid project id")
        if set(project) != {"repository", "base_sha", "base_branch", "classification", "tests", "write_prefixes"}:
            raise ValueError("unexpected project policy fields")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", project["repository"]):
            raise ValueError("only fixed GitHub repositories are supported")
        if not re.fullmatch(r"[0-9a-f]{40}", project["base_sha"]) or set(project["base_sha"]) == {"0"}:
            raise ValueError("base_sha must be an observed immutable commit")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]*", project["base_branch"]):
            raise ValueError("invalid base branch")
        if project["classification"] != "cloud_allowed":
            raise ValueError("local-only projects cannot be sent to cloud Codex")
        if not project["tests"] or not all(isinstance(v, str) and v for v in project["tests"]):
            raise ValueError("tests must be a fixed nonempty argv")
        if not project["write_prefixes"] or not all(
            isinstance(v, str) and v.endswith("/") and safe_path(v.rstrip("/"))
            for v in project["write_prefixes"]
        ):
            raise ValueError("write prefixes must be relative directory paths")
    return value["projects"]


def safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (bool(value) and not path.is_absolute() and "\\" not in value
            and all(p not in {"", ".", ".."} and not p.startswith(".") for p in value.split("/"))
            and not any(ord(c) < 32 for c in value))


def validate_files(files: object, project: dict[str, Any]) -> list[dict[str, str]]:
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise ValueError("patch must have 1..20 files")
    seen = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ValueError("unexpected patch fields")
        path = item["path"]
        if not isinstance(path, str) or not safe_path(path) or path in seen:
            raise ValueError("unsafe/duplicate patch path")
        if not any(path.startswith(prefix) for prefix in project["write_prefixes"]):
            raise ValueError("patch outside operator-approved directories")
        data = base64.b64decode(item["content"], validate=True)
        total += len(data)
        if len(data) > 262144 or total > 1048576:
            raise ValueError("patch too large")
        seen.add(path)
    return files
