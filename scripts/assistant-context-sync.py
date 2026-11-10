#!/usr/bin/env python3
"""Run bounded HomeCompute source snapshots and submit them to the local context API.

Configuration and credentials are private operator-owned files. API endpoint and
provider bridge locations are fixed in source.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as local_time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from assistant_context_sources import (RevisionLedger, Snapshot, DaythreadClient, aula_snapshot,
    aula_calendar_coverage, calendar_snapshot, ha_snapshot, rfc3339)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import homeassistant_observations
import openclaw_calendar
import openclaw_services

CONFIG_PATH = Path("/var/lib/homecompute-assistant/context-sync.json")
STATE_PATH = Path("/var/lib/homecompute-assistant/source-revisions.sqlite3")
INGEST_TOKEN_PATH = Path("/var/lib/homecompute-assistant/context-ingest-token")
DAYTHREAD_TOKEN_PATH = Path("/var/lib/homecompute-assistant/daythread-export-token")
INGEST_BASE = "http://127.0.0.1:18826"
OPENCLAW_CONFIG = Path("/var/lib/homecompute-openclaw/config")
MAX_CONFIG_BYTES = 16_384
MAX_INGEST_RESPONSE = 4096
MAX_DAYTHREAD_RESPONSE = 128 * 1024


def private_bytes(path: Path, maximum: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > maximum):
            raise ValueError("Private assistant configuration refused")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Private assistant configuration refused")
        return data
    finally:
        os.close(fd)


def load_config(path: Path) -> dict:
    try:
        config = json.loads(private_bytes(path, MAX_CONFIG_BYTES))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Private assistant configuration unavailable") from None
    if (not isinstance(config, dict) or set(config) not in (
            {"schema_version", "aula", "calendar"}, {"schema_version", "aula", "calendar", "daythread"})
            or type(config.get("schema_version")) is not int or config["schema_version"] != 1):
        raise ValueError("Assistant source configuration refused")
    config.setdefault("daythread", {"enabled": False, "host": ""})
    aula, calendar = config["aula"], config["calendar"]
    if not isinstance(aula, dict) or set(aula) - {"enabled", "profile_ids", "range", "child_ids", "institution_codes", "iso_weeks"}:
        raise ValueError("Aula configuration refused")
    if type(aula.get("enabled", False)) is not bool:
        raise ValueError("Aula configuration refused")
    profiles = aula.get("profile_ids", [])
    if (not isinstance(profiles, list) or len(profiles) > 8
            or any(type(value) is not int or not 1 <= value <= 999_999_999 for value in profiles)
            or len(set(profiles)) != len(profiles)):
        raise ValueError("Aula profile selection refused")
    if aula.get("range", "this_week") not in {"today", "tomorrow", "this_week", "next_week"}:
        raise ValueError("Aula range refused")
    child_ids, institutions, weeks = (aula.get("child_ids", []), aula.get("institution_codes", []),
                                      aula.get("iso_weeks", []))
    if (not isinstance(child_ids, list) or len(child_ids) > 8
            or any(type(value) is not int or not 1 <= value <= 999_999_999 for value in child_ids)
            or len(set(child_ids)) != len(child_ids)):
        raise ValueError("Aula child selection refused")
    if (not isinstance(institutions, list) or len(institutions) > 8
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value) for value in institutions)
            or len(set(institutions)) != len(institutions)):
        raise ValueError("Aula institution selection refused")
    if (not isinstance(weeks, list) or len(weeks) > 4
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-W[0-9]{2}", value) for value in weeks)
            or len(set(weeks)) != len(weeks)):
        raise ValueError("Aula weekly-letter selection refused")
    if weeks and (not child_ids or not institutions):
        raise ValueError("Aula weekly letters require child and institution selections")
    if not isinstance(calendar, dict) or set(calendar) - {"enabled", "aliases", "start", "end", "rolling"}:
        raise ValueError("Calendar configuration refused")
    if type(calendar.get("enabled", False)) is not bool:
        raise ValueError("Calendar configuration refused")
    aliases = calendar.get("aliases", [])
    if (not isinstance(aliases, list) or not 1 <= len(aliases) <= 5
            or len(set(aliases)) != len(aliases)
            or any(value not in openclaw_calendar.ALIASES for value in aliases)):
        if calendar.get("enabled", False) or aliases:
            raise ValueError("Calendar aliases refused")
    if calendar.get("enabled", False):
        fixed = "start" in calendar or "end" in calendar
        rolling = "rolling" in calendar
        if fixed == rolling:
            raise ValueError("Select exactly one calendar window mode")
        if fixed:
            try:
                openclaw_calendar.times(calendar["start"], calendar["end"], False)
            except (KeyError, ValueError):
                raise ValueError("Calendar interval refused") from None
        else:
            window = calendar["rolling"]
            if (not isinstance(window, dict) or set(window) != {"past_days", "future_days"}
                    or type(window.get("past_days")) is not int or type(window.get("future_days")) is not int
                    or not 0 <= window["past_days"] <= 31 or not 1 <= window["future_days"] <= 31
                    or not 1 <= window["past_days"] + window["future_days"] <= 30):
                raise ValueError("Rolling calendar window refused")
    daythread = config["daythread"]
    if (not isinstance(daythread, dict) or set(daythread) - {"enabled", "host"}
            or type(daythread.get("enabled", False)) is not bool):
        raise ValueError("Daythread configuration refused")
    host = daythread.get("host", "")
    if not isinstance(host, str) or (host and not re.fullmatch(
            r"(?:daythread\.home\.arpa|daythread\.[a-z0-9-]{1,40}\.ts\.net)", host)):
        raise ValueError("Daythread private host refused")
    if daythread.get("enabled") and not host:
        raise ValueError("Daythread export host required")
    return config


def _rows(value, keys: tuple[str, ...]) -> list[dict]:
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    if isinstance(value, dict):
        for key in keys:
            rows = value.get(key)
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
    raise ValueError("Source did not return a recognized bounded collection")


def read_ha() -> Snapshot:
    token = private_bytes(OPENCLAW_CONFIG / "ha-metadata-reader-token", 256).decode("ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("Home Assistant bridge credential refused")
    projection = homeassistant_observations.local_read(token)
    return ha_snapshot(projection)


def read_aula(config: dict) -> list[Snapshot]:
    selection = config["aula"]
    if not selection.get("enabled"):
        return []
    profiles = selection.get("profile_ids", [])
    if not profiles:
        raise ValueError("Aula needs explicit owner profile IDs")
    facade = openclaw_services.Services(OPENCLAW_CONFIG / "services.json", reuse_mcp_sessions=True)
    try:
        return _read_aula_with_facade(config, facade)
    finally:
        facade.close()


def _read_aula_with_facade(config: dict, facade) -> list[Snapshot]:
    selection = config["aula"]
    profiles = selection.get("profile_ids", [])
    discovery = facade.request("homecompute_school_discover", {}).get("data")
    children = discovery.get("children") if isinstance(discovery, dict) else None
    if (not isinstance(children, list) or not 1 <= len(children) <= 8
            or any(not isinstance(row, dict) or type(row.get("id")) is not int
                   or not 1 <= row["id"] <= 999_999_999 for row in children)):
        raise ValueError("Aula child roster is unavailable")
    available = {row["id"] for row in children}
    if len(available) != len(children) or not set(profiles) <= available:
        raise ValueError("Aula selection differs from the current authorized child roster")
    account_feeds = set(profiles) == available
    observed = datetime.now(timezone.utc).isoformat()
    snapshots = []
    offset = 0
    rows: list[dict] = []
    complete = False
    for _ in range(25):
        result = facade.request("homecompute_school_calendar", {
            "profile_ids": ",".join(str(value) for value in profiles),
            "range": selection.get("range", "this_week"), "offset": str(offset),
        })
        data = result.get("data")
        rows.extend(_rows(data, ("events", "items", "calendar", "results")))
        if len(rows) > 1000 or not isinstance(data, dict):
            raise ValueError("Aula calendar page exceeds source limits")
        next_offset = data.get("next_offset")
        if next_offset is None:
            complete = True
            break
        if not isinstance(next_offset, (str, int)) or str(next_offset) == str(offset):
            raise ValueError("Aula calendar pagination refused")
        try:
            offset = int(next_offset)
        except ValueError:
            raise ValueError("Aula calendar pagination refused") from None
        if not 0 <= offset <= 10_000:
            raise ValueError("Aula calendar pagination refused")
    # Relative Aula ranges move with the date. Pin a scan to its local run date
    # so today's complete page cannot tombstone records seen in a different window.
    local_day = datetime.now(ZoneInfo("Europe/Copenhagen")).date().isoformat()
    coverage = aula_calendar_coverage(selection.get("range", "this_week"), profiles,
                                      date.fromisoformat(local_day))
    snapshots.append(aula_snapshot(rows, observed_at=observed, coverage_key=coverage, complete=complete,
                         family="calendar", limitation="Aula bridge pages are fresh reads, not a stable provider snapshot."))

    # The current source bridge exposes only bounded first pages for posts and
    # threads; preserve partial coverage and never tombstone from these reads.
    post_rows = []
    messages = []
    if account_feeds:
        # Pinned Aula discovery explicitly requires omitting child profile IDs
        # for the guardian's institution/club post feed. Never broaden a subset
        # selection to these account-wide posts or message threads.
        posts = facade.request("homecompute_school_posts", {})
        post_rows = _rows(posts.get("data"), ("posts", "items", "results"))
        threads = facade.request("homecompute_school_threads", {})
        thread_rows = _rows(threads.get("data"), ("threads", "items", "results"))
        for row in thread_rows[:20]:
            thread_id = row.get("threadId", row.get("id"))
            if thread_id is None:
                continue
            detail = facade.request("homecompute_school_thread", {"thread_id": str(thread_id)})
            data = detail.get("data")
            if isinstance(data, dict):
                for message in data.get("messages", [])[:20] if isinstance(data.get("messages"), list) else []:
                    if isinstance(message, dict):
                        messages.append(dict(message, threadId=thread_id))
    skipped = "Account-wide feed not read because only a subset of the current child roster is selected."
    snapshots.append(aula_snapshot(post_rows,
        observed_at=observed, coverage_key="posts:first-page:profiles:" + ",".join(map(str, profiles)),
        complete=False, family="post", limitation=(
            "Existing Aula post bridge reads one bounded account page; source removals are not inferred."
            if account_feeds else skipped)))
    snapshots.append(aula_snapshot(messages, observed_at=observed,
        coverage_key="thread-messages:first-page", complete=False, family="thread",
        limitation="Existing Aula thread bridge exposes first-page messages only." if account_feeds else skipped))

    for week in selection.get("iso_weeks", []):
        offset = 0
        letters = []
        full = False
        for _ in range(25):
            result = facade.request("homecompute_school_weekly_letter", {
                "child_ids": ",".join(str(value) for value in selection["child_ids"]),
                "institution_codes": ",".join(selection["institution_codes"]),
                "iso_week": week, "offset": str(offset),
            })
            data = result.get("data")
            letters.extend(_rows(data, ("letters", "items")))
            if len(letters) > 1000 or not isinstance(data, dict):
                raise ValueError("Aula weekly letter page exceeds source limits")
            next_offset = data.get("next_offset")
            if next_offset is None:
                full = True
                break
            if not isinstance(next_offset, (str, int)) or str(next_offset) == str(offset):
                raise ValueError("Aula weekly letter pagination refused")
            offset = int(next_offset)
            if not 0 <= offset <= 10_000:
                raise ValueError("Aula weekly letter pagination refused")
        letters = [dict(row, iso_week=week) for row in letters]
        snapshots.append(aula_snapshot(letters, observed_at=observed,
            coverage_key="weekly-letter:" + week + ":children:" + ",".join(map(str, selection["child_ids"])),
            complete=full, family="letter", limitation="Weekly-letter chunks are paged source projections."))
    return snapshots


def read_calendar(config: dict, state: Path, *, now: datetime | None = None) -> list[Snapshot]:
    selection = config["calendar"]
    if not selection.get("enabled"):
        return []
    start, end = _calendar_bounds(selection, now=now)
    facade = openclaw_calendar.Calendar(OPENCLAW_CONFIG / "calendar.json", state.parent)
    observed = datetime.now(timezone.utc).isoformat()
    snapshots = []
    for alias in selection["aliases"]:
        result = facade.request("homecompute_calendar_events", {
            "calendar": alias, "start": start, "end": end,
        })
        rows = _rows(result, ("events",))
        complete = result.get("truncated") is False
        coverage = f"{alias}:{start}:{end}"
        snapshots.append(calendar_snapshot(rows, observed_at=observed, coverage_key=coverage,
            complete=complete, limitation="Google bridge returns one bounded page; truncated pages never imply deletion."))
    return snapshots


def _calendar_bounds(selection: dict, *, now: datetime | None = None) -> tuple[str, str]:
    """Resolve fixed or operator-bounded rolling windows in Copenhagen time."""
    if "rolling" not in selection:
        return selection["start"], selection["end"]
    zone = ZoneInfo("Europe/Copenhagen")
    local_date = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    window = selection["rolling"]
    if (not isinstance(window, dict) or type(window.get("past_days")) is not int
            or type(window.get("future_days")) is not int
            or not 0 <= window["past_days"] <= 30 or not 1 <= window["future_days"] <= 30
            or window["past_days"] + window["future_days"] > 30):
        raise ValueError("Rolling calendar window refused: at most 30 local days")
    start_date = local_date - timedelta(days=window["past_days"])
    end_date = local_date + timedelta(days=window["future_days"])
    start = datetime.combine(start_date, local_time.min, zone).isoformat(timespec="seconds")
    end = datetime.combine(end_date, local_time.min, zone).isoformat(timespec="seconds")
    openclaw_calendar.times(start, end, False)
    return start, end


class Ingest:
    def __init__(self, token: str):
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), openclaw_services.NoRedirect())

    def _post(self, path: str, body: dict):
        if path not in {"/v1/events", "/v1/coverage", "/v1/coverage/failure"}:
            raise ValueError("Unsupported context API operation")
        data = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        request = urllib.request.Request(INGEST_BASE + path, data=data, method="POST", headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        try:
            with self.opener.open(request, timeout=8) as response:
                payload = response.read(MAX_INGEST_RESPONSE + 1)
                if response.status not in {200, 201, 202} or len(payload) > MAX_INGEST_RESPONSE:
                    raise ValueError("Context ingest refused")
                result = json.loads(payload)
            if not isinstance(result, dict) or result.get("accepted") is not True:
                raise ValueError("Context ingest not acknowledged")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Never include URLs, response bodies, tokens or source content in errors.
            raise ValueError("Context ingest unavailable") from None

    def __call__(self, event: dict):
        self._post("/v1/events", event)

    def coverage(self, source: str, observed_at: str, complete: bool):
        self._post("/v1/coverage", {"source": source, "observed_at": rfc3339(observed_at),
                                    "complete": complete})

    def failure(self, source: str, observed_at: str):
        self._post("/v1/coverage/failure", {"source": source, "observed_at": rfc3339(observed_at)})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DaythreadHTTPS:
    """HTTPS transport to one operator-pinned private Daythread hostname."""
    def __init__(self, host: str, token: str):
        if not re.fullmatch(r"(?:daythread\.home\.arpa|daythread\.[a-z0-9-]{1,40}\.ts\.net)", host):
            raise ValueError("Daythread private host refused")
        self.base = "https://" + host
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, path: str, params: dict) -> dict:
        if path != "/v1/integrations/assistant-context" or set(params) - {"limit", "cursor"}:
            raise ValueError("Daythread export route refused")
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
        })
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ValueError("Daythread export unavailable")
                raw = response.read(MAX_DAYTHREAD_RESPONSE + 1)
                if len(raw) > MAX_DAYTHREAD_RESPONSE:
                    raise ValueError("Daythread export response too large")
            def unique_fields(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate provider field")
                    value[key] = item
                return value

            value = json.loads(raw, object_pairs_hook=unique_fields,
                               parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError("Daythread export refused")
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("Daythread export unavailable") from None

    def post(self, path: str, body: dict) -> dict:
        if path != "/v1/integrations/assistant-context/resolve" or set(body) != {"object_ids"}:
            raise ValueError("Daythread resolve route refused")
        data = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        request = urllib.request.Request(self.base + path, data=data, method="POST", headers={
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with self.opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ValueError("Daythread resolve unavailable")
                raw = response.read(MAX_DAYTHREAD_RESPONSE + 1)
                if len(raw) > MAX_DAYTHREAD_RESPONSE:
                    raise ValueError("Daythread resolve response too large")
            value = json.loads(raw, object_pairs_hook=lambda pairs: _unique_object(pairs),
                               parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError("Daythread resolve refused")
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("Daythread resolve unavailable") from None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate provider field")
        value[key] = item
    return value


def sync(config_path: Path = CONFIG_PATH, state_path: Path = STATE_PATH) -> dict:
    config = load_config(config_path)
    ingest_token = private_bytes(INGEST_TOKEN_PATH, 256).decode("ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", ingest_token):
        raise ValueError("Context ingest credential refused")
    ledger = RevisionLedger(str(state_path))
    snapshots_by_source: dict[str, list[Snapshot]] = {}
    failed: set[str] = set()

    def capture(source: str, reader):
        try:
            snapshots = reader()
            for snapshot in snapshots if isinstance(snapshots, list) else [snapshots]:
                if not isinstance(snapshot, Snapshot) or snapshot.source != source:
                    raise ValueError("Source snapshot refused")
                snapshots_by_source.setdefault(source, []).append(snapshot)
        except Exception:
            snapshots_by_source.pop(source, None)
            failed.add(source)

    # Keep provider reads independent: one outage must not discard the other
    # source projections already available in this cycle.
    capture("home-assistant", read_ha)
    capture("aula", lambda: read_aula(config))
    capture("calendar", lambda: read_calendar(config, state_path))
    daythread = config["daythread"]
    if daythread.get("enabled"):
        def read_daythread():
            daythread_token = private_bytes(DAYTHREAD_TOKEN_PATH, 512).decode("ascii").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{40,256}", daythread_token):
                raise ValueError("Daythread export credential refused")
            client = DaythreadClient(DaythreadHTTPS(daythread["host"], daythread_token))
            return client.read(ledger.known_ids("daythread"))
        capture("daythread", read_daythread)
    try:
        ingest = Ingest(ingest_token)
        result: dict = {}
        active_sources = set(snapshots_by_source) | failed
        for source in sorted(active_sources):
            counts = {"upserted": 0, "unchanged": 0, "deleted": 0}
            rows = snapshots_by_source.get(source, [])
            if source not in failed:
                try:
                    for snapshot in rows:
                        applied = ledger.apply(snapshot, ingest)
                        for key, value in applied.items():
                            counts[key] += value
                    latest = max(row.observed_at for row in rows)
                    ingest.coverage(source, latest, all(row.complete for row in rows))
                except Exception:
                    failed.add(source)
            if source in failed:
                try:
                    ingest.failure(source, datetime.now(timezone.utc).isoformat())
                except Exception:
                    pass
            result[source] = counts
        if failed:
            result["errors"] = [{"source": source, "failed": True} for source in sorted(failed)]
        return result
    finally:
        ledger.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args(argv)
    try:
        result = sync(args.config, args.state)
    except Exception:
        print("Assistant context sync failed; inspect private operator logs.", file=sys.stderr)
        return 1
    # Counts only. Never print source payloads, event IDs, references or credentials.
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
