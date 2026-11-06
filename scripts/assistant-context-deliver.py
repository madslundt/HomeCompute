#!/usr/bin/env python3
"""Explicit one-shot delivery candidate; default behavior only records a shadow row."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import fcntl

from assistant_attention import AttentionLedger
from assistant_context_delivery import DeliveryOutbox, load_delivery_auth, read_source_coverage
from assistant_context_store import ContextStore, Scope

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path("/srv/state/assistant-context")
DEFAULT_AUTH = Path("/etc/homecompute-assistant-context/delivery-auth.json")


@contextmanager
def _single_cli_runner(state: Path):
    """Keep concurrent one-shot runners from recovering or claiming each other's work."""
    path = state / "delivery.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("Delivery lock must be owned by the service user and mode 0600")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another delivery process is active") from error
        yield
    finally:
        os.close(fd)


def _private_state(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Context state directory must exist and be a real directory")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Context state directory must be owned by the service user and mode 0700")
    for name in ("context.sqlite3", "attention.sqlite3", "coverage.sqlite3"):
        candidate = path / name
        if (candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_uid != os.geteuid()
                or stat.S_IMODE(candidate.stat().st_mode) != 0o600):
            raise ValueError(f"Expected private existing state file: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--apply", action="store_true", help="one fixed-route attempt after active-policy and owner approval gates")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(4100)
        if len(raw) > 4000:
            raise ValueError("message input exceeds 4000 bytes")
        message = raw.decode("utf-8")
        policy = json.loads((ROOT / "config/assistant-attention.json").read_text(encoding="utf-8"))
        if args.apply and policy.get("mode") != "active":
            raise ValueError("--apply requires the source policy to be active")
        now = datetime.now(timezone.utc)
        auth = load_delivery_auth(DEFAULT_AUTH, now=now) if args.apply else None
        _private_state(args.state_dir)
        scope = Scope("owner", "personal", "private")
        store = ContextStore(args.state_dir / "context.sqlite3")
        ledger = AttentionLedger(args.state_dir / "attention.sqlite3", dry_run=policy.get("mode") != "active")
        with _single_cli_runner(args.state_dir):
            outbox = DeliveryOutbox(args.state_dir / "delivery.sqlite3")
            outbox.recover_inflight(scope=scope, now=now)
            coverage = read_source_coverage(args.state_dir / "coverage.sqlite3", scope=scope, now=now,
                max_age_minutes=int(policy.get("freshness_max_age_minutes", 1440)))
            row = outbox.admit(scope=scope, decision_id=args.decision_id, message=message,
                ledger=ledger, context=store, source_status=coverage, policy=policy, now=now,
                dry_run=not args.apply)
            if not args.apply or row["status"] != "pending":
                print(json.dumps({"status": row["status"], "decision_id": row["decision_id"],
                                  "delivery_id": row["delivery_id"], "route": row["route_name"]}, sort_keys=True))
                return 0
            result = outbox.send_once(scope=scope, decision_id=args.decision_id, ledger=ledger,
                context=store, source_status=lambda: read_source_coverage(args.state_dir / "coverage.sqlite3",
                    scope=scope, now=datetime.now(timezone.utc),
                    max_age_minutes=int(policy.get("freshness_max_age_minutes", 1440))),
                policy=policy, auth=auth, now=now)
            print(json.dumps({"status": result["status"], "decision_id": result["decision_id"],
                              "delivery_id": result["delivery_id"], "hermes_delivery_id": result.get("hermes_delivery_id"),
                              "response_status": result.get("response_status")}, sort_keys=True))
            return 0 if result["status"] == "delivered" else 2
    except Exception as error:
        print(json.dumps({"error": "delivery_refused", "reason_code": type(error).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
