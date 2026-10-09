"""Bounded transport outbox; task, observation and conversation owners stay separate."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
HEX = re.compile(r"[0-9a-f]{64}")
STATES = {"pending", "queued", "running", "review", "publishing", "completed", "failed", "cancelled"}
EVIDENCE = {"count", "candidate_count", "available", "failed", "state", "reason",
            "source_report_generated_at", "installed_version"}


def instant(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return dt.timestamp()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def quiet(now: float, settings: dict[str, Any]) -> bool:
    hour = datetime.fromtimestamp(now, ZoneInfo(settings["timezone"])).hour
    start, end = settings["quiet_start_hour"], settings["quiet_end_hour"]
    return start <= hour < end if start < end else hour >= start or hour < end


def observation(row: dict[str, Any], allowed: dict[str, Any], now: float) -> dict[str, Any]:
    fields = {"system_id", "check_id", "stable_key", "category", "status", "severity", "evidence",
              "observed_at", "expires_at"}
    if (not isinstance(row, dict) or not fields <= row.keys() or row["stable_key"] not in allowed
            or row["stable_key"] != row["system_id"] + ":" + row["check_id"]
            or row["status"] not in {"healthy", "degraded", "unknown"}
            or row["category"] not in {"health", "updates"}
            or row["severity"] != ("warning" if row["status"] == "degraded" else "info")):
        raise ValueError("observation outside reviewed registry")
    policy = allowed[row["stable_key"]]
    if row["category"] != policy["category"]:
        raise ValueError("observation category differs from reviewed check")
    ttl = policy["ttl"]
    observed, expiry = instant(row["observed_at"]), instant(row["expires_at"])
    if observed > now + 30 or expiry <= observed or expiry - observed > ttl:
        raise ValueError("invalid observation freshness")
    evidence = dict(row["evidence"]) if isinstance(row["evidence"], dict) else row["evidence"]
    if not isinstance(evidence, dict) or not evidence.keys() <= EVIDENCE:
        raise ValueError("unbounded evidence")
    if (row["check_id"].startswith("model-update-report.") and row["status"] != "unknown"
            and set(evidence) != {"count", "source_report_generated_at"}):
        raise ValueError("model report counters require source provenance")
    for key, val in evidence.items():
        if key in {"count", "candidate_count"}:
            valid = type(val) is int and 0 <= val <= 10000000
        elif key in {"available", "failed"}:
            valid = type(val) is bool
        elif key == "state":
            valid = val in {"missing", "unhealthy", "healthy", "running", "unknown"}
        elif key == "reason":
            valid = val in {"collection_failed", "not_provisioned", "missing", "stale", "unsupported_schema",
                            "health_not_reported", "update_metadata_unavailable", "cached_inventory_only", "never_started"}
        elif key == "source_report_generated_at":
            valid = (isinstance(val, str) and observed - ttl <= instant(val) <= observed + 30
                     and expiry <= instant(val) + ttl)
        else:
            valid = isinstance(val, str) and bool(re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", val))
        if not valid:
            raise ValueError("invalid evidence")
    # Drop all untrusted extra fields, including household states and assistant instructions.
    normalized = {key: row[key] for key in fields}
    for field in ("observed_at", "expires_at"):
        normalized[field] = datetime.fromtimestamp(instant(row[field]), timezone.utc).isoformat().replace("+00:00", "Z")
    if "source_report_generated_at" in evidence:
        evidence["source_report_generated_at"] = datetime.fromtimestamp(instant(evidence["source_report_generated_at"]), timezone.utc).isoformat().replace("+00:00", "Z")
    normalized["evidence"] = evidence
    return normalized


class Outbox:
    def __init__(self, path: Path, settings: dict[str, Any]):
        os.umask(0o077)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
            raise ValueError("outbox requires a private directory")
        self.settings = settings
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA max_page_count=32768;
          CREATE TABLE IF NOT EXISTS baseline (key TEXT PRIMARY KEY, body TEXT);
          CREATE TABLE IF NOT EXISTS observation_watermarks (key TEXT PRIMARY KEY, at REAL, digest TEXT);
          CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, key TEXT UNIQUE, topic TEXT,
            kind TEXT, body TEXT, created REAL, state TEXT DEFAULT 'pending', claim TEXT, receipt TEXT);
          CREATE TABLE IF NOT EXISTS turns (key TEXT PRIMARY KEY, digest TEXT, state TEXT, reply TEXT);
        """)
        # A delivery/model call might have completed. Never replay after process loss.
        with self.db:
            self.db.execute("UPDATE events SET state='uncertain' WHERE state='sending'")
            self.db.execute("UPDATE turns SET state='uncertain' WHERE state='running'")

    def add(self, key: str, topic: str, kind: str, body: dict[str, Any], now: float) -> None:
        if self.db.execute("SELECT 1 FROM events WHERE key=?", (key,)).fetchone():
            return
        if self.db.execute("SELECT count(*) FROM events").fetchone()[0] >= 10000:
            raise ValueError("outbox capacity exhausted; retain receipts and reconcile")
        self.db.execute("INSERT INTO events(key,topic,kind,body,created) VALUES(?,?,?,?,?)",
                        (key, topic, kind, canonical(body), now))

    def observations(self, report: dict[str, Any], allowed: dict[str, Any], now: float) -> int:
        if (report.get("schema_version") != 1 or report.get("document_type") != "system_observation_report"
                or report.get("mode") != "observe-only" or report.get("automatic_actions") is not False
                or report.get("physical_device_actions") is not False
                or not isinstance(report.get("observations"), list) or len(report["observations"]) > 1000):
            raise ValueError("unsupported observation report")
        generated = instant(report["generated_at"])
        if not now - 900 <= generated <= now + 30:
            raise ValueError("report is stale or future dated")
        rows = [observation(x, allowed, now) for x in report["observations"]]
        if len({x["stable_key"] for x in rows}) != len(rows):
            raise ValueError("duplicate observations")
        with self.lock, self.db:
            before = self.db.total_changes
            for row in rows:
                key = row["stable_key"]
                observed = instant(row["observed_at"])
                digest = hashlib.sha256(canonical({"status": row["status"], "evidence": row["evidence"]}).encode()).hexdigest()
                seen = self.db.execute("SELECT at,digest FROM observation_watermarks WHERE key=?", (key,)).fetchone()
                if seen and observed < seen["at"]:
                    continue
                if seen and observed == seen["at"]:
                    if digest != seen["digest"]:
                        raise ValueError("conflicting equal-time observation")
                    continue
                # Unknown collection advances receipt ordering while retaining the last incident evidence.
                self.db.execute("INSERT OR REPLACE INTO observation_watermarks VALUES(?,?,?)", (key, observed, digest))
                prior = self.db.execute("SELECT body FROM baseline WHERE key=?", (key,)).fetchone()
                old = json.loads(prior[0]) if prior else None
                if old and instant(old["observed_at"]) > instant(row["observed_at"]):
                    continue
                if old and old["observed_at"] == row["observed_at"]:
                    if old["status"] != row["status"] or old["evidence"] != row["evidence"]:
                        raise ValueError("conflicting equal-time observation")
                    continue
                if row["status"] == "unknown" or instant(row["expires_at"]) <= now:
                    continue  # Unknown/stale observations cannot recover a known incident.
                if row["status"] == "degraded":
                    self.db.execute("UPDATE events SET state='superseded' WHERE topic=? AND kind='recovery' AND state='pending'", (key,))
                    row["episode_started_at"] = old.get("episode_started_at", row["observed_at"]) if old and old["status"] == "degraded" else row["observed_at"]
                    row["episode_key"] = hashlib.sha256((key + ":" + row["episode_started_at"]).encode()).hexdigest()
                    if not old or old["status"] != "degraded" or old["evidence"] != row["evidence"]:
                        marker = hashlib.sha256((canonical(row["evidence"]) + row["observed_at"]).encode()).hexdigest()[:24]
                        self.db.execute("UPDATE events SET state='superseded' WHERE topic=? AND kind='incident' AND state='pending'", (key,))
                        self.add("observation:" + row["episode_key"] + ":" + marker, key, "incident",
                                 {"observation": row, "issue_key": "monitor:" + row["episode_key"],
                                  "text": f"HomeCompute incident: {key}. Evidence: {canonical(row['evidence'])}. Review only; actions require operator approval."}, now)
                    else:
                        # Fresh unchanged collection extends the pending envelope, without creating a notification.
                        for event in self.db.execute("SELECT key,body FROM events WHERE topic=? AND kind='incident' AND state='pending'", (key,)).fetchall():
                            body = json.loads(event["body"])
                            body["observation"] = row
                            self.db.execute("UPDATE events SET body=? WHERE key=?", (canonical(body), event["key"]))
                elif old and old["status"] == "degraded":
                    episode = old["episode_key"]
                    sent = self.db.execute("SELECT 1 FROM events WHERE topic=? AND kind='incident' AND state IN ('delivered','sending','uncertain') AND json_extract(body,'$.observation.episode_key')=?", (key, episode)).fetchone()
                    # Quiet-hour incidents that recover before any delivery are quiet.
                    self.db.execute("UPDATE events SET state='superseded' WHERE topic=? AND state='pending' AND kind='incident'", (key,))
                    if sent:
                        self.add("recovery:" + episode, key, "recovery",
                                 {"observation": row, "episode_key": episode,
                                  "text": f"HomeCompute recovered: {key}. Fresh read-only evidence confirms healthy state."}, now)
                self.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, canonical(row)))
            return self.db.total_changes - before

    def tasks(self, tasks: list[dict[str, Any]], now: float) -> None:
        if not isinstance(tasks, list) or len(tasks) > 100:
            raise ValueError("task batch exceeds bounded contract")
        selected = []
        for task in tasks:
            tid, project, state, updated = task.get("id"), task.get("project"), task.get("state"), task.get("updated")
            if (not isinstance(tid, str) or not re.fullmatch(r"[0-9a-f-]{36}", tid)
                    or str(uuid.UUID(tid)) != tid or project not in self.settings["projects"] or state not in STATES
                    or type(updated) not in {float, int} or not 0 <= updated <= now + 30):
                raise ValueError("task outside reviewed contract")
            # Error/context/URLs are untrusted, never forwarded as notification text.
            selected.append({"id": tid, "project": project, "state": state, "updated": updated})
        with self.lock, self.db:
            for task in selected:
                tid, state = task["id"], task["state"]
                key = "task:" + tid
                old = self.db.execute("SELECT body FROM baseline WHERE key=?", (key,)).fetchone()
                if old:
                    prior = json.loads(old[0])
                    if prior["project"] != task["project"]:
                        raise ValueError("task project cannot change")
                    if prior["updated"] > task["updated"]:
                        continue
                    if prior["updated"] == task["updated"] and prior["state"] != state:
                        raise ValueError("conflicting equal-time task")
                    if prior["state"] == state:
                        self.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, canonical(task)))
                        continue
                    if prior["state"] in {"completed", "failed", "cancelled"}:
                        raise ValueError("terminal task cannot regress")
                    order = {name: rank for rank, name in enumerate(("pending", "queued", "running", "review", "publishing", "completed"))}
                    if state in order and prior["state"] in order and order[state] < order[prior["state"]]:
                        raise ValueError("task state cannot regress")
                self.db.execute("UPDATE events SET state='superseded' WHERE topic=? AND state='pending' AND kind='approval'", (key,))
                if state in {"pending", "review", "completed", "failed", "cancelled"}:
                    advice = {"pending": f"Execution approval required: assistant-task.py approve {tid}",
                              "review": f"Proposal review required: assistant-task.py review {tid}; publication requires its reviewed digest.",
                              "completed": "Completed; check the owning broker for the PR and independent CI.",
                              "failed": "Failed; inspect private evidence and reconcile before any retry.",
                              "cancelled": "Cancelled."}[state]
                    self.add(key + ":" + state, key, "approval" if state in {"pending", "review"} else "terminal",
                             {"task": task, "text": f"HomeCompute task {tid} ({task['project']}): {advice} Chat replies do not approve actions."}, now)
                self.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, canonical(task)))

    def claim(self, now: float) -> dict[str, Any] | None:
        import uuid
        with self.lock, self.db:
            # An expired sending lease requires reconciliation, never automatic resend.
            self.db.execute("UPDATE events SET state='uncertain' WHERE state='sending' AND created<?", (now - 300,))
            if self.db.execute("SELECT 1 FROM events WHERE state='sending'").fetchone():
                return None
            candidates = self.db.execute("SELECT * FROM events WHERE state='pending' ORDER BY CASE WHEN kind='reply' THEN 0 ELSE 1 END,seq").fetchall()
            for row in candidates:
                if row["kind"] != "reply" and quiet(now, self.settings):
                    continue
                body = json.loads(row["body"])
                if row["kind"] == "recovery":
                    current = self.db.execute("SELECT body FROM baseline WHERE key=?", (row["topic"],)).fetchone()
                    current = json.loads(current[0]) if current else {}
                    if current.get("status") != "healthy" or instant(current["expires_at"]) <= now:
                        continue
                if row["kind"] == "incident":
                    if instant(body["observation"]["expires_at"]) <= now:
                        continue  # Keep pending until fresh matching observation arrives.
                    last = self.db.execute("SELECT max(created) FROM events WHERE topic=? AND state='delivered'", (row["topic"],)).fetchone()[0]
                    if last is not None and now - last < self.settings["cooldown_seconds"]:
                        continue
                claim = uuid.uuid4().hex
                self.db.execute("UPDATE events SET state='sending',claim=?,created=? WHERE seq=?", (claim, now, row["seq"]))
                return {"delivery_key": row["key"], "claim": claim, "destination": self.settings["destination"],
                        "kind": row["kind"], "text": body["text"]}
            return None

    def actions(self, actions: list[dict[str, Any]], now: float) -> None:
        if not isinstance(actions, list) or len(actions) > 100:
            raise ValueError("action batch exceeds bounded contract")
        states = {"pending", "approved", "running", "postchecking", "rolling_back", "completed",
                  "rolled_back", "reconcile_required", "cancelled"}
        selected = []
        for item in actions:
            tid, aid, target, state, updated = (item.get(x) for x in ("id", "action_id", "target", "state", "updated"))
            if (not isinstance(tid, str) or str(uuid.UUID(tid)) != tid
                    or self.settings["actions"].get(aid) != target or state not in states
                    or type(updated) not in {float, int} or not 0 <= updated <= now + 30):
                raise ValueError("action outside reviewed contract")
            selected.append({"id": tid, "action_id": aid, "target": target, "state": state, "updated": updated})
        with self.lock, self.db:
            for item in selected:
                key, state = "action:" + item["id"], item["state"]
                old = self.db.execute("SELECT body FROM baseline WHERE key=?", (key,)).fetchone()
                if old:
                    prior = json.loads(old[0])
                    if prior["action_id"] != item["action_id"] or prior["target"] != item["target"]:
                        raise ValueError("action identity cannot change")
                    if prior["updated"] > item["updated"]:
                        continue
                    if prior["updated"] == item["updated"] and prior["state"] != state:
                        raise ValueError("conflicting equal-time action")
                    if prior["state"] == state:
                        self.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, canonical(item)))
                        continue
                    if prior["state"] in {"completed", "rolled_back", "reconcile_required", "cancelled"}:
                        raise ValueError("terminal action cannot regress")
                self.db.execute("UPDATE events SET state='superseded' WHERE topic=? AND kind='approval' AND state='pending'", (key,))
                if state in {"pending", "completed", "rolled_back", "reconcile_required", "cancelled"}:
                    advice = (f"Approval required: assistant-action.py review {item['id']}; approval requires its exact reviewed digest."
                              if state == "pending" else "Result: " + state + ". Check the owning action broker and postcheck evidence.")
                    self.add(key + ":" + state + ":" + hashlib.sha256(str(item["updated"]).encode()).hexdigest()[:16],
                             key, "approval" if state == "pending" else "terminal",
                             {"action": item, "text": f"HomeCompute action {item['action_id']} on {item['target']}: {advice} Chat replies do not approve actions."}, now)
                self.db.execute("INSERT OR REPLACE INTO baseline VALUES(?,?)", (key, canonical(item)))

    def acknowledge(self, key: str, claim: str, receipt: str, now: float) -> None:
        if not isinstance(receipt, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", receipt):
            raise ValueError("invalid delivery receipt")
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM events WHERE key=?", (key,)).fetchone()
            if not row or row["claim"] != claim or row["state"] not in {"sending", "uncertain", "delivered"}:
                raise ValueError("invalid delivery acknowledgement")
            if row["state"] == "delivered":
                if row["receipt"] != receipt:
                    raise ValueError("receipt conflict")
                return
            self.db.execute("UPDATE events SET state='delivered',receipt=?,created=? WHERE key=?", (receipt, now, key))

    def status(self, now: float) -> dict[str, Any]:
        with self.lock:
            counts = {x[0]: x[1] for x in self.db.execute("SELECT state,count(*) FROM events GROUP BY state")}
            findings = [json.loads(x[0]) for x in self.db.execute("SELECT body FROM baseline WHERE key NOT LIKE 'task:%' AND key NOT LIKE 'action:%' LIMIT 100")]
            tasks = [json.loads(x[0]) for x in self.db.execute("SELECT body FROM baseline WHERE key LIKE 'task:%' LIMIT 100")]
            actions = [json.loads(x[0]) for x in self.db.execute("SELECT body FROM baseline WHERE key LIKE 'action:%' LIMIT 100")]
        incidents = [{"stable_key": x["stable_key"], "status": x["status"], "fresh": instant(x["expires_at"]) > now}
                     for x in findings if x["status"] == "degraded"]
        return {"outbox": counts, "incidents": incidents, "quiet": quiet(now, self.settings),
                "tasks": tasks, "actions": actions, "actions_enabled": False,
                "model_health": "not_probed", "destination": self.settings["destination"]}

    def reserve_turn(self, key: str, digest: str) -> dict[str, Any] | None:
        with self.lock, self.db:
            old = self.db.execute("SELECT * FROM turns WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise ValueError("request id reused with different input")
                return {"state": old["state"], "reply": old["reply"]}
            if self.db.execute("SELECT count(*) FROM turns").fetchone()[0] >= 10000:
                raise ValueError("turn receipt capacity exhausted")
            if self.db.execute("SELECT 1 FROM turns WHERE state='running'").fetchone():
                raise ValueError("assistant already processing one turn")
            self.db.execute("INSERT INTO turns VALUES(?,?,?,NULL)", (key, digest, "running"))
            return None

    def incident(self, key: str, now: float) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT body,kind FROM events WHERE key=?", (key,)).fetchone()
            if not row or row["kind"] != "incident":
                raise ValueError("analysis requires a known incident")
            body = json.loads(row["body"])
            current = self.db.execute("SELECT body FROM baseline WHERE key=?", (body["observation"]["stable_key"],)).fetchone()
            current = json.loads(current[0]) if current else {}
            if (instant(body["observation"]["expires_at"]) <= now or current.get("status") != "degraded"
                    or current.get("episode_key") != body["observation"]["episode_key"]
                    or current.get("evidence") != body["observation"]["evidence"]):
                raise ValueError("incident is stale, changed or recovered")
            return {"observation": body["observation"], "issue_key": body["issue_key"]}

    def finish_turn(self, key: str, reply: str | None, now: float | None = None) -> dict[str, Any]:
        state = "completed" if reply is not None else "uncertain"
        with self.lock, self.db:
            self.db.execute("UPDATE turns SET state=?,reply=? WHERE key=?", (state, reply, key))
            if reply is not None and now is not None:
                self.add("reply:" + key, "conversation:" + key.split(":", 1)[0], "reply", {"text": reply}, now)
        return {"state": state, "reply": reply}
