#!/usr/bin/env python3
"""Validate pinned CUDA ELF dependencies during image build."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


def validate_ldd_output(output: str) -> tuple[bool, str]:
    if not output.strip():
        return False, "dynamic dependency report is empty"
    missing = re.findall(r"^\s*(\S+)\s+=>\s+not found\s*$", output, re.MULTILINE)
    unexpected = sorted(set(missing) - {"libcuda.so.1"})
    if unexpected:
        return False, "unresolved shared libraries: " + ", ".join(unexpected)
    cuda = re.search(r"^\s*libggml-cuda\.so(?:\.\d+)*\s+=>\s+(/\S+)", output, re.MULTILINE)
    if not cuda or not cuda.group(1).startswith("/usr/local/lib/libggml-cuda"):
        return False, "CUDA backend shared library is not resolved from the image"
    if "libcuda.so.1" in missing:
        return True, "CUDA backend resolved; host driver libcuda.so.1 is runtime-injected"
    return True, "CUDA backend and driver dependency resolved"


def validate_driver_probe(output: str) -> tuple[bool, str]:
    if not output.strip():
        return False, "driver link probe report is empty"
    missing = re.findall(r"^\s*(\S+)\s+=>\s+not found\s*$", output, re.MULTILINE)
    libc = re.search(r"^\s*libc\.so\.6\s+=>\s+/\S+", output, re.MULTILINE)
    if set(missing) != {"libcuda.so.1"} or not libc:
        return False, "CUDA SDK driver-stub probe did not isolate libcuda.so.1"
    return True, "linked SDK driver probe confirms libcuda.so.1 is host-injected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver-probe", action="store_true")
    parser.add_argument("ldd_output", type=Path)
    args = parser.parse_args()
    try:
        output = args.ldd_output.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("CUDA linkage report is unavailable", file=sys.stderr)
        return 2
    valid, message = (validate_driver_probe(output) if args.driver_probe
                      else validate_ldd_output(output))
    if not valid:
        print(message, file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
