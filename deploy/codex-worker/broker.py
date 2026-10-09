"""Small durable task ledger. Assistant tokens cannot approve execution/publishing."""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from policy import load_projects, validate_files
from publisher import Publisher
from mcp_adapter import handle_http as handle_mcp

PUBLIC = {"id", "project", "issue_key", "state", "created", "updated", "session_id", "commit", "pr_url", "error"}


class Ledger:
    def __init__(self, path: Path, projects: dict[str, dict[str, Any]]):
        self.projects = projects
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, project TEXT, issue_key TEXT, state TEXT,
              created REAL, updated REAL, body TEXT, result TEXT DEFAULT '{}',
              session_id TEXT, commit_sha TEXT, pr_url TEXT, error TEXT,
              UNIQUE(project, issue_key));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY, task_id TEXT, state TEXT, at REAL);
        """)
        # A restart cannot safely infer whether a write already happened.
        with self.lock, self.db:
            for row in self.db.execute("SELECT id FROM tasks WHERE state IN ('running','publishing')").fetchall():
                self.transition(row["id"], "failed", error="broker restarted; operator reconciliation required")

    def get(self, task_id: str, private: bool = False) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError("task not found")
            value = dict(row)
            value["commit"] = value.pop("commit_sha")
            if private:
                value["body"] = json.loads(value["body"])
                value["result"] = json.loads(value["result"])
                value["policy"] = self.projects[value["project"]]
                return value
            return {key: v for key, v in value.items() if key in PUBLIC}

    def transition(self, task_id: str, state: str, **fields: Any) -> None:
        assignments = ["state=?", "updated=?"] + [key + "=?" for key in fields]
        self.db.execute("UPDATE tasks SET " + ",".join(assignments) + " WHERE id=?",
                        [state, time.time(), *fields.values(), task_id])
        self.db.execute("INSERT INTO events(task_id,state,at) VALUES (?,?,?)", (task_id, state, time.time()))

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"project", "issue_key", "summary", "context"}:
            raise ValueError("expected project, issue_key, summary, context")
        if body["project"] not in self.projects:
            raise ValueError("project not approved")
        if not isinstance(body["issue_key"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["issue_key"]):
            raise ValueError("invalid stable issue key")
        if any(not isinstance(body[key], str) or len(body[key]) > limit
               for key, limit in (("summary", 1000), ("context", 16000))):
            raise ValueError("context/summary exceeds budget")
        with self.lock, self.db:
            existing = self.db.execute("SELECT id FROM tasks WHERE project=? AND issue_key=?",
                                       (body["project"], body["issue_key"])).fetchone()
            if existing:
                return self.get(existing["id"])
            count = self.db.execute("SELECT count(*) FROM tasks WHERE state IN ('pending','queued','running','publishing')").fetchone()[0]
            if count >= 32:
                raise ValueError("pending task budget exhausted")
            task_id, now = str(uuid.uuid4()), time.time()
            self.db.execute("INSERT INTO tasks(id,project,issue_key,state,created,updated,body) VALUES (?,?,?,?,?,?,?)",
                            (task_id, body["project"], body["issue_key"], "pending", now, now, json.dumps(body)))
            self.db.execute("INSERT INTO events(task_id,state,at) VALUES (?,?,?)", (task_id, "pending", now))
            return self.get(task_id)

    def action(self, task_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.db:
            task = self.get(task_id, private=True)
            state = task["state"]
            if action == "cancel" and state in {"pending", "queued", "running", "review"}:
                self.transition(task_id, "cancelled")
            elif action == "approve" and state == "pending":
                self.transition(task_id, "queued")
            elif action == "heartbeat" and state == "running":
                self.transition(task_id, "running")
            elif action == "result" and state == "running":
                if set(body) != {"ok", "session_id", "files"} or type(body["ok"]) is not bool:
                    raise ValueError("invalid worker result")
                session = body["session_id"]
                if session is not None and (not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", session)):
                    raise ValueError("invalid session id")
                if body["ok"]:
                    validate_files(body["files"], task["policy"])
                self.transition(task_id, "review" if body["ok"] else "failed",
                                result=json.dumps(body), session_id=session,
                                error=None if body["ok"] else "worker failed; inspect private evidence")
            else:
                raise ValueError("invalid state transition")
            return self.get(task_id)

    def claim(self) -> dict[str, Any] | None:
        with self.lock, self.db:
            stale = self.db.execute("SELECT id FROM tasks WHERE state='running' AND updated<?", (time.time()-60,)).fetchall()
            for row in stale:
                self.transition(row["id"], "failed", error="worker lease expired; no automatic retry")
            if self.db.execute("SELECT 1 FROM tasks WHERE state='running'").fetchone():
                return None
            row = self.db.execute("SELECT id FROM tasks WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                self.transition(row["id"], "running")
                return self.get(row["id"], private=True)
            return None

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.get(row["id"]) for row in self.db.execute(
                "SELECT id FROM tasks ORDER BY created DESC LIMIT 100").fetchall()]

    def review(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id, private=True)
        digest = hashlib.sha256(json.dumps(task["result"], sort_keys=True).encode()).hexdigest()
        return {"task": self.get(task_id), "result": task["result"], "result_sha256": digest}

    def publish(self, task_id: str, publisher: Publisher, digest: str) -> dict[str, Any]:
        with self.lock, self.db:
            task = self.get(task_id, private=True)
            if task["state"] != "review":
                raise ValueError("only reviewed worker results can be published")
            if not hmac.compare_digest(digest, self.review(task_id)["result_sha256"]):
                raise ValueError("approval must match reviewed result digest")
            self.transition(task_id, "publishing")
        try:
            result = publisher.publish(task, task["policy"])
        except Exception:
            with self.lock, self.db:
                self.transition(task_id, "failed", error="publish failed; reconcile GitHub branch before retry")
            raise ValueError("publish failed; credentials/output omitted") from None
        with self.lock, self.db:
            self.transition(task_id, "completed", commit_sha=result["commit"], pr_url=result["pr_url"])
        return self.get(task_id)


def serve(ledger: Ledger, tokens: dict[str, str], publisher: Publisher, address: tuple[str, int]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass  # No context, credentials, request bodies or query strings in logs.

        def handle_request(self) -> None:
            try:
                if self.path == "/healthz" and self.command == "GET":
                    return self.respond(200, {"ok": True})
                token = self.headers.get("Authorization", "")
                role = next((name for name, secret in tokens.items()
                             if hmac.compare_digest(token, "Bearer " + secret)), None)
                if role is None:
                    return self.respond(401, {"error": "unauthorized"})
                if self.path == "/mcp":
                    return handle_mcp(self, ledger, role)
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 1500000:
                    return self.respond(413, {"error": "body budget exceeded"})
                body = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                parts = self.path.strip("/").split("/")
                if self.path == "/tasks" and self.command == "POST" and role in {"assistant", "operator"}:
                    result = ledger.submit(body)
                elif self.path == "/tasks" and self.command == "GET" and role in {"assistant", "operator"}:
                    result = ledger.list_tasks()
                elif self.path == "/worker/claim" and self.command == "POST" and role == "worker":
                    result = ledger.claim()
                elif len(parts) == 2 and parts[0] == "tasks" and self.command == "GET":
                    result = ledger.get(parts[1])
                elif len(parts) == 3 and parts[0] == "tasks" and parts[2] == "review" and self.command == "GET" and role == "operator":
                    result = ledger.review(parts[1])
                elif len(parts) == 3 and parts[0] == "tasks" and self.command == "POST":
                    action = parts[2]
                    allowed = {"assistant": {"cancel"}, "worker": {"result", "heartbeat"},
                               "operator": {"cancel", "approve", "publish"}}
                    if action not in allowed[role]:
                        return self.respond(403, {"error": "operator approval required"})
                    result = ledger.publish(parts[1], publisher, body.get("result_sha256", "")) if action == "publish" else ledger.action(parts[1], action, body)
                else:
                    return self.respond(404, {"error": "unknown route"})
                self.respond(200, result)
            except KeyError:
                self.respond(404, {"error": "task not found"})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.respond(400, {"error": "invalid request or state transition"})
            except Exception:
                self.respond(500, {"error": "internal error; details suppressed"})

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def respond(self, status: int, value: Any) -> None:
            encoded = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        do_POST = handle_request
        do_GET = handle_request
        do_DELETE = handle_request

    return ThreadingHTTPServer(address, Handler)


def main() -> None:
    os.umask(0o077)
    tokens = {name: Path("/run/secrets/" + name + "_token").read_text().strip()
              for name in ("assistant", "worker", "operator")}
    if any(len(v) < 32 for v in tokens.values()) or len(set(tokens.values())) != 3:
        raise ValueError("three distinct tokens of at least 32 characters required")
    ledger = Ledger(Path("/state/tasks.sqlite3"), load_projects(Path("/config/projects.json")))
    server = serve(ledger, tokens, Publisher(Path("/run/secrets/github_token")), ("0.0.0.0", 8080))
    print(json.dumps({"event": "broker_started", "projects": len(ledger.projects)}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
