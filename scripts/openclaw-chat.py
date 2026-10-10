#!/usr/bin/env python3
"""Private operator console for the synthetic OpenClaw canary; no delivery override."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any, Callable, Iterator
import uuid

CONVERSATION = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
MAX_MESSAGE = 16384
MAX_RECEIPT = 2 * 1024 * 1024
STATE = Path.home() / ".local/state/homecompute/openclaw-chat"
PREFIX = ["env", "PATH=/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:/usr/local/bin:/usr/bin:/bin",
          "NEMOCLAW_GATEWAY_PORT=9123", "nemoclaw", "agent-openclaw", "exec", "--", "openclaw"]


class ChatError(Exception):
    """Safe operator-facing failure; native diagnostics stay private."""


def ssh_command(arguments: list[str]) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            "-J", "home-core", "-i", str(Path.home() / ".ssh/id_ed25519_ai-services-01"),
            "hermes-operator@10.77.20.2", shlex.join(PREFIX + arguments)]


def run_native(arguments: list[str], runner: Callable[..., Any] = subprocess.run, *,
               allow_completed_tools: bool = False) -> dict[str, Any]:
    try:
        result = runner(ssh_command(arguments), capture_output=True, text=True, timeout=65,
                        stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise ChatError("Connection ended without a verified receipt. No retry was attempted.") from None
    if result.returncode != 0 and not (allow_completed_tools and result.returncode == 1
                                      and arguments[0] == "agent" and "--session-id" in arguments):
        raise ChatError("Native command failed; outcome may be uncertain. No retry was attempted.")
    if len(result.stdout.encode()) > MAX_RECEIPT:
        raise ChatError("Native receipt exceeded its budget; no retry was attempted.")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ChatError("Native receipt was invalid; no retry was attempted.") from None
    if not isinstance(value, dict):
        raise ChatError("Native receipt must be an object.")
    if result.returncode != 0:
        # This pinned CLI returns 1 for completed non-replayable tool turns.
        # Only a verified terminal receipt can distinguish that from failure.
        projected = parse_turn(value, arguments[arguments.index("--session-id") + 1])
        if projected["replay_safe"] is not False:
            raise ChatError("Native command failed without a completed tool receipt. No retry was attempted.")
    return value


def private_file(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(fd)
        raise ChatError("Conversation files must be owned by you with mode 0600.")
    return fd


@contextmanager
def conversation(name: str, directory: Path = STATE) -> Iterator[tuple[dict[str, Any], Callable[[], None]]]:
    if not isinstance(name, str) or not CONVERSATION.fullmatch(name):
        raise ChatError("Conversation name must use lowercase letters, numbers, underscore or hyphen.")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ChatError("Conversation directory must be owned by you with mode 0700.")
    lock = private_file(directory / (name + ".lock"))
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ChatError("This conversation already has a turn in progress.") from None
        path = directory / (name + ".json")
        fd = private_file(path)
        try:
            content = os.read(fd, 8193)
            if len(content) > 8192:
                raise ChatError("Conversation state exceeded its budget.")
            if content:
                try:
                    state = json.loads(content)
                    if (set(state) != {"session_id", "uncertain"} or type(state["uncertain"]) is not bool
                            or str(uuid.UUID(state["session_id"])) != state["session_id"]):
                        raise ValueError()
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise ChatError("Invalid conversation state; no remote command was sent.") from None
            else:
                state = {"session_id": str(uuid.uuid4()), "uncertain": False}

            def save() -> None:
                encoded = json.dumps(state).encode()
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, encoded)
                os.ftruncate(fd, len(encoded))
                os.fsync(fd)

            save()
            yield state, save
        finally:
            os.close(fd)
    finally:
        os.close(lock)


def parse_turn(value: dict[str, Any], session_id: str) -> dict[str, Any]:
    try:
        result = value["result"]
        meta = result["meta"]
        agent = meta["agentMeta"]
        receipt = agent["terminalReceipt"]
        if (value["status"] != "ok" or type(meta["replayInvalid"]) is not bool
                or meta["aborted"] is not False or receipt["rerouted"] is not False
                or agent["sessionId"] != session_id or receipt["sessionId"] != session_id
                or agent["provider"] != "inference" or agent["model"] != "automation-moe"
                or receipt["effective"]["provider"] != "inference"
                or receipt["effective"]["model"] != "automation-moe"
                or meta.get("error") is not None or meta.get("stopReason") == "error"):
            raise ValueError()
        tools = receipt["successfulToolNames"]
        if not isinstance(tools, list) or any(t not in {"read", "browser", "exec", "session_status", "memory_search", "memory_get"} for t in tools):
            raise ValueError()
        effect_tools = bool(set(tools).intersection({"browser", "exec"}))
        if effect_tools:
            if (receipt.get("terminalDisposition") != "visible"
                    or not isinstance(value.get("runId"), str) or not value["runId"]
                    or receipt.get("runId") != value["runId"]
                    or not isinstance(receipt.get("turnId"), str) or not receipt["turnId"]
                    or not set(tools).issubset({"read", "browser", "exec"})):
                raise ValueError()
        elif meta["replayInvalid"]:
            raise ValueError()
        payloads = result["payloads"]
        if not isinstance(payloads, list) or not 1 <= len(payloads) <= 16:
            raise ValueError()
        texts = []
        for payload in payloads:
            if (not isinstance(payload, dict) or not isinstance(payload.get("text"), str)
                    or payload.get("isError") is True):
                raise ValueError()
            texts.append(payload["text"])
        text = "\n".join(texts)
        if not text.strip() or len(text.encode()) > 65536:
            raise ValueError()
        return {"text": text, "provider": "inference", "model": "automation-moe",
                "session_id": session_id, "verified": True,
                "replay_safe": not meta["replayInvalid"], "successful_tools": tools}
    except (KeyError, TypeError, ValueError):
        raise ChatError("Turn lacked a safe completed receipt or had possible side effects. No retry was attempted; this conversation is paused for operator reconciliation.") from None


def send(message: str, name: str, *, directory: Path = STATE,
         runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if not isinstance(message, str) or not message.strip() or "\0" in message or len(message.encode()) > MAX_MESSAGE:
        raise ChatError("Message must contain 1-16384 UTF-8 bytes and no NUL characters.")
    with conversation(name, directory) as (state, save):
        if state["uncertain"]:
            raise ChatError("Conversation is paused after an uncertain turn; reconcile its native session before continuing. No remote command was sent.")
        # Persist before dispatch: interruption cannot silently replay a turn.
        state["uncertain"] = True
        save()
        value = run_native(["agent", "--agent", "main", "--session-id", state["session_id"],
                            "--message", message, "--timeout", "45", "--thinking", "off", "--json"], runner,
                           allow_completed_tools=True)
        answer = parse_turn(value, state["session_id"])
        state["uncertain"] = False
        save()
        return answer


def status(name: str, *, directory: Path = STATE,
           runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    with conversation(name, directory) as (state, _):
        result = run_native(["gateway", "health", "--timeout", "10000", "--json"], runner)
        if type(result.get("ok")) is not bool:
            raise ChatError("Native health response did not include a verified health flag.")
        return {"gateway_healthy": result["ok"], "conversation": name,
                "session_id": state["session_id"], "uncertain_turn": state["uncertain"],
                "agent": "main", "gateway_port": 9123, "scope": "synthetic-only"}


def visible(text: str) -> str:
    # Prevent terminal escape/control sequences from untrusted assistant text.
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["chat", "send", "status"], default="chat")
    parser.add_argument("--conversation", default="main", help="private local conversation name")
    parser.add_argument("--json", action="store_true", help="print only projected receipts")
    args = parser.parse_args()
    try:
        if args.action == "status":
            print(json.dumps(status(args.conversation), sort_keys=True))
        elif args.action == "chat" and sys.stdin.isatty():
            print("Synthetic canary. Enter /status or /quit. Consequential actions require separate operator approval.")
            while True:
                try:
                    message = input("You: ")
                except EOFError:
                    break
                if message == "/quit":
                    break
                if message == "/status":
                    print(json.dumps(status(args.conversation), sort_keys=True))
                elif message.strip():
                    answer = send(message, args.conversation)
                    print(json.dumps(answer) if args.json else "OpenClaw: " + visible(answer["text"]))
        else:
            message = sys.stdin.read(MAX_MESSAGE + 1)
            answer = send(message, args.conversation)
            print(json.dumps(answer) if args.json else visible(answer["text"]))
        return 0
    except (ChatError, OSError) as error:
        print(str(error) if isinstance(error, ChatError) else "Private conversation state or SSH unavailable.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; no retry attempted. An in-flight conversation remains paused.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
