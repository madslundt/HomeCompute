"""Bounded, source-specific adapters for the proactive assistant context journal.

Adapters return untrusted source snapshots. The trusted context service assigns
principal, domain and visibility from the ingest credential; none are serialized
in an event. Provider state is never treated as a durable change feed unless its
adapter explicitly reports a complete scan for a stable coverage key.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
import os
import sqlite3
import stat
from typing import Any, Callable, Iterable, Mapping, Protocol

SCHEMA_VERSION = 1
SOURCES = frozenset({"home-assistant", "daythread", "aula", "calendar"})
MAX_OBJECT_ID = 200
MAX_EVENT_TYPE = 80
MAX_SOURCE_REF = 400
MAX_PAYLOAD_BYTES = 8_192
MAX_OBJECTS = 2_000
MAX_DEPTH = 8
MAX_TEXT = 1_800
_FORBIDDEN_KEYS = frozenset({
    "principal", "principal_scope", "domain", "data_domain", "visibility",
    "authorization", "token", "password", "cookie", "headers", "audio",
    "recording", "audio_path", "transcript_url", "raw_audio",
})


def rfc3339(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Source timestamp must be RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Source timestamp must be RFC3339") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Source timestamp requires an explicit zone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bounded(value: Any, depth: int = 0) -> Any:
    """Copy JSON values under strict size, depth and privacy-key bounds."""
    if depth > MAX_DEPTH:
        raise ValueError("Source payload nesting exceeds limit")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Non-finite source value")
        return value
    if isinstance(value, str):
        text = "".join(c for c in value if ord(c) >= 32 or c in "\n\t\r")
        return text.encode("utf-8")[:MAX_TEXT].decode("utf-8", errors="ignore")
    if isinstance(value, list):
        if len(value) > 20:
            raise ValueError("Source collection exceeds limit")
        return [_bounded(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 60 or any(not isinstance(key, str) or len(key) > 100 for key in value):
            raise ValueError("Source object exceeds limit")
        result = {}
        for key, item in value.items():
            if key.lower() in _FORBIDDEN_KEYS:
                continue
            result[key] = _bounded(item, depth + 1)
        return result
    raise ValueError("Source payload must contain JSON values")


def normalize_event(source: str, object_id: str, revision: int, event_type: str,
                    operation: str, occurred_at: str, source_ref: str,
                    payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create the exact source event wire contract, with no trust/scope fields."""
    if source not in SOURCES:
        raise ValueError("Unsupported assistant context source")
    if (not isinstance(object_id, str) or not 1 <= len(object_id) <= MAX_OBJECT_ID
            or any(ord(c) < 33 for c in object_id)):
        raise ValueError("Invalid source object ID")
    if type(revision) is not int or revision < 1:
        raise ValueError("Revision must be a positive integer")
    if (not isinstance(event_type, str) or not 1 <= len(event_type) <= MAX_EVENT_TYPE
            or not re.fullmatch(r"[a-z][a-z0-9_.-]*", event_type)):
        raise ValueError("Invalid source event type")
    if operation not in {"upsert", "delete"}:
        raise ValueError("Invalid source operation")
    if not isinstance(source_ref, str) or not 1 <= len(source_ref) <= MAX_SOURCE_REF:
        raise ValueError("Invalid source reference")
    bounded_payload = _bounded(dict(payload))
    if not isinstance(bounded_payload, dict) or (operation == "delete" and bounded_payload):
        raise ValueError("Invalid source payload")
    event = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "object_id": object_id,
        "revision": revision,
        "event_type": event_type,
        "operation": operation,
        "occurred_at": rfc3339(occurred_at),
        "source_ref": source_ref,
        "payload": bounded_payload,
    }
    if len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Source event exceeds byte limit")
    return event


@dataclass(frozen=True)
class SourceObject:
    object_id: str
    event_type: str
    occurred_at: str
    source_ref: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class Snapshot:
    source: str
    coverage_key: str
    objects: tuple[SourceObject, ...]
    complete: bool
    observed_at: str
    cursor: str | None = None
    limitation: str = ""

    def __post_init__(self):
        if self.source not in SOURCES or not self.coverage_key or len(self.coverage_key) > 200:
            raise ValueError("Invalid snapshot identity")
        if len(self.objects) > MAX_OBJECTS:
            raise ValueError("Source snapshot exceeds object limit")
        rfc3339(self.observed_at)
        if self.cursor is not None and (not isinstance(self.cursor, str) or len(self.cursor) > 300):
            raise ValueError("Invalid source cursor")


