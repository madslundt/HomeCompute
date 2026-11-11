from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("AI_AUTOMATION_DATA_ROOT", "/data"))
DATABASE = DATA_ROOT / "automations.sqlite3"
SESSION_COOKIE = "homecompute_ai_automation_session"
SESSION_TTL = 8 * 60 * 60
HA_WEBHOOK_ENV = Path(os.environ.get("AI_AUTOMATION_INBOX_ENV_FILE", "/run/secrets/inbox-webhook-env"))
HA_BASE_URL = "http://homeassistant.local"
TOKEN_FILE = Path(os.environ.get("AI_AUTOMATION_WORKFLOW_TOKEN_FILE", "/run/secrets/ai-automation-workflow-token"))
USERNAME_FILE = Path(os.environ.get("AI_AUTOMATION_USERNAME_FILE", "/run/secrets/ai-automation-username"))
PASSWORD_FILE = Path(os.environ.get("AI_AUTOMATION_PASSWORD_FILE", "/run/secrets/ai-automation-password"))
PUBLIC_ORIGIN = os.environ.get("AI_AUTOMATION_PUBLIC_ORIGIN", "").rstrip("/")
TRUSTED_PROXY_IP = os.environ.get("AI_AUTOMATION_TRUSTED_PROXY_IP", "")
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
SLOT_NAMES = {"Morning", "Evening"}
STATUS_NAMES = {"Draft", "Ready", "Resolved", "Archived"}
UPDATE_STYLES = {"briefing", "changes", "status_check"}
NOTIFY_RULES = {"every_run", "on_change", "action_needed"}
COMPLETION_RULES = {"success_criteria", "manual_archive"}
CADENCES = {"time_slot", "hourly"}

app = FastAPI(title="HomeCompute AI automation manager", docs_url=None, redoc_url=None)
sessions: dict[str, tuple[float, str, str]] = {}
session_lock = threading.Lock()
login_failures: dict[str, tuple[int, float, float]] = {}
login_lock = threading.Lock()
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW = 300
LOGIN_BLOCK = 300
LOGIN_MAX_CLIENTS = 1024


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class AutomationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=160)
    task_description: str = Field(min_length=1, max_length=12000)
    current_context: str = Field(default="", max_length=24000)
    success_criteria: str = Field(default="", max_length=6000)
    status: Literal["Draft", "Ready", "Resolved", "Archived"]
    cadence: Literal["time_slot", "hourly"]
    days_to_run: list[str] = Field(min_length=1, max_length=7)
    time_to_run: Literal["Morning", "Evening"] | None = None
    update_style: Literal["briefing", "changes", "status_check"]
    notify_when: Literal["every_run", "on_change", "action_needed"]
    completion_rule: Literal["success_criteria", "manual_archive"]
    project: str = Field(default="", max_length=120)
    tag: str = Field(default="", max_length=120)
    recipients: list[str] = Field(min_length=1, max_length=10)

    @field_validator("name", "task_description", "current_context", "success_criteria", "project", "tag")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("days_to_run")
    @classmethod
    def valid_days(cls, value: list[str]) -> list[str]:
        if not value or any(day not in WEEKDAYS for day in value):
            raise ValueError("Select one or more valid weekdays.")
        return list(dict.fromkeys(value))

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, value: list[str]) -> list[str]:
        clean = list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))
        if not clean or len(clean) > 10 or any(len(item) > 128 for item in clean):
            raise ValueError("Select one or more Home Assistant Inbox recipients.")
        return clean

    @model_validator(mode="after")
    def valid_schedule(self) -> "AutomationWrite":
        if self.cadence == "time_slot" and self.time_to_run not in SLOT_NAMES:
            raise ValueError("Choose Morning or Evening for this schedule.")
        if self.cadence == "hourly" and self.time_to_run is not None:
            raise ValueError("Hourly schedules do not use a Morning or Evening slot.")
        if self.completion_rule == "success_criteria" and not self.success_criteria:
            raise ValueError("Add success criteria or choose manual archive.")
        return self


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    local_date: str = Field(min_length=10, max_length=10)
    weekday: str
    slot: Literal["Morning", "Evening", "Hourly"]
    local_hour_key: str = Field(min_length=18, max_length=18)

    @field_validator("local_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @field_validator("local_hour_key")
    @classmethod
    def valid_local_hour_key(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3])[+-](?:0\d|1[0-4])[0-5]\d", value):
            raise ValueError("Use a Copenhagen local hour with its UTC offset.")
        datetime.strptime(value, "%Y-%m-%dT%H%z")
        return value

    @field_validator("weekday")
    @classmethod
    def valid_weekday(cls, value: str) -> str:
        if value not in WEEKDAYS:
            raise ValueError("Invalid weekday.")
        return value

    @model_validator(mode="after")
    def date_matches_local_hour(self) -> "ClaimRequest":
        if self.local_date != self.local_hour_key[:10]:
            raise ValueError("The local date must match the local hour key.")
        return self


class ResultWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    updated_context: str = Field(min_length=1, max_length=24000)
    change_summary: str = Field(min_length=1, max_length=2000)
    new_status: Literal["Ready", "Resolved"]
    changed: bool
    action_needed: bool


class DeliveryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["delivered", "skipped"]
    receipt_id: str | None = Field(default=None, max_length=128)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def connection() -> Any:
    DATA_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE, timeout=10, isolation_level=None)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    database.execute("PRAGMA busy_timeout = 10000")
    try:
        yield database
    finally:
        database.close()


def initialize() -> None:
    with connection() as database:
        database.execute("PRAGMA journal_mode = WAL")
        database.execute("PRAGMA synchronous = FULL")
        database.executescript("""
            CREATE TABLE IF NOT EXISTS automations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                task_description TEXT NOT NULL,
                current_context TEXT NOT NULL DEFAULT '',
                success_criteria TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('Draft','Ready','Resolved','Archived')),
                cadence TEXT NOT NULL CHECK (cadence IN ('time_slot','hourly')),
                days_to_run TEXT NOT NULL,
                time_to_run TEXT,
                update_style TEXT NOT NULL CHECK (update_style IN ('briefing','changes','status_check')),
                notify_when TEXT NOT NULL CHECK (notify_when IN ('every_run','on_change','action_needed')),
                completion_rule TEXT NOT NULL CHECK (completion_rule IN ('success_criteria','manual_archive')),
                project TEXT NOT NULL DEFAULT '',
                tag TEXT NOT NULL DEFAULT '',
                recipients TEXT NOT NULL,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS automation_due ON automations(status, updated_at);
            CREATE TABLE IF NOT EXISTS automation_runs (
                id TEXT PRIMARY KEY,
                automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
                schedule_key TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                state TEXT NOT NULL CHECK (state IN ('running','delivery_pending','delivered','skipped','failed')),
                updated_context TEXT,
                change_summary TEXT,
                recipients TEXT NOT NULL,
                changed INTEGER NOT NULL DEFAULT 0,
                action_needed INTEGER NOT NULL DEFAULT 0,
                delivery_receipt TEXT,
                error_code TEXT,
                UNIQUE (automation_id, schedule_key)
            );
            CREATE INDEX IF NOT EXISTS run_delivery ON automation_runs(state, started_at);
            CREATE INDEX IF NOT EXISTS run_history ON automation_runs(automation_id, started_at DESC);
        """)
        run_columns = {row[1] for row in database.execute("PRAGMA table_info(automation_runs)").fetchall()}
        if "changed" not in run_columns:
            database.execute("ALTER TABLE automation_runs ADD COLUMN changed INTEGER NOT NULL DEFAULT 0")
        if "action_needed" not in run_columns:
            database.execute("ALTER TABLE automation_runs ADD COLUMN action_needed INTEGER NOT NULL DEFAULT 0")


