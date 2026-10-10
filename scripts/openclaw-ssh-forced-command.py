#!/usr/bin/env python3
"""Restricted SSH entry point for the home-core OpenClaw adapter key.

Install root-owned at /usr/local/libexec/homecompute-openclaw-command.py and
invoke with /usr/bin/python3 -I from that key's authorized_keys forced command.
Only the fixed console transport's main-agent turn and gateway health are
accepted. Neither shell syntax nor client-provided environment is executed.
"""
from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import sys
import time
import uuid

HOME = "/home/hermes-operator"
PATH = HOME + "/.local/bin:" + HOME + "/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
PREFIX = ["env", "PATH=" + PATH, "NEMOCLAW_GATEWAY_PORT=9123", "nemoclaw",
          "agent-openclaw", "exec", "--", "openclaw"]
HEALTH = ["gateway", "health", "--timeout", "10000", "--json"]
MAX_MESSAGE = 16384
MAX_COMMAND = MAX_MESSAGE * 5 + 4096
MAX_OUTPUT = 2 * 1024 * 1024
ENV = {"HOME": HOME, "USER": "hermes-operator", "LOGNAME": "hermes-operator",
       "PATH": HOME + "/.nvm/versions/node/v22.23.3/bin:" + PATH,
       "NEMOCLAW_GATEWAY_PORT": "9123", "LANG": "C.UTF-8", "NO_COLOR": "1", "CI": "1"}


class Refused(Exception):
    """Fixed diagnostics only; never echo untrusted commands or native output."""


def validate_command(command: str) -> list[str]:
    try:
        if not command or "\0" in command or len(command.encode()) > MAX_COMMAND:
            raise Refused("command-budget-refused")
        arguments = shlex.split(command, posix=True)
        if arguments[:len(PREFIX)] != PREFIX:
            raise Refused("command-prefix-refused")
        arguments = arguments[len(PREFIX):]
        if arguments == HEALTH:
            return arguments
        if (len(arguments) != 12 or arguments[:4] != ["agent", "--agent", "main", "--session-id"]
                or arguments[5] != "--message"
                or arguments[7:] != ["--timeout", "45", "--thinking", "off", "--json"]):
            raise Refused("command-form-refused")
        if str(uuid.UUID(arguments[4])) != arguments[4]:
            raise Refused("session-id-refused")
        message = arguments[6]
        if not message.strip() or "\0" in message or not 1 <= len(message.encode()) <= MAX_MESSAGE:
            raise Refused("message-budget-refused")
        return arguments
    except (ValueError, UnicodeError, AttributeError):
        raise Refused("command-encoding-refused") from None


def execute(arguments: list[str]) -> tuple[int, bytes]:
    timeout = 60 if arguments[0] == "agent" else 25
    argv = [HOME + "/.local/bin/nemoclaw", "agent-openclaw", "exec", "--", "openclaw", *arguments]
    child = subprocess.Popen(argv, env=ENV.copy(), cwd=HOME, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
    output = bytearray()
    deadline = time.monotonic() + timeout
    previous = {}

    def interrupted(_signum, _frame):
        raise Refused("command-interrupted")

    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[signum] = signal.signal(signum, interrupted)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refused("command-timeout")
                for key, _ in selector.select(min(remaining, 1)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        if len(output) + len(data) > MAX_OUTPUT:
                            raise Refused("output-budget-refused")
                        output.extend(data)
            try:
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise Refused("command-timeout") from None
        return code, bytes(output)
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if child.poll() is None:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait()
        child.stdout.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main() -> int:
    try:
        arguments = validate_command(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
        code, output = execute(arguments)
        sys.stdout.buffer.write(output)
        return code if 0 <= code <= 255 else 1
    except Refused as error:
        print("OpenClaw SSH command refused: " + str(error), file=sys.stderr)
        return 2
    except OSError:
        print("OpenClaw SSH command unavailable.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