class SnapshotClient(Protocol):
    def read(self) -> Snapshot: ...


class FixedDaythreadTransport(Protocol):
    """Authenticated same-host transport; callers cannot supply a network URL."""
    def get(self, path: str, params: Mapping[str, str | int]) -> Mapping[str, Any]: ...


class DaythreadClient:
    """Consume the authenticated Daythread assistant-context export contract."""

    def __init__(self, transport: FixedDaythreadTransport, *, pages: int = 50):
        if not 1 <= pages <= 50:
            raise ValueError("Daythread page bound refused")
        self.transport, self.pages = transport, pages

    def read(self) -> Snapshot:
        cursor = None
        records: list[Mapping[str, Any]] = []
        snapshot_at = None
        coverage_key = None
        complete_window = False
        limitation = "Daythread export has no deletion feed; removals are never inferred."
        for page_number in range(self.pages):
            params = {"limit": 20}
            if cursor is not None:
                params["cursor"] = cursor
            page = self.transport.get("/v1/integrations/assistant-context", params)
            if (
                not isinstance(page, Mapping)
                or set(page) != {"schema_version", "source", "snapshot_at", "window", "coverage",
                                 "items", "next_cursor"}
                or type(page.get("schema_version")) is not int
                or page.get("schema_version") != 1
                or page.get("source") != "daythread"
            ):
                raise ValueError("Daythread export page refused")
            observed = rfc3339(page.get("snapshot_at"))
            coverage = page.get("coverage")
            window = page.get("window")
            rows = page.get("items")
            if not isinstance(rows, list) or len(rows) > 20:
                raise ValueError("Daythread export items refused")
            if (not isinstance(coverage, Mapping)
                    or set(coverage) != {"window_complete", "deletion_complete", "snapshot_consistent",
                                         "record_limit_reached", "record_count", "returned_records"}
                    or type(coverage.get("window_complete")) is not bool
                    or type(coverage.get("snapshot_consistent")) is not bool
                    or type(coverage.get("record_limit_reached")) is not bool
                    or type(coverage.get("record_count")) is not int or coverage["record_count"] < 0
                    or type(coverage.get("returned_records")) is not int
                    or coverage["returned_records"] != len(rows)
                    or coverage.get("deletion_complete") is not False
                    or not isinstance(window, Mapping)):
                raise ValueError("Daythread export coverage refused")
            if (set(window) != {"start", "end", "days"} or type(window.get("days")) is not int
                    or window["days"] != 30):
                raise ValueError("Daythread export window refused")
            start, end = rfc3339(window.get("start")), rfc3339(window.get("end"))
            if snapshot_at is None:
                snapshot_at = observed
                coverage_key = f"{start}:{end}"
            elif observed != snapshot_at or f"{start}:{end}" != coverage_key:
                raise ValueError("Daythread export cursor changed snapshot")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("Daythread export item refused")
                required = ("object_id", "revision", "event_type", "source_timestamp", "modified_at",
                            "version_ref", "source_ref", "payload")
                if (set(row) != set(required) or type(row["revision"]) is not int or row["revision"] < 1
                        or not isinstance(row["object_id"], str) or not 1 <= len(row["object_id"]) <= 200
                        or not isinstance(row["event_type"], str)
                        or row["event_type"] not in {"daythread.summary", "daythread.commitment"}
                        or not isinstance(row["version_ref"], str) or not 1 <= len(row["version_ref"]) <= 300
                        or not isinstance(row["source_ref"], str) or not 1 <= len(row["source_ref"]) <= 400
                        or not isinstance(row["payload"], Mapping)):
                    raise ValueError("Daythread export item refused")
                expected_ref_prefix = "daythread:diary/" if row["event_type"] == "daythread.summary" else "daythread:decision/"
                if not row["source_ref"].startswith(expected_ref_prefix):
                    raise ValueError("Daythread source reference refused")
                payload = dict(row["payload"])
                expected_payload = ({"day", "summary", "source_refs", "pending_chunks"}
                                    if row["event_type"] == "daythread.summary" else
                                    {"text", "review_status", "state", "relationships", "source_refs"})
                if set(payload) != expected_payload:
                    raise ValueError("Daythread export payload refused")
                refs = payload.get("source_refs")
                if (not isinstance(refs, list) or len(refs) > 20
                        or any(not isinstance(ref, Mapping) or set(ref) != {"id", "version"}
                               or not isinstance(ref["id"], str) or len(ref["id"]) > 200
                               or not isinstance(ref["version"], str) or len(ref["version"]) > 120
                               for ref in refs)):
                    raise ValueError("Daythread source references refused")
                if row["event_type"] == "daythread.summary":
                    if (not isinstance(payload["day"], str) or len(payload["day"]) != 10
                            or not isinstance(payload["summary"], str)
                            or len(payload["summary"].encode("utf-8")) > 2400
                            or type(payload["pending_chunks"]) is not int):
                        raise ValueError("Daythread summary payload refused")
                else:
                    relationships = payload.get("relationships")
                    if (not isinstance(payload["text"], str)
                            or len(payload["text"].encode("utf-8")) > 1000
                            or not isinstance(payload["review_status"], str)
                            or payload["review_status"] not in {"proposed", "confirmed"}
                            or not isinstance(payload["state"], str)
                            or not isinstance(relationships, list) or len(relationships) > 8
                            or any(not isinstance(rel, Mapping) or set(rel) != {"kind", "label"}
                                   or rel["kind"] != "commitment" or not isinstance(rel["label"], str)
                                   or len(rel["label"]) > 200 for rel in relationships)):
                        raise ValueError("Daythread commitment payload refused")
                payload["modified_at"] = rfc3339(row["modified_at"])
                payload["version_ref"] = str(row["version_ref"])[:300]
                records.append({"object_id": row["object_id"], "event_type": row["event_type"],
                                "occurred_at": row["source_timestamp"], "source_ref": row["source_ref"],
                                "payload": payload})
                if len(records) > MAX_OBJECTS:
                    raise ValueError("Daythread export record bound exceeded")
            next_cursor = page.get("next_cursor")
            if next_cursor is None:
                complete_window = (coverage["window_complete"] is True
                                   and coverage["snapshot_consistent"] is True
                                   and coverage["record_limit_reached"] is False)
                limitation = "Daythread snapshot has no deletion feed; absence never means deletion."
                break
            if not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 2048:
                raise ValueError("Daythread export cursor refused")
            if page_number + 1 == self.pages:
                limitation = "Daythread page bound reached; retry from the first page."
                break
            cursor = next_cursor
        return daythread_snapshot(records, observed_at=snapshot_at or datetime.now(timezone.utc).isoformat(),
            coverage_key=coverage_key or "unknown-window", complete=False,
            limitation=limitation + (" Window is incomplete." if not complete_window else ""))


