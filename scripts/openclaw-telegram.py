#!/usr/bin/env python3
"""Private dedicated Telegram ingress. Replies stay in the n8n-owned outbox."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import warnings


class ClosedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        raise ValueError("transport redirect refused")


def private_open(path: Path, mode: str, create: bool = False) -> Any:
    flags = (os.O_RDONLY if mode == "r" else os.O_RDWR) | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    if mode == "a":
        flags |= os.O_APPEND
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        raise ValueError("private file cannot be opened safely") from None
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(descriptor)
        raise ValueError("file must be operator-owned, regular and private")
    handle = os.fdopen(descriptor, mode)
    if mode == "w":
        handle.truncate(0)
    return handle


def post(url: str, body: dict[str, Any], token: str | None = None, timeout: int = 30) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    # Ignore environment proxies; never forward a bot URL or bearer to another origin.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ClosedRedirect())
    with opener.open(request, timeout=timeout) as response:
        data = response.read(262145)
    if len(data) > 262144:
        raise ValueError("bounded transport response exceeded")
    return json.loads(data)


def load_config(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text())
    if (set(cfg) != {"bot_id", "chat_id", "user_id", "destination", "conversation"}
            or any(not isinstance(cfg[x], str) or not re.fullmatch(r"[1-9][0-9]{0,15}", cfg[x])
                   for x in ("bot_id", "chat_id", "user_id"))
            or not isinstance(cfg["destination"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", cfg["destination"])
            or cfg["destination"].startswith("replace")
            or not isinstance(cfg["conversation"], str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", cfg["conversation"])):
        raise ValueError("pin a dedicated bot, private chat and human sender")
    return cfg


def load_tokens(path: Path, inspect: bool = False) -> dict[str, str]:
    with private_open(path, "r") as handle:
        tokens = json.load(handle)
    expected = [{"bot_token"}, {"bot_token", "conversation_token"}] if inspect else [{"bot_token", "conversation_token"}]
    if (set(tokens) not in expected or not isinstance(tokens["bot_token"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,100}", tokens["bot_token"])
            or (not inspect and (not isinstance(tokens["conversation_token"], str)
                                or len(tokens["conversation_token"]) < 32))):
        raise ValueError("invalid private transport credentials")
    return tokens


def bot_call(token: str, method: str, body: dict[str, Any]) -> Any:
    if method not in {"getMe", "getWebhookInfo", "getUpdates"}:
        raise ValueError("bot method outside read-only ingress contract")
    result = post("https://api.telegram.org/bot" + token + "/" + method, body)
    if not isinstance(result, dict) or result.get("ok") is not True or "result" not in result:
        raise ValueError("Telegram ingress unavailable")
    return result["result"]


def qualify(call: Callable[[str, dict[str, Any]], Any], bot_id: str | None = None) -> dict[str, Any]:
    me = call("getMe", {})
    if (not isinstance(me, dict) or type(me.get("id")) is not int or me.get("is_bot") is not True
            or (bot_id is not None and str(me["id"]) != bot_id)):
        raise ValueError("dedicated bot identity mismatch")
    webhook = call("getWebhookInfo", {})
    if not isinstance(webhook, dict) or webhook.get("url") != "":
        raise ValueError("existing webhook owner; do not steal bot ingress")
    return me


def envelope(update: Any, cfg: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(update, dict) or type(update.get("update_id")) is not int or not 0 <= update["update_id"] < 2**53:
        raise ValueError("invalid update identity")
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat, sender, text = message.get("chat"), message.get("from"), message.get("text")
    if (not isinstance(chat, dict) or chat.get("type") != "private" or str(chat.get("id")) != cfg["chat_id"]
            or not isinstance(sender, dict) or str(sender.get("id")) != cfg["user_id"]
            or sender.get("is_bot") is not False or not isinstance(text, str)
            or not 1 <= len(text) <= 4000 or not text.strip()):
        return None
    return {"conversation": cfg["conversation"], "destination": cfg["destination"],
            "request_id": f"telegram:{cfg['bot_id']}:{update['update_id']}", "text": text}


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reconnect_safe(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or 500 <= error.code <= 599
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


class Cursor:
    def __init__(self, directory: Path, cfg: dict[str, Any], supervised: bool = False):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = directory.lstat()
        if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 0o077:
            raise ValueError("cursor directory must be private and operator-owned")
        self.path, self.identity = directory / "telegram-cursor.json", cfg
        self.admission = directory / "telegram-admission.json"
        self.supervised = supervised
        self.lock = private_open(directory / "telegram.lock", "a", True)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.offset, self.paused = 0, False
            if self.path.exists():
                with private_open(self.path, "r") as handle:
                    value = json.load(handle)
                if (set(value) != {"identity", "offset", "paused"} or value["identity"] != cfg
                        or type(value["offset"]) is not int or not 0 <= value["offset"] <= 2**53
                        or type(value["paused"]) is not bool):
                    raise ValueError("cursor belongs to another identity or is invalid")
                self.offset, self.paused = value["offset"], value["paused"]
            if self.paused:
                raise ValueError("reconcile the adapter turn before resuming the paused cursor")
            if supervised and self.admission.exists():
                with private_open(self.admission, "r") as handle:
                    pending = json.load(handle)
                if (set(pending) != {"update_id"} or type(pending["update_id"]) is not int
                        or not 0 <= pending["update_id"] < 2**53
                        or self.offset <= pending["update_id"]):
                    raise ValueError("reconcile the interrupted adapter admission before restart")
                self.admission.unlink()
                sync_directory(directory)
        except Exception:
            self.lock.close()
            raise

    def begin_admission(self, update_id: int) -> None:
        if not self.supervised:
            return
        descriptor = os.open(self.admission, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"update_id": update_id}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        sync_directory(self.path.parent)

    def advance(self, update_id: int, paused: bool = False) -> None:
        value = {"identity": self.identity, "offset": update_id + 1, "paused": paused}
        temporary = self.path.with_suffix(".tmp")
        with private_open(temporary, "w", True) as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        sync_directory(self.path.parent)
        self.offset, self.paused = value["offset"], paused
        if self.supervised and not paused and self.admission.exists():
            self.admission.unlink()
            sync_directory(self.path.parent)


def poll(call: Callable[[str, dict[str, Any]], Any], body: dict[str, Any],
         wait: Callable[[float], None] = time.sleep) -> Any:
    for attempt in range(5):
        try:
            return call("getUpdates", body)
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError):
                code = error.code
                error.close()
                if code != 429 and not 500 <= code <= 599:
                    raise
            if attempt == 4:
                raise
            wait(min(30, 2 ** (attempt + 1)))


def receive_once(cursor: Cursor, cfg: dict[str, Any], call: Callable[[str, dict[str, Any]], Any],
                 submit: Callable[[dict[str, Any]], Any]) -> None:
    updates = poll(call, {"offset": cursor.offset, "limit": 1, "timeout": 20, "allowed_updates": ["message"]})
    if not isinstance(updates, list) or len(updates) > 1:
        raise ValueError("invalid bounded update batch")
    for update in updates:
        body = envelope(update, cfg)
        if update["update_id"] < cursor.offset:
            raise ValueError("Telegram returned an older update")
        paused = False
        if body is not None:
            cursor.begin_admission(update["update_id"])
            receipt = submit(body)
            if not isinstance(receipt, dict) or receipt.get("state") not in {"completed", "running", "uncertain"}:
                raise ValueError("unconfirmed adapter admission; preserve cursor")
            paused = receipt["state"] != "completed"
        cursor.advance(update["update_id"], paused)
        if paused:
            raise ValueError("turn requires reconciliation; receiver paused")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("store-token", "inspect", "run"))
    parser.add_argument("--tokens", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--supervised", action="store_true",
                        help="allow safe reconnects; durably stop interrupted admissions for reconciliation")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.mode == "store-token":
            if not sys.stdin.isatty():
                raise ValueError("store the token from an interactive Terminal with hidden input")
            if args.tokens.exists() or args.tokens.is_symlink():
                raise ValueError("refuse to overwrite an existing token file")
            args.tokens.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = args.tokens.parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("select a private operator-owned token directory")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("New dedicated BotFather token (hidden): ")
            if not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,100}", token):
                raise ValueError("invalid bot token")
            descriptor = os.open(args.tokens, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"bot_token": token}, handle)
            print("Dedicated bot token saved privately. Receiver remains inactive.")
            return
        tokens = load_tokens(args.tokens, args.mode == "inspect")
        call = lambda method, body: bot_call(tokens["bot_token"], method, body)
        cfg = load_config(args.config) if args.mode == "run" and args.config else None
        if args.mode == "run" and (cfg is None or args.state is None):
            raise ValueError("run requires pinned configuration and private cursor directory")
        me = qualify(call, cfg["bot_id"] if cfg else None)
        if args.mode == "inspect":
            # No offset: inspect does not acknowledge updates or enroll the first sender.
            updates = call("getUpdates", {"limit": 100, "timeout": 0, "allowed_updates": ["message"]})
            candidates = set()
            for update in updates:
                msg = update.get("message", {})
                if (msg.get("text") == "/start" and msg.get("chat", {}).get("type") == "private"
                        and msg.get("from", {}).get("is_bot") is False):
                    candidates.add((str(msg["chat"]["id"]), str(msg["from"]["id"])))
            print(json.dumps({"bot_id": str(me["id"]), "username": me.get("username"),
                              "start_candidates": [{"chat_id": c, "user_id": u} for c, u in sorted(candidates)]}))
            return
        cursor = Cursor(args.state, cfg, args.supervised)
        submit = lambda body: post("http://127.0.0.1:18793/conversation", body, tokens["conversation_token"], 75)
        while True:
            receive_once(cursor, cfg, call, submit)
    except Exception as error:
        # urllib exceptions contain the secret-bearing URL; never print exceptions or traces.
        print("Telegram receiver stopped safely. Check private configuration, bot ingress ownership and adapter/cursor reconciliation.", file=sys.stderr)
        # launchd restarts nonzero exits. An admission may have reached the model;
        # leave its durable guard and stop automatic retries even after SIGKILL.
        admission = args.state / "telegram-admission.json" if args.state else None
        restart = (args.supervised and reconnect_safe(error)
                   and admission is not None and not admission.exists())
        raise SystemExit(1 if not args.supervised or restart else 0) from None


if __name__ == "__main__":
    main()
