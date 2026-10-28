#!/usr/bin/env python3
"""Readiness of the local Wyoming listener AND its private inference dependency."""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
from urllib.parse import urlsplit


def probe(kind: str) -> None:
    if kind == "stt":
        import importlib.util
        spec = importlib.util.spec_from_file_location("wyoming_openai_stt", "/opt/homecompute/wyoming-openai-stt.py")
        adapter = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = adapter
        spec.loader.exec_module(adapter)
        config = adapter.load_config()
        url = config.upstream_url.removesuffix("/v1/audio/transcriptions") + "/health"
        headers = {"Authorization": "Bearer " + config.api_key.decode()}
        port, capability = config.port, "asr"
    elif kind == "tts":
        upstream = os.environ.get("PLAPRE_UPSTREAM_URL")
        if upstream is None:
            raise ValueError("missing fixed inference upstream")
        parsed = urlsplit(upstream)
        if parsed.scheme != "http" or parsed.hostname not in {"172.28.200.1", "10.77.10.10"} or parsed.port not in {18201, 8004}:
            raise ValueError("unapproved inference upstream")
        url, headers = upstream + "/health", {}
        port, capability = 10201, "tts"
    else:
        raise ValueError("unknown speech service")
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
        if response.status != 200:
            raise ValueError("inference dependency unavailable")
        response.read(1024)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(b'{"type":"describe"}\n')
        stream = connection.makefile("rb")
        header = json.loads(stream.readline(65537))
        size = header.get("data_length", 0)
        if not isinstance(size, int) or not 0 < size <= 65536:
            raise ValueError("invalid Wyoming response")
        data = json.loads(stream.read(size))
        if header.get("type") != "info" or not data.get(capability):
            raise ValueError("Wyoming service unavailable")


if __name__ == "__main__":
    try:
        probe(sys.argv[1])
    except Exception:
        # Neither a body nor credentials belong in healthcheck logs.
        raise SystemExit("speech readiness failed")
