"""Bounded, no-retry Hermes delivery candidate for scoped attention decisions.

This module has no listener and no scheduler. It reads ContextStore and
AttentionLedger as the same private service user, admits one occurrence to a
durable outbox, then claims it before making one fixed-target HTTP request.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
import uuid

from assistant_attention import AttentionError, _now, evaluate
from assistant_context_store import ContextStore, Scope

ROUTE_NAME = "homecompute-context-owner"
ENDPOINT = "http://10.77.20.2:8644/webhooks/homecompute-context-owner"
TARGET = "mattermost"
PROFILE = "default"
MAX_MESSAGE_BYTES = 4000
MAX_RESPONSE_BYTES = 8192
ALLOWED_SOURCE_SCOPES = {("owner", "personal", "private"), ("family", "household", "shared")}
TERMINAL = {"shadow", "accepted", "delivered", "uncertain", "held", "cancelled"}


class DeliveryError(ValueError):
    """Delivery is not admissible or a bounded protocol validation failed."""


@dataclass(frozen=True)
class DeliveryAuth:
    route_secret: str
    owner_user_id: str
    bot_user_id: str
    owner_destination_id: str
    route_config_sha256: str
    standing_grant_id: str
    allowed_sources: frozenset[str]
    allowed_policy_versions: frozenset[str]
    grant_expires_at: str
    _proof: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._proof is not _AUTH_PROOF:
            raise DeliveryError("DeliveryAuth can only be loaded from the private standing-grant file")


_AUTH_PROOF = object()


def _scope(scope: Scope | Mapping[str, str]) -> tuple[str, str, str]:
    if isinstance(scope, Mapping):
        identity = (scope.get("principal_scope"), scope.get("data_domain"), scope.get("visibility", "private"))
    else:
        identity = (getattr(scope, "principal_scope", None), getattr(scope, "data_domain", None), getattr(scope, "visibility", "private"))
    if identity not in ALLOWED_SOURCE_SCOPES:
        raise DeliveryError("Authenticated owner or approved family scope required")
    return identity  # type: ignore[return-value]


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def hermes_v2_headers(secret: str, body: bytes, *, timestamp: int | str, request_id: str,
                      now: datetime | str | None = None) -> dict[str, str]:
    """Build pinned Generic V2 authentication headers over the exact request bytes."""
    if not isinstance(secret, str) or len(secret) < 32 or secret.startswith("REPLACE_"):
        raise DeliveryError("A private Hermes route HMAC secret is required")
    stamp = str(timestamp)
    if not re.fullmatch(r"[0-9]{1,12}", stamp):
        raise DeliveryError("Invalid Hermes V2 timestamp")
    current = int(_now(now).timestamp())
    if abs(current - int(stamp)) > 300:
        raise DeliveryError("Hermes V2 timestamp must be within five minutes of request time")
    if not isinstance(body, bytes) or len(body) > MAX_MESSAGE_BYTES + 2048:
        raise DeliveryError("Hermes request body exceeds its bound")
    if not isinstance(request_id, str) or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
        raise DeliveryError("Stable 32-character delivery ID required")
    signature = hmac.new(secret.encode("utf-8"), stamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": stamp,
        "X-Webhook-Signature-V2": signature,
        "X-Request-ID": request_id,
    }


def _validate_message(message: str) -> str:
    if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise DeliveryError("Message must be nonempty and at most 4000 UTF-8 bytes")
    if any(ord(ch) < 0x20 and ch not in "\n\r\t" for ch in message):
        raise DeliveryError("Message contains a control character")
    return message


def _public(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys() if key not in {"message", "request_hash"}}


def classify_hermes_response(status: int, body: bytes, *, delivery_id: str) -> tuple[str, dict[str, Any]]:
    """Classify actual pinned Hermes evidence without treating admission as delivery."""
    try:
        payload = json.loads(body[:MAX_RESPONSE_BYTES]) if body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result: dict[str, Any] = {"response_status": int(status), "hermes_delivery_id": None,
                              "mattermost_post_id": None, "mattermost_root_id": None}
    for field in ("post_id", "mattermost_post_id"):
        value = payload.get(field)
        if isinstance(value, str) and 1 <= len(value) <= 128:
            result["mattermost_post_id"] = value
            break
    for field in ("root_id", "mattermost_root_id"):
        value = payload.get(field)
        if isinstance(value, str) and 1 <= len(value) <= 128:
            result["mattermost_root_id"] = value
            break
    upstream_id = payload.get("delivery_id")
    if isinstance(upstream_id, str) and 1 <= len(upstream_id) <= 128:
        result["hermes_delivery_id"] = upstream_id
    if status == 200 and payload.get("status") == "delivered" and payload.get("route") == ROUTE_NAME \
            and payload.get("target") == TARGET and upstream_id == delivery_id:
        return "delivered", result
    if status == 202 and payload.get("status") == "accepted" and upstream_id == delivery_id:
        return "accepted", result
    if status == 200 and payload.get("status") == "duplicate" and upstream_id == delivery_id:
        # The pinned direct-delivery route records an in-memory ID before sending.
        # A duplicate proves only that the ID was seen, not that the target got it.
        return "uncertain", result
    if status >= 500 or status < 200 or status >= 300:
        return ("uncertain" if status == 502 or status >= 500 else "held"), result
    return "held", result


class HermesHTTPTransport:
    """Direct fixed-host HTTP transport; no proxies, redirects, or configurable URL."""
    def __call__(self, body: bytes, headers: Mapping[str, str], *, timeout: float = 8.0) -> tuple[int, bytes]:
        parsed = urlsplit(ENDPOINT)
        if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != (
                "http", "10.77.20.2", 8644, "/webhooks/" + ROUTE_NAME):
            raise DeliveryError("Fixed Hermes endpoint declaration changed")
        conn = http.client.HTTPConnection("10.77.20.2", 8644, timeout=timeout)
        try:
            request_headers = {**headers, "Host": "agents.home.arpa:8644"}
            conn.request("POST", "/webhooks/" + ROUTE_NAME, body=body, headers=request_headers)
            response = conn.getresponse()
            if response.getheader("Transfer-Encoding", "").lower() == "chunked":
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            else:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return response.status, b""
            return response.status, raw
        finally:
            conn.close()


class DeliveryOutbox:
    """Private SQLite outbox keyed by authenticated scope and attention occurrence."""
    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077 or self.path.is_symlink():
            raise DeliveryError("Outbox must be a private regular file in a 0700 directory")
        created = not self.path.exists()
        self._init_db()
        if created:
            os.chmod(self.path, 0o600)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init_db(self) -> None:
        with closing(self._db()) as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS delivery_outbox(
                delivery_id TEXT PRIMARY KEY,
                principal TEXT NOT NULL, domain TEXT NOT NULL, visibility TEXT NOT NULL,
                decision_id TEXT NOT NULL, occurrence_key TEXT NOT NULL,
                source TEXT NOT NULL, object_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
                route_name TEXT NOT NULL, target TEXT NOT NULL,
                message TEXT, message_hash TEXT NOT NULL, request_hash TEXT NOT NULL,
                status TEXT NOT NULL, revision INTEGER NOT NULL, reason TEXT,
                response_status INTEGER, hermes_delivery_id TEXT,
                mattermost_post_id TEXT, mattermost_root_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(principal,domain,visibility,occurrence_key),
                UNIQUE(principal,domain,visibility,decision_id));
              CREATE INDEX IF NOT EXISTS delivery_status ON delivery_outbox(status,updated_at);
              PRAGMA user_version=1;
            """)
        os.chmod(self.path, 0o600)

    def get_by_decision(self, *, scope: Scope | Mapping[str, str], decision_id: str) -> dict[str, Any] | None:
        identity = _scope(scope)
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM delivery_outbox WHERE principal=? AND domain=? AND visibility=? AND decision_id=?",
                             (*identity, decision_id)).fetchone()
            return _public(row) if row else None

    def admit(self, *, scope: Scope | Mapping[str, str], decision_id: str, message: str,
              ledger: Any, context: ContextStore, source_status: Mapping[str, Any],
              policy: Mapping[str, Any], now: datetime | str, dry_run: bool = True) -> dict[str, Any]:
        identity = _scope(scope)
        if identity != ("owner", "personal", "private"):
            raise DeliveryError("Hermes notification route is owner-private")
        body_text = _validate_message(message)
        decision = ledger.get_decision(scope=scope, decision_id=decision_id)
        if decision is None or decision.get("source_ref") == "[deleted]":
            raise DeliveryError("Decision is unavailable or its source was deleted")
        allowed_states = {"shadow", "pending"} if dry_run else {"pending"}
        if decision.get("outcome") != "notify" or decision.get("state") not in allowed_states:
            raise DeliveryError("Only a current discretionary notify decision can enter the outbox")
        current = _now(now)
        projection = context.get(scope, decision["source"], decision["object_id"])
        if not projection or projection.get("revision") != decision["source_revision"]:
            raise DeliveryError("Source is missing or its revision changed")
        event = {"schema_version": 1, **{key: projection[key] for key in (
            "source", "object_id", "revision", "event_type", "operation", "occurred_at", "source_ref", "payload")}}
        checked = evaluate(event, scope=scope, now=current, source_status=source_status, policy=policy)
        if checked["outcome"] != "notify":
            raise DeliveryError("Current source coverage, quiet hours, or policy suppresses delivery")
        daily = [row for row in ledger.list_decisions(scope=scope, limit=100)
                 if row.get("local_day") == checked["local_day"] and row.get("outcome") == "notify"]
        if len(daily) > int(policy.get("daily_discretionary_notification_limit", 2)):
            raise DeliveryError("Daily discretionary notification budget is exhausted")
        if not dry_run and (policy.get("mode") != "active" or decision.get("mode") != "active"):
            raise DeliveryError("Active attention policy is required")
        occurrence = decision.get("occurrence_key")
        if not isinstance(occurrence, str) or not re.fullmatch(r"[a-f0-9]{64}", occurrence):
            raise DeliveryError("Decision has no stable occurrence key")
        delivery_id = uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join((*identity, occurrence))).hex
        message_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        request_body = {"event_type": "homecompute_attention", "decision_id": decision_id,
                        "source": decision["source"], "object_id": decision["object_id"],
                        "revision": decision["source_revision"], "message": body_text}
        request_hash = hashlib.sha256(_json_bytes(request_body)).hexdigest()
        status = "shadow" if dry_run else "pending"
        reason = "dry_run" if dry_run else None
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM delivery_outbox WHERE principal=? AND domain=? AND visibility=? AND occurrence_key=?",
                                  (*identity, occurrence)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    if existing["status"] == "shadow" and not dry_run:
                        stamp = current.astimezone(timezone.utc).isoformat()
                        db.execute("UPDATE delivery_outbox SET decision_id=?,source_revision=?,message=?,message_hash=?,request_hash=?,status='pending',reason=NULL,revision=revision+1,updated_at=? WHERE delivery_id=?",
                            (decision_id, decision["source_revision"], body_text, message_hash, request_hash, stamp, existing["delivery_id"]))
                        promoted = db.execute("SELECT * FROM delivery_outbox WHERE delivery_id=?", (existing["delivery_id"],)).fetchone()
                        db.commit()
                        return _public(promoted)
                    raise DeliveryError("This decision occurrence was already admitted with different content")
                db.commit()
                return _public(existing)
            stamp = current.astimezone(timezone.utc).isoformat()
            db.execute("""INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (delivery_id, *identity, decision_id, occurrence, decision["source"], decision["object_id"],
                 decision["source_revision"], ROUTE_NAME, TARGET, body_text, message_hash, request_hash,
                 status, 1, reason, None, None, None, None, stamp, stamp))
            row = db.execute("SELECT * FROM delivery_outbox WHERE delivery_id=?", (delivery_id,)).fetchone()
            db.commit()
            return _public(row)

    def _row_for_claim(self, db: sqlite3.Connection, identity: tuple[str, str, str], decision_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM delivery_outbox WHERE principal=? AND domain=? AND visibility=? AND decision_id=?",
                         (*identity, decision_id)).fetchone()
        if not row:
            raise DeliveryError("Outbox item not found in authenticated scope")
        return row

    def claim(self, *, scope: Scope | Mapping[str, str], decision_id: str, now: datetime | str) -> dict[str, Any]:
        identity = _scope(scope)
        stamp = _now(now).astimezone(timezone.utc).isoformat()
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row_for_claim(db, identity, decision_id)
            if row["status"] != "pending":
                raise DeliveryError("Outbox item is not pending and cannot be sent")
            db.execute("UPDATE delivery_outbox SET status='claimed',revision=revision+1,updated_at=? WHERE delivery_id=? AND status='pending'",
                       (stamp, row["delivery_id"]))
            claimed = self._row_for_claim(db, identity, decision_id)
            db.commit()
            return {**_public(claimed), "message": claimed["message"]}

    def finish(self, *, scope: Scope | Mapping[str, str], decision_id: str, state: str,
               now: datetime | str, result: Mapping[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
        if state not in {"accepted", "delivered", "uncertain", "held"}:
            raise DeliveryError("Invalid terminal delivery state")
        identity = _scope(scope)
        stamp = _now(now).astimezone(timezone.utc).isoformat()
        values = result or {}
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row_for_claim(db, identity, decision_id)
            if row["status"] != "claimed":
                raise DeliveryError("Only an atomic claimed item can be finished")
            db.execute("""UPDATE delivery_outbox SET status=?,revision=revision+1,reason=?,response_status=?,
                hermes_delivery_id=?,mattermost_post_id=?,mattermost_root_id=?,updated_at=? WHERE delivery_id=?""",
                (state, (reason or "")[:256] or None, values.get("response_status"), values.get("hermes_delivery_id"),
                 values.get("mattermost_post_id"), values.get("mattermost_root_id"), stamp, row["delivery_id"]))
            final = self._row_for_claim(db, identity, decision_id)
            db.commit()
            return _public(final)

    def recover_inflight(self, *, scope: Scope | Mapping[str, str], now: datetime | str) -> int:
        """On process restart, uncertain claims become terminal and are never retried."""
        identity = _scope(scope)
        stamp = _now(now).astimezone(timezone.utc).isoformat()
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("UPDATE delivery_outbox SET status='uncertain',reason='process_restarted_after_claim',revision=revision+1,updated_at=? "
                                "WHERE principal=? AND domain=? AND visibility=? AND status='claimed'", (stamp, *identity))
            db.commit()
            return result.rowcount

    def purge_source(self, *, scope: Scope | Mapping[str, str], source: str, object_id: str,
                     now: datetime | str | None = None) -> int:
        """Scrub message bodies on source deletion; claimed effects become uncertain."""
        identity = _scope(scope)
        if source not in {"home-assistant", "daythread", "aula", "calendar"} or not isinstance(object_id, str):
            raise DeliveryError("Invalid source tombstone")
        stamp = _now(now or self.clock()).astimezone(timezone.utc).isoformat()
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute("""UPDATE delivery_outbox SET message=NULL,status=CASE
                WHEN status='claimed' THEN 'uncertain' WHEN status IN ('pending','shadow') THEN 'cancelled' ELSE status END,
                reason='source_deleted',revision=revision+1,updated_at=?
                WHERE principal=? AND domain=? AND visibility=? AND source=? AND object_id=? AND message IS NOT NULL""",
                (stamp, *identity, source, object_id))
            db.commit()
            return result.rowcount

    def send_once(self, *, scope: Scope | Mapping[str, str], decision_id: str, ledger: Any,
                  context: ContextStore, source_status: Mapping[str, Any] | Callable[[], Mapping[str, Any]], policy: Mapping[str, Any],
                  auth: DeliveryAuth, now: datetime | str,
                  transport: Callable[..., tuple[int, bytes]] | None = None) -> dict[str, Any]:
        """Recheck, persist claim, then make exactly one fixed-route transport call."""
        identity = _scope(scope)
        if identity != ("owner", "personal", "private"):
            raise DeliveryError("Hermes notification route is owner-private")
        current = _now(now)
        if policy.get("mode") != "active":
            raise DeliveryError("Policy is not active")
        if not isinstance(auth, DeliveryAuth) or len(auth.route_secret) < 32:
            raise DeliveryError("Private route auth and qualified standing grant are required")
        if (not auth.owner_destination_id or auth.owner_destination_id.startswith("REPLACE_")
                or _now(auth.grant_expires_at) <= current):
            raise DeliveryError("Qualified owner Mattermost destination grant is expired or missing")
        decision = ledger.get_decision(scope=scope, decision_id=decision_id)
        if decision is None or decision.get("state") != "pending" or decision.get("outcome") != "notify" \
                or decision.get("mode") != "active" or decision.get("source_ref") == "[deleted]":
            raise DeliveryError("Decision is no longer pending, active, or source-backed")
        if (decision.get("source") not in auth.allowed_sources
                or decision.get("policy_version") not in auth.allowed_policy_versions):
            raise DeliveryError("Source or policy version is outside the qualified standing grant")
        projection = context.get(scope, decision["source"], decision["object_id"])
        if not projection or projection.get("revision") != decision["source_revision"]:
            self.hold(scope=scope, decision_id=decision_id, now=current, reason="source_missing_or_revision_changed")
            raise DeliveryError("Source is missing or its revision changed")
        event = {"schema_version": 1, **{key: projection[key] for key in (
            "source", "object_id", "revision", "event_type", "operation", "occurred_at", "source_ref", "payload")}}
        coverage = source_status() if callable(source_status) else source_status
        checked = evaluate(event, scope=scope, now=current, source_status=coverage, policy=policy)
        if checked["outcome"] != "notify":
            self.hold(scope=scope, decision_id=decision_id, now=current, reason="quiet_hours_freshness_or_policy_changed")
            raise DeliveryError("Current policy or source coverage suppresses delivery")
        daily = [row for row in ledger.list_decisions(scope=scope, limit=100)
                 if row.get("local_day") == checked["local_day"] and row.get("outcome") == "notify"]
        if len(daily) > int(policy.get("daily_discretionary_notification_limit", 2)):
            self.hold(scope=scope, decision_id=decision_id, now=current, reason="daily_budget_changed")
            raise DeliveryError("Daily notification budget is exhausted")
        if not auth.route_config_sha256:
            raise DeliveryError("Qualified fixed Hermes route fingerprint is missing")
        claimed = self.claim(scope=scope, decision_id=decision_id, now=current)
        # Re-read the canonical source and decision after committing the claim and immediately before I/O.
        dispatch_now = _now(self.clock())
        latest = ledger.get_decision(scope=scope, decision_id=decision_id)
        latest_source = context.get(scope, decision["source"], decision["object_id"])
        if latest is None or latest.get("state") != "pending" or latest.get("revision") != decision.get("revision") \
                or latest_source is None or latest_source.get("revision") != decision["source_revision"]:
            state = self.get_by_decision(scope=scope, decision_id=decision_id)
            if state and state["status"] == "claimed":
                return self.finish(scope=scope, decision_id=decision_id, state="held", now=dispatch_now,
                                   reason="decision_or_source_changed_after_claim")
            return state or {"status": "uncertain", "decision_id": decision_id}
        if _now(auth.grant_expires_at) <= dispatch_now:
            return self.finish(scope=scope, decision_id=decision_id, state="held", now=dispatch_now,
                               reason="standing_grant_expired_after_claim")
        latest_event = {"schema_version": 1, **{key: latest_source[key] for key in (
            "source", "object_id", "revision", "event_type", "operation", "occurred_at", "source_ref", "payload")}}
        latest_coverage = source_status() if callable(source_status) else source_status
        latest_eval = evaluate(latest_event, scope=scope, now=dispatch_now,
                               source_status=latest_coverage, policy=policy)
        latest_daily = [item for item in ledger.list_decisions(scope=scope, limit=100)
                        if item.get("local_day") == latest_eval["local_day"] and item.get("outcome") == "notify"]
        if latest_eval["outcome"] != "notify" or len(latest_daily) > int(policy.get("daily_discretionary_notification_limit", 2)):
            return self.finish(scope=scope, decision_id=decision_id, state="held", now=dispatch_now,
                               reason="fresh_dispatch_gate_suppressed")
        body = _json_bytes({"event_type": "homecompute_attention", "decision_id": decision_id,
                            "source": decision["source"], "object_id": decision["object_id"],
                            "revision": decision["source_revision"], "message": claimed["message"]})
        headers = hermes_v2_headers(auth.route_secret, body, timestamp=int(dispatch_now.timestamp()),
                                    request_id=claimed["delivery_id"], now=dispatch_now)
        try:
            status, response_body = (transport or HermesHTTPTransport())(body, headers, timeout=8.0)
            state, result = classify_hermes_response(status, response_body, delivery_id=claimed["delivery_id"])
            finished = self.finish(scope=scope, decision_id=decision_id, state=state, now=dispatch_now, result=result,
                                   reason=None if state == "delivered" else "upstream_" + state)
        except Exception:
            finished = self.finish(scope=scope, decision_id=decision_id, state="uncertain", now=dispatch_now,
                                   reason="transport_result_uncertain")
        # The outbox is authoritative for this candidate. Ledger update is a scoped receipt only;
        # a crash between the two leaves a safe non-retryable outbox state.
        if finished["status"] in {"delivered", "uncertain", "held"}:
            try:
                latest = ledger.get_decision(scope=scope, decision_id=decision_id)
                if latest and latest.get("state") in {"pending", "claimed", "held", "uncertain"}:
                    ledger.mark_delivery(scope=scope, decision_id=decision_id, state=finished["status"],
                        expected_revision=latest["revision"], request_id="delivery-" + claimed["delivery_id"], now=dispatch_now)
            except Exception:
                pass
        return finished

    def hold(self, *, scope: Scope | Mapping[str, str], decision_id: str, now: datetime | str, reason: str) -> dict[str, Any]:
        identity = _scope(scope)
        stamp = _now(now).astimezone(timezone.utc).isoformat()
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row_for_claim(db, identity, decision_id)
            if row["status"] in TERMINAL:
                db.commit()
                return _public(row)
            if row["status"] not in {"pending", "claimed"}:
                raise DeliveryError("Outbox item cannot be held from its current state")
            final_state = "uncertain" if row["status"] == "claimed" else "held"
            db.execute("UPDATE delivery_outbox SET status=?,reason=?,revision=revision+1,updated_at=? WHERE delivery_id=?",
                       (final_state, reason[:256], stamp, row["delivery_id"]))
            result = self._row_for_claim(db, identity, decision_id)
            db.commit()
            return _public(result)


def load_delivery_auth(path: str | Path, *, now: datetime | str) -> DeliveryAuth:
    """Load the private, expiring owner-DM standing grant and route secret."""
    file_path = Path(path)
    if file_path.is_symlink():
        raise DeliveryError("Delivery auth file may not be a symlink")
    info = file_path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096:
        raise DeliveryError("Delivery auth file must be owned by this service user and mode 0600")
    value = json.loads(file_path.read_text(encoding="utf-8"))
    required = {"schema_version", "route_name", "route_registered", "target", "route_config_sha256",
                "standing_grant_id", "standing_grant_status", "owner_user_id", "bot_user_id", "channel_type",
                "owner_destination_id", "allowed_sources", "allowed_policy_versions", "grant_issued_at",
                "grant_expires_at", "qualification_evidence_ref", "route_secret"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
        raise DeliveryError("Exact delivery readiness/auth schema required")
    if (value["route_name"] != ROUTE_NAME or value["route_registered"] is not True or value["target"] != TARGET
            or value["standing_grant_status"] != "active" or value["channel_type"] != "D"):
        raise DeliveryError("Fixed registered route and active owner-DM standing grant are required")
    current = _now(now)
    route_hash = value["route_config_sha256"]
    if not isinstance(route_hash, str) or re.fullmatch(r"[a-f0-9]{64}", route_hash) is None:
        raise DeliveryError("Observed private Hermes route configuration hash required")
    for field in ("owner_user_id", "bot_user_id", "owner_destination_id"):
        if not isinstance(value[field], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[field]) is None:
            raise DeliveryError("Qualified immutable Mattermost owner, bot, and DM IDs required")
    if value["owner_destination_id"].startswith("REPLACE"):
        raise DeliveryError("Owner Mattermost destination is still a template")
    if not isinstance(value["owner_destination_id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["owner_destination_id"]) is None:
        raise DeliveryError("Fixed owner Mattermost destination ID required")
    if not isinstance(value["route_secret"], str) or len(value["route_secret"]) < 32 or value["route_secret"].startswith("REPLACE_"):
        raise DeliveryError("Private route HMAC secret required")
    grant_issued, grant_expires = _now(value["grant_issued_at"]), _now(value["grant_expires_at"])
    if (grant_issued > current + timedelta(minutes=5) or grant_issued < current - timedelta(days=90)
            or grant_expires <= current or grant_expires > grant_issued + timedelta(days=90)):
        raise DeliveryError("Standing grant is expired or outside its 90-day validity")
    if not isinstance(value["standing_grant_id"], str) or re.fullmatch(r"[a-f0-9]{32}", value["standing_grant_id"]) is None:
        raise DeliveryError("Stable standing-grant identifier required")
    if not isinstance(value["qualification_evidence_ref"], str) or not 1 <= len(value["qualification_evidence_ref"]) <= 256:
        raise DeliveryError("Owner-DM qualification evidence reference required")
    sources = value["allowed_sources"]
    policy_versions = value["allowed_policy_versions"]
    valid_sources = {"home-assistant", "daythread", "aula", "calendar"}
    if (not isinstance(sources, list) or not sources or any(item not in valid_sources for item in sources)
            or len(sources) != len(set(sources))):
        raise DeliveryError("Finite standing-grant source allowlist required")
    if (not isinstance(policy_versions, list) or not policy_versions or any(not isinstance(item, str) or not item for item in policy_versions)
            or len(policy_versions) != len(set(policy_versions))):
        raise DeliveryError("Finite attention policy-version allowlist required")
    return DeliveryAuth(value["route_secret"], value["owner_user_id"], value["bot_user_id"],
                        value["owner_destination_id"], route_hash, value["standing_grant_id"],
                        frozenset(sources), frozenset(policy_versions), value["grant_expires_at"], _AUTH_PROOF)


def read_source_coverage(path: str | Path, *, scope: Scope | Mapping[str, str], now: datetime | str,
                         max_age_minutes: int = 1440) -> dict[str, dict[str, Any]]:
    """Read the ContextService coverage table using the same private service identity."""
    identity = _scope(scope)
    sources = ("home-assistant", "daythread", "aula", "calendar")
    result = {source: {"state": "unavailable", "last_success_at": None} for source in sources}
    try:
        with closing(sqlite3.connect(path, timeout=3)) as db:
            rows = db.execute("SELECT source,observed_at,complete FROM coverage WHERE principal=? AND domain=? AND visibility=?", identity).fetchall()
    except (OSError, sqlite3.Error):
        return result
    current = _now(now)
    for source, observed_at, complete in rows:
        if source not in result:
            continue
        observed = _now(observed_at)
        age = current - observed
        state = "stale" if age < timedelta(0) or age > timedelta(minutes=max_age_minutes) else ("fresh" if complete else "partial")
        result[source] = {"state": state, "last_success_at": observed_at}
    return result
