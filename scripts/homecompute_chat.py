#!/usr/bin/env python3
"""Minimal stdlib example for HomeCompute's OpenAI-compatible Chat API."""
import argparse
import json
import os
import ssl
import sys
import urllib.request

TOOL = {
    "type": "function",
    "function": {
        "name": "get_temperature",
        "description": "Read the temperature in a room; do not claim to execute it.",
        "parameters": {
            "type": "object",
            "properties": {"room": {"type": "string"}},
            "required": ["room"],
            "additionalProperties": False,
        },
    },
}


def build_body(model, message, *, include_tools=False, tool_choice="auto"):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "max_tokens": 512,
    }
    if include_tools:
        body["tools"] = [TOOL]
        body["tool_choice"] = tool_choice
    return body


def render_message(message):
    """Render either ordinary text or structured tool calls without hiding either."""
    tool_calls = message.get("tool_calls")
    if tool_calls:
        return json.dumps(
            {"content": message.get("content"), "tool_calls": tool_calls},
            ensure_ascii=False,
            indent=2,
        )
    return message.get("content") or ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="Text sent as the user message")
    parser.add_argument(
        "--model",
        default="automation",
        help=(
            "Requested stable HomeCompute capability (default: automation); this does not grant access. "
            "The deployed client credentials still need migration to the stable alias."
        ),
    )
    parser.add_argument("--tools", action="store_true", help="Offer the example get_temperature function")
    parser.add_argument("--tool-choice", choices=("auto", "required", "none"), default="auto")
    args = parser.parse_args(argv)
    key = os.environ.get("HOMECOMPUTE_SCRIPT_API_KEY")
    if not key:
        raise SystemExit("Set HOMECOMPUTE_SCRIPT_API_KEY (source ~/.config/homecompute/clients.env)")
    if args.tool_choice != "auto" and not args.tools:
        parser.error("--tool-choice requires --tools")

    message = " ".join(args.message) or "Reply with READY."
    body = build_body(args.model, message, include_tools=args.tools, tool_choice=args.tool_choice)
    request = urllib.request.Request(
        "https://ai.home.arpa/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    ca_path = os.environ.get("HOMECOMPUTE_CA_CERT", os.path.expanduser("~/.config/homecompute/home-core-root.crt"))
    context = ssl.create_default_context(cafile=ca_path)
    with urllib.request.urlopen(request, timeout=120, context=context) as response:
        result = json.load(response)
    print(render_message(result["choices"][0]["message"]))


if __name__ == "__main__":
    main()
