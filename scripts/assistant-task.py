#!/usr/bin/env python3
"""Operator task approval/review client. Never accepts a model-supplied command."""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "status", "approve", "cancel", "review", "publish"])
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--token-file", type=Path, default=Path("/run/secrets/openclaw/operator_token"))
    parser.add_argument("--export", type=Path, help="new private directory for proposed files")
    parser.add_argument("--result-sha256", help="digest from the exact reviewed proposal")
    args = parser.parse_args()
    if args.action != "list" and not args.task_id:
        parser.error("task_id required")
    if args.action == "publish" and not args.result_sha256:
        parser.error("publish requires --result-sha256 from review")
    path = "/tasks" if args.action == "list" else "/tasks/" + args.task_id
    body = None
    if args.action not in {"list", "status"}:
        path += "/" + args.action
    if args.action in {"approve", "cancel", "publish"}:
        body = {} if args.action != "publish" else {"result_sha256": args.result_sha256}
    request = urllib.request.Request("http://127.0.0.1:18792" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + args.token_file.read_text().strip(), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    if args.action == "review":
        if args.export:
            os.umask(0o077)
            directory = args.export.resolve()
            directory.mkdir(mode=0o700)  # Refuse existing directories.
            for item in result["result"].get("files", []):
                target = directory / item["path"]
                if not target.resolve().is_relative_to(directory):
                    raise ValueError("unsafe broker proposal")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(item["content"], validate=True))
        result = {"task": result["task"], "result_sha256": result["result_sha256"],
                  "files": [item["path"] for item in result["result"].get("files", [])],
                  "exported_to": str(args.export) if args.export else None}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
