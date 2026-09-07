#!/usr/bin/env python3
"""Merge trusted legacy compute values into the current configuration template."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

KEY_VALUE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
REMOVED_KEYS = {"FIREWALL_CONFIRMED"}


def parse_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = KEY_VALUE.fullmatch(line)
        if match is None:
            raise ValueError(f"invalid KEY=VALUE line in {path}")
        key = match.group(1)
        if key in values:
            raise ValueError(f"duplicate key in {path}: {key}")
        values[key] = match.group(2)
    return values


def migrate(existing: Path, template: Path, output: Path) -> None:
    old_values = parse_values(existing)
    template_values = parse_values(template)
    unknown = set(old_values) - set(template_values) - REMOVED_KEYS
    if unknown:
        raise ValueError(f"legacy configuration has unknown keys: {', '.join(sorted(unknown))}")

    rendered: list[str] = []
    for raw_line in template.read_text(encoding="utf-8").splitlines():
        match = KEY_VALUE.fullmatch(raw_line.strip())
        if match is not None and match.group(1) in old_values:
            rendered.append(f"{match.group(1)}={old_values[match.group(1)]}")
        else:
            rendered.append(raw_line)
    output.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("existing", type=Path)
    parser.add_argument("template", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    migrate(arguments.existing, arguments.template, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
