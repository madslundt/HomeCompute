#!/usr/bin/env python3
"""Read a bounded scheduled briefing through the authenticated context tool API."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from assistant_context_briefings import load_config
from assistant_context_tools import ContextClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/var/lib/homecompute-assistant/context-client.json"))
    parser.add_argument("--window", choices=("today", "tomorrow", "week"), default="today")
    args = parser.parse_args()
    load_config(ROOT / "config/assistant-briefings.json")
    result = ContextClient(args.config).request("homecompute_context_briefing", {"window": args.window})
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