def _stamp_from(*values: Any, fallback: str) -> str:
    for value in values:
        if isinstance(value, str):
            try:
                return rfc3339(value)
            except ValueError:
                continue
    return rfc3339(fallback)


def _safe_id(value: Any, fallback: str, maximum: int = 180) -> str:
    text = str(value) if value is not None else ""
    text = re.sub(r"[^A-Za-z0-9_.:@/-]", "_", text)
    if len(text) > maximum:
        suffix = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        text = text[:maximum - 17] + "_" + suffix
    return text or fallback


def _aula_event(row: Mapping[str, Any], family: str, observed: str) -> SourceObject:
    if family == "thread":
        rid = row.get("messageId", row.get("id"))
    else:
        rid = row.get("id", row.get("eventId", row.get("postId", row.get("threadId"))))
    if rid is None and family == "letter":
        child = str(row.get("childName", ""))
        if not child or not isinstance(row.get("week_number"), (str, int)) or type(row.get("chunk_index")) is not int:
            raise ValueError("Aula weekly letter chunk missing stable identity")
        child_key = hashlib.sha256(child.encode("utf-8")).hexdigest()[:12]
        rid = f"{child_key}:{row.get('iso_week', row.get('week_number', 'week'))}:{row.get('chunk_index', 0)}"
    if rid is None:
        raise ValueError("Aula record is missing its stable source ID")
    object_id = f"{family}:{_safe_id(rid, 'missing')}"
    allowed = {"id", "eventId", "postId", "threadId", "messageId", "title", "name", "text", "content", "body",
               "summary", "subject", "start", "end", "startDateTime", "endDateTime", "startDate", "endDate",
               "date", "createdAt", "updatedAt", "publishedAt", "sendDateTime", "validFrom", "validTo", "senderName", "type",
               "status", "isImportant", "unread", "eventType", "allDay", "isAllDay", "location", "url", "link",
               "week_number", "iso_week", "chunk_index", "chunk_count"}
    payload = {key: row[key] for key in allowed if key in row}
    return SourceObject(object_id, f"{family}.snapshot", _stamp_from(row.get("updatedAt"), row.get("createdAt"),
                      row.get("publishedAt"), row.get("sendDateTime"), row.get("startDateTime"), row.get("date"), fallback=observed),
                      f"aula:{family}:{_safe_id(rid, object_id)}", payload)