def row_item(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["days_to_run"] = json.loads(value["days_to_run"])
    value["recipients"] = json.loads(value["recipients"])
    return value


def row_run(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["recipients"] = json.loads(value["recipients"])
    value.pop("updated_context", None)
    return value


def site_credentials() -> tuple[str, str]:
    try:
        return USERNAME_FILE.read_text(encoding="utf-8").strip(), PASSWORD_FILE.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Site sign-in is not configured.") from exc


def client_address(request: Request) -> str:
    peer = request.client.host if request.client and request.client.host else "unknown"
    if peer != TRUSTED_PROXY_IP or not TRUSTED_PROXY_IP:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return peer


def lock_remaining(address: str) -> int:
    now = time.time()
    with login_lock:
        state = login_failures.get(address)
        if not state:
            return 0
        attempts, window_start, blocked_until = state
        if blocked_until > now:
            return max(1, int(blocked_until - now))
        if window_start + LOGIN_WINDOW <= now:
            login_failures.pop(address, None)
    return 0


def failed_login(address: str) -> None:
    now = time.time()
    with login_lock:
        attempts, window_start, _ = login_failures.get(address, (0, now, 0))
        if window_start + LOGIN_WINDOW <= now:
            attempts, window_start = 0, now
        attempts += 1
        login_failures[address] = (attempts, window_start, now + LOGIN_BLOCK if attempts >= LOGIN_ATTEMPTS else 0)
        if len(login_failures) > LOGIN_MAX_CLIENTS:
            oldest = min(login_failures, key=lambda key: login_failures[key][1])
            login_failures.pop(oldest, None)


def clear_failures(address: str) -> None:
    with login_lock:
        login_failures.pop(address, None)


def require_site_session(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE, "")
    now = time.time()
    with session_lock:
        entry = sessions.get(token)
        if entry and entry[0] > now:
            return entry[1]
        if token:
            sessions.pop(token, None)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")


def require_same_origin(request: Request) -> None:
    if not PUBLIC_ORIGIN or request.headers.get("origin", "").rstrip("/") != PUBLIC_ORIGIN:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")
    session = request.cookies.get(SESSION_COOKIE, "")
    with session_lock:
        entry = sessions.get(session)
    csrf = request.headers.get("x-csrf-token", "")
    if not entry or not csrf or not hmac.compare_digest(csrf, entry[2]):
        raise HTTPException(status_code=403, detail="Request verification failed.")


def require_workflow_token(request: Request) -> None:
    expected = ""
    try:
        expected = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    supplied = request.headers.get("authorization", "")
    if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
        raise HTTPException(status_code=401, detail="Workflow authentication failed.")


def parse_env_file(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Home Assistant Inbox access is not configured.") from exc
    matches = [line.partition("=")[2].strip() for line in lines if line.startswith(key + "=")]
    if len(matches) != 1 or not matches[0] or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", matches[0]):
        raise HTTPException(status_code=503, detail="Home Assistant Inbox access is not configured.")
    return matches[0]


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def inbox_request(payload: dict[str, Any]) -> dict[str, Any]:
    webhook_id = parse_env_file(HA_WEBHOOK_ENV, "INBOX_WEBHOOK_ID")
    request = urllib.request.Request(
        f"{HA_BASE_URL}/api/webhook/{webhook_id}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise HTTPException(status_code=503, detail="Home Assistant Inbox is unavailable.")
            body = response.read(65537)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Home Assistant Inbox is unavailable.") from exc
    if len(body) > 65536:
        raise HTTPException(status_code=503, detail="Home Assistant Inbox returned an invalid response.")
    try:
        result = json.loads(body)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="Home Assistant Inbox returned an invalid response.") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=503, detail="Home Assistant Inbox returned an invalid response.")
    return result


def recipient_directory() -> list[dict[str, str]]:
    result = inbox_request({"action": "recipients"})
    users = result.get("users")
    if not isinstance(users, list):
        raise HTTPException(status_code=503, detail="Home Assistant recipient list is unavailable.")
    clean: dict[str, dict[str, str]] = {}
    for user in users:
        if not isinstance(user, dict) or set(user) != {"id", "name"}:
            continue
        user_id = user["id"]
        name = user["name"]
        if (isinstance(user_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id)
                and isinstance(name, str) and name == name.strip() and 1 <= len(name) <= 100):
            clean[user_id] = {"id": user_id, "name": name}
    return list(clean.values())


def public_item(item: dict[str, Any], recipients: list[dict[str, str]]) -> dict[str, Any]:
    names = {user["id"]: user["name"] for user in recipients}
    return {**item, "recipient_names": [names[item_id] for item_id in item["recipients"] if item_id in names]}


def load_item(database: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    row = database.execute("SELECT * FROM automations WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found.")
    return row_item(row)


def validate_recipient_ids(ids: list[str]) -> None:
    available = {user["id"] for user in recipient_directory()}
    if not set(ids) <= available:
        raise HTTPException(status_code=422, detail="One or more selected Home Assistant recipients are no longer available.")


def schedule_key(payload: ClaimRequest, automation: dict[str, Any]) -> str:
    if payload.slot == "Hourly":
        if automation["cadence"] != "hourly":
            return ""
        return f"hourly:{payload.local_hour_key}"
    if automation["cadence"] != "time_slot" or automation["time_to_run"] != payload.slot:
        return ""
    return f"slot:{payload.local_date}:{payload.slot}"


@app.on_event("startup")
def on_startup() -> None:
    initialize()


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        with connection() as database:
            database.execute("SELECT 1").fetchone()
        return {"ok": True}
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="Automation storage is unavailable.") from None


@app.post("/api/session")
def sign_in(body: SignIn, request: Request, response: Response) -> dict[str, Any]:
    address = client_address(request)
    remaining = lock_remaining(address)
    if remaining:
        raise HTTPException(status_code=429, detail="Sign in is temporarily locked.", headers={"Retry-After": str(remaining)})
    try:
        expected_user, expected_password = site_credentials()
    except HTTPException:
        raise
    if (not expected_user or not expected_password or not hmac.compare_digest(body.username, expected_user)
            or not hmac.compare_digest(body.password, expected_password)):
        failed_login(address)
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    clear_failures(address)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    with session_lock:
        sessions[token] = (time.time() + SESSION_TTL, expected_user, csrf)
        if len(sessions) > 2048:
            expired = [key for key, value in sessions.items() if value[0] <= time.time()]
            for key in expired:
                sessions.pop(key, None)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=True,
                        samesite="strict", path="/")
    return {"authenticated": True, "csrf_token": csrf, "username": expected_user}


@app.get("/api/session")
def session_state(request: Request, username: str = Depends(require_site_session)) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE, "")
    with session_lock:
        entry = sessions.get(token)
    if not entry:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return {"authenticated": True, "username": username, "csrf_token": entry[2]}


