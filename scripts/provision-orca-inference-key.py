#!/usr/bin/env python3
"""Create or verify the Orca relay's model-only LiteLLM credential."""

from pathlib import Path
import os
import runpy
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path("/var/lib/homecompute/orca")


def main():
    release = sys.argv[1]
    helper = runpy.run_path(str(ROOT / "scripts/setup-assistant-context-inference-credentials.py"))
    helper["published_revision"](release, ROOT)
    metadata = DIRECTORY.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("Orca credential directory must be root-owned and private")
    lock = helper["acquire_setup_lock"]()
    try:
        setup = helper["InferenceKeySetup"](key_specs={
            "orca": {
                "path": DIRECTORY / "inference-key",
                "owner": "orca-inference",
                "model": "automation-moe",
                "purpose": "Private Orca OpenCode inference relay",
                "verify_mcp_denied": True,
            },
        }, root_uid=10001, root_gid=10001)
        plan = setup.plan(release)
        result = setup.apply(release, plan["plan_sha256"])
        print(f"Orca inference key scope verified ({result['status']}).")
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        main()
    except (IndexError, OSError, ValueError, RuntimeError):
        sys.exit("Orca inference credential setup refused; inspect its owner-only state.")