def aula_snapshot(rows: Iterable[Mapping[str, Any]], *, observed_at: str,
                  coverage_key: str, complete: bool, family: str = "calendar",
                  cursor: str | None = None, limitation: str = "") -> Snapshot:
    if family not in {"calendar", "post", "thread", "letter"}:
        raise ValueError("Unsupported Aula record family")
    observed = rfc3339(observed_at)
    objects = tuple(_aula_event(row, family, observed) for row in rows if isinstance(row, Mapping))
    return Snapshot("aula", coverage_key, objects, complete, observed, cursor, limitation)


def aula_calendar_coverage(range_name: str, profile_ids: Iterable[int], as_of: date) -> str:
    if range_name not in {"today", "tomorrow", "this_week", "next_week"}:
        raise ValueError("Unsupported Aula calendar range")
    ids = tuple(profile_ids)
    if not ids or len(ids) > 8 or any(type(value) is not int or value < 1 for value in ids):
        raise ValueError("Explicit bounded Aula profiles required")
    return f"calendar:{range_name}:asof:{as_of.isoformat()}:profiles:" + ",".join(map(str, ids))


def _calendar_object(row: Mapping[str, Any], observed: str) -> SourceObject:
    event_id = row.get("event_id")
    start = row.get("start")
    try:
        when = rfc3339(start)
    except ValueError:
        when = observed
    alias = _safe_id(row.get("calendar"), "unknown")
    ident = _safe_id(event_id, "missing")
    if ident == "missing":
        raise ValueError("Calendar event is missing its stable provider ID")
    payload = {key: row[key] for key in ("calendar", "event_id", "etag", "summary", "start", "end",
                 "all_day", "timezone", "description", "location", "writable", "source_managed") if key in row}
    return SourceObject(f"{alias}:{ident}", "calendar.event", when,
                        f"google-calendar:{alias}:{ident}", payload)


def calendar_snapshot(rows: Iterable[Mapping[str, Any]], *, observed_at: str,
                      coverage_key: str, complete: bool,
                      cursor: str | None = None, limitation: str = "") -> Snapshot:
    observed = rfc3339(observed_at)
    objects = tuple(_calendar_object(row, observed) for row in rows if isinstance(row, Mapping))
    return Snapshot("calendar", coverage_key, objects, complete, observed, cursor, limitation)