@app.delete("/api/session")
def sign_out(request: Request, response: Response) -> dict[str, Any]:
    require_site_session(request)
    require_same_origin(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    with session_lock:
        sessions.pop(token, None)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"authenticated": False}


@app.get("/api/recipients")
def get_recipients(_: str = Depends(require_site_session)) -> dict[str, Any]:
    return {"recipients": recipient_directory()}


@app.get("/api/automations")
def list_automations(_: str = Depends(require_site_session)) -> dict[str, Any]:
    try:
        recipients = recipient_directory()
    except HTTPException:
        # Keep saved work visible when Home Assistant is temporarily offline.
        recipients = []
    with connection() as database:
        rows = database.execute("SELECT * FROM automations ORDER BY updated_at DESC, name COLLATE NOCASE").fetchall()
    return {"automations": [public_item(row_item(row), recipients) for row in rows]}


def save_automation(body: AutomationWrite, username: str, item_id: str | None = None) -> dict[str, Any]:
    validate_recipient_ids(body.recipients)
    now = utc_now()
    payload = body.model_dump()
    payload["days_to_run"] = json.dumps(payload["days_to_run"], separators=(",", ":"))
    payload["recipients"] = json.dumps(payload["recipients"], separators=(",", ":"))
    with connection() as database:
        if item_id is None:
            item_id = str(uuid.uuid4())
            database.execute("""
                INSERT INTO automations (
                    id,name,task_description,current_context,success_criteria,status,cadence,days_to_run,time_to_run,
                    update_style,notify_when,completion_rule,project,tag,recipients,last_run_at,created_at,created_by,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)
            """, (item_id, payload["name"], payload["task_description"], payload["current_context"],
                  payload["success_criteria"], payload["status"], payload["cadence"], payload["days_to_run"],
                  payload["time_to_run"], payload["update_style"], payload["notify_when"], payload["completion_rule"],
                  payload["project"], payload["tag"], payload["recipients"], now, username, now))
        else:
            database.execute("""
                UPDATE automations SET name=?,task_description=?,current_context=?,success_criteria=?,status=?,cadence=?,
                    days_to_run=?,time_to_run=?,update_style=?,notify_when=?,completion_rule=?,project=?,tag=?,recipients=?,updated_at=?
                WHERE id=?
            """, (payload["name"], payload["task_description"], payload["current_context"], payload["success_criteria"],
                  payload["status"], payload["cadence"], payload["days_to_run"], payload["time_to_run"],
                  payload["update_style"], payload["notify_when"], payload["completion_rule"], payload["project"],
                  payload["tag"], payload["recipients"], now, item_id))
            if database.execute("SELECT changes()").fetchone()[0] == 0:
                raise HTTPException(status_code=404, detail="Automation not found.")
        row = database.execute("SELECT * FROM automations WHERE id = ?", (item_id,)).fetchone()
    return row_item(row)


