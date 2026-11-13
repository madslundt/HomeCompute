#!/usr/bin/env python3
"""Apply the HomeCompute reasoning-model test-budget compatibility patch."""

from __future__ import annotations

import sys
from pathlib import Path


OLD = '"temperature": 0, "max_tokens": 16'
NEW = '"temperature": 0, "max_tokens": 128'


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    count = source.count(OLD)
    if count == 1:
        path.write_text(source.replace(OLD, NEW), encoding="utf-8")
        return True
    if count == 0 and source.count(NEW) == 1:
        return False
    raise SystemExit("PodWave test-budget patch target changed; review the upstream source before rebuilding.")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-upstream.py PATH_TO_SERVER_SOURCE")
    changed = apply(Path(sys.argv[1]))
    print("Applied reasoning-model probe budget patch." if changed else "Reasoning-model probe budget patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
