#!/usr/bin/env python3
"""Serve a text-only ACP model for the existing HomeCompute OpenBot server."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
import re
import ssl
import stat
import sys
from pathlib import Path
from threading import Lock
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


MODEL = "chatgpt-sol-latest"
ENDPOINT = "https://llm.home.arpa/v1/chat/completions"
CA_FILE = "/run/homecompute/home-core-root.crt"
MODEL_KEY_FILE = Path("/data/.local/libexec/homecompute/sol-chatgpt-models-key")
INSTRUCTIONS = (
    "You are a personal planning, reasoning, and drafting assistant. "
    "Use the text the user shares in this chat. State uncertainty clearly. "
    "You can draft actions for review and cannot run tools or perform actions."
)
MAX_INPUT = 120_000
MAX_TURNS = 12
MAX_RESPONSE = 512_000

# OpenBot 0.73.0 adds these markers to the same text block as the message.
GENERATED_INPUT = (
    "Continue this OpenBot conversation.",
    "OpenBot note, not a message from the user.",
    "The user is replying to message ",
    "The user tagged these agents in this message:",
    "This turn starts with ",
    "Message from OpenBot teammate ",
    "--- collaborator message ---",
    "Execute one run of an existing OpenBot routine now.",
    "Attached local files:",
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def model_key():
    descriptor = os.open(MODEL_KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("Sol model credential file is not private")
        secret = handle.read(4097)
    if len(secret) > 4096:
        raise ValueError("Sol model credential is invalid")
    value = secret.strip()
    if not re.fullmatch(rb"sk-[A-Za-z0-9_-]{1,4093}", value):
        raise ValueError("Sol model credential is invalid")
    return value.decode("ascii")


def infer(messages):
    request = Request(
        ENDPOINT,
        data=json.dumps({
            "model": MODEL, "messages": messages, "reasoning_effort": "medium",
            "max_completion_tokens": 8192,
        }).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {model_key()}"},
    )
    tls = ssl.create_default_context(cafile=CA_FILE)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=tls), NoRedirect())
    with opener.open(request, timeout=60) as response:
        payload = response.read(MAX_RESPONSE + 1)
    if len(payload) > MAX_RESPONSE:
        raise ValueError("Gateway response too large")
    reply = json.loads(payload)["choices"][0]["message"]["content"]
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("Gateway returned no text")
    return reply


def prompt_text(blocks):
    if not isinstance(blocks, list) or not blocks or any(
        not isinstance(block, dict) or block.get("type") != "text"
        or not isinstance(block.get("text"), str) for block in blocks
    ):
        raise ValueError("Send a text-only message")
    # Drop standalone OpenBot context, including stored memories. Accept exactly
    # one remaining block and reject wrapper text from non-chat entry points.
    text_blocks = [
        block["text"] for block in blocks
        if not block["text"].lstrip().startswith(("<openbot-", "<openbot_"))
    ]
    if len(text_blocks) != 1:
        raise ValueError("Send one direct text message")
    text, = text_blocks
    if not text.strip() or len(text) > MAX_INPUT or any(marker in text for marker in GENERATED_INPUT):
        raise ValueError("Send a plain text message without OpenBot-generated content")
    if text.startswith(("Attached local file:", "Your teammate ", "You started this task with openbot.start_task")):
        raise ValueError("Send a direct chat message")
    return text


@dataclass
class Session:
    history: list = field(default_factory=list)
    active: bool = False
    cancelled: bool = False


class Agent:
    def __init__(self, complete=infer, output=sys.stdout, preferences=""):
        self.complete = complete
        self.output = output
        if len(preferences) > 3000:
            raise ValueError("Approved preferences exceed 3000 characters")
        self.instructions = INSTRUCTIONS
        if preferences.strip():
            self.instructions += "\nUser-approved preferences:\n" + preferences.strip()
        self.sessions = {}
        self.lock = Lock()

    def send(self, **message):
        print(json.dumps({"jsonrpc": "2.0", **message}), file=self.output, flush=True)

    def error(self, request_id, code, message):
        self.send(id=request_id, error={"code": code, "message": message})

    def reply_to_prompt(self, request_id, session_id, text, session):
        # Keep context local to the ACP session and below the request limit.
        history = []
        remaining = MAX_INPUT - len(text)
        for exchange in reversed(session.history):
            size = sum(len(message["content"]) for message in exchange)
            if size > remaining:
                break
            history[:0] = exchange
            remaining -= size
        user_message = {"role": "user", "content": text}
        try:
            reply = self.complete([
                {"role": "system", "content": self.instructions}, *history, user_message,
            ])
        except Exception:
            # Provider exceptions can contain the request or response body.
            reply = None
        with self.lock:
            session.active = False
            if session.cancelled:
                self.send(id=request_id, result={"stopReason": "cancelled"})
            elif reply is None:
                self.error(request_id, -32603, "Sol gateway request failed")
            else:
                exchange = [user_message, {"role": "assistant", "content": reply}]
                session.history = [*session.history[-(MAX_TURNS - 1):], exchange]
                self.send(method="session/update", params={
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": reply},
                    },
                })
                self.send(id=request_id, result={"stopReason": "end_turn"})

    def handle(self, request, jobs):
        method, params = request.get("method"), request.get("params", {})
        request_id = request.get("id")
        session_id = params.get("sessionId")
        session = self.sessions.get(session_id)
        if method == "session/cancel":
            if session:
                session.cancelled = True
            return
        if "id" not in request:
            return
        if method == "initialize":
            self.send(id=request_id, result={
                "protocolVersion": 1, "agentCapabilities": {"loadSession": False},
                "agentInfo": {"name": "homecompute-sol", "title": "HomeCompute Sol", "version": "1"},
            })
        elif method == "session/new":
            session_id = uuid4().hex
            self.sessions[session_id] = Session()
            self.send(id=request_id, result={"sessionId": session_id, "models": {
                "currentModelId": MODEL, "availableModels": [{"modelId": MODEL, "name": "ChatGPT Sol"}],
            }})
        elif method == "session/set_model" and session and params.get("modelId") == MODEL:
            self.send(id=request_id, result={})
        elif method == "session/prompt" and session:
            if session.active:
                self.error(request_id, -32600, "A prompt is already running in this session")
                return
            text = prompt_text(params.get("prompt"))
            session.active = True
            session.cancelled = False
            jobs.submit(self.reply_to_prompt, request_id, session_id, text, session)
        else:
            self.error(request_id, -32601, "ACP method or session unavailable")

    def run(self, lines):
        with ThreadPoolExecutor(max_workers=4) as jobs:
            for line in lines:
                request_id = None
                with self.lock:
                    try:
                        request = json.loads(line)
                        request_id = request.get("id")
                        self.handle(request, jobs)
                    except (AttributeError, TypeError, ValueError):
                        self.error(request_id, -32602, "Invalid ACP request or non-text prompt")


if __name__ == "__main__":
    Agent(preferences=os.environ.get("SOL_APPROVED_PREFERENCES", "")).run(sys.stdin)
