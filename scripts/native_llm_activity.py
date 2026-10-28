#!/usr/bin/env python3
"""Fixed authenticated native gateway inventory; no chat data or model requests."""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from typing import Callable

REMOTE = ('env PATH=/home/hermes-operator/.local/bin:/home/hermes-operator/.npm-global/bin:/usr/local/bin:/usr/bin:/bin '
          'NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- openclaw gateway call '
          'homecompute-safety.llm.activity --json --timeout 5000')
IDENTITY = '/var/lib/homecompute-openclaw/.ssh/'
COMMAND = ['/run/current-system/sw/bin/ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=3',
           '-o', 'ConnectionAttempts=1', '-o', 'GlobalKnownHostsFile=/dev/null',
           '-o', 'UserKnownHostsFile=' + IDENTITY + 'known_hosts', '-o', 'IdentityAgent=none',
           '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-T',
           '-i', IDENTITY + 'id_ed25519', 'hermes-operator@10.77.20.2', REMOTE]


def collect(runner: Callable = subprocess.run, clock: Callable = time.time) -> dict:
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'SSH_AUTH_SOCK'}}
    result = runner(COMMAND, capture_output=True, timeout=8, check=True, env=environment)
    if len(result.stdout) > 4096:
        raise ValueError('Native activity exceeds metadata budget')
    value = json.loads(result.stdout)
    if (not isinstance(value, dict) or set(value) != {'active_turns', 'observed_at'} or
            type(value['active_turns']) is not int or not 0 <= value['active_turns'] <= 1000000 or
            type(value['observed_at']) not in (int, float) or not math.isfinite(value['observed_at']) or
            not -5 <= clock() - value['observed_at'] <= 15):
        raise ValueError('Native activity is stale or invalid')
    return value
