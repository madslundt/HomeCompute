"""Deterministic, non-dispatching attention policy and scoped metadata ledger.

The caller authenticates ``Scope`` and supplies source coverage. This module
never sends notifications or invokes task/device/calendar controllers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/assistant-attention.json"
SOURCES = {"home-assistant", "daythread", "aula", "calendar"}
OUTCOMES = {"retain", "digest", "prepare", "notify"}
FEEDBACK = {"useful", "not_useful", "correction"}
CANDIDATE_STATES = {"proposed", "tested", "approved", "rejected", "retired"}
TRANSITIONS = {
    "proposed": {"tested", "rejected", "retired"},
    "tested": {"approved", "rejected", "retired"},
    "approved": {"retired"},
    "rejected": {"retired"},
    "retired": set(),
}


class AttentionError(ValueError):
    """Invalid request or a denied scoped/state transition."""


@dataclass(frozen=True)
class Scope:
    principal_scope: str
    data_domain: str
    visibility: str = "private"


@dataclass(frozen=True)
class TrustedAuthority:
    """Construct only in a trusted adapter after authenticating a human action."""
    principal_scope: str
    data_domain: str
    can_approve_candidates: bool = False


def _scope(scope: Scope | Mapping[str, str]) -> tuple[str, str, str]:
    if isinstance(scope, Mapping):
        values = (scope.get("principal_scope"), scope.get("data_domain"), scope.get("visibility", "private"))
    else:
        values = (getattr(scope, "principal_scope", None), getattr(scope, "data_domain", None), getattr(scope, "visibility", "private"))
    principal, domain, visibility = values
    if not all(isinstance(v, str) and v and len(v) <= 128 for v in (principal, domain, visibility)):
        raise AttentionError("Authenticated scope required")
    if visibility not in {"private", "shared"}:
        raise AttentionError("Invalid visibility")
    valid = ((principal == "owner" and domain == "personal" and visibility == "private")
             or (principal == "family" and domain == "household" and visibility == "shared"))
    if not valid:
        raise AttentionError("Principal, domain, and visibility combination is not admitted")
    return principal, domain, visibility


def _instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise AttentionError(f"{label} must be RFC3339 with a zone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttentionError(f"{label} must be RFC3339 with a zone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AttentionError(f"{label} must include a timezone")
    return result


def _now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        return _instant(value, "now")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AttentionError("now must include a timezone")
    return value.astimezone(timezone.utc)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _policy(value: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if value is None:
        value = DEFAULT_POLICY
    if isinstance(value, (str, Path)):
        with Path(value).open(encoding="utf-8") as handle:
            value = json.load(handle)
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise AttentionError("Unsupported attention policy")
    result = dict(value)
    if result.get("timezone") != "Europe/Copenhagen":
        raise AttentionError("Policy timezone must be Europe/Copenhagen")
    if result.get("mode") not in {"shadow", "active"}:
        raise AttentionError("Policy mode must be shadow or active")
    if not isinstance(result.get("rules"), list):
        raise AttentionError("Policy rules must be a list")
    limit = result.get("daily_discretionary_notification_limit", 2)
    if type(limit) is not int or not 0 <= limit <= 20:
        raise AttentionError("Daily discretionary notification limit must be an integer from 0 to 20")
    threshold = result.get("learning", {}).get("repeated_feedback_threshold", 3)
    if type(threshold) is not int or not 2 <= threshold <= 10:
        raise AttentionError("Repeated feedback threshold must be an integer from 2 to 10")
    return result


def _validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "source", "object_id", "revision", "event_type", "operation", "occurred_at", "source_ref", "payload"}
    if not isinstance(event, Mapping) or set(event) != required:
        raise AttentionError("Event must match the canonical schema exactly")
    row = dict(event)
    if row["schema_version"] != 1 or row["source"] not in SOURCES:
        raise AttentionError("Unsupported event schema or source")
    for key, limit in (("object_id", 256), ("event_type", 128), ("source_ref", 1024)):
        if not isinstance(row[key], str) or not row[key] or len(row[key]) > limit:
            raise AttentionError(f"Invalid {key}")
    if type(row["revision"]) is not int or row["revision"] < 1:
        raise AttentionError("revision must be a positive integer")
    if row["operation"] not in {"upsert", "delete"}:
        raise AttentionError("Invalid operation")
    _instant(row["occurred_at"], "occurred_at")
    if not isinstance(row["payload"], dict) or len(json.dumps(row["payload"], ensure_ascii=False).encode()) > 8192:
        raise AttentionError("payload must be a bounded object (8 KiB max)")
    if row["operation"] == "delete" and row["payload"]:
        raise AttentionError("delete event payload must be empty")
    return row


def _minute(text: str) -> int:
    if not isinstance(text, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
        raise AttentionError("Invalid local time")
    hour, minute = map(int, text.split(":"))
    return hour * 60 + minute


def _quiet(local: datetime, quiet: Mapping[str, str]) -> bool:
    start, end = _minute(quiet["start"]), _minute(quiet["end"])
    current = local.hour * 60 + local.minute
    return (start <= current < end) if start < end else (current >= start or current < end)


def evaluate(event: Mapping[str, Any], *, scope: Scope | Mapping[str, str], now: datetime | str,
             source_status: Mapping[str, Any], policy: Mapping[str, Any] | str | Path | None = None) -> dict[str, Any]:
    """Return a pure deterministic decision. Model urgency is intentionally ignored."""
    row = _validate_event(event)
    _scope(scope)  # ensure auth context exists, but do not accept it from event payload
    cfg = _policy(policy)
    current = _now(now)
    status = source_status.get(row["source"]) if isinstance(source_status, Mapping) else None
    if not isinstance(status, Mapping) or status.get("state") not in {"fresh", "partial", "stale", "unavailable"}:
        outcome, reason = "retain", "source_coverage_unknown"
    elif status["state"] in {"stale", "unavailable"}:
        outcome, reason = "retain", f"source_{status['state']}"
    elif status["state"] == "partial":
        outcome, reason = "digest", "source_coverage_partial"
    else:
        last_success = status.get("last_success_at")
        if not last_success:
            outcome, reason = "retain", "source_last_success_unknown"
        else:
            age = current - _instant(last_success, "last_success_at").astimezone(timezone.utc)
            max_age = timedelta(minutes=int(cfg.get("freshness_max_age_minutes", 1440)))
            if age < timedelta(0) or age > max_age:
                outcome, reason = "retain", "source_data_stale"
            elif row["operation"] == "delete":
                outcome, reason = "retain", "source_deletion"
            else:
                matched = next((rule for rule in cfg["rules"] if isinstance(rule, Mapping)
                                and rule.get("source") == row["source"]
                                and rule.get("event_type") == row["event_type"]), None)
                if matched and matched.get("outcome") in OUTCOMES:
                    outcome, reason = matched["outcome"], f"configured:{matched.get('class', 'event')}"
                else:
                    outcome, reason = "digest", "ordinary_source_change"
    local = current.astimezone(ZoneInfo("Europe/Copenhagen"))
    if outcome == "notify" and _quiet(local, cfg.get("quiet_hours", {"start": "22:00", "end": "07:00"})):
        outcome, reason = "digest", "quiet_hours"
    return {
        "schema_version": 1,
        "policy_version": str(cfg.get("policy_version", "unknown")),
        "outcome": outcome,
        "reason": reason,
        "source": row["source"],
        "object_id": row["object_id"],
        "revision": row["revision"],
        "event_type": row["event_type"],
        "source_ref": row["source_ref"],
        "coverage": status.get("state", "unknown") if isinstance(status, Mapping) else "unknown",
        "last_success_at": status.get("last_success_at") if isinstance(status, Mapping) else None,
        "occurred_at": row["occurred_at"],
        "local_day": local.date().isoformat(),
        "mode": cfg["mode"],
        "discretionary": outcome == "notify",
        "daily_notification_limit": int(cfg.get("daily_discretionary_notification_limit", 2)),
    }


class AttentionLedger:
    """Scoped SQLite ledger; metadata only, no dispatch, task, or device effects."""
    def __init__(self, path: str | Path, *, dry_run: bool = True, busy_timeout_ms: int = 5000):
        self.path = Path(path)
        self.dry_run = dry_run
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise AttentionError("Ledger directory must be private (0700)")
        existed = self.path.exists()
        self._init_db()
        if not existed:
            os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS decisions(
                  decision_id TEXT PRIMARY KEY, principal TEXT NOT NULL, domain TEXT NOT NULL,
                  visibility TEXT NOT NULL, occurrence_key TEXT NOT NULL, dedupe_key TEXT NOT NULL,
                  source TEXT NOT NULL, object_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
                  event_type TEXT NOT NULL, source_ref TEXT NOT NULL, outcome TEXT NOT NULL,
                  reason TEXT NOT NULL, coverage TEXT NOT NULL, last_success_at TEXT,
                  policy_version TEXT NOT NULL, mode TEXT NOT NULL, local_day TEXT NOT NULL,
                  state TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                  snoozed_until TEXT, UNIQUE(principal,domain,visibility,dedupe_key));
                CREATE INDEX IF NOT EXISTS decisions_day ON decisions(principal,domain,visibility,local_day,outcome,state);
                CREATE TABLE IF NOT EXISTS requests(
                  principal TEXT NOT NULL, domain TEXT NOT NULL, visibility TEXT NOT NULL,
                  request_id TEXT NOT NULL, request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
                  PRIMARY KEY(principal,domain,visibility,request_id));
                CREATE TABLE IF NOT EXISTS feedback(
                  feedback_id TEXT PRIMARY KEY, principal TEXT NOT NULL, domain TEXT NOT NULL,
                  visibility TEXT NOT NULL, decision_id TEXT NOT NULL, kind TEXT NOT NULL,
                  source_refs_json TEXT NOT NULL, detail TEXT, request_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                  FOREIGN KEY(decision_id) REFERENCES decisions(decision_id));
                CREATE TABLE IF NOT EXISTS candidates(
                  candidate_id TEXT PRIMARY KEY, principal TEXT NOT NULL, domain TEXT NOT NULL,
                  visibility TEXT NOT NULL, candidate_key TEXT NOT NULL, version INTEGER NOT NULL,
                  allowed_class TEXT NOT NULL, proposed_diff_json TEXT NOT NULL,
                  source_refs_json TEXT NOT NULL, replay_evidence_json TEXT,
                  state TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, UNIQUE(principal,domain,visibility,candidate_key,version));
                CREATE TABLE IF NOT EXISTS candidate_requests(
                  principal TEXT NOT NULL, domain TEXT NOT NULL, visibility TEXT NOT NULL,
                  request_id TEXT NOT NULL, request_hash TEXT NOT NULL, response_json TEXT NOT NULL,
                  PRIMARY KEY(principal,domain,visibility,request_id));
            """)
        os.chmod(self.path, 0o600)

    @staticmethod
    def _identity(scope: Scope | Mapping[str, str]) -> tuple[str, str, str]:
        return _scope(scope)

    @staticmethod
    def _json(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("source_refs_json", "proposed_diff_json", "replay_evidence_json", "response_json"):
            if key in data and data[key] is not None:
                data[key[:-5] if key.endswith("_json") else key] = json.loads(data.pop(key))
        return data

    def record_decision(self, event: Mapping[str, Any], *, scope: Scope | Mapping[str, str],
                        decision: Mapping[str, Any], now: datetime | str) -> dict[str, Any]:
        row, identity = _validate_event(event), self._identity(scope)
        current = _now(now).astimezone(timezone.utc)
        if decision.get("source") != row["source"] or decision.get("object_id") != row["object_id"] or decision.get("revision") != row["revision"]:
            raise AttentionError("Decision does not match event")
        if decision.get("outcome") not in OUTCOMES:
            raise AttentionError("Invalid outcome")
        dedupe = hashlib.sha256("\x1f".join((*identity, row["source"], row["object_id"], str(row["revision"]), row["event_type"])).encode()).hexdigest()
        occurrence = hashlib.sha256("\x1f".join((*identity, row["source"], row["object_id"], row["event_type"])).encode()).hexdigest()
        candidate_outcome = decision["outcome"]
        mode = decision.get("mode", "shadow")
        shadow = self.dry_run or mode == "shadow"
        did = uuid.uuid5(uuid.NAMESPACE_URL, dedupe).hex
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? AND dedupe_key=?", (*identity, dedupe)).fetchone()
            if existing:
                db.commit()
                return self._json(existing)
            stopped = db.execute("SELECT 1 FROM decisions WHERE principal=? AND domain=? AND visibility=? AND occurrence_key=? AND state='stopped' LIMIT 1", (*identity, occurrence)).fetchone()
            snoozed = db.execute("SELECT MAX(snoozed_until) FROM decisions WHERE principal=? AND domain=? AND visibility=? AND occurrence_key=? AND state='snoozed'", (*identity, occurrence)).fetchone()[0]
            if stopped:
                candidate_outcome = "retain"
                decision_reason = "occurrence_stopped"
                forced_state = "stopped"
            elif snoozed and _instant(snoozed, "snoozed_until") > current:
                candidate_outcome = "retain"
                decision_reason = "occurrence_snoozed"
                forced_state = "snoozed"
            else:
                decision_reason = str(decision.get("reason", ""))[:256]
                forced_state = None
            stored_outcome = candidate_outcome
            source_ref = row["source_ref"]
            if row["operation"] == "delete":
                self._purge_source_db(db, identity, row["source"], row["object_id"], source_ref)
                source_ref = "[deleted]"
            if candidate_outcome == "notify" and not shadow:
                spent = db.execute("SELECT COUNT(*) FROM decisions WHERE principal=? AND domain=? AND visibility=? AND local_day=? AND outcome='notify'", (*identity, decision["local_day"])).fetchone()[0]
                # A deterministic ceiling is enforced even if a caller supplied a looser policy.
                budget = max(0, min(20, int(decision.get("daily_notification_limit", 2))))
                if spent >= budget:
                    stored_outcome = "digest"
            state = forced_state or ("shadow" if shadow else "pending")
            db.execute("""INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (did, *identity, occurrence, dedupe, row["source"], row["object_id"], row["revision"], row["event_type"],
                        source_ref, stored_outcome, decision_reason, str(decision.get("coverage", "unknown"))[:32],
                        decision.get("last_success_at"), str(decision.get("policy_version", "unknown"))[:64], mode,
                        str(decision.get("local_day", current.date().isoformat())), state, 1, current.isoformat(), None))
            saved = db.execute("SELECT * FROM decisions WHERE decision_id=?", (did,)).fetchone()
            db.commit()
            return self._json(saved)

    @staticmethod
    def _purge_source_db(db: sqlite3.Connection, identity: tuple[str, str, str], source: str,
                         object_id: str, deletion_ref: str | None = None) -> None:
        old = db.execute("SELECT decision_id,source_ref,state FROM decisions WHERE principal=? AND domain=? AND visibility=? AND source=? AND object_id=?", (*identity, source, object_id)).fetchall()
        decision_ids = [row["decision_id"] for row in old]
        refs = {row["source_ref"] for row in old if row["source_ref"] and row["source_ref"] != "[deleted]"}
        if deletion_ref:
            refs.add(deletion_ref)
        for row in old:
            # Held notifications remain explicitly held and budgeted, but lose the deleted reference.
            db.execute("DELETE FROM feedback WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, row["decision_id"]))
            if row["state"] == "held":
                db.execute("UPDATE decisions SET source_ref='[deleted]',reason='source_deleted_held',revision=revision+1 WHERE decision_id=?", (row["decision_id"],))
            else:
                db.execute("DELETE FROM decisions WHERE decision_id=?", (row["decision_id"],))
            db.execute("DELETE FROM requests WHERE principal=? AND domain=? AND visibility=? AND response_json LIKE ?", (*identity, f'%"decision_id": "{row["decision_id"]}"%'))
            db.execute("DELETE FROM feedback WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, row["decision_id"]))
        for feedback in db.execute("SELECT feedback_id,source_refs_json FROM feedback WHERE principal=? AND domain=? AND visibility=?", identity).fetchall():
            if refs.intersection(json.loads(feedback["source_refs_json"])):
                db.execute("DELETE FROM feedback WHERE feedback_id=?", (feedback["feedback_id"],))
        removed_candidates = []
        for candidate in db.execute("SELECT candidate_id,source_refs_json FROM candidates WHERE principal=? AND domain=? AND visibility=?", identity).fetchall():
            if refs.intersection(json.loads(candidate["source_refs_json"])):
                removed_candidates.append(candidate["candidate_id"])
        if removed_candidates:
            db.execute("DELETE FROM candidates WHERE principal=? AND domain=? AND visibility=? AND candidate_id IN (%s)" % ",".join("?" for _ in removed_candidates), (*identity, *removed_candidates))
            db.execute("DELETE FROM candidate_requests WHERE principal=? AND domain=? AND visibility=?", identity)

    def purge_source(self, *, scope: Scope | Mapping[str, str], source: str, object_id: str) -> dict[str, Any]:
        """Remove derived attention/learning data for a source tombstone, preserving held rows."""
        identity = self._identity(scope)
        if source not in SOURCES or not isinstance(object_id, str) or not object_id or len(object_id) > 256:
            raise AttentionError("Invalid source tombstone")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._purge_source_db(db, identity, source, object_id)
            db.commit()
        return {"purged": True, "source": source, "object_id": object_id}

    def get_decision(self, *, scope: Scope | Mapping[str, str], decision_id: str) -> dict[str, Any] | None:
        identity = self._identity(scope)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, decision_id)).fetchone()
            return self._json(row) if row else None

    def list_decisions(self, *, scope: Scope | Mapping[str, str], limit: int = 20,
                       sources: set[str] | frozenset[str] | None = None) -> list[dict[str, Any]]:
        identity = self._identity(scope)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AttentionError("limit must be between 1 and 100")
        if sources is not None:
            if not isinstance(sources, (set, frozenset)) or any(source not in SOURCES for source in sources):
                raise AttentionError("sources must be an explicit subset of canonical sources")
            if not sources:
                return []
            placeholders = ",".join("?" for _ in sources)
            with closing(self._connect()) as db:
                rows = db.execute(f"SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? AND source IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
                                  (*identity, *sorted(sources), limit)).fetchall()
                return [self._json(row) for row in rows]
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? ORDER BY created_at DESC LIMIT ?", (*identity, limit)).fetchall()
            return [self._json(row) for row in rows]

    def today_summary(self, *, scope: Scope | Mapping[str, str], now: datetime | str | None = None) -> dict[str, Any]:
        identity = self._identity(scope)
        local_day = _now(now).astimezone(ZoneInfo("Europe/Copenhagen")).date().isoformat()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT outcome,state,COUNT(*) AS count FROM decisions WHERE principal=? AND domain=? AND visibility=? AND local_day=? GROUP BY outcome,state", (*identity, local_day)).fetchall()
        return {"local_day": local_day, "counts": {f"{r['outcome']}:{r['state']}": r["count"] for r in rows}}

    def apply_decision(self, *, scope: Scope | Mapping[str, str], decision_id: str, operation: str,
                       expected_revision: int, request_id: str, now: datetime | str,
                       until: datetime | str | None = None) -> dict[str, Any]:
        identity = self._identity(scope)
        if operation not in {"snooze", "dismiss", "stop"} or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise AttentionError("Invalid decision operation or request_id")
        current = _now(now).astimezone(timezone.utc)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute("SELECT response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity, request_id)).fetchone()
            if replay:
                request = db.execute("SELECT request_hash FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity, request_id)).fetchone()
                fingerprint = _fingerprint({"decision_id": decision_id, "operation": operation, "expected_revision": expected_revision, "until": until})
                if request[0] != fingerprint:
                    raise AttentionError("request_id was already used for a different request")
                db.commit()
                return json.loads(replay[0])
            row = db.execute("SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, decision_id)).fetchone()
            if not row:
                raise AttentionError("Decision not found in authenticated scope")
            if row["revision"] != expected_revision:
                raise AttentionError("Decision revision conflict")
            if operation == "snooze":
                target = _instant(until, "until").astimezone(timezone.utc) if until else None
                max_days = 30
                if target is None or target <= current or target > current + timedelta(days=max_days):
                    raise AttentionError("Snooze must be in the future and within 30 days")
                state, snoozed = "snoozed", target.isoformat()
            else:
                state, snoozed = ("dismissed" if operation == "dismiss" else "stopped"), None
            db.execute("UPDATE decisions SET state=?,snoozed_until=?,revision=revision+1 WHERE decision_id=?", (state, snoozed, decision_id))
            result = self._json(db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone())
            fingerprint = _fingerprint({"decision_id": decision_id, "operation": operation, "expected_revision": expected_revision, "until": until})
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)", (*identity, request_id, fingerprint, json.dumps(result, sort_keys=True)))
            db.commit()
            return result

    def mark_delivery(self, *, scope: Scope | Mapping[str, str], decision_id: str, state: str,
                      expected_revision: int, request_id: str, now: datetime | str) -> dict[str, Any]:
        """Record caller-observed delivery status; performs no transport operation."""
        identity = self._identity(scope)
        allowed = {"pending", "claimed", "held", "delivered", "uncertain"}
        if state not in allowed or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise AttentionError("Invalid delivery state or request_id")
        current = _now(now).astimezone(timezone.utc)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            replay = db.execute("SELECT response_json FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity, request_id)).fetchone()
            if replay:
                request = db.execute("SELECT request_hash FROM requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity, request_id)).fetchone()
                fingerprint = _fingerprint({"decision_id": decision_id, "state": state, "expected_revision": expected_revision})
                if request[0] != fingerprint:
                    raise AttentionError("request_id was already used for a different request")
                db.commit()
                return json.loads(replay[0])
            row = db.execute("SELECT * FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, decision_id)).fetchone()
            if not row:
                raise AttentionError("Decision not found in authenticated scope")
            if row["revision"] != expected_revision or row["outcome"] != "notify":
                raise AttentionError("Decision revision conflict or not a notification")
            transitions = {"pending": {"claimed", "held", "delivered", "uncertain"}, "claimed": {"held", "delivered", "uncertain"}, "held": {"uncertain"},
                           "uncertain": {"held", "delivered"}, "delivered": set()}
            if state not in transitions.get(row["state"], set()):
                raise AttentionError("Invalid delivery state transition")
            db.execute("UPDATE decisions SET state=?,revision=revision+1 WHERE decision_id=?", (state, decision_id))
            result = self._json(db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone())
            fingerprint = _fingerprint({"decision_id": decision_id, "state": state, "expected_revision": expected_revision})
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?)", (*identity, request_id, fingerprint, json.dumps(result, sort_keys=True)))
            db.commit()
            return result

    def record_feedback(self, *, scope: Scope | Mapping[str, str], decision_id: str, kind: str,
                        source_refs: list[str], request_id: str, now: datetime | str,
                        detail: str | None = None) -> dict[str, Any]:
        identity = self._identity(scope)
        if kind not in FEEDBACK or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise AttentionError("Invalid feedback kind or request_id")
        if not isinstance(source_refs, list) or not source_refs or len(source_refs) > 8 or any(not isinstance(s, str) or not s or len(s) > 1024 for s in source_refs):
            raise AttentionError("Feedback requires bounded source references")
        if detail is not None and (not isinstance(detail, str) or len(detail) > 1000):
            raise AttentionError("Feedback detail must be at most 1000 characters")
        current = _now(now).astimezone(timezone.utc)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            fid = uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join((*identity, request_id))).hex
            fingerprint = _fingerprint({"decision_id": decision_id, "kind": kind, "source_refs": source_refs, "detail": detail})
            prior = db.execute("SELECT feedback_id,request_hash FROM feedback WHERE principal=? AND domain=? AND visibility=? AND feedback_id=?", (*identity, fid)).fetchone()
            if prior:
                if prior["request_hash"] != fingerprint:
                    raise AttentionError("request_id was already used for different feedback")
                result = {"feedback_id": prior[0], "replayed": True}
                db.commit()
                return result
            decision = db.execute("SELECT decision_id FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, decision_id)).fetchone()
            if not decision:
                raise AttentionError("Decision not found in authenticated scope")
            decision_row = db.execute("SELECT source_ref FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?", (*identity, decision_id)).fetchone()
            if decision_row["source_ref"] == "[deleted]":
                raise AttentionError("Deleted source decisions cannot receive feedback")
            db.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?,?,?)", (fid, *identity, decision_id, kind, json.dumps(source_refs), detail, fingerprint, current.isoformat()))
            db.commit()
            return {"feedback_id": fid, "decision_id": decision_id, "kind": kind, "replayed": False}

    def propose_candidate(self, *, scope: Scope | Mapping[str, str], candidate_key: str, allowed_class: str,
                          proposed_diff: Mapping[str, Any], source_refs: list[str], now: datetime | str,
                          policy: Mapping[str, Any] | str | Path | None = None) -> dict[str, Any]:
        identity = self._identity(scope)
        cfg = _policy(policy)
        learning = cfg.get("learning", {})
        if allowed_class not in learning.get("allowed_candidate_classes", []) or learning.get("automatic_activation") is not False:
            raise AttentionError("Candidate class is not eligible for review")
        if not isinstance(candidate_key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", candidate_key):
            raise AttentionError("Invalid candidate_key")
        if not isinstance(proposed_diff, Mapping) or len(json.dumps(proposed_diff).encode()) > 4096:
            raise AttentionError("Candidate diff must be a bounded object")
        diff_fields = {"briefing-format": {"max_bullets", "max_chars", "include_source_links"},
                       "attention-threshold": {"minimum_relevance", "minimum_repeat_evidence"}}
        if not proposed_diff or set(proposed_diff) - diff_fields.get(allowed_class, set()):
            raise AttentionError("Candidate diff contains fields outside its allowed class")
        ranges = {"max_bullets": lambda v: type(v) is int and 1 <= v <= 8,
                  "max_chars": lambda v: type(v) is int and 100 <= v <= 1000,
                  "include_source_links": lambda v: type(v) is bool,
                  "minimum_relevance": lambda v: type(v) in {int, float} and 0 <= v <= 1,
                  "minimum_repeat_evidence": lambda v: type(v) is int and 2 <= v <= 10}
        if any(not ranges[field](value) for field, value in proposed_diff.items()):
            raise AttentionError("Candidate diff value is outside its bound")
        if not isinstance(source_refs, list) or not source_refs or len(source_refs) > 16 or any(not isinstance(s, str) or not s or len(s) > 1024 for s in source_refs):
            raise AttentionError("Candidate requires bounded source references")
        current = _now(now).astimezone(timezone.utc)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM candidates WHERE principal=? AND domain=? AND visibility=? AND candidate_key=?", (*identity, candidate_key)).fetchone()[0]
            cid = uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join((*identity, candidate_key, str(version)))).hex
            threshold = int(learning.get("repeated_feedback_threshold", 3))
            relevant_decisions = set()
            for feedback_row in db.execute("SELECT decision_id,kind,source_refs_json FROM feedback WHERE principal=? AND domain=? AND visibility=?", identity).fetchall():
                refs = set(json.loads(feedback_row["source_refs_json"]))
                if feedback_row["kind"] in {"not_useful", "correction"} and refs.intersection(source_refs):
                    relevant_decisions.add(feedback_row["decision_id"])
            if len(relevant_decisions) < threshold:
                raise AttentionError("Repeated explicit feedback threshold not met")
            db.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (cid, *identity, candidate_key, version, allowed_class,
                json.dumps(proposed_diff, sort_keys=True), json.dumps(source_refs), None, "proposed", 1, current.isoformat(), current.isoformat()))
            row = db.execute("SELECT * FROM candidates WHERE candidate_id=?", (cid,)).fetchone()
            db.commit()
            return self._json(row)

    def review_feedback(self, *, scope: Scope | Mapping[str, str], decision_id: str, now: datetime | str,
                        policy: Mapping[str, Any] | str | Path | None = None) -> dict[str, Any] | None:
        """Create an inspectable deterministic policy candidate after distinct explicit feedback."""
        identity = self._identity(scope)
        cfg = _policy(policy)
        learning = cfg.get("learning", {})
        if "attention-threshold" not in learning.get("allowed_candidate_classes", []):
            return None
        threshold = int(learning.get("repeated_feedback_threshold", 3))
        with closing(self._connect()) as db:
            target = db.execute("SELECT source,event_type,source_ref FROM decisions WHERE principal=? AND domain=? AND visibility=? AND decision_id=?",
                                (*identity, decision_id)).fetchone()
            if not target or target["source_ref"] == "[deleted]":
                raise AttentionError("Decision not found or its source was deleted")
            evidence = db.execute("""SELECT DISTINCT d.decision_id,d.source_ref,f.kind
                FROM feedback f JOIN decisions d ON d.decision_id=f.decision_id
                WHERE f.principal=? AND f.domain=? AND f.visibility=? AND d.principal=? AND d.domain=? AND d.visibility=?
                  AND d.source=? AND d.event_type=? AND f.kind IN ('not_useful','correction') AND d.source_ref<>'[deleted]'
                ORDER BY d.created_at,d.decision_id""", (*identity, *identity, target["source"], target["event_type"])).fetchall()
        decision_refs: dict[str, str] = {}
        for item in evidence:
            decision_refs.setdefault(item["decision_id"], item["source_ref"])
        if len(decision_refs) < threshold:
            return None
        source_refs = sorted(set(decision_refs.values()))[-16:]
        candidate_key = f"attention-threshold:{target['source']}:{target['event_type']}"
        # Return the existing proposal when the exact evidence set was already reviewed.
        for existing in self.list_candidates(scope=scope, limit=100):
            if (existing["candidate_key"] == candidate_key and existing["allowed_class"] == "attention-threshold"
                    and existing["source_refs"] == source_refs and existing["state"] != "retired"):
                return existing
        return self.propose_candidate(scope=scope, candidate_key=candidate_key,
            allowed_class="attention-threshold", proposed_diff={"minimum_repeat_evidence": min(10, threshold + 1)},
            source_refs=source_refs, now=now, policy=cfg)

    def transition_candidate(self, *, scope: Scope | Mapping[str, str], candidate_id: str, transition: str,
                             expected_revision: int, request_id: str, now: datetime | str,
                             replay_evidence: Mapping[str, Any] | None = None,
                             authority: TrustedAuthority | None = None) -> dict[str, Any]:
        identity = self._identity(scope)
        if transition not in CANDIDATE_STATES or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise AttentionError("Invalid candidate transition")
        if transition == "approved" and (not isinstance(authority, TrustedAuthority) or not authority.can_approve_candidates
                or (authority.principal_scope, authority.data_domain) != identity[:2]):
            raise AttentionError("Trusted scoped approval authority required")
        if transition == "tested" and (not isinstance(replay_evidence, Mapping) or not replay_evidence.get("passed") is True
                or not replay_evidence.get("replay_ref") or len(json.dumps(replay_evidence).encode()) > 4096):
            raise AttentionError("Passing bounded replay evidence required")
        current = _now(now).astimezone(timezone.utc)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            fingerprint = _fingerprint({"candidate_id": candidate_id, "transition": transition,
                "expected_revision": expected_revision, "replay_evidence": replay_evidence,
                "authority": ([authority.principal_scope, authority.data_domain, authority.can_approve_candidates] if authority else None)})
            replay = db.execute("SELECT response_json,request_hash FROM candidate_requests WHERE principal=? AND domain=? AND visibility=? AND request_id=?", (*identity, request_id)).fetchone()
            if replay:
                if replay["request_hash"] != fingerprint:
                    raise AttentionError("request_id was already used for a different candidate transition")
                db.commit()
                return json.loads(replay[0])
            row = db.execute("SELECT * FROM candidates WHERE principal=? AND domain=? AND visibility=? AND candidate_id=?", (*identity, candidate_id)).fetchone()
            if not row:
                raise AttentionError("Candidate not found in authenticated scope")
            if row["revision"] != expected_revision:
                raise AttentionError("Candidate revision conflict")
            if transition not in TRANSITIONS[row["state"]]:
                raise AttentionError("Invalid candidate state transition")
            evidence = json.dumps(replay_evidence, sort_keys=True) if replay_evidence is not None else row["replay_evidence_json"]
            db.execute("UPDATE candidates SET state=?,revision=revision+1,updated_at=?,replay_evidence_json=? WHERE candidate_id=?", (transition, current.isoformat(), evidence, candidate_id))
            result = self._json(db.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone())
            db.execute("INSERT INTO candidate_requests VALUES(?,?,?,?,?,?)", (*identity, request_id, fingerprint, json.dumps(result, sort_keys=True)))
            db.commit()
            return result

    def get_candidate(self, *, scope: Scope | Mapping[str, str], candidate_id: str) -> dict[str, Any] | None:
        identity = self._identity(scope)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM candidates WHERE principal=? AND domain=? AND visibility=? AND candidate_id=?", (*identity, candidate_id)).fetchone()
            return self._json(row) if row else None

    def list_candidates(self, *, scope: Scope | Mapping[str, str], limit: int = 20) -> list[dict[str, Any]]:
        identity = self._identity(scope)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AttentionError("limit must be between 1 and 100")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT * FROM candidates WHERE principal=? AND domain=? AND visibility=? ORDER BY created_at DESC LIMIT ?", (*identity, limit)).fetchall()
            return [self._json(row) for row in rows]
