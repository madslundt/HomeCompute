#!/usr/bin/env python3
"""Recognize only a fresh platform snapshot with NAS backup as its sole miss."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys
import time

MAX_BYTES = 16 * 1024
MAX_AGE_SECONDS = 180
FUTURE_SKEW_SECONDS = 30
METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
REQUIRED_HEALTHY = {
    "homecompute_edge_process_healthy",
    "homecompute_n8n_ready",
    "homecompute_n8n_edge_ready",
    "homecompute_wyoming_tts_ready",
    "homecompute_wyoming_stt_ready",
    "homecompute_text_ready",
    "homecompute_embedding_ready",
    "homecompute_stt_danish_ready",
    "homecompute_stt_multilingual_ready",
    "homecompute_tts_ready",
    "homecompute_nas_check_ready",
}
BACKUP_METRIC = "homecompute_nas_backup_ready"
PLATFORM_METRIC = "homecompute_platform_ready"


def only_backup_deferred(path: Path, *, now: float | None = None,
                         expected_uid: int | None = None) -> bool:
    """Fail closed unless recent root-owned metrics show only backup freshness false."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            info = os.fstat(stream.fileno())
            uid = os.geteuid() if expected_uid is None else expected_uid
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                    or info.st_mode & 0o022 or not 0 < info.st_size <= MAX_BYTES):
                return False
            current = time.time() if now is None else now
            age = current - info.st_mtime
            if age < -FUTURE_SKEW_SECONDS or age > MAX_AGE_SECONDS:
                return False
            raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return False
        metrics: dict[str, int] = {}
        for line in raw.decode("ascii").splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 2 or not METRIC_NAME.fullmatch(fields[0]) or fields[1] not in {"0", "1"}:
                return False
            if fields[0] in metrics:
                return False
            metrics[fields[0]] = int(fields[1])
        if not REQUIRED_HEALTHY.issubset(metrics):
            return False
        if metrics.get(BACKUP_METRIC) != 0 or metrics.get(PLATFORM_METRIC) != 0:
            return False
        return all(value == 1 for name, value in metrics.items()
                   if name not in {BACKUP_METRIC, PLATFORM_METRIC})
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: platform-health-backup-deferral.py METRICS_FILE", file=sys.stderr)
        return 2
    return 0 if only_backup_deferred(Path(argv[1])) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
