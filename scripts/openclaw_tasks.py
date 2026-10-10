"""Trusted fixed-origin coding handoff and durable notification cursor; no execution authority."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
import uuid
from typing import Any

BROKER = "http://127.0.0.1:18792"
ADAPTER = "http://127.0.0.1:18793"
KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def private_json(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("task transport files require owner mode 0600")
        data = os.read(fd, 65537)
        if len(data) > 65536:
            raise ValueError("private configuration exceeds budget")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("expected private JSON object")
        return value
    finally:
        os.close(fd)


def request(origin: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_: Any) -> None:
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with opener.open(req, timeout=10) as response:
        data = response.read(262145)
        if response.status != 200 or len(data) > 262144:
            raise ValueError("bounded task response required")
        return json.loads(data)


class Tasks:
    def __init__(self, path: Path, projects: list[str]):
        config = private_json(path)
        if (set(config) != {"schema_version", "broker_origin", "tokens_file", "project_policy"}
                or config["schema_version"] != 1 or config["broker_origin"] != BROKER
                or any(not isinstance(config[key], str) or not Path(config[key]).is_absolute()
                       for key in ("tokens_file", "project_policy"))):
            raise ValueError("invalid fixed-origin task transport")
        tokens = private_json(Path(config["tokens_file"]))
        if (set(tokens) != {"assistant", "snapshot"} or len(set(tokens.values())) != 2
                or any(not isinstance(value, str) or len(value) < 32 for value in tokens.values())):
            raise ValueError("distinct assistant and read-only snapshot credentials required")
        policy = json.loads(Path(config["project_policy"]).read_text())
        if policy.get("schema_version") != 1 or not isinstance(policy.get("projects"), dict):
            raise ValueError("invalid authoritative project policy")
        if not set(projects) <= policy["projects"].keys():
            raise ValueError("communication projects exceed authoritative policy")
        self.projects = frozenset(projects)
        self.tokens = tokens

    def respond(self, text: str) -> str | None:
        words = text.strip().split(maxsplit=3)
        if not words or words[0] not in {"/code", "/cancel-task"}:
            return None
        if words[0] == "/code":
            if len(words) != 4 or words[1] not in self.projects or not KEY.fullmatch(words[2]):
                return "Use /code PROJECT STABLE_KEY SUMMARY. The project must be reviewed. Submission requires separate execution approval."
            summary = words[3]
            if len(summary) > 1000:
                return "Coding summary exceeds 1,000 characters; no task submitted."
            value = request(BROKER, "/tasks", self.tokens["assistant"],
                            {"project": words[1], "issue_key": words[2], "summary": summary, "context": ""})
        else:
            try:
                valid = len(words) == 2 and str(uuid.UUID(words[1])) == words[1]
            except ValueError:
                valid = False
            if not valid:
                return "Use /cancel-task TASK_UUID."
            task = request(BROKER, "/tasks/" + words[1], self.tokens["snapshot"])
            if task.get("project") not in self.projects:
                return "Task is outside the reviewed project allowlist; no cancellation sent."
            value = request(BROKER, "/tasks/" + words[1] + "/cancel", self.tokens["assistant"], {})
        if not isinstance(value, dict) or value.get("project") not in self.projects:
            raise ValueError("task receipt outside reviewed projects")
        return ("Broker task " + str(value["id"]) + ": " + str(value["state"]) +
                ". Use /task TASK_UUID for the latest collected result. Chat cannot approve execution or publication. "
                "Reuse the same project and stable key when reconciling a lost submission receipt.")


def feed_once(client: Tasks, after: int, collector_token: str) -> int:
    envelope = request(BROKER, "/task-events?after=" + str(after) + "&limit=100", client.tokens["snapshot"])
    if (not isinstance(envelope, dict) or not isinstance(envelope.get("events"), list)
            or len(envelope["events"]) > 100 or type(envelope.get("next_cursor")) is not int
            or envelope["next_cursor"] < after or type(envelope.get("has_more")) is not bool):
        raise ValueError("invalid durable broker event envelope")
    seq = after
    for event in envelope["events"]:
        if (not isinstance(event, dict) or type(event.get("seq")) is not int or event["seq"] <= seq
                or event.get("snapshot_available") is not True or not isinstance(event.get("task"), dict)):
            raise ValueError("historical snapshot unavailable; reconcile before advancing cursor")
        seq = event["seq"]
        task = event["task"]
        if task.get("project") in client.projects:
            # Each historical state is admitted in order. Repeating after a lost
            # acknowledgement is safe: the existing outbox deduplicates state.
            receipt = request(ADAPTER, "/tasks", collector_token, {"tasks": [task]})
            if receipt != {"accepted": True}:
                raise ValueError("unverified snapshot admission")
    if envelope["next_cursor"] != seq:
        raise ValueError("cursor advanced beyond delivered events")
    return seq


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--communication-config", type=Path, required=True)
    parser.add_argument("--transport-tokens", type=Path, required=True)
    parser.add_argument("--cursor", type=Path, required=True)
    parser.add_argument("--follow", action="store_true", help="poll every ten seconds; no model calls or external sends")
    args = parser.parse_args()
    os.umask(0o077)
    settings = private_json(args.communication_config)
    client = Tasks(args.transport, settings["projects"])
    collector = private_json(args.transport_tokens)["collector"]
    fd = os.open(args.cursor, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("cursor must be private")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = os.read(fd, 4097)
        state = json.loads(data) if data else {"schema_version": 1, "after": 0}
        if set(state) != {"schema_version", "after"} or state["schema_version"] != 1 or type(state["after"]) is not int or state["after"] < 0:
            raise ValueError("invalid cursor; reconcile without resetting it")
        while True:
            state["after"] = feed_once(client, state["after"], collector)
            os.lseek(fd, 0, os.SEEK_SET)
            encoded = json.dumps(state).encode()
            os.write(fd, encoded)
            os.ftruncate(fd, len(encoded))
            os.fsync(fd)
            if not args.follow:
                break
            time.sleep(10)
        return 0
    except Exception:
        print("Task feed stopped; cursor retained. Reconcile broker and adapter before restarting.")
        return 2
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