def ha_snapshot(projection: Mapping[str, Any], *, observed_at: str | None = None) -> Snapshot:
    """Project the existing validated HA metadata reader as selected snapshot records."""
    if not isinstance(projection, Mapping) or projection.get("host") != "home-assistant":
        raise ValueError("Validated Home Assistant metadata projection required")
    generated = rfc3339(projection.get("generated_at"))
    observed = rfc3339(observed_at or generated)
    objects: list[SourceObject] = []
    base = {key: projection[key] for key in ("installed_version", "update_available_count", "unavailable_count")
            if key in projection}
    objects.append(SourceObject("metadata", "ha.metadata", generated, "home-assistant:metadata", base))
    checks = projection.get("checks", [])
    if isinstance(checks, list):
        for row in checks:
            if not isinstance(row, Mapping) or not isinstance(row.get("check_id"), str):
                continue
            objects.append(SourceObject("check:" + _safe_id(row["check_id"], "unknown"), "ha.check",
                _stamp_from(row.get("observed_at"), fallback=generated), "home-assistant:" + row["check_id"],
                {key: row[key] for key in ("status", "evidence", "observed_at") if key in row}))
    energy = projection.get("energy")
    if isinstance(energy, Mapping):
        entities = energy.get("entities", {})
        if isinstance(entities, Mapping):
            for alias, row in entities.items():
                if isinstance(row, Mapping):
                    objects.append(SourceObject("energy:" + _safe_id(alias, "unknown"), "ha.energy",
                        _stamp_from(row.get("sampled_at"), row.get("reported_at"), fallback=generated),
                        "home-assistant:energy:" + str(alias), dict(row)))
        for name in ("plan", "solar", "tomorrow_prices"):
            value = energy.get(name)
            if isinstance(value, (dict, str)):
                objects.append(SourceObject("energy:" + name, "ha.energy." + name, generated,
                    "home-assistant:energy:" + name, value if isinstance(value, dict) else {"status": value}))
        slots = energy.get("price_slots")
        if isinstance(slots, list):
            objects.append(SourceObject("energy:price_slots", "ha.energy.price_slots", generated,
                "home-assistant:energy:price_slots", {"slots": slots[:20], "truncated": len(slots) > 20}))
    return Snapshot("home-assistant", "metadata-v1", tuple(objects), False, observed,
        limitation="HA metadata reader is a point-in-time projection, not an event feed; absence never implies deletion.")


