#!/usr/bin/env python3
"""Authenticated Mobilerun Portal notification listener with bounded deduplication."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import urllib.request

MAX_SEEN = 4096


def next_backoff(delay: int) -> int:
    return min(max(delay, 1) * 2, 60)


def normalize(frame: dict, device_id: str) -> dict | None:
    """Map current raw events and legacy NOTIFICATION frames to a safe envelope."""
    if not isinstance(frame, dict):
        return None
    if frame.get("method") == "events/device":
        frame = frame.get("params", {})
    kind = frame.get("type")
    payload = frame.get("payload", {})
    if not isinstance(payload, dict):
        return None
    if kind == "NOTIFICATION":
        kind = "NOTIFICATION_REMOVED" if payload.get("removed") else "NOTIFICATION_POSTED"
    if kind not in {"NOTIFICATION_POSTED", "NOTIFICATION_REMOVED"}:
        return None
    package = payload.get("package") or payload.get("packageName") or payload.get("package_name")
    if not isinstance(package, str) or not package or len(package) > 255:
        return None
    timestamp = payload.get("post_time") or frame.get("timestamp") or payload.get("timestamp")
    if type(timestamp) is not int or not 0 <= timestamp <= 10**15:
        timestamp = int(time.time() * 1000)
    key = payload.get("key")
    if not isinstance(key, (str, int)):
        key = f"{package}:{payload.get('id', '')}:{payload.get('tag', '')}"
    title, body = payload.get("title"), payload.get("text")
    return {
        "event_type": kind,
        "device_id": device_id,
        "application_package": package,
        "sender": title[:512] if isinstance(title, str) else None,
        "notification_text": body[:4096] if isinstance(body, str) else None,
        "timestamp": timestamp,
        "notification_key": str(key)[:512],
        "metadata": {k: payload[k] for k in ("id", "tag", "is_ongoing", "post_time") if k in payload
                     and isinstance(payload[k], (str, int, float, bool, type(None)))},
    }


class Deduplicator:
    def __init__(self, database: Path | None = None):
        self.seen = OrderedDict()
        self.database = database
        self.db = sqlite3.connect(database, timeout=5) if database else None
        if self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS seen (digest TEXT PRIMARY KEY, at INTEGER NOT NULL)")
            self.db.commit()

    def accept(self, event: dict) -> bool:
        identity = "\0".join(str(event.get(k, "")) for k in
            ("event_type", "application_package", "notification_key"))
        if not event.get("notification_key"):
            identity += "\0" + str(event.get("timestamp", ""))
        identity += "\0" + hashlib.sha256(json.dumps(
            [event.get("sender"), event.get("notification_text")], ensure_ascii=False).encode()).hexdigest()
        digest = hashlib.sha256(identity.encode()).hexdigest()
        if digest in self.seen or self.db and self.db.execute("SELECT 1 FROM seen WHERE digest=?", (digest,)).fetchone():
            if digest in self.seen:
                self.seen.move_to_end(digest)
            return False
        self.seen[digest] = None
        if self.db:
            self.db.execute("INSERT OR IGNORE INTO seen(digest, at) VALUES (?, ?)", (digest, int(time.time())))
            self.db.execute("DELETE FROM seen WHERE at < ?", (int(time.time()) - 30 * 86400,))
            self.db.commit()
        if len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)
        return True

    def forget(self, event: dict) -> None:
        identity = "\0".join(str(event.get(k, "")) for k in
            ("event_type", "application_package", "notification_key"))
        if not event.get("notification_key"):
            identity += "\0" + str(event.get("timestamp", ""))
        identity += "\0" + hashlib.sha256(json.dumps(
            [event.get("sender"), event.get("notification_text")], ensure_ascii=False).encode()).hexdigest()
        digest = hashlib.sha256(identity.encode()).hexdigest()
        self.seen.pop(digest, None)
        if self.db:
            self.db.execute("DELETE FROM seen WHERE digest=?", (digest,))
            self.db.commit()


@dataclass
class State:
    connected: bool = False
    last_event_at: float | None = None
    last_error: str | None = None
    forwarded: int = 0


class Listener:
    def __init__(self, config: dict, portal_token: str, webhook_token: str, dedup_path: Path | None = None):
        self.config, self.portal_token, self.webhook_token = config, portal_token, webhook_token
        self.state, self.dedup = State(), Deduplicator(dedup_path)

    def process(self, frame: dict) -> bool:
        event = normalize(frame, self.config["device_id"])
        if event is None or event["application_package"] not in self.config["allowed_packages"]:
            return False
        if not self.webhook_token:
            raise OSError("notification webhook credential is not provisioned")
        if not self.dedup.accept(event):
            return False
        req = urllib.request.Request(self.config["webhook_url"], json.dumps(event).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.webhook_token})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status not in (200, 201, 202, 204):
                    raise OSError("webhook rejected notification")
        except Exception:
            self.dedup.forget(event)
            raise
        self.state.forwarded += 1
        self.state.last_event_at = time.time()
        self.state.last_error = None
        return True

    def status(self) -> dict:
        return {"connected": self.state.connected, "last_event_at": self.state.last_event_at,
                "last_error": self.state.last_error, "forwarded": self.state.forwarded}


def health_server(listener: Listener, port: int = 18795) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404); return
            body = json.dumps(listener.status()).encode()
            self.send_response(200 if listener.state.connected and not listener.state.last_error else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def run(config_path: Path, portal_token_path: Path, webhook_token_path: Path) -> None:
    import websockets.sync.client

    cfg = json.loads(config_path.read_text())
    required = {"device_id", "websocket_url", "allowed_packages", "webhook_url"}
    if (not isinstance(cfg, dict) or set(cfg) != required or not isinstance(cfg["allowed_packages"], list)
            or not cfg["allowed_packages"] or not cfg["websocket_url"].startswith("ws://")
            or not cfg["webhook_url"].startswith("http://")):
        raise ValueError("Android notification configuration refused")
    state = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/homecompute-android-gateway"))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    listener = Listener(cfg, "", "", state / "dedup.sqlite")
    server = health_server(listener)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    backoff = 1
    while True:
        try:
            listener.portal_token = portal_token_path.read_text().strip()
            listener.webhook_token = webhook_token_path.read_text().strip()
            if not listener.portal_token:
                raise OSError("Portal credential is not provisioned")
            url = cfg["websocket_url"].rstrip("/") + "/?eventFormat=rpc"
            with websockets.sync.client.connect(url, additional_headers={"Authorization": "Bearer " + token},
                    open_timeout=10, close_timeout=2, max_size=1_048_576) as ws:
                listener.state.connected = True
                listener.state.last_error = None
                backoff = 1
                while True:
                    try:
                        frame = json.loads(ws.recv(timeout=30))
                    except TimeoutError:
                        ws.send(json.dumps({"type": "PING", "timestamp": int(time.time() * 1000)}))
                        continue
                    try:
                        listener.process(frame)
                    except Exception:
                        # Do not log event bodies or error strings from remote endpoints.
                        listener.state.last_error = "notification forwarding failed"
        except Exception:
            listener.state.connected = False
            listener.state.last_error = "Portal disconnected or authentication failed"
            time.sleep(backoff)
            backoff = next_backoff(backoff)


def credential_paths(env: dict[str, str] | None = None) -> tuple[Path, Path]:
    values = os.environ if env is None else env
    directory = Path(values.get(
        "CREDENTIALS_DIRECTORY", "/run/credentials/homecompute-android-notifications.service"))
    return directory / "portal-token", directory / "n8n-webhook-token"


if __name__ == "__main__":
    portal_token, webhook_token = credential_paths()
    run(Path(os.environ["ANDROID_CONFIG"]), portal_token, webhook_token)
