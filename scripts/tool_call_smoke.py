#!/usr/bin/env python3
"""Probe OpenAI-compatible tool selection and response parsing for one route."""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


TOOL = {
    "type": "function",
    "function": {
        "name": "get_temperature",
        "description": "Read the temperature in a room",
        "parameters": {
            "type": "object",
            "properties": {"room": {"type": "string"}},
            "required": ["room"],
            "additionalProperties": False,
        },
    },
}


def probe(base_url: str, model: str, api_key: str, choice: str, ca_file: str | None) -> dict:
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Use the supplied function to answer. Do not answer directly."},
            {"role": "user", "content": "Read the temperature in the kitchen."},
        ],
        "tools": [TOOL],
        "tool_choice": choice,
        "temperature": 0,
        "max_tokens": 160,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(request_body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context(cafile=ca_file)
    try:
        with urllib.request.urlopen(request, timeout=300, context=context) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{model} tool_choice={choice} request failed: {error}") from error

    try:
        message = result["choices"][0]["message"]
        calls = message["tool_calls"]
        call = calls[0]
        function = call["function"]
        arguments = function["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if function["name"] != "get_temperature" or not isinstance(arguments, dict):
            raise ValueError("wrong function or invalid arguments")
        if not str(arguments.get("room", "")).strip():
            raise ValueError("room argument is missing")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{model} tool_choice={choice} returned no valid get_temperature tool call") from error
    return {"model": result.get("model"), "tool_choice": choice, "tool": function["name"], "arguments": arguments}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    key_source = parser.add_mutually_exclusive_group(required=True)
    key_source.add_argument("--api-key-file")
    key_source.add_argument("--api-key-env", help="Environment variable containing the API key")
    parser.add_argument("--ca-file")
    parser.add_argument("--choices", nargs="+", choices=("required", "auto"), default=("required", "auto"))
    args = parser.parse_args(argv)
    key = (
        Path(args.api_key_file).read_text(encoding="utf-8").strip()
        if args.api_key_file
        else os.environ.get(args.api_key_env, "").strip()
    )
    if not key:
        parser.error("API key file is empty")
    try:
        for choice in args.choices:
            print(json.dumps(probe(args.base_url, args.model, key, choice, args.ca_file), ensure_ascii=False))
    except RuntimeError as error:
        print(f"tool-call smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
