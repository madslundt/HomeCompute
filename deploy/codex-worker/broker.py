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
from mcp_adapter import handle_http as handle_mcp, metadata
from urllib.parse import parse_qs, urlsplit

PHASES = {"checkout", "baseline", "codex", "postcheck", "collect"}
ERROR_CODES = {"sandbox_unavailable", "checkout_failed", "codex_failed", "tests_failed", "output_rejected",
               "budget_exhausted", "worker_failed"}
STATUS = {"pending": "queued", "queued": "queued", "running": "running", "review": "completed",
          "publishing": "running", "completed": "completed", "failed": "failed", "cancelled": "cancelled"}


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
        if "policy_sha256" not in {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}:
            self.db.execute("ALTER TABLE tasks ADD COLUMN policy_sha256 TEXT")
        for column, kind in (("phase", "TEXT"), ("public_updated", "REAL")):
            if column not in {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}:
                self.db.execute("ALTER TABLE tasks ADD COLUMN " + column + " " + kind)
        if "snapshot" not in {row[1] for row in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN snapshot TEXT")
        self.db.execute("UPDATE tasks SET public_updated=updated WHERE public_updated IS NULL")
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
            value["updated"] = value["public_updated"]
            state = value["state"]
            value["status"] = STATUS[state]
            value["phase"] = value["phase"] if state == "running" and value["phase"] else state
            value["progress"] = {"checkout": "checkout", "baseline": "testing", "codex": "coding", "postcheck": "testing",
                                 "collect": "collecting", "running": "sandbox"}.get(value["phase"])
            value["approval_required"] = state in {"pending", "review"}
            value["approval_kind"] = {"pending": "execution", "review": "publication"}.get(state)
            result = json.loads(value["result"])
            value["error_code"] = result.get("error_code")
            value["result"] = {key: result[key] for key in ("ok", "tests_passed", "session_id") if key in result}
            if "files" in result:
                value["result"]["changed_files"] = len(result["files"])
            value["artifacts"] = [{"kind": "pull_request", "url": value["pr_url"]}] if value["pr_url"] else []
            return metadata(value)

    def transition(self, task_id: str, state: str, **fields: Any) -> None:
        now = time.time()
        assignments = ["state=?", "updated=?", "public_updated=?"] + [key + "=?" for key in fields]
        self.db.execute("UPDATE tasks SET " + ",".join(assignments) + " WHERE id=?",
                        [state, now, now, *fields.values(), task_id])
        self.db.execute("INSERT INTO events(task_id,state,at,snapshot) VALUES (?,?,?,?)",
                        (task_id, state, now, json.dumps(self.get(task_id))))

    def check_task_policy(self, task: dict[str, Any]) -> None:
        current = hashlib.sha256(json.dumps(task["policy"], sort_keys=True).encode()).hexdigest()
        if task.get("policy_sha256") != current:
            raise ValueError("task policy changed; operator reconciliation required")

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"project", "issue_key", "summary", "context"}:
            raise ValueError("expected project, issue_key, summary, context")
        if not isinstance(body["project"], str) or body["project"] not in self.projects:
            raise ValueError("project not approved")
        if not isinstance(body["issue_key"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["issue_key"]):
            raise ValueError("invalid stable issue key")
        if any(not isinstance(body[key], str) or len(body[key]) > limit
               for key, limit in (("summary", 1000), ("context", 16000))):
            raise ValueError("context/summary exceeds budget")
        if not body["summary"]:
            raise ValueError("task summary required")
        with self.lock, self.db:
            existing = self.db.execute("SELECT id,body FROM tasks WHERE project=? AND issue_key=?",
                                       (body["project"], body["issue_key"])).fetchone()
            if existing:
                if json.loads(existing["body"]) != body:
                    raise ValueError("stable issue key reused with different task content")
                return self.get(existing["id"])
            count = self.db.execute("SELECT count(*) FROM tasks WHERE state IN ('pending','queued','running','publishing')").fetchone()[0]
            if count >= 32:
                raise ValueError("pending task budget exhausted")
            task_id, now = str(uuid.uuid4()), time.time()
            policy_digest = hashlib.sha256(json.dumps(self.projects[body["project"]], sort_keys=True).encode()).hexdigest()
            self.db.execute("INSERT INTO tasks(id,project,issue_key,state,created,updated,public_updated,body,policy_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                            (task_id, body["project"], body["issue_key"], "pending", now, now, now, json.dumps(body), policy_digest))
            self.db.execute("INSERT INTO events(task_id,state,at,snapshot) VALUES (?,?,?,?)", (task_id, "pending", now, json.dumps(self.get(task_id))))
            return self.get(task_id)

    def action(self, task_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self.db:
            task = self.get(task_id, private=True)
            state = task["state"]
            if action in {"cancel", "approve"} and body != {}:
                raise ValueError("empty action body required")
            if action == "cancel" and state in {"pending", "queued", "running", "review"}:
                self.transition(task_id, "cancelled")
            elif action == "cancel" and state == "cancelled":
                pass
            elif action == "approve" and state == "pending":
                self.check_task_policy(task)
                self.transition(task_id, "queued")
            elif action == "heartbeat" and state == "running":
                if set(body) - {"phase"} or ("phase" in body and body["phase"] not in PHASES):
                    raise ValueError("invalid worker progress")
                if body.get("phase") and body["phase"] != task["phase"]:
                    self.transition(task_id, "running", phase=body["phase"])
                else:
                    self.db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time(), task_id))
            elif action == "result" and state == "running":
                if (set(body) - {"ok", "session_id", "files", "tests_passed", "error_code"}
                        or not {"ok", "session_id", "files"} <= set(body) or type(body["ok"]) is not bool
                        or ("tests_passed" in body and type(body["tests_passed"]) is not bool)
                        or ("error_code" in body and body["error_code"] not in ERROR_CODES)):
                    raise ValueError("invalid worker result")
                session = body["session_id"]
                if session is not None and (not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", session)):
                    raise ValueError("invalid session id")
                if body["ok"]:
                    validate_files(body["files"], task["policy"])
                elif body["files"] != []:
                    raise ValueError("failed worker cannot return files")
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
                task = self.get(row["id"], private=True)
                try:
                    self.check_task_policy(task)
                except ValueError:
                    self.transition(row["id"], "failed", error="task policy changed; operator reconciliation required")
                    return None
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

    def task_events(self, after: int, limit: int) -> dict[str, Any]:
        """Ordered durable projections; historical pre-migration events are explicit gaps."""
        with self.lock:
            rows = self.db.execute("SELECT seq,task_id,state,at,snapshot FROM events WHERE seq>? ORDER BY seq LIMIT ?",
                                   (after, limit + 1)).fetchall()
            selected = rows[:limit]
            return {"events": [{"seq": row["seq"], "task_id": row["task_id"], "state": row["state"], "at": row["at"],
                                "task": json.loads(row["snapshot"]) if row["snapshot"] else None,
                                "snapshot_available": bool(row["snapshot"])} for row in selected],
                    "next_cursor": selected[-1]["seq"] if selected else after, "has_more": len(rows) > limit}

    def publish(self, task_id: str, publisher: Publisher, digest: str) -> dict[str, Any]:
        with self.lock, self.db:
            task = self.get(task_id, private=True)
            if task["state"] != "review":
                raise ValueError("only reviewed worker results can be published")
            self.check_task_policy(task)
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


def serve(ledger: Ledger, tokens: dict[str, str], publisher: Publisher, address: tuple[str, int],
          actions: Any = None) -> ThreadingHTTPServer:
    # Action tables use the same durable SQLite file. Their authority remains
    # separate from coding tasks and is absent unless operator policy is loaded.
    ledger.actions = actions
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
                parsed = urlsplit(self.path)
                if parsed.path == "/task-events" and self.command == "GET" and role in {"snapshot", "operator"}:
                    query = parse_qs(parsed.query, strict_parsing=True)
                    if set(query) - {"after", "limit"} or any(len(values) != 1 for values in query.values()):
                        raise ValueError("invalid cursor")
                    after, limit = int(query.get("after", ["0"])[0]), int(query.get("limit", ["100"])[0])
                    if not 0 <= after <= 9223372036854775807 or not 1 <= limit <= 100:
                        raise ValueError("invalid cursor")
                    return self.respond(200, ledger.task_events(after, limit))
                if role == "snapshot" and not (self.command == "GET" and
                        (self.path in {"/tasks", "/actions"} or re.fullmatch(r"/(tasks|actions)/[a-fA-F0-9-]{36}", self.path))):
                    return self.respond(403, {"error": "read-only snapshot credential"})
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 1500000:
                    return self.respond(413, {"error": "body budget exceeded"})
                body = json.loads(self.rfile.read(size)) if size else {}
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                parts = self.path.strip("/").split("/")
                if parts[0] == "actions" and actions is not None:
                    if self.path == "/actions" and self.command == "POST" and role in {"assistant", "operator"}:
                        result = actions.propose(body)
                    elif self.path == "/actions" and self.command == "GET" and role in {"assistant", "operator", "snapshot"}:
                        result = actions.list_actions()
                    elif self.path == "/actions/evidence" and self.command == "POST" and role == "operator":
                        result = actions.ingest_evidence(body)
                    elif len(parts) == 2 and self.command == "GET" and role in {"assistant", "operator", "snapshot"}:
                        result = actions.get(parts[1])
                    elif len(parts) == 3 and parts[2] == "review" and self.command == "GET" and role == "operator":
                        result = actions.review(parts[1])
                    elif len(parts) == 3 and self.command == "POST":
                        action = parts[2]
                        if role == "operator" and action == "approve" and set(body) == {"approval_sha256"}:
                            result = actions.approve(parts[1], body["approval_sha256"])
                        elif role == "operator" and action == "refresh" and set(body) == {"evidence_id"}:
                            result = actions.refresh(parts[1], body["evidence_id"])
                        elif role == "operator" and action == "execute" and body == {}:
                            result = actions.execute(parts[1])
                        elif role in {"assistant", "operator"} and action == "cancel" and body == {}:
                            result = actions.cancel(parts[1])
                        else:
                            return self.respond(403, {"error": "operator approval required"})
                    else:
                        return self.respond(404, {"error": "unknown route"})
                elif self.path == "/tasks" and self.command == "POST" and role in {"assistant", "operator"}:
                    result = ledger.submit(body)
                elif self.path == "/tasks" and self.command == "GET" and role in {"assistant", "operator", "snapshot"}:
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
                    if action == "publish" and publisher is None:
                        return self.respond(403, {"error": "publisher credential not provisioned"})
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
    secrets = Path(os.environ.get("CODEX_BROKER_SECRETS", "/run/secrets"))
    config = Path(os.environ.get("CODEX_BROKER_CONFIG", "/config"))
    tokens = {name: (secrets / (name + "_token")).read_text().strip()
              for name in ("assistant", "worker", "operator")}
    if (secrets / "snapshot_token").exists():
        tokens["snapshot"] = (secrets / "snapshot_token").read_text().strip()
    if any(len(v) < 32 for v in tokens.values()) or len(set(tokens.values())) != len(tokens):
        raise ValueError("distinct tokens of at least 32 characters required")
    state_path = Path(os.environ.get("CODEX_BROKER_STATE", "/state")) / "tasks.sqlite3"
    actions = None
    if (config / "actions.json").exists():
        from actions import ActionLedger, load_actions
        actions = ActionLedger(state_path, load_actions(config / "actions.json"),
                               json.loads((config / "monitoring.json").read_text()))
    # Acquire action ownership before any coding-task crash recovery can write.
    ledger = Ledger(state_path, load_projects(config / "projects.json"))
    publisher = Publisher(secrets / "github_token") if (secrets / "github_token").exists() else None
    server = serve(ledger, tokens, publisher,
                   (os.environ.get("CODEX_BROKER_BIND", "0.0.0.0"), int(os.environ.get("CODEX_BROKER_PORT", "8080"))), actions)
    print(json.dumps({"event": "broker_started", "projects": len(ledger.projects)}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