def daythread_snapshot(records: Iterable[Mapping[str, Any]], *, observed_at: str,
                       coverage_key: str, complete: bool, cursor: str | None = None,
                       limitation: str = "") -> Snapshot:
    """Normalize Daythread export records after an authenticated source adapter reads them.

    The separately authenticated export includes only saved summaries and
    explicit commitments. Daythread snapshots can never be deletion-complete.
    """
    if complete:
        raise ValueError("Daythread has no authoritative delete feed; snapshots stay partial")
    observed = rfc3339(observed_at)
    objects = []
    for row in records:
        if not isinstance(row, Mapping):
            continue
        source_id = row.get("object_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("Daythread record missing stable source ID")
        event_type = row.get("event_type")
        source_ref = row.get("source_ref")
        occurred = rfc3339(row.get("occurred_at"))
        if event_type not in {"daythread.summary", "daythread.commitment"}:
            raise ValueError("Daythread event type refused")
        if not isinstance(source_ref, str) or not source_ref.startswith("daythread:"):
            raise ValueError("Daythread source reference refused")
        objects.append(SourceObject(_safe_id(source_id, "unknown"), event_type, occurred,
                                    source_ref[:MAX_SOURCE_REF], dict(row.get("payload", {}))))
    return Snapshot("daythread", coverage_key, tuple(objects), complete, observed, cursor,
                    limitation or "Daythread API has no callback or delete feed; reconciliation is bounded to this scan.")


class RevisionLedger:
    """Restart-safe monotonically increasing revisions and complete-scan tombstones."""
    def __init__(self, path: str):
        database = os.path.abspath(path)
        parent = os.path.dirname(database)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        directory = os.stat(parent, follow_symlinks=False)
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) & 0o077):
            raise ValueError("Private assistant revision state required")
        try:
            info = os.stat(database, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError("Private assistant revision state required")
        except FileNotFoundError:
            pass
        self.db = sqlite3.connect(database, timeout=10)
        os.chmod(database, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS source_objects (
              source TEXT NOT NULL, coverage TEXT NOT NULL, object_id TEXT NOT NULL,
              revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, event_type TEXT NOT NULL,
              occurred_at TEXT NOT NULL, source_ref TEXT NOT NULL, current INTEGER NOT NULL,
              PRIMARY KEY(source, coverage, object_id));
            CREATE TABLE IF NOT EXISTS source_cursors (
              source TEXT NOT NULL, coverage TEXT NOT NULL, cursor TEXT NOT NULL, observed_at TEXT NOT NULL,
              PRIMARY KEY(source, coverage));
            CREATE TABLE IF NOT EXISTS source_outbox (
              outbox_id INTEGER PRIMARY KEY AUTOINCREMENT, event_json TEXT NOT NULL);
        """)

    @staticmethod
    def _fingerprint(obj: SourceObject) -> str:
        raw = json.dumps({"event_type": obj.event_type, "source_ref": obj.source_ref,
                          "payload": _bounded(dict(obj.payload))}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def drain(self, ingest: Callable[[dict[str, Any]], Any]) -> None:
        """Replay durable envelopes before admitting newer source revisions."""
        while True:
            row = self.db.execute("SELECT outbox_id,event_json FROM source_outbox ORDER BY outbox_id LIMIT 1").fetchone()
            if row is None:
                return
            event = json.loads(row[1])
            ingest(event)
            with self.db:
                self.db.execute("DELETE FROM source_outbox WHERE outbox_id=?", (row[0],))

    def apply(self, snapshot: Snapshot, ingest: Callable[[dict[str, Any]], Any]) -> dict[str, int]:
        # If the last caller lost an HTTP receipt after server acceptance, replay
        # that exact envelope before creating any newer local revision.
        self.drain(ingest)
        seen: set[str] = set()
        counts = {"upserted": 0, "unchanged": 0, "deleted": 0}
        with self.db:
            for obj in snapshot.objects:
                if obj.object_id in seen:
                    raise ValueError("Duplicate source object in snapshot")
                seen.add(obj.object_id)
                fingerprint = self._fingerprint(obj)
                prior = self.db.execute("SELECT revision,fingerprint,current FROM source_objects "
                    "WHERE source=? AND coverage=? AND object_id=?",
                    (snapshot.source, snapshot.coverage_key, obj.object_id)).fetchone()
                if prior and prior[1] == fingerprint and prior[2] == 1:
                    counts["unchanged"] += 1
                    continue
                revision = (prior[0] + 1) if prior else 1
                event = normalize_event(snapshot.source, obj.object_id, revision, obj.event_type, "upsert",
                                        obj.occurred_at, obj.source_ref, obj.payload)
                self.db.execute("INSERT INTO source_outbox(event_json) VALUES(?)",
                    (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))
                self.db.execute("INSERT INTO source_objects VALUES(?,?,?,?,?,?,?,?,1) "
                    "ON CONFLICT(source,coverage,object_id) DO UPDATE SET revision=excluded.revision,"
                    "fingerprint=excluded.fingerprint,event_type=excluded.event_type,occurred_at=excluded.occurred_at,"
                    "source_ref=excluded.source_ref,current=1",
                    (snapshot.source, snapshot.coverage_key, obj.object_id, revision, fingerprint,
                     obj.event_type, event["occurred_at"], obj.source_ref))
                counts["upserted"] += 1
            if snapshot.complete:
                rows = self.db.execute("SELECT object_id,revision,event_type,source_ref,current FROM source_objects "
                    "WHERE source=? AND coverage=?", (snapshot.source, snapshot.coverage_key)).fetchall()
                for object_id, revision, event_type, source_ref, current in rows:
                    if current and object_id not in seen:
                        event = normalize_event(snapshot.source, object_id, revision + 1, event_type, "delete",
                            snapshot.observed_at, source_ref, {})
                        self.db.execute("INSERT INTO source_outbox(event_json) VALUES(?)",
                            (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))
                        self.db.execute("UPDATE source_objects SET revision=?,fingerprint='',occurred_at=?,current=0 "
                            "WHERE source=? AND coverage=? AND object_id=?",
                            (revision + 1, event["occurred_at"], snapshot.source, snapshot.coverage_key, object_id))
                        counts["deleted"] += 1
            if snapshot.cursor is not None:
                self.db.execute("INSERT INTO source_cursors VALUES(?,?,?,?) ON CONFLICT(source,coverage) "
                    "DO UPDATE SET cursor=excluded.cursor,observed_at=excluded.observed_at",
                    (snapshot.source, snapshot.coverage_key, snapshot.cursor, snapshot.observed_at))
        self.drain(ingest)
        return counts

    def close(self) -> None:
        self.db.close()