@app.post("/api/automations")
def create_automation(body: AutomationWrite, request: Request, username: str = Depends(require_site_session)) -> dict[str, Any]:
    require_same_origin(request)
    return {"automation": public_item(save_automation(body, username), recipient_directory())}


@app.put("/api/automations/{item_id}")
def update_automation(item_id: str, body: AutomationWrite, request: Request, username: str = Depends(require_site_session)) -> dict[str, Any]:
    require_same_origin(request)
    try:
        uuid.UUID(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Automation not found.") from exc
    return {"automation": public_item(save_automation(body, username, item_id), recipient_directory())}


@app.delete("/api/automations/{item_id}")
def delete_automation(item_id: str, request: Request, _: str = Depends(require_site_session)) -> dict[str, bool]:
    require_same_origin(request)
    try:
        uuid.UUID(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Automation not found.") from exc
    with connection() as database:
        database.execute("DELETE FROM automations WHERE id = ?", (item_id,))
        if database.execute("SELECT changes()").fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="Automation not found.")
    return {"deleted": True}


@app.get("/api/automations/{item_id}/runs")
def list_runs(item_id: str, _: str = Depends(require_site_session)) -> dict[str, Any]:
    with connection() as database:
        load_item(database, item_id)
        rows = database.execute("""
            SELECT id,automation_id,schedule_key,scheduled_at,started_at,finished_at,state,
                   change_summary,recipients,delivery_receipt,error_code
            FROM automation_runs WHERE automation_id = ? ORDER BY started_at DESC LIMIT 50
        """, (item_id,)).fetchall()
    return {"runs": [row_run(row) for row in rows]}


@app.post("/api/workflows/claim", dependencies=[Depends(require_workflow_token)])
def claim_due(body: ClaimRequest) -> dict[str, Any]:
    due: list[dict[str, Any]] = []
    now = utc_now()
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        rows = database.execute("SELECT * FROM automations WHERE status = 'Ready' ORDER BY last_run_at IS NOT NULL, last_run_at, created_at LIMIT 200").fetchall()
        for row in rows:
            item = row_item(row)
            if body.weekday not in item["days_to_run"]:
                continue
            key = schedule_key(body, item)
            if not key:
                continue
            run_id = str(uuid.uuid4())
            try:
                database.execute("""
                    INSERT INTO automation_runs (id,automation_id,schedule_key,scheduled_at,started_at,state,recipients)
                    VALUES (?,?,?,?,?,'running',?)
                """, (run_id, item["id"], key, body.local_date if body.slot != "Hourly" else body.local_hour_key,
                      now, json.dumps(item["recipients"], separators=(",", ":"))))
            except sqlite3.IntegrityError:
                continue
            due.append({"run_id": run_id, **item})
            if len(due) >= 10:
                break
        database.commit()
    return {"items": due}


@app.post("/api/workflows/{run_id}/result", dependencies=[Depends(require_workflow_token)])
def write_result(run_id: str, body: ResultWrite) -> dict[str, Any]:
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc
    updated_context = body.updated_context.strip()
    summary = body.change_summary.strip()
    if not updated_context or not summary:
        raise HTTPException(status_code=422, detail="Run result and summary are required.")
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("""
            SELECT r.*,a.success_criteria,a.completion_rule FROM automation_runs r
            JOIN automations a ON a.id=r.automation_id WHERE r.id=?
        """, (run_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Run not found.")
        if row["state"] not in {"running", "delivery_pending"}:
            raise HTTPException(status_code=409, detail="Run result is already final.")
        status_value = "Resolved" if body.new_status == "Resolved" else "Ready"
        if status_value == "Resolved" and (not row["success_criteria"] or row["completion_rule"] != "success_criteria"):
            raise HTTPException(status_code=422, detail="This automation cannot resolve from this run.")
        database.execute("""
            UPDATE automations SET current_context=?,status=?,last_run_at=?,updated_at=? WHERE id=?
        """, (updated_context, status_value, utc_now(), utc_now(), row["automation_id"]))
        database.execute("""
            UPDATE automation_runs SET state='delivery_pending',finished_at=?,updated_context=?,change_summary=?,
                changed=?,action_needed=?,error_code=NULL WHERE id=?
        """, (utc_now(), updated_context, summary, int(body.changed), int(body.action_needed), run_id))
        item = load_item(database, row["automation_id"])
        database.commit()
    should_notify = (item["notify_when"] == "every_run"
                     or (item["notify_when"] == "on_change" and body.changed)
                     or (item["notify_when"] == "action_needed" and body.action_needed))
    return {"run_id": run_id, "automation": item, "changed": body.changed,
            "action_needed": body.action_needed, "change_summary": summary, "should_notify": should_notify}


@app.get("/api/workflows/pending-deliveries", dependencies=[Depends(require_workflow_token)])
def pending_deliveries() -> dict[str, Any]:
    with connection() as database:
        rows = database.execute("""
            SELECT r.id,r.automation_id,r.scheduled_at,r.updated_context,r.change_summary,r.recipients,
                   r.changed,r.action_needed,a.name,a.notify_when,a.update_style
            FROM automation_runs r JOIN automations a ON a.id=r.automation_id
            WHERE r.state='delivery_pending' ORDER BY r.started_at LIMIT 50
        """).fetchall()
    return {"runs": [dict(row, recipients=json.loads(row["recipients"]),
                           should_notify=(row["notify_when"] == "every_run"
                               or (row["notify_when"] == "on_change" and bool(row["changed"]))
                               or (row["notify_when"] == "action_needed" and bool(row["action_needed"]))))
            for row in rows]}


@app.post("/api/workflows/{run_id}/delivery", dependencies=[Depends(require_workflow_token)])
def record_delivery(run_id: str, body: DeliveryWrite) -> dict[str, Any]:
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc
    state = "delivered" if body.state == "delivered" else "skipped"
    if body.state == "delivered" and not body.receipt_id:
        raise HTTPException(status_code=422, detail="Home Assistant receipt is required.")
    with connection() as database:
        database.execute("""
            UPDATE automation_runs SET state=?,delivery_receipt=?,error_code=NULL
            WHERE id=? AND state='delivery_pending'
        """, (state, body.receipt_id or None, run_id))
        if database.execute("SELECT changes()").fetchone()[0] == 0:
            row = database.execute("SELECT state FROM automation_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Run not found.")
            if row["state"] not in {"delivered", "skipped"}:
                raise HTTPException(status_code=409, detail="Run is not awaiting delivery.")
    return {"run_id": run_id, "state": state}


@app.post("/api/workflows/{run_id}/failure", dependencies=[Depends(require_workflow_token)])
def record_failure(run_id: str, body: dict[str, str]) -> dict[str, Any]:
    code = body.get("error_code", "workflow_failed")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        code = "workflow_failed"
    with connection() as database:
        database.execute("""
            UPDATE automation_runs SET state='failed',finished_at=?,error_code=? WHERE id=? AND state='running'
        """, (utc_now(), code, run_id))
        if database.execute("SELECT changes()").fetchone()[0] == 0:
            row = database.execute("SELECT state FROM automation_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Run not found.")
    return {"run_id": run_id, "state": "failed"}


if (APP_ROOT / "dist").is_dir():
    app.mount("/", StaticFiles(directory=APP_ROOT / "dist", html=True), name="site")
