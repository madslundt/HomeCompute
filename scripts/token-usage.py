#!/usr/bin/env python3
"""Read metadata-only LiteLLM token totals on home-core (Python stdlib only)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def usage_query(period: str, by_model: bool) -> str:
    if period not in {"day", "week", "month"}:
        raise ValueError("period must be day, week, or month")
    model = "COALESCE(NULLIF(model_group, ''), NULLIF(model, ''), 'unknown')" if by_model else "'all'::text"
    return f"""
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
WITH buckets AS (
    SELECT date_trunc('{period}', "startTime" AT TIME ZONE 'UTC' AT TIME ZONE :'timezone')::date AS period_start,
           {model} AS model,
           count(*) AS requests,
           sum(prompt_tokens)::bigint AS input_tokens,
           sum(completion_tokens)::bigint AS output_tokens,
           sum(total_tokens)::bigint AS total_tokens,
           COALESCE(sum((metadata->>'homecompute_cached_input_tokens')::bigint), 0)::bigint AS cached_input_tokens,
           COALESCE(sum(prompt_tokens) FILTER (WHERE metadata ? 'homecompute_cached_input_tokens'), 0)::bigint AS cache_tracked_input_tokens,
           count(*) FILTER (WHERE metadata ? 'homecompute_cached_input_tokens') AS cache_reported_requests
    FROM "LiteLLM_SpendLogs"
    WHERE "startTime" >= (:'since'::date::timestamp AT TIME ZONE :'timezone') AT TIME ZONE 'UTC'
      AND "startTime" < (:'until'::date::timestamp AT TIME ZONE :'timezone') AT TIME ZONE 'UTC'
    GROUP BY 1, 2
)
SELECT json_build_object(
    'tracking_since', (SELECT tracking_since FROM homecompute_token_usage_state WHERE singleton),
    'timezone', :'timezone', 'period', '{period}', 'since', :'since', 'until_exclusive', :'until',
    'rows', COALESCE((SELECT json_agg(b ORDER BY period_start, model) FROM buckets b), '[]'::json)
);
COMMIT;
"""


def date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use a date in YYYY-MM-DD format") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--period", choices=("day", "week", "month"), default="day")
    result.add_argument("--since", type=date_argument, help="first included local date (default: last 30 days)")
    result.add_argument("--until", type=date_argument, help="exclusive local end date (default: tomorrow)")
    result.add_argument("--timezone", default="Europe/Copenhagen")
    result.add_argument("--by-model", action="store_true", help="split each period by gateway model alias")
    result.add_argument("--json", action="store_true")
    result.add_argument("--publish", type=Path, help="atomically publish aggregate-only dashboard JSON to this file")
    return result


def publish_report(report: dict, destination: Path) -> None:
    # Explicit projection: the public dashboard never receives a database row,
    # key hash, client identity, or arbitrary metadata from the reporting path.
    rows = []
    for row in report["rows"]:
        rows.append({"period_start": row["period_start"], "model": row["model"],
                     **{key: int(row[key]) for key in ("requests", "input_tokens", "output_tokens", "total_tokens")},
                     **{key: int(row.get(key, 0)) for key in ("cached_input_tokens", "cache_tracked_input_tokens", "cache_reported_requests")}})
    payload = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
               "tracking_since": report["tracking_since"], "timezone": report["timezone"], "rows": rows}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, prefix=".usage-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        report_timezone = ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        cli.error("unknown timezone")
    today = datetime.now(report_timezone).date()
    if args.publish:
        args.period, args.by_model = "day", True
        args.since, args.until = date(1970, 1, 1), today + timedelta(days=1)
    since = args.since or today - timedelta(days=29)
    until = args.until or today + timedelta(days=1)
    if since >= until:
        cli.error("--since must be earlier than --until (exclusive)")
    command = [
        "docker", "exec", "-i", "homecompute-control-plane-postgres-1",
        "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "litellm",
        "-v", f"since={since.isoformat()}", "-v", f"until={until.isoformat()}",
        "-v", f"timezone={args.timezone}",
    ]
    try:
        completed = subprocess.run(command, input=usage_query(args.period, args.by_model),
                                   text=True, capture_output=True, timeout=40, check=True)
        report = json.loads(completed.stdout)
        if args.publish:
            publish_report(report, args.publish)
            return 0
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        print("Cannot read token usage. Run with sudo on home-core after installing tracking.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"Token usage ({args.timezone}, {args.period}; {since} to {until} exclusive)")
    print(f"Tracking since: {report['tracking_since']}; recent requests may take a minute to appear.")
    print(f"{'Period':10}  {'Model':28}  {'Requests':>8}  {'Input':>12}  {'Output':>12}  {'Total':>12}")
    totals = dict.fromkeys(("requests", "input_tokens", "output_tokens", "total_tokens"), 0)
    for row in report["rows"]:
        # Model aliases are server configuration, but strip control characters
        # before displaying them in an operator terminal.
        model = "".join(c for c in row["model"] if c.isprintable())
        print(f"{row['period_start']:10}  {model:28}  {row['requests']:8,}  {row['input_tokens']:12,}  {row['output_tokens']:12,}  {row['total_tokens']:12,}")
        for key in totals:
            totals[key] += row[key]
    print(f"{'TOTAL':10}  {'':28}  {totals['requests']:8,}  {totals['input_tokens']:12,}  {totals['output_tokens']:12,}  {totals['total_tokens']:12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
